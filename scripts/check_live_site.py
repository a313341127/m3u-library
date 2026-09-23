#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""线上站点部署后验收：① 前端关键代码是否上线 ② 分类归属是否正常 ③ 卡片总量是否合理。

为什么要写成脚本：部署 run 结束 ≠ 线上生效（Pages 还要发布 + CDN 生效），
而且**空转的验收比不验收更危险** —— 曾用「扫 0 张卡 → 0 个命中 → 判 PASS」报了个假通过。

三个必踩的坑（都写进断言里防住了）：
  1. `api/all.json` 的 `pageFiles` 是**相对 /api/ 的裸文件名**（`cat_movie_p0.json`）。
     拼成「站点根 + 文件名」会 404 拿到 HTML、json 解析失败被吞掉 → 变成 0 张卡。
     → 所以脚本**强制断言扫到的卡数 > 阈值**，取不到就报错而不是静默通过。
  2. 偶发 403 **不是 UA 被拦**（极简 `Mozilla/5.0` 实测 120 次并发 12 全部 200、无限流）
     → 一律重试，别去改请求头。
  3. 判定「某次修复是否上线」用的字符串必须是**该次修改独有的**。
     曾用 `ensureCat(currentCat, () => renderGridOnly())` 判「搜索修复上线没」，
     但那句在更早的「加载更多」按钮里就有 → 假 PASS。

用法：
    python scripts/check_live_site.py                 # 立即验收一次
    python scripts/check_live_site.py --wait          # 等线上 updated 变化后再验收
    python scripts/check_live_site.py --min-cards 5000
退出码 0 = PASS。
"""
import argparse
import gzip
import io
import json
import pathlib
import sys
import time
import urllib.request
import zlib
from concurrent.futures import ThreadPoolExecutor

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from generator.m3u import is_tv_gala  # noqa: E402

BASE = "https://qinjin.pages.dev/"
H = {"User-Agent": "Mozilla/5.0"}
CTX = __import__("ssl").create_default_context()
CTX.check_hostname = False
CTX.verify_mode = __import__("ssl").CERT_NONE

# 前端修复的「独有标记」：新增/改动前端逻辑时，把本次改动独有的字符串挂到这里，
# 这样验收脚本才能判断「这次修复到底上线没有」。
FRONTEND_MARKERS = {
    "分片幂等门闩 __INJECTED__": ("__INJECTED__", True),
    "加载排队 __PENDING__": ("__PENDING__", True),
    "终态标记 __READY__": ("__READY__", True),
    "并发注入 const PART_CONC": ("const PART_CONC", True),
    "旧 push 单行已消失": ("for (var i = 0; i < a.length; i++) r.push(a[i]);", False),
    "搜索补全提示": ("正在加载全部片库", True),
    "搜索触发 ensureCat": ("if (searchQuery) ensureCat(currentCat", True),
    "筛选/排序补 ensureCat": ("ensureCat(currentCat, render)", True),
}


def bust(url):
    """给 URL 加 cache-busting 参数：Pages 各边缘节点在发布瞬间会短暂拿旧副本，
    实测同一秒内 index.html 出现过 89,515 / 96,509 两种长度（md5 不同），
    不破缓存会让「刚部署完」的验收随机假 FAIL。"""
    return url + ("&" if "?" in url else "?") + "_cb=" + str(int(time.time() * 1000))


def fetch(url, tries=4, timeout=60):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=H)
            with urllib.request.build_opener(urllib.request.HTTPSHandler(context=CTX)).open(req, timeout=timeout) as r:
                raw = r.read()
                enc = (r.headers.get("Content-Encoding") or "").lower()
                if enc == "gzip":
                    raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
                elif enc == "deflate":
                    raw = zlib.decompress(raw, -zlib.MAX_WBITS)
                return raw
        except Exception as e:
            if i == tries - 1:
                print(f"  [warn] 取不到 {url[-46:]} {type(e).__name__} {str(e)[:60]}")
                return None
            time.sleep(2)


def get_json(url):
    d = fetch(url)
    try:
        return json.loads(d.decode("utf-8", "ignore")) if d else None
    except Exception:
        return None


def scan_cat(idx, cat, pages):
    """按分类拉前 N 页卡片。pageFiles 是相对 /api/ 的裸文件名，必须补前缀。"""
    cat_info = idx["cats"][cat]
    files = cat_info["pageFiles"][:pages]
    urls = [bust(f if f.startswith("http") else BASE + "api/" + f) for f in files]
    out, bad = [], 0
    with ThreadPoolExecutor(10) as ex:
        for d in ex.map(get_json, urls):
            if d:
                out.extend(d.get("movies", []))
            else:
                bad += 1
    if bad:
        print(f"  [warn] {cat}: {bad}/{len(urls)} 页取不到")
    return out


def verify(pages=60, min_cards=5000):
    print("\n" + "=" * 64)
    idx = get_json(bust(BASE + "api/all.json"))
    if not idx:
        print("api/all.json 取不到，无法验收")
        return False
    print(f"线上 updated = {idx['updated']}")
    print(f"库存：movie {idx['cats']['movie']['count']:,} / "
          + " / ".join(f"{k} {idx['cats'][k]['count']:,}" for k in ("tv", "anime", "variety")))

    html = (fetch(bust(BASE)) or b"").decode("utf-8", "ignore")
    print("\n① 前端代码（index.html %d 字节）" % len(html))
    front_ok = True
    for label, (pat, want) in FRONTEND_MARKERS.items():
        got = pat in html
        ok = got == want
        front_ok &= ok
        print(f"   {'OK  ' if ok else 'FAIL'} {label}")

    print(f"\n② 分类归属（电影抽前 {pages} 页、综艺抽前 {max(10, pages // 2)} 页）")
    mv = scan_cat(idx, "movie", pages)
    va = scan_cat(idx, "variety", max(10, pages // 2))
    if len(mv) < min_cards:
        print(f"   [FAIL] 电影只扫到 {len(mv)} 张卡（阈值 {min_cards}）—— 数据没取到，"
              f"这种情况判 PASS 就是空转，必须报错")
        return False
    gala_in_movie = sorted({m["name"] for m in mv if is_tv_gala(m["name"])})
    gala_in_variety = sorted({m["name"] for m in va if is_tv_gala(m["name"])})
    print(f"   电影 {len(mv):,} 张 → 电视晚会 {len(gala_in_movie)} 张")
    for n in gala_in_movie[:8]:
        print(f"      {n!r}")
    print(f"   综艺 {len(va):,} 张 → 电视晚会 {len(gala_in_variety)} 张（应远大于 0）")
    for n in gala_in_variety[:5]:
        print(f"      {n!r}")

    cat_ok = not gala_in_movie and len(gala_in_variety) > 0
    print("\nVERDICT: " + ("PASS" if front_ok and cat_ok else "FAIL"))
    return front_ok and cat_ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--wait", action="store_true", help="等线上 updated 变化后再验收")
    ap.add_argument("--pages", type=int, default=60)
    ap.add_argument("--min-cards", type=int, default=5000)
    ap.add_argument("--timeout-min", type=int, default=90)
    a = ap.parse_args()

    if a.wait:
        base = (get_json(bust(BASE + "api/all.json")) or {}).get("updated")
        print(f"基线 updated={base}，等待变化…", flush=True)
        deadline = time.time() + a.timeout_min * 60
        while time.time() < deadline:
            time.sleep(45)
            cur = (get_json(bust(BASE + "api/all.json")) or {}).get("updated")
            if cur and cur != base:
                print(f"[{time.strftime('%H:%M:%S')}] 线上已更新 → {cur}，等 120s 让 CDN 各节点一致", flush=True)
                time.sleep(120)
                break
        else:
            print("等待超时", flush=True)

    return 0 if verify(a.pages, a.min_cards) else 1


if __name__ == "__main__":
    sys.exit(main())
