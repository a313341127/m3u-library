#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""增量同步：把 media.db 中「自上次同步以来新增」的影片写入 Cloudflare KV，
供 output/_worker.js 在静态基库(每 6 小时才部署一次)之间实时呈现新片，实现
「0 部署、数据实时同步」——热/冷分离：静态分片扛全量(冷)，KV 增量扛实时新增(热)。

设计要点（规避 Cloudflare 免费档隐形限额）：
  * KV 写率 1000/天：每轮仅 ~4-6 写(manifest+index+轮值+marker)，48 轮/天 ≈ 240-290 写，
    加部署轮清空(4 轮/天 × ~27 写) ≈ 400/天，远低于 1000。
  * 单值 25MiB：一轮新增按 20MiB 分片写入 delta:{roundId}:{part}。
  * 存储 1GB：保留 RETENTION_ROUNDS 轮(默认 16 ≈ 8h)增量，约 30MiB，远低于 1GB。
  * 不触发 Pages 构建(500/月)：增量走 KV，不经部署。
  * 无卡：KV 免费档，无需信用卡。

KV 键结构（每轮独立写自己的键，无「读-改-写」竞争，规避 60s 最终一致导致丢轮）：
  delta:manifest        -> { generatedAt, retention, rounds:[ {id,ts,parts,idxKey,movieKeys,catCounts,total} ] }
  delta:idx:{roundId}   -> [ {id,cat,name,sort,year,round}, ... ]  轻索引(搜索/定位用，不含正文)
  delta:{roundId}:{p}   -> { ts, part, movies:[ 规范影片记录, ... ] }  增量正文(可多片)
  delta:marker          -> 上次同步时间戳(ISO)，作为下一轮 created_at  cutoff

影片记录格式与 generate_movies_json.build_from_rows 完全一致
(id/cat/name/sort/region/year/cover/overview/quality/score/hits/pop/url/sources/srcs)，
确保 Worker 合并后既能浏览又能播放，且与静态库逐字段对齐。

用法：
  python scripts/sync_delta_kv.py                 # 提取并写入自上次以来的新增
  python scripts/sync_delta_kv.py --dry-run       # 只打印将写入的内容，不调用 KV API
  python scripts/sync_delta_kv.py --clear         # 清空全部增量(静态库已含全量时调用)
  python scripts/sync_delta_kv.py --since 2026-09-05T08:00:00   # 手动指定 cutoff

环境变量(优先于参数)：CLOUDFLARE_API_TOKEN / CLOUDFLARE_ACCOUNT_ID / KV_DELTA_ID
（CI 中以仓库 Secrets 注入；本地调试可 export 上述变量后运行。）
"""
import argparse
import json
import os
import sqlite3
import sys
import time
import datetime
import urllib.request
import urllib.error

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(REPO)
sys.path.insert(0, REPO)

# ---------- 配置 ----------
# 参与增量的分类（resources 表驱动；live 走独立表且变化少，由静态库覆盖，不进增量）
DELTA_CATS = [
    ("movie", "m_", "电影"),
    ("tv", "t_", "剧集"),
    ("variety", "v_", "综艺"),
    ("anime", "a_", "动漫"),
]
RETENTION_ROUNDS = 16          # 保留最近 N 轮增量（≈8h @30min/轮；静态库每 6h 部署即覆盖）
MAX_VALUE_BYTES = 20 * 1024 * 1024   # 单 KV 值上限 25MiB，取 20MiB 留余量
MANIFEST_KEY = "delta:manifest"
INDEX_KEY_FMT = "delta:idx:{}"
ROUND_KEY_FMT = "delta:{}{}"   # delta:{roundId} 或 delta:{roundId}:{part}
MARKER_KEY = "delta:marker"


# ---------- 规范影片记录构建（复用 generate_movies_json，失败回退本地副本）----------
def _local_build_from_rows(rows, cat, prefix):
    """generate_movies_json.build_from_rows 的本地副本，仅在 import 失败时启用，
    保证脚本在 CI 轻量环境下也能独立运行（不强行拉起整个生成模块的网络依赖）。"""
    import hashlib
    import re

    def clean_sort(name):
        n = (name or "").strip()
        n = re.sub(r"[\（\(]\d{4}[\）\)]", "", n)
        n = re.sub(r"\s*[第][\d一二三四五六七八九十百千]+[季部集话]", "", n)
        return n.strip() or (name or "")

    def norm_key(name, year):
        n = clean_sort(name).lower()
        n = re.sub(r"\s+", "", n)
        return (n, year)

    def popularity(hits, score, lines=1, year=None):
        import math
        h = hits or 0
        lines = max(1, lines or 1)
        try:
            s = float(score)
        except (TypeError, ValueError):
            s = 0
        s = s if (isinstance(s, (int, float)) and 0 < s <= 10) else 5.0
        eff = h if h > 0 else lines * 1000
        norm = math.log1p(eff) * 1000
        base = norm * (s / 10.0) ** 2
        if year and year >= 2020:
            base *= (1.0 + (year - 2020) * 0.05)
        return base

    def _is_direct(u):
        u = (u or "").lower().split("?")[0]
        return u.endswith((".m3u8", ".mp4", ".ts"))

    def _is_domestic(u):
        try:
            from urllib.parse import urlparse
            h = (urlparse(u or "").hostname or "").lower()
        except Exception:
            return False
        domestic = ("baofeng", "fengbao", "bfvvs", "lzcdn", "liangzi", "maotai", "mtzy",
                   "vodcnd", "wgslsw", "qncdn", "cdnd", "jszy", "qq", "bilibili",
                   "iqiyi", "youku", "le.com", "mgtv", "1905")
        return any(d in h for d in domestic)

    def _sort_sources(sources):
        def key(item):
            u = (item[1] or "").lower().split("?")[0]
            return (0 if _is_direct(u) else 1, 0 if _is_domestic(u) else 1)
        out = [p for p in sources]
        try:
            out.sort(key=key)
        except Exception:
            pass
        return out

    merged = {}
    order = []
    for row in rows:
        yi = row["year"]
        year = yi if (isinstance(yi, int) and 1900 <= yi <= 2026) else None
        key = norm_key(row["name"], year)
        u = (row["url"] or "").strip()
        _ln = (row["line_name"] or "").strip()
        _src = (row["source"] or "").strip()
        line_name = _ln or _src or "未知线路"
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
                "region": (row["region"] or "") or "其他",
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
            if u not in (p[1] for p in rec["sources"]):
                rec["sources"].append((line_name, u))
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
        rec["pop"] = popularity(rec["hits"], rec["score"], len(rec["sources"]), rec["year"])
        paired = rec.pop("sources")
        rec.pop("_best_score", None)
        primary_first = _sort_sources(paired)
        urls = [p[1] for p in primary_first]
        srcs = [p[0] for p in primary_first]
        if not urls:
            continue
        import hashlib as _h
        movies.append({
            "id": prefix + _h.md5(("%s|%s" % (key[0], key[1])).encode("utf-8")).hexdigest()[:14],
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


def get_builder():
    try:
        import generate_movies_json as _gen
        return _gen.build_from_rows, "generate_movies_json.build_from_rows"
    except Exception as e:
        return _local_build_from_rows, "local-fallback(%s)" % type(e).__name__


# ---------- Cloudflare KV REST 客户端（urllib，支持 HTTPS_PROXY）----------
class KVClient:
    def __init__(self, token, account_id, ns_id, dry_run=False):
        self.token = token
        self.account_id = account_id
        self.ns_id = ns_id
        self.dry_run = dry_run
        self.writes = 0
        self.base = "https://api.cloudflare.com/client/v4/accounts/%s/storage/kv/namespaces/%s" % (
            account_id, ns_id)
        # 代理探测：优先直连，失败回退 HTTPS_PROXY（与本项目网络约定一致）
        proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy") or ""
        self.opener = None
        if proxy:
            try:
                from urllib.request import ProxyHandler, build_opener
                self.opener = build_opener(ProxyHandler({"http": proxy, "https": proxy}))
            except Exception:
                self.opener = None

    def _req(self, method, key, body=None):
        url = self.base + "/values/" + key
        data = body.encode("utf-8") if isinstance(body, str) else body
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Authorization", "Bearer " + self.token)
        req.add_header("Content-Type", "application/json")
        opener = self.opener or urllib.request
        try:
            with opener.urlopen(req, timeout=60) as r:
                txt = r.read().decode("utf-8", "replace")
            return txt
        except urllib.error.HTTPError as e:
            txt = e.read().decode("utf-8", "replace") if hasattr(e, "read") else ""
            raise RuntimeError("KV %s %s -> %s: %s" % (method, key, e.code, txt[:300]))
        except Exception as e:
            raise RuntimeError("KV %s %s -> %s" % (method, key, e))

    def get(self, key):
        if self.dry_run:
            return None
        try:
            return self._req("GET", key)
        except RuntimeError as e:
            if "404" in str(e):
                return None
            raise

    def put(self, key, value):
        self.writes += 1
        if self.dry_run:
            return
        self._req("PUT", key, value)

    def delete(self, key):
        self.writes += 1
        if self.dry_run:
            return
        try:
            self._req("DELETE", key)
        except RuntimeError as e:
            if "404" in str(e):
                return
            raise


# ---------- 主逻辑 ----------
def db_path():
    try:
        import config
        return config.DB_PATH
    except Exception:
        return os.path.join(REPO, "data", "media.db")


def fetch_recent_rows(cutoff):
    """取各分类中 created_at > cutoff 的行（cutoff 为 ISO 时间戳字符串，
    格式 'YYYY-MM-DD HH:MM:SS'，与 DB 中 created_at 同格式 → 字典序即时间序）。"""
    con = sqlite3.connect(db_path())
    con.row_factory = sqlite3.Row
    out = {}
    total = 0
    for cat, _, _ in DELTA_CATS:
        cur = con.execute(
            "SELECT id,name,region,year,cover,description,url,line_name,quality,score,hits,source "
            "FROM resources WHERE category=? AND created_at > ?", (cat, cutoff))
        rows = cur.fetchall()
        out[cat] = rows
        total += len(rows)
    con.close()
    return out, total


def json_bytes(obj):
    return len(json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def split_by_bytes(movies, max_bytes):
    """把影片列表按序列化体积切成 ≤ max_bytes 的多个分片。"""
    parts, cur, used = [], [], 0
    for m in movies:
        b = json_bytes(m)
        if cur and used + b > max_bytes:
            parts.append(cur)
            cur, used = [], 0
        cur.append(m)
        used += b
    if cur:
        parts.append(cur)
    return parts


def run(args):
    token = args.token or os.environ.get("CLOUDFLARE_API_TOKEN")
    account_id = args.account_id or os.environ.get("CLOUDFLARE_ACCOUNT_ID")
    ns_id = args.kv_id or os.environ.get("KV_DELTA_ID")
    dry = args.dry_run

    if not dry:
        missing = [n for n, v in (("CLOUDFLARE_API_TOKEN", token),
                                  ("CLOUDFLARE_ACCOUNT_ID", account_id),
                                  ("KV_DELTA_ID", ns_id)) if not v]
        if missing:
            print("::error::缺少 KV 凭证：%s（请通过环境变量或参数提供）" % ", ".join(missing))
            return 2
    kv = KVClient(token, account_id, ns_id, dry_run=dry)
    builder, builder_src = get_builder()
    print("[sync_delta_kv] builder = %s" % builder_src)

    now_iso = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    round_id = str(int(time.time() * 1000))   # 毫秒时间戳，杜绝同秒碰撞

    # ---- --clear：清空全部增量 ----
    if args.clear:
        manifest_txt = kv.get(MANIFEST_KEY)
        deleted = 0
        if manifest_txt:
            try:
                mf = json.loads(manifest_txt)
            except Exception:
                mf = {"rounds": []}
            for r in mf.get("rounds", []):
                for mk in r.get("movieKeys", []):
                    kv.delete(mk)
                    deleted += 1
                if r.get("idxKey"):
                    kv.delete(r["idxKey"])
                    deleted += 1
        kv.put(MANIFEST_KEY, json.dumps({"generatedAt": now_iso, "retention": RETENTION_ROUNDS,
                                         "rounds": [], "clearedAt": now_iso}, ensure_ascii=False))
        kv.put(MARKER_KEY, now_iso)
        print("[sync_delta_kv] --clear 完成：删除 %d 个增量键，marker 重置为 %s（写操作 %d 次）"
              % (deleted, now_iso, kv.writes))
        return 0

    # ---- 确定 cutoff ----
    if args.since:
        cutoff = args.since
        print("[sync_delta_kv] 使用手动 cutoff: %s" % cutoff)
    else:
        marker = kv.get(MARKER_KEY)
        if marker:
            cutoff = marker.strip()
            print("[sync_delta_kv] 上次同步标记: %s" % cutoff)
        else:
            # 首跑：无标记 → cutoff=now，本轮不产出（静态库已含此前全量）；仅落 marker。
            cutoff = now_iso
            print("[sync_delta_kv] 无同步标记(首跑) → cutoff=now，本轮不产出增量")

    # ---- 提取并构建增量 ----
    recent, total_rows = fetch_recent_rows(cutoff)
    all_movies = []
    cat_counts = {}
    for cat, prefix, _ in DELTA_CATS:
        recs = builder(recent.get(cat, []), cat, prefix)
        cat_counts[cat] = len(recs)
        all_movies.extend(recs)

    if not all_movies:
        kv.put(MARKER_KEY, now_iso)
        print("[sync_delta_kv] 自 %s 起无新增影片，仅更新 marker → %s（写操作 %d 次）"
              % (cutoff, now_iso, kv.writes))
        return 0

    # ---- 分片写入本轮正文 + 轻索引 ----
    parts = split_by_bytes(all_movies, MAX_VALUE_BYTES)
    movie_keys = []
    for i, part in enumerate(parts):
        key = ROUND_KEY_FMT.format(round_id, "" if len(parts) == 1 else ":%d" % i)
        payload = json.dumps({"ts": now_iso, "part": i, "movies": part},
                             ensure_ascii=False, separators=(",", ":"))
        kv.put(key, payload)
        movie_keys.append(key)
    idx = [{"id": m["id"], "cat": m["cat"], "name": m["name"], "sort": m["sort"],
            "year": m["year"], "round": round_id} for m in all_movies]
    idx_key = INDEX_KEY_FMT.format(round_id)
    kv.put(idx_key, json.dumps(idx, ensure_ascii=False, separators=(",", ":")))

    # ---- 更新 manifest（追加本轮 + 按保留轮数裁剪）----
    manifest_txt = kv.get(MANIFEST_KEY)
    try:
        mf = json.loads(manifest_txt) if manifest_txt else {"rounds": []}
    except Exception:
        mf = {"rounds": []}
    mf.setdefault("retention", RETENTION_ROUNDS)
    mf["generatedAt"] = now_iso
    mf["rounds"].append({
        "id": round_id,
        "ts": now_iso,
        "parts": len(parts),
        "idxKey": idx_key,
        "movieKeys": movie_keys,
        "catCounts": cat_counts,
        "total": len(all_movies),
    })
    # 裁剪：仅保留最近 RETENTION_ROUNDS 轮，删除被裁掉的键
    if len(mf["rounds"]) > RETENTION_ROUNDS:
        dropped = mf["rounds"][:-RETENTION_ROUNDS]
        for r in dropped:
            for mk in r.get("movieKeys", []):
                kv.delete(mk)
            if r.get("idxKey"):
                kv.delete(r["idxKey"])
        mf["rounds"] = mf["rounds"][-RETENTION_ROUNDS:]
    kv.put(MANIFEST_KEY, json.dumps(mf, ensure_ascii=False, separators=(",", ":")))
    kv.put(MARKER_KEY, now_iso)

    total_bytes = sum(json_bytes(m) for m in all_movies)
    print("[sync_delta_kv] 本轮新增 %d 部影片（行 %d，分片 %d，约 %.2f MiB），分类计数 %s"
          % (len(all_movies), total_rows, len(parts), total_bytes / 1048576, cat_counts))
    print("[sync_delta_kv] 保留轮数 %d；写操作 %d 次（KV 免费档 1000/天，余量充足）"
          % (RETENTION_ROUNDS, kv.writes))
    print("[sync_delta_kv] round_id=%s，marker → %s" % (round_id, now_iso))
    return 0


def main():
    ap = argparse.ArgumentParser(description="把 media.db 新增影片增量同步到 Cloudflare KV")
    ap.add_argument("--dry-run", action="store_true", help="只打印，不调用 KV API")
    ap.add_argument("--clear", action="store_true", help="清空全部增量（静态库已含全量时）")
    ap.add_argument("--since", default=None, help="手动指定 created_at cutoff (ISO)")
    ap.add_argument("--token", default=None, help="Cloudflare API Token（需 KV Edit）")
    ap.add_argument("--account-id", default=None, help="Cloudflare Account ID")
    ap.add_argument("--kv-id", default=None, help="KV Namespace ID")
    args = ap.parse_args()
    try:
        return run(args)
    except Exception as e:
        print("::error::sync_delta_kv 失败: %s" % e)
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
