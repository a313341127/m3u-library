#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""线上站点部署后验收：① 前端关键代码 ② 分类归属 ③ 布局 ④ 线路名中文化 ⑤ 卡片总量。

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
import re
import sys
import time
import urllib.request
from urllib.parse import quote
import zlib
from collections import Counter
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
    # 2026-09-23：播放体验四项改动
    "画面冻结自愈 stallEvaluate": ("function stallEvaluate", True),
    "隐藏死链线路 isDeadSource": ("function isDeadSource", True),
    "冻结自愈提示文案": ("画面卡住，正在自动修复", True),
    "隐藏线路入口文案": ("显示全部（另有 ", True),
    # 2026-09-24：首页/全站搜索改走服务端 /site/search
    "搜索服务端检索 runSearch": ("function runSearch", True),
    "首页搜索结果视图 applyHomeSearchView": ("function applyHomeSearchView", True),
    "搜索状态提示 appendSearchHint": ("function appendSearchHint", True),
    "调用同源搜索接口": ("/site/search?cat=", True),
    "条目自带分类 _cat": ("it._cat = c;", True),
    # 2026-09-24 补：服务端撞到分页数上限时客户端凭 nextOff 续拉（否则散落命中 0 结果）
    "搜索续拉 searchCatTask": ("function searchCatTask", True),
    "续拉游标 nextOff": ("d.nextOff", True),
}

# 布局断言：搜索框必须与分类 Tab 同一行（在 <header> 内），且 <main> 里不得重复
LAYOUT_MARKERS = {
    "搜索框在 <header> 内": ('id="search"', "header"),
    "分类 Tab 与搜索框同一行 header-row": ("header-row", "header"),
    "<main> 内无重复的 search-wrap": ("search-wrap", "!main"),
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

    # ④ 布局：搜索框是否已并入导航栏（与分类 Tab 同一行）
    print("\n④ 布局")
    head_html = html[html.index("<header>"):html.index("</header>")] if "<header>" in html else ""
    main_html = html[html.index("<main"):] if "<main" in html else ""
    lay_ok = True
    for label, (pat, where) in LAYOUT_MARKERS.items():
        if where == "header":
            ok = pat in head_html
        else:
            ok = pat not in main_html
        lay_ok &= ok
        print(f"   {'OK  ' if ok else 'FAIL'} {label}")
    print(f"   （header 片段：{re.sub(r'\\s+', ' ', head_html)[:120]}…)")

    # ⑤ 线路名必须全是中文（hym3u8 这类裸代码不该出现在用户界面）
    print("\n⑤ 线路名（api srcs 抽样）")
    line_names = Counter()
    for m in mv:
        for s in (m.get("srcs") or []):
            line_names[s] += 1
    latin = {k: v for k, v in line_names.items()
             if re.search(r"[a-zA-Z]{3,}", str(k)) and not re.search(r"[\u4e00-\u9fff]", str(k))}
    print(f"   {len(line_names)} 个不同线路名；TOP: " + ", ".join(f"{k}×{v}" for k, v in line_names.most_common(8)))
    print(f"   仍是裸露英文代码的：{list(latin)[:8] or '无'}")
    name_ok = (not latin) and len(line_names) > 0

    # ⑥ 搜索接口：首页搜索完全依赖它（跨分类、覆盖全库，不下载分片）
    print("\n⑥ 搜索接口 /site/search")
    sr = get_json(bust(BASE + "site/search?cat=movie&limit=20&q=" + quote("功夫女足")))
    if sr is None:
        print("   [FAIL] 接口取不到（404 → worker 路由没上线）")
        search_ok = False
    else:
        hits = sr.get("movies") or []
        print(f"   ok={sr.get('ok')} total={sr.get('total')} 返回 {len(hits)} 条")
        for m in hits[:3]:
            print(f"      {m.get('name')} | {m.get('year')} | 线路 {(m.get('sources') or []) and len(m['sources']) or 0} 条")
        bad_cat = get_json(bust(BASE + "site/search?cat=live&q=x"))
        empty = get_json(bust(BASE + "site/search?cat=movie&q="))
        search_ok = (bool(sr.get("ok")) and sr.get("total", 0) >= 1 and len(hits) >= 1
                     and all(m.get("name") and m.get("id") and (m.get("url") or m.get("sources"))
                             for m in hits))
        # 直播不参与片名搜索、空词直接返回空：都应是「干净失败」而不是 500
        print(f"   边界：cat=live → {'拒绝(400)' if bad_cat is None else bad_cat.get('error', 'ok?')}"
              f" | 空词 → {'空结果' if not (empty or {}).get('movies') else '有结果?'}")
        if bad_cat is not None and bad_cat.get("ok") is not False:
            print("   [warn] live 分类未被拒绝")

        # ⑦ 散落命中的重灾区：worker 单次调用子请求上限约 50，而一部的各版本可能
        # 散落在几十个分页上（实测 tv「狂飙」42 条命中散在 37 个分页）→ 早期实现直接
        # 500 "Too many subrequests"。现在必须：①不 500 ②给 nextOff 续拉游标 ③凭 off 能续拉。
        print("\n⑦ 散落命中不再 500（分页数上限 + 续拉）")
        scat = get_json(bust(BASE + "site/search?cat=tv&limit=200&q=" + quote("狂飙")))
        if scat is None:
            print("   [FAIL] 散落命中查询直接失败（很可能是 Too many subrequests 500）")
            scattered_ok = False
        else:
            got = len(scat.get("movies") or [])
            print(f"   ok={scat.get('ok')} total={scat.get('total')} 返回 {got} 条"
                  f" | pages={scat.get('pages')} truncated={scat.get('truncated')}"
                  f" nextOff={scat.get('nextOff')}")
            scattered_ok = bool(scat.get("ok")) and got >= 1 and isinstance(scat.get("pages"), int)
            if scat.get("truncated"):
                nx = scat.get("nextOff")
                tail = get_json(bust(BASE + f"site/search?cat=tv&limit=200&q={quote('狂飙')}&off={nx}"))
                tgot = len((tail or {}).get("movies") or [])
                first_ids = {m.get("id") for m in (scat.get("movies") or [])}
                tail_ids = {m.get("id") for m in ((tail or {}).get("movies") or [])}
                dup = first_ids & tail_ids
                print(f"   续拉 off={nx} → {'失败' if tail is None else str(tgot) + ' 条，与首轮重复 ' + str(len(dup))}")
                scattered_ok = scattered_ok and tail is not None and bool(tail.get("ok")) and \
                    tgot >= 1 and not dup
            else:
                print("   （本轮未被截断，说明命中集中，无需续拉）")
        search_ok = search_ok and scattered_ok

    print("\nVERDICT: " + ("PASS" if (front_ok and cat_ok and lay_ok and name_ok and search_ok) else "FAIL")
          + f"  (前端 {front_ok} / 分类 {cat_ok} / 布局 {lay_ok} / 线路名 {name_ok} / 搜索 {search_ok})")
    return front_ok and cat_ok and lay_ok and name_ok and search_ok


def readiness_gaps(html):
    """本轮的「正向标记 + 布局断言」里，还有哪些没上线。

    为什么不用 updated 是否变化来判断：**CI 自己也会推提交并触发部署**
    （update/backfill 工作流提交进度文件 → deploy.yml 的 paths 命中），
    那种 run 跑的是旧代码、却同样会改 updated。只看 updated 会对着旧构建
    验收一轮、白报一次 FAIL（2026-09-23 实际踩过）。改成直接探「新代码到没到」。
    """
    gaps = [k for k, (pat, want) in FRONTEND_MARKERS.items() if want and pat not in html]
    if "<header>" not in html or "<main" not in html:
        return gaps + ["header/main 结构缺失"]
    head_html = html[html.index("<header>"):html.index("</header>")]
    main_html = html[html.index("<main"):]
    for label, (pat, where) in LAYOUT_MARKERS.items():
        ok = (pat in head_html) if where == "header" else (pat not in main_html)
        if not ok:
            gaps.append(label)
    return gaps


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--wait", action="store_true",
                    help="等「本轮新增标记」真的上线后再验收（比只看 updated 变化可靠）")
    ap.add_argument("--pages", type=int, default=60)
    ap.add_argument("--min-cards", type=int, default=5000)
    ap.add_argument("--timeout-min", type=int, default=90)
    a = ap.parse_args()

    if a.wait:
        base = (get_json(bust(BASE + "api/all.json")) or {}).get("updated")
        print(f"基线 updated={base}；等待本轮新代码上线（探标记，不只看 updated）…", flush=True)
        deadline = time.time() + a.timeout_min * 60
        last = None
        while time.time() < deadline:
            html = (fetch(bust(BASE)) or b"").decode("utf-8", "ignore")
            gaps = readiness_gaps(html)
            if not gaps:
                cur = (get_json(bust(BASE + "api/all.json")) or {}).get("updated")
                print(f"[{time.strftime('%H:%M:%S')}] 新构建已上线（updated={cur}），"
                      f"等 120s 让 CDN 各节点一致", flush=True)
                time.sleep(120)
                break
            if gaps != last:
                print(f"[{time.strftime('%H:%M:%S')}] 线上仍缺 {len(gaps)} 项："
                      f"{', '.join(gaps[:3])}{' …' if len(gaps) > 3 else ''}", flush=True)
                last = gaps
            time.sleep(45)
        else:
            print("等待超时（标记未全部上线，下面按现状验收）", flush=True)

    return 0 if verify(a.pages, a.min_cards) else 1


if __name__ == "__main__":
    sys.exit(main())
