#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""分集回填编排器（云端 GitHub Actions 调用，也可本机运行）。

背景：episodes(分集) 功能是 2026-08-31 才加进采集链路，而此前全量回填的大批老数据
episodes 为空。源站其实对电视剧返回完整分集（第01集$url1#第02集$url2#...），只是老数据
没被重新采集刷新。

本脚本由 backfill-episodes.yml 调用，做「分批全量重采 tv/anime/variety」：
- 进度存 data/backfill_progress.json（done_srcs=已完成源列表, done=是否全完成）
- **多进程并发**：用 ThreadPoolExecutor 同时回填多个源（网络 IO 密集，并发显著加速），
  单源内部按 tv/anime/variety 顺序采到末页（某分类本次 0 新增且非首页 -> 判末页切走）。
- 兼容旧进度格式 {"idx":N,"page":M}（视为已完成前 N 个源）。
- 时间预算 TIME_BUDGET_MIN：到时停止提交新源、落盘断点，下一轮续跑（每天 schedule 触发）。

需在仓库根目录运行（脚本会自动 chdir 到仓库根）。
"""
import json
import os
import sqlite3
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(REPO)

STATE = "data/backfill_progress.json"
DRY = os.environ.get("BACKFILL_DRYRUN") == "1"   # 本地模拟用，CI 不设置

# 并发度：CI runner 一般 4 vCPU，网络 IO 密集，默认 6（env BACKFILL_WORKERS 可调，最大 12）。
WORKERS = min(int(os.environ.get("BACKFILL_WORKERS", "6")), 12)
# 单 run 时间预算（分钟）：到时停新任务、落盘续跑。默认 330（workflow timeout 360 留余量）。
TIME_BUDGET_MIN = int(os.environ.get("BACKFILL_TIME_BUDGET", "330"))
# 进度落盘间隔（秒）
SAVE_INTERVAL = 300

# 22 个直连源 + 4 个配置中心源（与 update.yml 保持一致）。
# ⚠️ 顺序按「体量从大到小」排列：大源贡献了绝大多数老标题，优先回填它们。
SOURCES = [
    "量子", "最大", "茅台", "魔都", "爱奇艺",   # 5 大源（约 15w/12w/14w/8.7w/6.6w 条）前置
    "红牛", "猫眼", "金鹰", "索尼", "非凡", "光速", "无尽", "速播", "极速", "火狐",
    "西瓜", "优酷", "百度", "豆瓣", "暴风", "星球", "樱花",
    "360", "旺旺", "如意", "率率",               # 配置中心源（较小）置后
]
CATS = ["tv", "anime", "variety"]   # 仅剧集类需要分集；movie 一般为整片，跳过省时

CHUNK = 1500    # 单个 (源,分类) 每次最多采的页数
MAX_PAGE_PER_SRC = 9000  # 单源安全上限，超过强制切下一源（防异常空转）

_lock = threading.Lock()
_state = {"done_srcs": [], "done": False}
_last_save = [0.0]


def load_state():
    if os.path.exists(STATE):
        try:
            d = json.load(open(STATE, encoding="utf-8"))
            # 兼容旧格式 {idx, page}：视为已完成前 idx 个源
            if "idx" in d and "done_srcs" not in d:
                idx = int(d.get("idx", 0))
                return {"done_srcs": SOURCES[:idx], "done": idx >= len(SOURCES)}
            return {"done_srcs": d.get("done_srcs", []), "done": bool(d.get("done", False))}
        except Exception:
            pass
    return {"done_srcs": [], "done": False}


def save_state(force=False):
    now = time.time()
    with _lock:
        if not force and (now - _last_save[0]) < SAVE_INTERVAL:
            return
        json.dump(_state, open(STATE, "w", encoding="utf-8"), ensure_ascii=False)
        _last_save[0] = now


def count_rows(cat, src):
    if DRY:
        return 0   # 占位；DRY 模式下 delta 由 collect_source 直接模拟
    try:
        con = sqlite3.connect("data/media.db")
        n = con.execute(
            "SELECT COUNT(*) FROM resources WHERE category=? AND line_name=?",
            (cat, src),
        ).fetchone()[0]
        con.close()
        return n
    except Exception:
        return -1


def collect(src, cat, page, pages):
    if DRY:
        time.sleep(0.003)
        return 0, f"[DRY] 模拟采集 {src}/{cat} 第{page}页×{pages}\n"
    cmd = [
        sys.executable, "main.py", "collect", "-n", "cc0cd",
        "-c", cat, "--sources", src,
        "--pages", str(pages), "--start-page", str(page), "--no-generate",
    ]
    r = subprocess.run(cmd, capture_output=True, text=True,
                       encoding="utf-8", errors="ignore")
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def collect_source(src):
    """对该源 3 个分类循环采页，直到各分类到末页或超单源上限。"""
    print(f"▶ 开始回填源 {src}")
    for cat in CATS:
        page = 1
        while page <= MAX_PAGE_PER_SRC:
            before = count_rows(cat, src)
            rc, out = collect(src, cat, page, CHUNK)
            after = count_rows(cat, src)
            if DRY:
                # 模拟：前 4500 页有新增，之后无（触发末页检测切分类）
                delta = CHUNK if page <= 4500 else 0
            else:
                delta = (after - before) if (before >= 0 and after >= 0) else -1
            tail = "\n".join([l for l in out.strip().splitlines()[-4:]])
            print(f"  [{src}/{cat}] start={page} rc={rc} rows_delta={delta}\n  {tail}")
            if delta <= 0 and page > 1:
                print(f"  [{src}/{cat}] 0 新增判定末页 -> 切下一分类")
                break
            page += CHUNK
            if page > MAX_PAGE_PER_SRC:
                print(f"  [{src}] 已达 {MAX_PAGE_PER_SRC} 页上限 -> 切下一分类")
                break
    print(f"✅ 源 {src} 回填完毕")
    return src


def main():
    global _state
    _state = load_state()
    if _state["done"]:
        print("✅ 所有源的分集回填已完成（done=true）。无需再采。")
        return

    todo = [s for s in SOURCES if s not in _state["done_srcs"]]
    print(f"▶ 待回填源 {len(todo)}/{len(SOURCES)} 个 | 并发 {WORKERS} | 时间预算 {TIME_BUDGET_MIN}min")
    print(f"  已完成: {_state['done_srcs']}")

    deadline = time.time() + TIME_BUDGET_MIN * 60
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = {ex.submit(collect_source, s): s for s in todo}
        for f in as_completed(futs):
            s = futs[f]
            try:
                f.result()
            except Exception as e:
                print(f"  [!] 源 {s} 异常: {e}")
            with _lock:
                if s not in _state["done_srcs"]:
                    _state["done_srcs"].append(s)
            save_state(force=True)
            print(f"✅ 进度: {len(_state['done_srcs'])}/{len(SOURCES)} 已完成（本轮含 {s}）")
            if time.time() > deadline:
                print("⏰ 时间预算耗尽，停止提交新源，等待在跑任务收尾...")
                for fu in futs:
                    fu.cancel()
                break

    if len(_state["done_srcs"]) >= len(SOURCES):
        _state["done"] = True
    save_state(force=True)
    done = "ALL DONE ✅" if _state["done"] else f"续跑待办 {len(todo) - len(_state['done_srcs'])} 个"
    print(f"✅ 本批次结束。done_srcs={len(_state['done_srcs'])}/{len(SOURCES)} | {done}")


if __name__ == "__main__":
    main()
