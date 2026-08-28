"""生成电影/剧集/动漫/综艺/直播元数据 JSON，供 Cloudflare Workers (Jellyfin 兼容后端) 运行时 fetch。

输出:
  output/api/all.json        # 五分类合并（电影+直播+剧集+综艺+动漫），每条带 cat 字段，统一库入口
  output/api/movies.json     # 仅电影（向后兼容旧 worker）
  output/api/movies_{region}.json  # 电影地区分片（向后兼容）

all.json 结构: {"updated": "...", "count": N, "movies": [ {...}, ... ]}
每条影片字段:
  id       稳定哈希 id(按 名称|年份 生成，重生成不变)；按分类加前缀 m_/l_/t_/v_/a_ 避免跨类重名
  cat      分类: movie / live / tv / variety / anime
  name     片名
  sort     排序名(用于 SortName，按名称拼音/原始)
  region   地区桶
  year     清洗后年份(int | null)
  cover    海报 URL
  overview 简介
  url      主播放直链(国内可直连源优先)
  quality  画质
  score    评分
  hits     播放量
  pop      热度(= hits * (score/10)^2, 无评分按5分兜底)
  sources  该片所有播放线路(去重后的 URL 列表，主线路在前，供途播切换)

去重策略:
  同一部影片(名称+年份相同)在数据库里往往有多行(多采集站/多线路)，
  这里按 (名称,年份) 合并为一条（分类内去重），把多条 URL 收集进 sources，
  国内可直连源排最前。直播(live)按频道+子分类去重，每个频道保留测速后的多条线路。
"""
import json
import sys
import os
import re
import datetime
import hashlib
import sqlite3
from collections import defaultdict

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
import config  # noqa: E402
from generator.m3u import _region_bucket, _is_domestic  # noqa: E402

DB = os.path.join(ROOT, "data", "media.db")
OUT_DIR = os.path.join(ROOT, "output", "api")
# 兼容旧文件名：movies.json / movies_{region}.json
OUT_MOVIES = os.path.join(OUT_DIR, "movies.json")

# 纳入统一库的五分类（顺序 = 途播视图展示顺序）
JELLYFIN_CATS = [
    ("movie", "m_", "电影"),
    ("live", "l_", "直播"),
    ("tv", "t_", "剧集"),
    ("variety", "v_", "综艺"),
    ("anime", "a_", "动漫"),
]


def clean_sort(name: str) -> str:
    """去掉常见后缀/年份括号，保留核心用于排序"""
    n = (name or "").strip()
    n = re.sub(r"[\（\(]\d{4}[\）\)]", "", n)
    n = re.sub(r"\s*[第][\d一二三四五六七八九十百千]+[季部集话]", "", n)
    return n.strip() or (name or "")


def norm_key(name: str, year) -> tuple:
    """去重主键：名称(去年份/去季集后缀, 小写去空白) + 年份"""
    n = clean_sort(name).lower()
    n = re.sub(r"\s+", "", n)
    return (n, year)


def popularity(hits, score, lines: int = 1, year: int = None) -> float:
    """综合人气分：播放量按评分平方加权，低分大幅降权，无评分按 5 分兜底。
    无播放量时用线路数兜底（源越多越热门），避免新片/0 播放量影片被完全埋没。
    对异常高播放量取对数压缩，避免个别采集站虚报 hits 垄断首页；并给近年影片小幅加权。
    """
    import math
    h = hits or 0
    lines = max(1, lines or 1)
    try:
        s = float(score)
    except (TypeError, ValueError):
        s = 0
    s = s if (isinstance(s, (int, float)) and 0 < s <= 10) else 5.0
    # 播放量兜底：源多 => 更热门
    effective_hits = h if h > 0 else lines * 1000
    # 对数压缩：防止 80 万 vs 500 这种量级差把正常影片挤到后面
    norm_hits = math.log1p(effective_hits) * 1000
    base = norm_hits * (s / 10.0) ** 2
    # 年份越近越吃香：2020 起每年 +5%（避免老片靠虚高播放量霸榜）
    if year and year >= 2020:
        base *= (1.0 + (year - 2020) * 0.05)
    return base


def build_live(prefix: str) -> list:
    """从 live 表构建直播频道列表（按 央视/卫视/地方/港澳台 排序，同频道多线路合并）。"""
    from collector.live import list_live

    # list_live 已按 LIVE_CATEGORY_ORDER + channel_sort_key + latency 排好序
    rows = list_live()

    # 聚合：key=(category, name) -> sources
    merged = {}
    order = []
    for r in rows:
        cat = r.get("category", "")
        name = r.get("name", "")
        url = (r.get("url") or "").strip()
        logo = r.get("logo") or ""
        if not url:
            continue
        key = (cat, name)
        if key not in merged:
            merged[key] = {
                "name": name,
                "sort": name,
                "region": config.LIVE_CATEGORIES.get(cat, cat),
                "year": None,
                "cover": logo,
                "overview": "",
                "quality": "",
                "score": 0,
                "hits": 0,
                "sources": [url],
            }
            order.append(key)
        else:
            rec = merged[key]
            if url not in rec["sources"]:
                rec["sources"].append(url)
            if not rec["cover"] and logo:
                rec["cover"] = logo

    channels = []
    for key in order:
        rec = merged[key]
        sources = rec.pop("sources")
        cat = key[0]
        # id 稳定：由 分类+频道名 决定
        ch_id = prefix + hashlib.md5(
            ("%s|%s" % (key[0], key[1])).encode("utf-8")
        ).hexdigest()[:14]
        # 优先使用采集源自带的真实台标 URL；外链失效/缺失时回退到本地生成封面
        cover = rec["cover"].strip() if rec["cover"] else ""
        if not cover:
            cover = "/covers/live_" + ch_id + ".jpg"
        channels.append({
            "id": ch_id,
            "cat": "live",
            "name": rec["name"],
            "sort": rec["sort"],
            "region": rec["region"],
            "year": None,
            "cover": cover,
            "overview": "",
            "quality": "",
            "score": 0,
            "hits": 0,
            "pop": 0,
            "url": sources[0] if sources else "",
            "sources": sources,
        })
    return channels


def build_category(cat: str, prefix: str) -> list:
    """构建单个分类的影片列表（分类内按 名称|年份 去重，合并线路）。"""
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    cur = con.execute(
        "SELECT id,name,region,year,cover,description,url,line_name,quality,score,hits "
        "FROM resources WHERE category=?", (cat,)
    )
    rows = cur.fetchall()
    con.close()

    merged = {}
    order = []
    for row in rows:
        yi = row["year"]
        year = yi if (isinstance(yi, int) and 1900 <= yi <= 2026) else None
        key = norm_key(row["name"], year)
        u = (row["url"] or "").strip()
        line_name = (row["line_name"] or "").strip() or "未知线路"
        if not u:
            continue
        if key not in merged:
            try:
                sc = float(row["score"] or 0)
            except (TypeError, ValueError):
                sc = 0
            rec = {
                "name": row["name"] or "",
                "sort": clean_sort(row["name"]),
                "region": _region_bucket(row["region"]),
                "year": year,
                "cover": row["cover"] or "",
                "overview": (row["description"] or "")[:500],
                "quality": row["quality"] or "",
                "score": row["score"] or 0,
                "hits": row["hits"] or 0,
                "pop": popularity(row["hits"], row["score"], 1, year),
                "sources": [(line_name, u)],
                "_best_score": sc,
            }
            merged[key] = rec
            order.append(key)
        else:
            rec = merged[key]
            # 按 URL 去重，同一 URL 只保留第一次出现的线路名
            if u not in (p[1] for p in rec["sources"]):
                rec["sources"].append((line_name, u))
            # 升级元数据：取封面更全、评分更高的那一行
            if not rec["cover"] and row["cover"]:
                rec["cover"] = row["cover"]
            try:
                sc = float(row["score"] or 0)
            except (TypeError, ValueError):
                sc = 0
            if sc > rec["_best_score"]:
                rec["_best_score"] = sc
                rec["score"] = row["score"] or 0
                rec["quality"] = row["quality"] or rec["quality"]
                rec["overview"] = (row["description"] or rec["overview"])[:500]
                rec["hits"] = row["hits"] or rec["hits"]
                rec["pop"] = popularity(rec["hits"], rec["score"], len(rec["sources"]), rec["year"])

    movies = []
    for key in order:
        rec = merged[key]
        # 最终人气分：用最终 hits/score/线路数/年份重新计算，确保新增线路也被计入兜底热度。
        rec["pop"] = popularity(rec["hits"], rec["score"], len(rec["sources"]), rec["year"])
        paired = rec.pop("sources")
        rec.pop("_best_score", None)
        # 主线路：直链优先 → 国内可直连优先（按 URL 排序，线路名同步移动）
        primary_first = _sort_sources(paired)
        # 输出：sources 保持 URL 列表（途播兼容），srcs 为对应线路名列表
        urls = [p[1] for p in primary_first]
        srcs = [p[0] for p in primary_first]
        movies.append({
            "id": prefix + hashlib.md5(
                ("%s|%s" % (key[0], key[1])).encode("utf-8")
            ).hexdigest()[:14],
            "cat": cat,
            "name": rec["name"],
            "sort": rec["sort"],
            "region": rec["region"],
            "year": rec["year"],
            "cover": rec["cover"],
            "overview": rec["overview"],
            "quality": rec["quality"],
            "score": rec["score"],
            "hits": rec["hits"],
            "pop": rec["pop"],
            "url": urls[0] if urls else "",
            "sources": urls,
            "srcs": srcs,
        })
    return movies


def _is_direct(u: str) -> bool:
    """判断是否为可直接播放的流地址(.m3u8/.mp4/.ts)，而非需要解析的播放页"""
    u = (u or "").lower().split("?")[0]
    return u.endswith((".m3u8", ".mp4", ".ts"))


def _sort_sources(sources):
    """排序：直链优先 → 国内可直连优先。
    sources 为 [(line_name, url), ...]，按 url 排序并同步移动 line_name。
    """
    def key(item):
        u = (item[1] or "").lower().split("?")[0]
        return (0 if _is_direct(u) else 1, 0 if _is_domestic(u) else 1)
    return sorted(sources, key=key)


def _write_json(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))


# 单分片上限：Cloudflare Pages 单文件 25MB 限制，按 ~0.9KB/条预留安全余量。
# 同时约束 Worker 内存（全量数据会常驻 CACHE_ALL），避免超大分类拖垮实例。
CHUNK = 12000
# 各分类安全上限（按热度降序后截断），总上限约 7 万条，控制在 Worker 内存与
# 单文件 25MB 双重约束内，同时覆盖绝大多数“找片”场景。
MAX_PER_CAT = {
    "movie": 30000, "tv": 18000, "anime": 12000, "variety": 8000, "live": 2000,
}


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    updated = datetime.datetime.now().isoformat(timespec="seconds")

    per_cat = {}
    cat_files = {}
    total = 0
    for cat, prefix, label in JELLYFIN_CATS:
        if cat == "live":
            lst = build_live(prefix)
        else:
            lst = build_category(cat, prefix)
        # 按热度降序，截断/分页时优先保留高热度影片；同热度新片优先（年份降序）
        lst.sort(key=lambda m: ((m.get("pop") or 0), (m.get("year") or 0)), reverse=True)
        cap = MAX_PER_CAT.get(cat)
        if cap and len(lst) > cap:
            print("分类 %s(%s): %d 部，按热度截断到 %d 部" % (label, cat, len(lst), cap))
            lst = lst[:cap]
        per_cat[cat] = lst
        total += len(lst)

        # 分片写入 cat_{cat}_{i}.json，避免单文件超过 25MB
        files = []
        n_chunks = max((len(lst) + CHUNK - 1) // CHUNK, 1)
        for i in range(n_chunks):
            chunk = lst[i * CHUNK:(i + 1) * CHUNK]
            fname = "cat_%s_%d.json" % (cat, i)
            _write_json(os.path.join(OUT_DIR, fname), {
                "updated": updated, "cat": cat, "count": len(chunk),
                "index": i, "movies": chunk,
            })
            files.append(fname)
        cat_files[cat] = files
        print("分类 %s(%s): %d 部 -> %d 个分片" % (label, cat, len(lst), len(files)))

    # 1) 统一库入口：all.json 作为分片清单（manifest），worker 按需加载各 cat 分片
    manifest = {
        "updated": updated,
        "count": total,
        "sharded": True,
        "cats": {
            cat: {"count": len(per_cat[cat]), "files": cat_files[cat]}
            for cat, _, _ in JELLYFIN_CATS
        },
    }
    _write_json(os.path.join(OUT_DIR, "all.json"), manifest)
    print("生成 all.json(manifest): %d 部, 分片文件 %d 个" % (
        total, sum(len(v) for v in cat_files.values())))

    # 2) 向后兼容：movies.json（仅电影，截断到单文件安全上限）
    movie_list = per_cat["movie"]
    _write_json(OUT_MOVIES, {
        "updated": updated, "count": len(movie_list[:CHUNK]),
        "movies": movie_list[:CHUNK],
    })

    # 3) 向后兼容：电影地区分片（每地区截断到单文件安全上限）
    by_region = defaultdict(list)
    for m in movie_list:
        by_region[m.get("region") or "其他"].append(m)
    LIGHT_FIELDS = ("id", "name", "sort", "region", "year", "cover", "quality", "score", "pop", "sources", "cat")
    for region, rmovies in by_region.items():
        light = [{k: m[k] for k in LIGHT_FIELDS} for m in rmovies[:CHUNK]]
        safe_name = region.replace(" ", "_").replace("/", "_")
        _write_json(os.path.join(OUT_DIR, f"movies_{safe_name}.json"), {
            "updated": updated, "count": len(light), "region": region, "movies": light,
        })
    print("电影地区分片: %d 个" % len(by_region))


if __name__ == "__main__":
    main()
