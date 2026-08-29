# -*- coding: utf-8 -*-
"""采集直播频道台标并固定到本地（data/live_logos/{ch_id}.png）。

背景：
  直播源 M3U 自带 tvg-logo（多指向国内台标 CDN，如 xn--rgv465a.top，
  浏览器常有反盗链/区域限制导致网页与途播里台标显示为空白）。
  本脚本把每个频道的台标下载到本地固化存储，部署时由 generate_covers.py
  同步到 output/covers/live/ 随 Pages 发布，彻底摆脱对易失效外链的依赖。

约定（务必与 web.py / generate_movies_json.build_live 一致）：
  ch_id = "l_" + md5("cat|name")[:14]
  文件名 = ch_id + ".png"
  网页/途播引用路径 = /covers/live/{ch_id}.png

特性：
  - 按 (category, name) 去重，每个频道取一个非空 logo URL（优先主用 CDN）。
  - 中文路径自动百分号编码。
  - 本机走 HTTPS_PROXY 代理；CI(ubuntu) 直连。
  - 单条下载失败不影响其余（404/超时等计入 fail，不抛异常）。
  - 幂等：已存在且为有效 PNG 则跳过；--force 重新下载。
"""
import os
import sys
import json
import hashlib
import argparse
import urllib.request
import urllib.error
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

LOCAL_DIR = os.path.join(ROOT, "data", "live_logos")
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
# 主用台标 CDN（覆盖绝大多数频道，成功率最高）；同名多 logo 时优先它
PREFERRED_HOST = "xn--rgv465a.top"


def ch_id_of(cat: str, name: str) -> str:
    return "l_" + hashlib.md5(("%s|%s" % (cat, name)).encode("utf-8")).hexdigest()[:14]


def load_channels():
    """读取 live 表，按 (category, name) 去重，每个频道挑一个 logo URL。"""
    import sqlite3
    from collector.live import LIVE_TABLE

    db_path = os.path.join(ROOT, "data", "media.db")
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    con.executescript(LIVE_TABLE)
    rows = con.execute(
        "SELECT category, name, logo FROM live WHERE logo IS NOT NULL AND logo <> ''"
    ).fetchall()
    con.close()

    best = {}
    for r in rows:
        key = (r["category"], r["name"])
        logo = (r["logo"] or "").strip()
        if not logo:
            continue
        if key not in best:
            best[key] = logo
        else:
            # 已有一个 logo：若当前是主用 CDN 且已有不是，则替换
            cur = best[key]
            if PREFERRED_HOST in urllib.parse.urlsplit(logo).netloc and \
               PREFERRED_HOST not in urllib.parse.urlsplit(cur).netloc:
                best[key] = logo
    return [{"category": k[0], "name": k[1], "logo": v} for k, v in best.items()]


def is_png(path: str) -> bool:
    try:
        with open(path, "rb") as f:
            return f.read(8) == PNG_MAGIC
    except Exception:
        return False


def _encode_url(url: str) -> str:
    """对 URL 路径中的非 ASCII 字符做百分号编码（保留 scheme/host/查询）。"""
    p = urllib.parse.urlsplit(url)
    path = urllib.parse.quote(p.path, safe="/")
    query = urllib.parse.quote(p.query, safe="=&")
    return urllib.parse.urlunsplit((p.scheme, p.netloc, path, query, p.fragment))


def fetch_one(task):
    cat, name, logo, force = task["category"], task["name"], task["logo"], task["force"]
    cid = ch_id_of(cat, name)
    dst = os.path.join(LOCAL_DIR, cid + ".png")
    if (not force) and os.path.exists(dst) and is_png(dst):
        return {"cat": cat, "name": name, "status": "skip-exists"}

    enc = _encode_url(logo)
    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
    handlers = []
    if proxy:
        handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    op = urllib.request.build_opener(*handlers)
    req = urllib.request.Request(enc, headers={"User-Agent": "Mozilla/5.0"})
    try:
        data = op.open(req, timeout=30).read()
        if data[:8] != PNG_MAGIC:
            return {"cat": cat, "name": name, "status": "skip-notpng"}
        os.makedirs(LOCAL_DIR, exist_ok=True)
        tmp = dst + ".tmp"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, dst)
        return {"cat": cat, "name": name, "status": "ok"}
    except Exception as e:
        return {"cat": cat, "name": name, "status": "fail:%s" % type(e).__name__}


def main():
    ap = argparse.ArgumentParser(description="采集直播台标到本地固定存储")
    ap.add_argument("--force", action="store_true", help="重新下载已存在的台标")
    ap.add_argument("--limit", type=int, default=0, help="仅处理前 N 个频道（调试）")
    ap.add_argument("--workers", type=int, default=16, help="并发下载线程数")
    args = ap.parse_args()

    channels = load_channels()
    if args.limit:
        channels = channels[: args.limit]
    tasks = [{"category": c["category"], "name": c["name"],
              "logo": c["logo"], "force": args.force} for c in channels]

    stats = {"ok": 0, "skip-exists": 0, "skip-notpng": 0, "fail": 0}
    fails = []
    os.makedirs(LOCAL_DIR, exist_ok=True)
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for res in pool.map(fetch_one, tasks):
            st = res["status"]
            if st.startswith("fail"):
                stats["fail"] += 1
                fails.append((res["cat"], res["name"], st))
            else:
                stats[st] = stats.get(st, 0) + 1

    print("直播台标采集完成: 共 %d 个频道" % len(channels))
    print("  结果:", json.dumps(stats, ensure_ascii=False))
    if fails:
        print("  失败的频道（前 20，不影响其余）:")
        for cat, name, st in fails[:20]:
            print("    [%s] %s -> %s" % (cat, name, st))
    print("  已保存目录:", LOCAL_DIR)


if __name__ == "__main__":
    main()
