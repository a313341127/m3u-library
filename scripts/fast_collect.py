#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""本地并发全量采集脚本（提速版）

借鉴 G 盘 ZCODE 途播 local_collect.py 的多源并发思路，直接写入 m3u-library 的 media.db。
- 多源并行（默认 5 源同时）
- 每源多 type 并行（默认 3 个 type 同时）
- 详情批量拉取（复用 config.detail_batch=20）
- 断点续传：进度存 data/fast_collect_progress.json
- 去重策略与现有链路一致：同分类 + 同名 + 同地址 跳过，否则新增

用法:
  python scripts/fast_collect.py
  python scripts/fast_collect.py --sources 量子,最大,茅台
  python scripts/fast_collect.py --pages 10 --workers 8
  python scripts/fast_collect.py -c tv
  python scripts/fast_collect.py --reset
"""
import argparse
import json
import os
import re
import sqlite3
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

# 把仓库根目录加入路径
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(REPO)
sys.path.insert(0, REPO)

import requests
import urllib3
from requests.adapters import HTTPAdapter

import config
from collector.cc0cd import (
    classify_category,
    extract_play_urls,
    extract_quality,
    join_params,
    norm_region,
)

urllib3.disable_warnings()

# 并发写锁：多源×多 type 共十几个线程各自开连接写同一 SQLite，
# 不加锁会互相踩出 "database is locked"。WAL 只解决读写并发，写写仍串行，
# 故用一把全局锁把「SELECT 查重 + INSERT + commit」包成原子段。
DB_LOCK = threading.Lock()

# ---------- 配置 ----------
PROGRESS_FILE = os.path.join(REPO, "data", "fast_collect_progress.json")
DB_PATH = str(config.DB_PATH)

SOURCE_WORKERS = 5       # 同时跑的源数
TYPE_WORKERS = 3         # 每个源内同时跑的 type 数
REQUEST_TIMEOUT = 15     # 单次请求超时（秒）
MAX_RETRY = 2            # 请求失败重试次数
REQUEST_DELAY = 0.08     # 比默认 0.25 更快，但仍留一点间隔防 ban

# 云端续跑：每次 GitHub Actions 运行只跑 TIME_LIMIT 秒，到点暂停并把进度写回仓库，
# 下一次定时/手动唤起自动从断点继续（对应 ZCODE 途播「半小时一轮、跑完自动继续」）。
_COLLECT_START = 0.0
_COLLECT_TIME_LIMIT = 0  # 0 = 不限时（本机单轮跑完）

# 默认图黑名单（与 ZCODE 途播 fast_collect 保持一致）
DEFAULT_COVER_FRAGMENTS = [
    "f8b245592640f76bc8e6bca0db4b8aa6",  # 爱奇艺默认图
    "f107f53f18c87d287c0f07f9aff00aaa",  # 最大默认图
    "5161ed49852f560e85cd52a1f7f995b7",  # 魔都默认图1
    "e85e5a693c6382ea3181d621e9c6fd6e",  # 魔都默认图2
    "863b4c3fbdee183907d1d16ad67c0cd0",  # 魔都默认图3
    "14770447", "default", "nopic", "no_pic", "placeholder",
    "暂无海报", "暂无图片", "default_cover",
]

# ---------- HTTP 客户端 ----------
_session_local = threading.local()


def get_session():
    if not hasattr(_session_local, "sess"):
        sess = requests.Session()
        sess.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        })
        sess.verify = False
        adapter = HTTPAdapter(max_retries=0, pool_connections=20, pool_maxsize=50)
        sess.mount("http://", adapter)
        sess.mount("https://", adapter)
        _session_local.sess = sess
    return _session_local.sess


def http_get_json(url, timeout=REQUEST_TIMEOUT):
    for attempt in range(MAX_RETRY + 1):
        try:
            r = get_session().get(url, timeout=timeout)
            if r.status_code == 200:
                return r.json()
        except Exception:
            pass
        if attempt < MAX_RETRY:
            time.sleep(0.3 * (attempt + 1))
    return None


# ---------- 字段处理 ----------
def is_default_cover(cover_url):
    if not cover_url:
        return True
    url_lower = cover_url.lower()
    return any(frag.lower() in url_lower for frag in DEFAULT_COVER_FRAGMENTS)


def parse_vod_to_items(v, source_name, source_type_name="",
                       forced_category=None, forced_media_type=None,
                       want_category=None):
    """把一条 vod 详情解析成可入库字典列表（每个播放线路一条）"""
    raw_type_name = source_type_name or (v.get("type_name") or "").strip()
    desc = re.sub(r"<[^>]+>", "", v.get("vod_content") or "").strip()

    if forced_category:
        category = forced_category
        media_type = forced_media_type or ""
    else:
        classified = classify_category(raw_type_name, desc)
        if classified is None:
            return []
        category, media_type = classified
        if want_category and category != want_category:
            return []

    # 简介关键词二次修正
    if "纪录片" in desc or "documentary" in desc.lower():
        media_type = "纪录片"

    name = (v.get("vod_name") or "").strip()
    line_eps = extract_play_urls(v.get("vod_play_url") or "", v.get("vod_play_from") or "")
    if not name or not line_eps:
        return []

    remarks = v.get("vod_remarks") or ""
    quality = extract_quality(remarks) or extract_quality(v.get("vod_pic") or "")

    year = v.get("vod_year")
    try:
        year = int(year) if str(year).strip().isdigit() else None
    except (TypeError, ValueError):
        year = None

    try:
        hits = int(v.get("vod_hits") or 0)
    except (TypeError, ValueError):
        hits = 0
    try:
        score = float(v.get("vod_score") or 0)
    except (TypeError, ValueError):
        score = 0.0
    if not (0 < score <= 10):
        score = 0.0

    region = norm_region(v.get("vod_area") or "")
    cover = (v.get("vod_pic") or "").strip()
    if is_default_cover(cover):
        cover = ""

    items = []
    for line_name, episodes in line_eps:
        if not episodes:
            continue
        first = episodes[0]
        eps_json = json.dumps(episodes, ensure_ascii=False) if len(episodes) > 1 else ""
        items.append({
            "name": name,
            "category": category,
            "media_type": media_type,
            "region": region,
            "year": year,
            "cover": cover,
            "description": desc,
            "url": first["url"],
            "quality": quality,
            "raw_type_name": raw_type_name,
            "source": "cc0cd",
            "line_name": line_name,
            "hits": hits,
            "score": score,
            "episodes": eps_json,
        })
    return items


def map_source_types(api):
    """获取源站类型映射"""
    try:
        data = http_get_json(join_params(api, ac="list"))
    except Exception:
        return []
    classes = data.get("class") or []
    mapped = []
    for cls in classes:
        tid = cls.get("type_id")
        tname = (cls.get("type_name") or "").strip()
        if not tname:
            continue
        classified = classify_category(tname)
        if classified is None:
            continue
        cat, mt = classified
        try:
            tid = int(tid) if tid is not None else None
        except (TypeError, ValueError):
            tid = None
        mapped.append((tid, tname, cat, mt))
    return mapped


# ---------- 数据库 ----------
def init_sqlite():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    # 关键：写冲突时等待而非立即报 "database is locked"（默认 busy_timeout=0）。
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def bulk_insert_items(conn, items):
    """批量插入，返回 (新增数, 重复数)。整段持 DB_LOCK 且带 busy_timeout 重试，
    彻底消除并发写 "database is locked"。"""
    if not items:
        return 0, 0
    inserted = 0
    dup = 0
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cur = conn.cursor()
    for it in items:
        for attempt in range(3):
            try:
                with DB_LOCK:
                    row = cur.execute(
                        "SELECT id FROM resources WHERE category=? AND name=? AND url=?",
                        (it["category"], it["name"], it["url"]),
                    ).fetchone()
                    if row:
                        dup += 1
                        break
                    cur.execute(
                        """INSERT INTO resources
                           (name, category, media_type, region, year, cover,
                            description, url, quality, source, line_name, raw_type_name,
                            episodes, hits, score, updated_at, created_at)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (it["name"], it["category"], it["media_type"], it["region"],
                         it["year"], it["cover"], it["description"], it["url"],
                         it["quality"], it["source"], it["line_name"], it["raw_type_name"],
                         it["episodes"], it["hits"], it["score"], now, now),
                    )
                    conn.commit()
                inserted += 1
                break
            except sqlite3.OperationalError as e:
                if attempt < 2:
                    time.sleep(0.5 * (attempt + 1))
                    continue
                # 实在写不进就跳过这条，不让整轮崩溃（已持锁提交失败极罕见）
                print(f"[warn] 插入失败已跳过: {it['name']} -> {e}")
                break
    return inserted, dup


# ---------- 采集 ----------
def collect_type(api, site_name, tid, tname, forced_cat, forced_mt,
                 pages, start_page, want_category, progress, lock):
    """采集单个源的一个 type"""
    key = f"{site_name}|{tid or 'all'}"
    pg = progress.get(key, start_page)
    max_pages = pages if (pages and pages > 0) else 10 ** 9
    total_new = 0
    total_dup = 0
    conn = init_sqlite()

    try:
        while pg <= max_pages:
            if _COLLECT_TIME_LIMIT and (time.time() - _COLLECT_START) >= _COLLECT_TIME_LIMIT:
                print(f"[{site_name}][{tname or '?'}] 达到 TIME_LIMIT，暂停，进度已保存")
                break
            params = {"ac": "list", "pg": pg}
            if tid is not None:
                params["t"] = tid
            list_url = join_params(api, **params)
            data = http_get_json(list_url)
            if not data or not data.get("list"):
                break

            vod_list = data["list"]
            total_pages = data.get("pagecount") or 0
            if (not pages or pages <= 0) and total_pages:
                max_pages = total_pages

            batch = config.COLLECTORS["cc0cd"]["detail_batch"]
            ids = [str(v.get("vod_id")) for v in vod_list if v.get("vod_id")]
            items_to_insert = []
            for i in range(0, len(ids), batch):
                chunk = ids[i:i + batch]
                detail_url = join_params(api, ac="detail", ids=",".join(chunk))
                detail = http_get_json(detail_url)
                if not detail:
                    continue
                for v in detail.get("list") or []:
                    parsed = parse_vod_to_items(
                        v, site_name, tname,
                        forced_category=forced_cat,
                        forced_media_type=forced_mt,
                        want_category=want_category,
                    )
                    items_to_insert.extend(parsed)
                time.sleep(REQUEST_DELAY)

            new, dup = bulk_insert_items(conn, items_to_insert)
            total_new += new
            total_dup += dup

            with lock:
                progress[key] = pg + 1
            pg += 1
            time.sleep(REQUEST_DELAY)

            if pg % 5 == 0:
                print(f"[{site_name}][{tname or '?'}] 页{pg}/{max_pages} "
                      f"新增{total_new} 重复{total_dup}")
    finally:
        conn.close()

    return site_name, total_new, total_dup


def collect_source(api, site_name, pages, start_page, want_category, progress, lock, type_workers):
    """采集单个源"""
    type_map = map_source_types(api)
    if not type_map:
        print(f"[{site_name}] 未获取到类型列表，fallback 全量混采")
        type_map = [(None, "", None, "")]

    # 探测 t=type_id 是否生效：部分源忽略 type 参数
    first_tid = next((t[0] for t in type_map if t[0] is not None), None)
    if first_tid is not None:
        probe = http_get_json(join_params(api, ac="list", t=first_tid, pg=1))
        if not probe or not probe.get("list"):
            print(f"[{site_name}] 源不支持按类型筛选，改用全量混采模式")
            type_map = [(None, "", None, "")]

    if want_category:
        type_map = [t for t in type_map if t[2] == want_category or t[2] is None]
    if not type_map:
        print(f"[{site_name}] 无匹配目标分类，跳过")
        return site_name, 0, 0

    total_new = 0
    total_dup = 0
    with ThreadPoolExecutor(max_workers=type_workers) as ex:
        futs = []
        for tid, tname, cat, mt in type_map:
            futs.append(ex.submit(
                collect_type, api, site_name, tid, tname, cat, mt,
                pages, start_page, want_category, progress, lock
            ))
        for f in as_completed(futs):
            _, n, d = f.result()
            total_new += n
            total_dup += d

    return site_name, total_new, total_dup


# ---------- 进度 ----------
def load_progress():
    if os.path.exists(PROGRESS_FILE):
        try:
            with open(PROGRESS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_progress(progress):
    os.makedirs(os.path.dirname(PROGRESS_FILE), exist_ok=True)
    with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
        json.dump(progress, f, ensure_ascii=False, indent=2)


# ---------- 主函数 ----------
def main():
    parser = argparse.ArgumentParser(description="m3u-library 本地并发采集（提速版）")
    parser.add_argument("--sources", default="",
                        help="指定源，逗号分隔（如 量子,最大,茅台）")
    parser.add_argument("--pages", type=int, default=None,
                        help="每源每 type 最大页数（0=全量）")
    parser.add_argument("--start-page", type=int, default=1, help="起始页码")
    parser.add_argument("-c", "--category",
                        choices=["movie", "tv", "anime", "variety"],
                        help="只采指定分类")
    parser.add_argument("--workers", type=int, default=SOURCE_WORKERS,
                        help=f"同时跑的源数（默认 {SOURCE_WORKERS}）")
    parser.add_argument("--type-workers", type=int, default=TYPE_WORKERS,
                        help=f"每个源内同时跑的 type 数（默认 {TYPE_WORKERS}）")
    parser.add_argument("--reset", action="store_true", help="重置进度从头开始")
    parser.add_argument("--time-limit", type=int, default=0,
                        help="单次运行时间上限（秒），到点暂停并保存进度以便云端续跑；0=不限时")
    args = parser.parse_args()

    global _COLLECT_START, _COLLECT_TIME_LIMIT
    _COLLECT_START = time.time()
    _COLLECT_TIME_LIMIT = args.time_limit or 0

    if args.reset and os.path.exists(PROGRESS_FILE):
        os.remove(PROGRESS_FILE)
        print("已重置进度")

    progress = load_progress()
    lock = threading.Lock()

    cfg = config.COLLECTORS["cc0cd"]
    direct = cfg.get("direct_sources", {})
    if args.sources:
        keys = [k.strip() for k in args.sources.split(",") if k.strip()]
        sources = {k: direct[k] for k in keys if k in direct}
    else:
        sources = direct

    if not sources:
        print("没有可用的采集源")
        return

    print(f"开始并发采集：源数={len(sources)}，源并发={args.workers}，"
          f"type并发={args.type_workers}，请求间隔={REQUEST_DELAY}s")
    t0 = time.time()
    grand_new = 0
    grand_dup = 0

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {
            ex.submit(collect_source, api, name, args.pages, args.start_page,
                      args.category, progress, lock, args.type_workers): name
            for name, api in sources.items()
        }
        for f in as_completed(futs):
            name, n, d = f.result()
            grand_new += n
            grand_dup += d
            print(f"[{name}] 完成，新增 {n}，重复 {d}")
            save_progress(progress)

    save_progress(progress)
    elapsed = time.time() - t0
    # 写统计文件，供工作流判定「是否有新数据」以决定是否重新生成+部署（节省 Pages 构建额度）。
    # 即便被 TIME_LIMIT 打断，此文件也已写入，下一轮续跑后继续累计。
    stats = {"new": grand_new, "dup": grand_dup, "elapsed_min": round(elapsed / 60, 1)}
    try:
        with open(os.path.join(REPO, "data", "_collect_stats.json"), "w", encoding="utf-8") as f:
            json.dump(stats, f, ensure_ascii=False, indent=2)
    except Exception:
        pass
    print(f"\n=== 全部完成：新增 {grand_new}，重复 {grand_dup}，"
          f"耗时 {elapsed / 60:.1f} 分钟 ===")


if __name__ == "__main__":
    main()
