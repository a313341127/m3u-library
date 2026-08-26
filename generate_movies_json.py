"""生成电影元数据 JSON，供 Cloudflare Workers (Jellyfin 兼容后端) 运行时 fetch。

输出: output/api/movies.json
结构: {"updated": "...", "count": N, "movies": [ {...}, ... ]}

字段说明 (每部电影):
  id       稳定哈希 id(按 名称|年份 生成，重生成不变，便于途播缓存)
  name     片名
  sort     排序名(用于 SortName，按名称拼音/原始)
  region   地区桶(中国大陆/港澳/台湾/美国/日本/韩国/英国/印度/泰国/欧美/其他)
  year     清洗后年份(int | null, 非 [1900,2026] 归 null)
  cover    海报 URL
  overview 简介
  url      主播放直链(国内可直连源优先)
  quality  画质
  score    评分
  hits     播放量
  pop      热度(= hits * (score/10)^2, 无评分按5分兜底)
  sources  该片所有播放线路(去重后的 URL 列表，主线路在前，供途播切换)

去重策略:
  同一部电影(名称+年份相同)在数据库里往往有多行(多采集站/多线路)，
  这里按 (名称,年份) 合并为一条，把多条 URL 收集进 sources，
  国内可直连源排最前 —— 海报墙不再出现重复片源，播放时可切换线路。
"""
import json
import sys
import os
import re
import datetime
import hashlib
import sqlite3

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
import config  # noqa: E402
from generator.m3u import _region_bucket, _is_domestic  # noqa: E402

DB = os.path.join(ROOT, "data", "media.db")
OUT_DIR = os.path.join(ROOT, "output", "api")
OUT = os.path.join(OUT_DIR, "movies.json")


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


def popularity(hits, score) -> float:
    h = hits or 0
    try:
        s = float(score)
    except (TypeError, ValueError):
        s = 0
    s = s if (isinstance(s, (int, float)) and 0 < s <= 10) else 5.0
    return h * (s / 10.0) ** 2


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    cur = con.execute(
        "SELECT id,name,region,year,cover,description,url,quality,score,hits "
        "FROM resources WHERE category='movie'"
    )
    rows = cur.fetchall()
    con.close()
    raw = len(rows)

    # 合并：按 (名称,年份) 去重，同一部片的多条线路收集为 sources
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
                "pop": popularity(row["hits"], row["score"]),
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
                rec["pop"] = popularity(rec["hits"], rec["score"])

    movies = []
    for key in order:
        rec = merged[key]
        sources = rec.pop("sources")
        rec.pop("_best_score", None)
        # 主线路：直链优先 → 国内可直连优先（途播默认播主线路，需是可直连流地址）
        primary_first = _sort_sources(sources)
        movies.append({
            "id": "m_" + hashlib.md5(
                ("%s|%s" % (key[0], key[1])).encode("utf-8")
            ).hexdigest()[:14],
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


    payload = {
        "updated": datetime.datetime.now().isoformat(timespec="seconds"),
        "count": len(movies),
        "movies": movies,
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
    size = os.path.getsize(OUT)
    multi = sum(1 for m in movies if len(m["sources"]) > 1)

    # 按地区分片：Jellyfin 后端按 ParentId 加载时只取对应地区，减少每次传输/解析量。
    # 列表分片去掉多线路 sources（只保留主线路 url），体积可降约一半。
    from collections import defaultdict
    by_region = defaultdict(list)
    for m in movies:
        by_region[m.get("region") or "其他"].append(m)
    region_files = []
    # 列表分片进一步瘦身：去掉 overview（列表页用不到），详情页从全量 JSON 取。
    LIGHT_FIELDS = ("id", "name", "sort", "region", "year", "cover", "quality", "score")
    for region, rmovies in by_region.items():
        light_movies = [
            {k: m[k] for k in LIGHT_FIELDS}
            for m in rmovies
        ]
        rpayload = {
            "updated": payload["updated"],
            "count": len(light_movies),
            "region": region,
            "movies": light_movies,
        }
        safe_name = region.replace(" ", "_").replace("/", "_")
        rpath = os.path.join(OUT_DIR, f"movies_{safe_name}.json")
        with open(rpath, "w", encoding="utf-8") as f:
            json.dump(rpayload, f, ensure_ascii=False, separators=(",", ":"))
        region_files.append((region, len(light_movies), os.path.getsize(rpath)))

    print(
        "生成完成: 原始 %d 行 -> 去重 %d 部电影(其中 %d 部含多线路), "
        "文件 %.2f MB -> %s" % (raw, len(movies), multi, size / 1024 / 1024, OUT)
    )
    print("地区分片:")
    for region, count, rsize in sorted(region_files, key=lambda x: -x[2]):
        print("  %s: %d 部, %.2f MB -> %s" % (region, count, rsize / 1024 / 1024, os.path.join(OUT_DIR, f"movies_{region.replace(' ', '_').replace('/', '_')}.json")))


def _is_direct(u: str) -> bool:
    """判断是否为可直接播放的流地址(.m3u8/.mp4/.ts)，而非需要解析的播放页"""
    u = (u or "").lower().split("?")[0]
    return u.endswith((".m3u8", ".mp4", ".ts"))


def _sort_sources(sources):
    """排序：直链优先 → 国内可直连优先。
    确保途播默认播放的「主线路」是可直连的流地址(而非 HTML 解析页)。"""
    def key(u):
        return (0 if _is_direct(u) else 1, 0 if _is_domestic(u) else 1)
    return sorted(sources, key=key)


if __name__ == "__main__":
    main()
