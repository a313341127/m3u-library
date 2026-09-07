"""并行分片下载 GitHub Release 资产（media.db.gz），用于本地核对权威库。

用法: python scripts/fetch_remote_db.py
说明: 仅本地核对用，不参与 CI；下载到 data/_remote_media.db(.gz)。
"""
import os
import sys
import time
import gzip
import shutil
import urllib.request
import threading

TOKEN = os.environ.get("GITHUB_TOKEN", "")
REPO = "a313341127/m3u-library"
TAG = "db-store"
ASSET = "media.db.gz"

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_GZ = os.path.join(REPO_ROOT, "data", "_remote_media.db.gz")
OUT_DB = os.path.join(REPO_ROOT, "data", "_remote_media.db")

WORKERS = 12
RETRY = 3
PART_TIMEOUT = 120


def api(accept="application/vnd.github+json"):
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    hdrs = [("Accept", accept), ("User-Agent", "m3u-fetch")]
    if TOKEN:
        hdrs.insert(0, ("Authorization", f"token {TOKEN}"))
    op.addheaders = hdrs
    return op


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def resolve_real_url(asset_url):
    """带 auth 请求 API 拿 302 Location（真实地址不带 auth 访问）。"""
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect)
    hdrs = [("Accept", "application/octet-stream"), ("User-Agent", "m3u-fetch")]
    if TOKEN:
        hdrs.insert(0, ("Authorization", f"token {TOKEN}"))
    op.addheaders = hdrs
    try:
        r = op.open(asset_url, timeout=60)
        r.read(0)
        loc = r.headers.get("Location")
        r.close()
        return loc
    except urllib.request.HTTPError as e:
        return e.headers.get("Location")


def fetch_range(url, start, end, out_path, idx, results):
    """下载单个分片，带重试；支持断点续传（已下载的字节不重复拉）。"""
    want = end - start + 1
    last_err = None
    for attempt in range(RETRY):
        have = os.path.getsize(out_path) if os.path.exists(out_path) else 0
        if have >= want:
            results[idx] = have
            return
        s = start + have
        req = urllib.request.Request(url, headers={
            "User-Agent": "m3u-fetch",
            "Range": f"bytes={s}-{end}",
        })
        try:
            with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=PART_TIMEOUT) as r, \
                    open(out_path, "ab") as f:
                while True:
                    b = r.read(1 << 18)
                    if not b:
                        break
                    f.write(b)
            got = os.path.getsize(out_path)
            if got >= want:
                results[idx] = got
                return
            last_err = f"短读 {got}/{want}"
        except Exception as e:
            last_err = f"{type(e).__name__}: {str(e)[:60]}"
        time.sleep(2 * (attempt + 1))
    results[idx] = f"ERR {last_err}"


def main():
    rel = __import__("json").loads(
        api().open(f"https://api.github.com/repos/{REPO}/releases/tags/{TAG}", timeout=40).read().decode())
    asset = next((a for a in rel.get("assets", []) if a["name"] == ASSET), None)
    if not asset:
        print("未找到资产", ASSET)
        return 1
    size = asset["size"]
    print(f"资产 {ASSET}: {size/1024/1024:.1f} MB  updated={asset['updated_at']}", flush=True)

    real = resolve_real_url(asset["url"])
    if not real:
        print("未解析到真实下载地址")
        return 1
    print("真实地址:", real[:90], flush=True)

    # 探测 Range 支持
    probe = urllib.request.Request(real, headers={"User-Agent": "m3u-fetch", "Range": "bytes=0-1023"})
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(probe, timeout=60) as r:
        code = r.getcode()
        cr = r.headers.get("Content-Range", "")
    print(f"Range 探测: HTTP {code} {cr}", flush=True)
    ranged = (code == 206)

    t0 = time.time()
    if not ranged:
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(
                urllib.request.Request(real, headers={"User-Agent": "m3u-fetch"}), timeout=1800) as r, \
                open(OUT_GZ, "wb") as f:
            while True:
                b = r.read(1 << 20)
                if not b:
                    break
                f.write(b)
    else:
        chunk = (size + WORKERS - 1) // WORKERS
        parts = []
        results = {}
        threads = []
        for i in range(WORKERS):
            s = i * chunk
            e = min(size - 1, s + chunk - 1)
            if s > e:
                continue
            p = OUT_GZ + f".part{i}"
            parts.append(p)
            t = threading.Thread(target=fetch_range, args=(real, s, e, p, i, results), daemon=True)
            t.start()
            threads.append(t)
        for t in threads:
            t.join()
        bad = [v for v in results.values() if isinstance(v, str)]
        if bad:
            print("分片失败:", bad[:3])
            return 1
        with open(OUT_GZ, "wb") as fo:
            for p in parts:
                with open(p, "rb") as fi:
                    shutil.copyfileobj(fi, fo, 1 << 20)
                os.remove(p)

    dt = time.time() - t0
    got = os.path.getsize(OUT_GZ)
    print(f"下载完成 {got/1024/1024:.1f} MB, {dt:.0f} 秒 ({got/1048576/max(dt,1):.2f} MB/s)", flush=True)
    if abs(got - size) > 1024:
        print(f"!! 大小不符: 期望 {size} 实际 {got}")
        return 1

    with gzip.open(OUT_GZ, "rb") as fi, open(OUT_DB, "wb") as fo:
        shutil.copyfileobj(fi, fo, 1 << 20)
    print(f"解压完成 {os.path.getsize(OUT_DB)/1024/1024:.1f} MB -> {OUT_DB}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
