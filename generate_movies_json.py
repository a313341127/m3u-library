"""生成电影/剧集/动漫/综艺元数据 JSON，供 Cloudflare Workers (Jellyfin 兼容后端) 运行时 fetch。

输出:
  output/api/all.json        # 四分类合并（电影+剧集+动漫+综艺），每条带 cat 字段，统一库入口
  output/api/movies.json     # 仅电影（向后兼容旧 worker）
  output/api/movies_{region}.json  # 电影地区分片（向后兼容）

all.json 结构: {"updated": "...", "count": N, "movies": [ {...}, ... ]}
每条影片字段:
  id       稳定哈希 id(按 名称|年份 生成，重生成不变)；按分类加前缀 m_/t_/a_/v_ 避免跨类重名
  cat      分类: movie / tv / anime / variety
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
  国内可直连源排最前。直播(live)不纳入本文件，单独走 live 模块。
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

# 纳入统一库的四分类（直播单独保留，不进来）
JELLYFIN_CATS = [
    ("movie", "m_", "电影"),
    ("tv", "t_", "剧集"),
    ("anime", "a_", "动漫"),
    ("variety", "v_", "综艺"),
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


def build_category(cat: str, prefix: str) -> list:
    """构建单个分类的影片列表（分类内按 名称|年份 去重，合并线路）。"""
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    cur = con.execute(
        "SELECT id,name,region,year,cover,description,url,quality,score,hits "
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
                "sources": [u],
                "_best_score": sc,
            }
            merged[key] = rec
            order.append(key)
        else:
            rec = merged[key]
            if u not in rec["sources"]:
                rec["sources"].append(u)
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
        sources = rec.pop("sources")
        rec.pop("_best_score", None)
        # 主线路：直链优先 → 国内可直连优先
        primary_first = _sort_sources(sources)
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
            "url": primary_first[0] if primary_first else "",
            "quality": rec["quality"],
            "score": rec["score"],
            "hits": rec["hits"],
            "pop": rec["pop"],
            "sources": primary_first,
        })
    return movies


def _is_direct(u: str) -> bool:
    """判断是否为可直接播放的流地址(.m3u8/.mp4/.ts)，而非需要解析的播放页"""
    u = (u or "").lower().split("?")[0]
    return u.endswith((".m3u8", ".mp4", ".ts"))


def _sort_sources(sources):
    """排序：直链优先 → 国内可直连优先。"""
    def key(u):
        return (0 if _is_direct(u) else 1, 0 if _is_domestic(u) else 1)
    return sorted(sources, key=key)


def main():
    os.makedirs(OUT_DIR, exist_ok=True)

    per_cat = {}
    all_movies = []
    for cat, prefix, label in JELLYFIN_CATS:
        lst = build_category(cat, prefix)
        per_cat[cat] = lst
        all_movies.extend(lst)
        print("分类 %s(%s): %d 部" % (label, cat, len(lst)))

    # 1) 统一库入口：四分类合并 all.json（带 cat 字段）
    all_payload = {
        "updated": datetime.datetime.now().isoformat(timespec="seconds"),
        "count": len(all_movies),
        "movies": all_movies,
    }
    out_all = os.path.join(OUT_DIR, "all.json")
    with open(out_all, "w", encoding="utf-8") as f:
        json.dump(all_payload, f, ensure_ascii=False, separators=(",", ":"))
    print("生成 %s: %d 部, %.2f MB" % (
        out_all, len(all_movies), os.path.getsize(out_all) / 1024 / 1024))

    # 2) 向后兼容：movies.json（仅电影）
    movie_list = per_cat["movie"]
    movies_payload = {
        "updated": all_payload["updated"],
        "count": len(movie_list),
        "movies": movie_list,
    }
    with open(OUT_MOVIES, "w", encoding="utf-8") as f:
        json.dump(movies_payload, f, ensure_ascii=False, separators=(",", ":"))

    # 3) 向后兼容：电影地区分片
    by_region = defaultdict(list)
    for m in movie_list:
        by_region[m.get("region") or "其他"].append(m)
    LIGHT_FIELDS = ("id", "name", "sort", "region", "year", "cover", "quality", "score", "pop", "sources", "cat")
    region_files = []
    for region, rmovies in by_region.items():
        light_movies = [{k: m[k] for k in LIGHT_FIELDS} for m in rmovies]
        rpayload = {
            "updated": all_payload["updated"],
            "count": len(light_movies),
            "region": region,
            "movies": light_movies,
        }
        safe_name = region.replace(" ", "_").replace("/", "_")
        rpath = os.path.join(OUT_DIR, f"movies_{safe_name}.json")
        with open(rpath, "w", encoding="utf-8") as f:
            json.dump(rpayload, f, ensure_ascii=False, separators=(",", ":"))
        region_files.append((region, len(light_movies), os.path.getsize(rpath)))
    print("电影地区分片:")
    for region, count, rsize in sorted(region_files, key=lambda x: -x[2]):
        print("  %s: %d 部, %.2f MB" % (region, count, rsize / 1024 / 1024))


if __name__ == "__main__":
    main()
