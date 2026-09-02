"""生成电影元数据 JSON，供 Cloudflare Workers (Jellyfin 兼容后端) 运行时 fetch。

被 generate_all() 调用，输出到 output/api/movies.json。
途播等 Jellyfin 客户端连上 Worker 后，由 Worker fetch 此 JSON 渲染海报墙。
"""
import sqlite3
import json
import re
import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "media.db"
OUT_DIR = ROOT / "output" / "api"

import config  # noqa: E402


def region_bucket(region: str) -> str:
    r = (region or "").strip().lower()
    if not r or r in ("其它", "其他"):
        return config.GROUP_FALLBACK
    for bucket, keywords in config.REGION_BUCKETS.items():
        if bucket == "欧美":
            continue
        for kw in keywords:
            if kw.lower() in r:
                return bucket
    for kw in config.REGION_BUCKETS["欧美"]:
        if kw.lower() in r:
            return "欧美"
    return config.GROUP_FALLBACK


def clean_sort(name: str) -> str:
    n = (name or "").strip()
    n = re.sub(r"[\（\(]\d{4}[\）\)]", "", n)
    n = re.sub(r"\s*[第][\d一二三四五六七八九十百千]+[季部集话]", "", n)
    return n.strip() or (name or "")


def popularity(hits, score) -> float:
    h = hits or 0
    try:
        s = float(score)
    except (TypeError, ValueError):
        s = 0
    s = s if (isinstance(s, (int, float)) and 0 < s <= 10) else 5.0
    return h * (s / 10.0) ** 2


def generate_movies_json(output_dir=None):
    out_dir = Path(output_dir) / "api" if output_dir else OUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "movies.json"
    con = sqlite3.connect(str(DB))
    con.row_factory = sqlite3.Row
    cur = con.execute(
        "SELECT id,name,region,year,cover,description,url,quality,score,hits "
        "FROM resources WHERE category='movie'"
    )
    movies = []
    for row in cur:
        yi = row["year"]
        year = yi if (isinstance(yi, int) and 1900 <= yi <= 2026) else None
        movies.append({
            "id": str(row["id"]),
            "name": row["name"] or "",
            "sort": clean_sort(row["name"]),
            "region": region_bucket(row["region"]),
            "year": year,
            "cover": row["cover"] or "",
            "overview": (row["description"] or "")[:500],
            "url": row["url"] or "",
            "quality": row["quality"] or "",
            "score": row["score"] or 0,
            "hits": row["hits"] or 0,
            "pop": popularity(row["hits"], row["score"]),
        })
    con.close()
    payload = {
        "updated": datetime.datetime.now().isoformat(timespec="seconds"),
        "count": len(movies),
        "movies": movies,
    }
    with open(out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
    print(f"[jellyfin_data] 生成 {len(movies)} 部电影 -> {out}")
    return out
