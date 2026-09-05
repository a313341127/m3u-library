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
import glob
import re
import datetime
import hashlib
import sqlite3
from collections import defaultdict
from urllib.parse import urlparse

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

# 固定的本地台标目录（data/live_logos/，已进 git；部署时同步到 output/covers/live/）。
# 途播优先引用这里的真实台标，彻底摆脱易失效的外链 CDN。
_LIVE_LOGO_ROOT = os.path.join(ROOT, "data", "live_logos")
import config  # noqa: E402
from generator.m3u import _region_bucket, _is_domestic  # noqa: E402
from generator import health as _health  # noqa: E402

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
        # 直播保持直连（不进 worker 代理），仅剔除已确认失效的线路
        sources = [u for u in rec.pop("sources") if _health.playable(u)]
        if not sources:
            continue  # 该频道所有线路都失效，丢弃避免途播点开黑屏
        cat = key[0]
        # id 稳定：由 分类+频道名 决定
        ch_id = prefix + hashlib.md5(
            ("%s|%s" % (key[0], key[1])).encode("utf-8")
        ).hexdigest()[:14]
        # 优先使用固定的本地台标（data/live_logos 已进 git，部署时同步到 /covers/live/），
        # 不再依赖易失效的外链 CDN；没有本地真台标（冷门地方台，源站未收录）则回退
        # 本地生成的渐变封面——比失效外链可靠（实测外链约 39% 404 且浏览器常有反盗链）。
        if os.path.exists(os.path.join(_LIVE_LOGO_ROOT, ch_id + ".png")):
            cover = "/covers/live/" + ch_id + ".png"
        else:
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
        "SELECT id,name,region,year,cover,description,url,line_name,quality,score,hits,source "
        "FROM resources WHERE category=?", (cat,)
    )
    rows = cur.fetchall()
    con.close()
    return build_from_rows(rows, cat, prefix)


def build_from_rows(rows, cat: str, prefix: str) -> list:
    """对「已按分类筛好的」资源行做 (名称,年份) 去重 + 合并线路 + 格式化，产出规范影片记录。

    抽出为独立函数，使 sync_delta_kv.py 能对「仅最近新增的行子集」复用同一套
    去重/格式逻辑（与全量构建保持逐字段一致，避免两处漂移导致增量与静态库不一致）。
    """
    merged = {}
    order = []
    for row in rows:
        yi = row["year"]
        year = yi if (isinstance(yi, int) and 1900 <= yi <= 2026) else None
        key = norm_key(row["name"], year)
        u = (row["url"] or "").strip()
        # 线路名优先；缺失时回退到采集源名（总比显示「未知线路」有用）
        _ln = (row["line_name"] or "").strip()
        _src = (row["source"] or "").strip()
        line_name = (config.SOURCE_LABELS.get(_ln)
                     or config.SOURCE_LABELS.get(_src)
                     or _ln or _src or "未知线路")
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
        # 所有线路都失效的影片直接丢弃，避免途播墙出现「点开黑屏」的死片
        if not urls:
            continue
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
    """排序：线路体检可用优先 → 直链优先 → 国内可直连优先。

    sources 为 [(line_name, url), ...]，按 url 排序并同步移动 line_name。
    已确认失效的线路（域名体检判 dead）会被剔除，避免途播/网页切到死链。
    """
    def key(item):
        u = (item[1] or "").lower().split("?")[0]
        return (_health.rank(item[1]), 0 if _is_direct(u) else 1,
                0 if _is_domestic(u) else 1)
    out = [p for p in sources if _health.playable(p[1])]
    # 全集都失效时返回空集（绝不回退到死链，否则途播会点到打不开的影片）
    return sorted(out, key=key)


# 途播侧可见源黑名单（CF 出口取不到 / 海外封禁），须与 output/_worker.js 的 TUBO_BLOCKED 保持一致。
# 文采 6g9ba6/hhuus/hhwenjian：CF 出口 530/1010 反爬封禁；非凡 ffzy-online*：CF 出口 403 海外封禁。
# 这些源网页端（本机直连）能放，但途播经 CF Worker 取不到 → 在途播侧过滤掉，只暴露「途播可播」的线路。
TUBO_BLOCKED = ["6g9ba6.com", "hhuus.com", "hhwenjian.com", "ffzy-online"]


def _host_blocked(u: str) -> bool:
    try:
        h = (urlparse(u or "").hostname or "").lower()
    except Exception:
        return False
    return any(b in h for b in TUBO_BLOCKED)


def tubo_playable(m: dict) -> bool:
    """途播可播 = 至少有一条非黑名单源。直播全部保留（途播直播走客户端直连，不过滤）。

    在「生成期」一次性判定，避免 worker 运行时为过滤死链而扫描全部分页
    （旧实现因此触发 Cloudflare 单次调用子请求上限 → /Items 返回 500 → 途播无法添加）。
    """
    if m.get("cat") == "live":
        return True
    srcs = m.get("sources") or ([m["url"]] if m.get("url") else [])
    if not srcs:
        return False
    return any(not _host_blocked(u) for u in srcs)


def _write_json(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))


# 单分片上限：Cloudflare Pages 单文件 25MB 限制，按 ~0.9KB/条预留安全余量。
CHUNK = 12000
# 单分片体积上限：Cloudflare Pages 单文件 25 MiB 是硬上限，超限会让「整个部署失败」、
# 站点回退到上一次成功部署（2026-09-02 事故：movie.m3u 50.3 MiB 卡死部署，导致全站与
# 途播内容回退为空）。取 20 MiB 留足余量。
# 注意：CHUNK 只约束条数，无法保证体积——数据量增长后 12000 条可能远超 25 MiB，
# 因此分片必须同时按体积切。
MAX_SHARD_BYTES = 20 * 1024 * 1024

# 途播列表分页尺寸：worker 改为「生成期预分页 + 按需读页」，内存占用与目录总量解耦
# （不再把全量数据常驻 Worker 内存，彻底突破 128MiB 内存墙）。途播每次列表请求只读
# 1~2 个分页文件（≤ ~200KB），详情/播放按 id 前缀定位分类后只读 1 个分页文件。
# 必须与 output/_worker.js 中的 PAGE_SIZE 保持一致。
PAGE_SIZE = 300


def _json_bytes(obj) -> int:
    """按实际写出方式（ensure_ascii=False + 紧凑分隔符）计算 UTF-8 字节数"""
    return len(json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def _cut_by_bytes(lst: list, max_bytes: int) -> list:
    """按序列化体积截断列表，保证单独写出后不超过 max_bytes"""
    out, used = [], 0
    for m in lst:
        b = _json_bytes(m)
        if out and used + b > max_bytes:
            break
        out.append(m)
        used += b
    return out


def _write_pages(cat: str, lst: list, updated: str):
    """生成期预分页：把已按热度排好序的 lst 切成固定尺寸的 cat_{cat}_p{p}.json（全量，
    供 getItem 按 id 定位），并输出轻量索引 idx_{cat}.json（仅有序 id 列表）。

    额外产出「途播可播」分页 cat_{cat}_tp_p{p}.json：仅保留至少一条非黑名单源的影片，
    顺序与全量一致。worker 列表浏览只读 1 个 tp 分页文件即可，单次调用子请求数恒为 1，
    彻底规避 Cloudflare 单次调用子请求上限（旧实现为过滤死链扫描全部分页 → 500）。

    返回 (page_files, idx_name, tp_page_files, tp_count)。
    """
    n = len(lst)
    npages = max((n + PAGE_SIZE - 1) // PAGE_SIZE, 1)
    page_files = []
    for i in range(npages):
        chunk = lst[i * PAGE_SIZE:(i + 1) * PAGE_SIZE]
        fname = "cat_%s_p%d.json" % (cat, i)
        _write_json(os.path.join(OUT_DIR, fname), {
            "updated": updated, "cat": cat, "count": len(chunk),
            "page": i, "pageSize": PAGE_SIZE, "total": n, "movies": chunk,
        })
        page_files.append(fname)
    idx_name = "idx_%s.json" % cat
    _write_json(os.path.join(OUT_DIR, idx_name), {
        "updated": updated, "cat": cat, "count": n,
        "pageSize": PAGE_SIZE, "pages": npages,
        "ids": [m["id"] for m in lst],
    })

    # 途播可播分页（过滤死链，生成期一次性完成）
    tp_lst = [m for m in lst if tubo_playable(m)]
    tp_n = len(tp_lst)
    tp_pages = max((tp_n + PAGE_SIZE - 1) // PAGE_SIZE, 1)
    tp_page_files = []
    for i in range(tp_pages):
        chunk = tp_lst[i * PAGE_SIZE:(i + 1) * PAGE_SIZE]
        fname = "cat_%s_tp_p%d.json" % (cat, i)
        _write_json(os.path.join(OUT_DIR, fname), {
            "updated": updated, "cat": cat, "count": len(chunk),
            "page": i, "pageSize": PAGE_SIZE, "total": tp_n, "movies": chunk,
        })
        tp_page_files.append(fname)

    # 生成期搜索索引：紧凑 TSV（id\tyear\tname\tlow），worker 单次 fetch 即可全量搜索。
    # 旧实现在运行时逐页读取来搜索，电影有上百个分页 → 单次调用子请求爆表 → /Items 500。
    search_name = _write_search_index(cat, tp_lst)
    return page_files, idx_name, tp_page_files, tp_n, search_name


def _write_search_index(cat: str, tp_lst: list) -> str:
    """写出 search_{cat}.txt：每行 `id\\tyear\\tname\\tlow`，按热度顺序（与 tp 分页一致）。

    - `low` 为小写检索键（含 name 与 sort），worker 无需在运行时做 toLowerCase 全量转换。
    - 纯文本而非 JSON：worker 用 indexOf 扫描即可，避免 JSON.parse 数万条对象的 CPU/内存开销。
    - 单文件体积可控（电影约 5 万条 ≈ 4MB），远小于 Cloudflare Pages 单文件 25MiB 上限。
    """
    def _clean(s):
        return (s or "").replace("\t", " ").replace("\n", " ").replace("\r", " ")

    lines = []
    for m in tp_lst:
        name = _clean(m.get("name"))
        sort = _clean(m.get("sort"))
        low = name.lower() if sort.lower() == name.lower() else (name + " " + sort).lower()
        lines.append("%s\t%s\t%s\t%s" % (m["id"], m.get("year") or "", name, low))
    fname = "search_%s.txt" % cat
    path = os.path.join(OUT_DIR, fname)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines))
    size = os.path.getsize(path)
    if size > MAX_SHARD_BYTES:
        print("  !! 警告：搜索索引 %s 体积 %.1f MiB 超过 Pages 单文件上限"
              % (fname, size / 1048576))
    return fname


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    # 先清理上一轮可能残留的更高序号分片（分类条数下降时旧分片不会被覆盖，
    # 若放任会在 Pages 留下孤立文件、并干扰本地校验）
    for _f in (glob.glob(os.path.join(OUT_DIR, "cat_*.json")) +
               glob.glob(os.path.join(OUT_DIR, "idx_*.json")) +
               glob.glob(os.path.join(OUT_DIR, "search_*.txt"))):
        try:
            os.remove(_f)
        except OSError:
            pass
    updated = datetime.datetime.now().isoformat(timespec="seconds")

    per_cat = {}
    cat_files = {}
    page_files = {}
    idx_files = {}
    tp_pages = {}
    tp_count = {}
    search_files = {}
    total = 0
    for cat, prefix, label in JELLYFIN_CATS:
        if cat == "live":
            lst = build_live(prefix)
        else:
            lst = build_category(cat, prefix)
        # 按热度降序；同热度新片优先（年份降序）。不再按热度截断——
        # 全量资源都进途播，Worker 内存墙由「生成期预分页 + 按需读页」突破（见 PAGE_SIZE）。
        lst.sort(key=lambda m: ((m.get("pop") or 0), (m.get("year") or 0)), reverse=True)
        per_cat[cat] = lst
        total += len(lst)

        # 分片写入 cat_{cat}_{i}.json：同时受「单文件体积」与「单文件条数」双重约束，
        # 任一超阈值就切一片。体积是硬约束（Pages 25MiB 上限），条数保护 Worker 内存。
        files = []
        cur: list = []
        cur_bytes = 0

        def _flush():
            idx = len(files)
            fname = "cat_%s_%d.json" % (cat, idx)
            _write_json(os.path.join(OUT_DIR, fname), {
                "updated": updated, "cat": cat, "count": len(cur),
                "index": idx, "movies": cur,
            })
            files.append(fname)

        for m in lst:
            b = _json_bytes(m)
            if cur and (cur_bytes + b > MAX_SHARD_BYTES or len(cur) >= CHUNK):
                _flush()
                cur, cur_bytes = [], 0
            cur.append(m)
            cur_bytes += b
        if cur:
            _flush()
        cat_files[cat] = files

        # 预分页：worker 不再把全量常驻内存，而是按页读取（见 output/_worker.js）。
        pf, idxn, tp_pf, tp_n, sname = _write_pages(cat, lst, updated)
        page_files[cat] = pf
        idx_files[cat] = idxn
        search_files[cat] = sname
        tp_pages[cat] = tp_pf
        tp_count[cat] = tp_n
        print("分类 %s(%s): %d 部(途播可播 %d) -> %d 个体积分片 / %d 个全量分页 / %d 个可播分页 / 索引 %s"
              % (label, cat, len(lst), tp_n, len(files), len(pf), len(tp_pf), idxn))

    # 1) 统一库入口：all.json 作为分片清单（manifest），worker 按需加载各 cat 分页/索引
    manifest = {
        "updated": updated,
        "count": total,
        "sharded": True,
        "pageSize": PAGE_SIZE,
        "cats": {
            cat: {
                "count": len(per_cat[cat]),
                "files": cat_files[cat],
                "pageFiles": page_files[cat],
                "idx": idx_files[cat],
                "pageSize": PAGE_SIZE,
                "tpCount": tp_count[cat],
                "tpPageFiles": tp_pages[cat],
                "search": search_files[cat],
            }
            for cat, _, _ in JELLYFIN_CATS
        },
    }
    _write_json(os.path.join(OUT_DIR, "all.json"), manifest)
    print("生成 all.json(manifest): %d 部, 体积分片 %d 个 / 分页 %d 个"
          % (total, sum(len(v) for v in cat_files.values()),
             sum(len(v) for v in page_files.values())))

    # 2) 向后兼容：movies.json（仅电影，条数 + 体积双重截断到单文件安全上限）
    movie_list = per_cat["movie"]
    compat_movies = _cut_by_bytes(movie_list[:CHUNK], MAX_SHARD_BYTES)
    _write_json(OUT_MOVIES, {
        "updated": updated, "count": len(compat_movies),
        "movies": compat_movies,
    })

    # 3) 向后兼容：电影地区分片（每地区截断到单文件安全上限）
    by_region = defaultdict(list)
    for m in movie_list:
        by_region[m.get("region") or "其他"].append(m)
    LIGHT_FIELDS = ("id", "name", "sort", "region", "year", "cover", "quality", "score", "pop", "sources", "cat")
    for region, rmovies in by_region.items():
        light = [{k: m[k] for k in LIGHT_FIELDS} for m in rmovies[:CHUNK]]
        light = _cut_by_bytes(light, MAX_SHARD_BYTES)
        safe_name = region.replace(" ", "_").replace("/", "_")
        _write_json(os.path.join(OUT_DIR, f"movies_{safe_name}.json"), {
            "updated": updated, "count": len(light), "region": region, "movies": light,
        })
    print("电影地区分片: %d 个" % len(by_region))


if __name__ == "__main__":
    main()
