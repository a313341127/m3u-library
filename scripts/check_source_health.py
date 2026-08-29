# -*- coding: utf-8 -*-
"""播放线路域名体检

按域名聚合所有播放地址，抽样探测每个域名在三种取流方式下的可用性：
  1. direct  —— 浏览器裸直连（用户浏览器在中国大陆的实际路径）
  2. refer   —— 浏览器直连并带同源 Referer（绕过部分防盗链）
  3. proxy   —— Cloudflare Worker 服务端中转（/proxy?u=，可自定义 Referer/UA）

结果写入 data/host_health.json（进 git，供 generator/web.py 在生成页面时
把「已知可用」的线路排到最前、把「全线已死」的线路直接隐藏，避免用户点了才发现播不了）。

用法：
    python scripts/check_source_health.py            # 全量，域名级抽样
    python scripts/check_source_health.py --top 40   # 只测前 40 个域名
    python scripts/check_source_health.py --samples 2 --workers 16
"""
import argparse
import collections
import concurrent.futures as cf
import json
import os
import sqlite3
import ssl
import sys
import time
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "data", "media.db")
OUT = os.path.join(ROOT, "data", "host_health.json")

# 线上 worker 中转入口（Pages 部署地址）
PROXY_ORIGIN = os.environ.get("HEALTH_PROXY_ORIGIN", "https://qinjin.pages.dev")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

_ctx = ssl.create_default_context()
_ctx.check_hostname = False
_ctx.verify_mode = ssl.CERT_NONE


def _opener(use_local_proxy: bool):
    """本地跑脚本时 pages.dev 需要走代理；CI 里直连即可。"""
    handlers = [urllib.request.HTTPSHandler(context=_ctx)]
    p = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy") or ""
    if use_local_proxy and p:
        handlers.insert(0, urllib.request.ProxyHandler({"http": p, "https": p}))
    return urllib.request.build_opener(*handlers)


def _status(url, use_local_proxy=False, referer=None, timeout=12):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "*/*",
        **({"Referer": referer} if referer else {}),
    })
    try:
        r = _opener(use_local_proxy).open(req, timeout=timeout)
        r.read(2048)
        return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception as e:
        return type(e).__name__


def is_ok(st):
    return isinstance(st, int) and 200 <= st < 400


def probe_host(host, urls):
    """返回该域名在三种方式下的最佳状态。urls 为该域名下抽样出的地址。"""
    own = "https://%s/" % host
    res = {"direct": None, "refer": None, "proxy": None}
    for u in urls:
        if res["direct"] is None or not is_ok(res["direct"]):
            st = _status(u, False)
            res["direct"] = st if is_ok(st) or res["direct"] is None else res["direct"]
        if not is_ok(res["direct"]):
            st = _status(u, False, referer=own)
            res["refer"] = st if is_ok(st) or res["refer"] is None else res["refer"]
        pu = PROXY_ORIGIN + "/proxy?u=" + urllib.parse.quote(u, safe="")
        st = _status(pu, True, timeout=25)
        res["proxy"] = st if is_ok(st) or res["proxy"] is None else res["proxy"]
        if all(is_ok(v) for v in res.values() if v is not None) and is_ok(res["direct"]):
            break
    # 只要任一方式可用即视为可播
    res["ok"] = any(is_ok(v) for v in (res["direct"], res["refer"], res["proxy"]))
    res["best"] = ("direct" if is_ok(res["direct"]) else
                   "refer" if is_ok(res["refer"]) else
                   "proxy" if is_ok(res["proxy"]) else "none")
    return host, res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=0, help="只测条数最多的前 N 个域名（0=全部）")
    ap.add_argument("--samples", type=int, default=2, help="每个域名抽样地址数")
    ap.add_argument("--workers", type=int, default=16)
    ap.add_argument("--min-urls", type=int, default=3, help="少于该条数的域名不测")
    args = ap.parse_args()

    db = sqlite3.connect(DB)
    rows = list(db.execute(
        "select url from resources where url like 'http%' "
        "union all select url from live where url like 'http%'"))
    cnt = collections.Counter()
    samples = collections.defaultdict(list)
    for (u,) in rows:
        host = urllib.parse.urlparse(u).netloc
        if not host:
            continue
        cnt[host] += 1
        if len(samples[host]) < args.samples:
            samples[host].append(u)

    hosts = [h for h, c in cnt.most_common() if c >= args.min_urls]
    if args.top:
        hosts = hosts[:args.top]
    print("域名总数 %d，本次体检 %d 个（覆盖 %d 条地址）"
          % (len(cnt), len(hosts), sum(cnt[h] for h in hosts)))

    out = {}
    t0 = time.time()
    done = 0
    with cf.ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(probe_host, h, samples[h]): h for h in hosts}
        for f in cf.as_completed(futs):
            h = futs[f]
            try:
                host, res = f.result()
            except Exception as e:
                host, res = h, {"direct": type(e).__name__, "refer": None,
                                "proxy": None, "ok": False, "best": "none"}
            res["urls"] = cnt[host]
            out[host] = res
            done += 1
            if done % 20 == 0:
                print("  %d/%d  用时 %.0fs" % (done, len(hosts), time.time() - t0))

    # 增量合并：未测到的域名保留上次结论
    if os.path.exists(OUT):
        try:
            with open(OUT, "r", encoding="utf-8") as f:
                old = json.load(f)
            for h, v in old.get("hosts", {}).items():
                out.setdefault(h, v)
        except Exception:
            pass

    ok = sum(1 for v in out.values() if v.get("ok"))
    covered = sum(v.get("urls", 0) for v in out.values() if v.get("ok"))
    total = sum(v.get("urls", 0) for v in out.values())
    payload = {
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "proxy_origin": PROXY_ORIGIN,
        "hosts": out,
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    print("写入 %s" % OUT)
    print("可用域名 %d/%d，覆盖地址 %d/%d (%.1f%%)"
          % (ok, len(out), covered, total, 100.0 * covered / max(total, 1)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
