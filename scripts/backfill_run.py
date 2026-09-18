#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""分集回填编排器（云端 GitHub Actions 调用，也可本机运行）。

背景：episodes(分集) 功能是 2026-08-31 才加进采集链路，而此前全量回填的大批老数据
episodes 为空。源站其实对电视剧返回完整分集（第01集$url1#第02集$url2#...），只是老数据
没被重新采集刷新。

本脚本由 backfill-episodes.yml 调用，做「分批全量重采 tv/anime/variety」：
- 进度存 data/backfill_progress.json：
    { done_srcs:[已完成源], done:是否全完成, src_pages:{源:{分类:续跑页码}} }
- **多进程并发**：ThreadPoolExecutor 同时回填多个源（网络 IO 密集，并发显著加速）。
- **断点续跑**：每个源记录「已采到哪一页」，单源预算( SRC_BUDGET_MIN )到时先收尾、
  续跑点落盘，下一轮从断点继续，直到该源 3 个分类都采到末页才算 done（不全量也行，
  不会因大源卡死而永远跑不完）。
- 兼容旧进度格式 {"idx":N,"page":M}（视为已完成前 N 个源）。

★ 2026-09-18 修复（根因：回填 run 总是被 360min 超时取消、进度全丢）：
  旧版用 `with ThreadPoolExecutor() as ex:` 退出时 shutdown(wait=True) 会**等所有在跑源
  全部采完**；大源(量子等)单源就可能采几百分钟，于是整段跑到 workflow timeout 都退不出
  来，关键的「上传 media.db + 提交进度」步骤永远没机会执行 -> 进度全丢 -> 下一轮从头来 ->
  采集看门狗把 backfill 计失败 -> 自动重投 -> 又超时 ... 滚到 fails=87、last_health=false。
  现改为：
  1) 显式创建 executor，到点/异常时先 kill_all() 杀掉在跑的子进程(main.py collect)，
     再 shutdown(wait=False, cancel_futures=True) **立刻退出**，绝不等在跑源；
  2) 单源预算 SRC_BUDGET_MIN：单个源采到预算即收尾、续跑点落盘，下轮继续，避免被单源拖垮；
  3) 子进程带 30min 硬超时(CHUNK_TIMEOUT_S)，异常慢源不会永久 hang。
  这样 backfill_run.py 必在 ~SRC_BUDGET 分钟内返回，留给「上传库+提交进度」充足余量，
  整体稳稳低于 360min workflow 超时。
"""
import json
import os
import sqlite3
import subprocess
import sys
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(REPO)

STATE = "data/backfill_progress.json"
DRY = os.environ.get("BACKFILL_DRYRUN") == "1"   # 本地模拟用，CI 不设置

# 并发度：CI runner 一般 2~4 vCPU，网络 IO 密集，默认 6（env BACKFILL_WORKERS 可调，最大 12）。
WORKERS = min(int(os.environ.get("BACKFILL_WORKERS", "6")), 12)
# 全局时间预算（分钟）：到点停止等待在跑任务、立即收尾。默认 240（留 120min 给上传+提交）。
TIME_BUDGET_MIN = int(os.environ.get("BACKFILL_TIME_BUDGET", "240"))
# 单源时间预算（分钟）：单个源采到该时长即收尾（续跑点已落盘），下轮从断点继续。
# 避免被某个超大源(量子等)拖到整体超时。默认 120。
SRC_BUDGET_MIN = int(os.environ.get("BACKFILL_SRC_BUDGET", "120"))
# 单 chunk(1500 页)子进程硬超时（秒）：异常慢源不会永久 hang。默认 1800(30min)。
CHUNK_TIMEOUT_S = int(os.environ.get("BACKFILL_CHUNK_TIMEOUT", "1800"))
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
_state = {"done_srcs": [], "done": False, "src_pages": {}}
_last_save = [0.0]

_procs = []          # 在跑的子进程(main.py collect)，用于到点/退出时强杀
_plock = threading.Lock()


def load_state():
    if os.path.exists(STATE):
        try:
            with open(STATE, encoding="utf-8") as f:
                d = json.load(f)
            # 兼容旧格式 {idx, page}：视为已完成前 idx 个源
            if "idx" in d and "done_srcs" not in d:
                idx = int(d.get("idx", 0))
                return {"done_srcs": SOURCES[:idx], "done": idx >= len(SOURCES), "src_pages": {}}
            return {
                "done_srcs": d.get("done_srcs", []),
                "done": bool(d.get("done", False)),
                "src_pages": d.get("src_pages", {}),
            }
        except Exception:
            pass
    return {"done_srcs": [], "done": False, "src_pages": {}}


def save_state(force=False):
    # 注意：本函数自身不加锁，调用方必须已持有 _lock（collect_source / main 均如此），
    # 否则会出现「同一线程重复获取非可重入 Lock」的自死锁。
    now = time.time()
    if not force and (now - _last_save[0]) < SAVE_INTERVAL:
        return
    # 必须用 with 关闭句柄：Windows 上 open("w") 不关会导致下一次 open 阻塞卡死。
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump(_state, f, ensure_ascii=False)
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
    """采一 chunk；返回 (rc, output)。子进程登记到 _procs 便于强杀。"""
    if DRY:
        time.sleep(0.003)
        return 0, f"[DRY] 模拟采集 {src}/{cat} 第{page}页×{pages}\n"
    cmd = [
        sys.executable, "main.py", "collect", "-n", "cc0cd",
        "-c", cat, "--sources", src,
        "--pages", str(pages), "--start-page", str(page), "--no-generate",
    ]
    try:
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             encoding="utf-8", errors="ignore")
    except Exception as e:
        return 1, f"[spawn fail] {e}"
    with _plock:
        _procs.append(p)
    try:
        out, _ = p.communicate(timeout=CHUNK_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        try:
            p.kill()
        except Exception:
            pass
        out, _ = p.communicate()
        return 1, f"[chunk timeout {CHUNK_TIMEOUT_S}s] killed\n"
    with _plock:
        if p in _procs:
            _procs.remove(p)
    return p.returncode, out or ""


def kill_all():
    """强杀所有在跑的子进程(main.py collect)，避免在跑源继续写 media.db。"""
    with _plock:
        procs = list(_procs)
    for p in procs:
        try:
            p.kill()
        except Exception:
            pass


def collect_source(src):
    """对该源 3 个分类循环采页（从续跑点开始），到末页或超单源预算则收尾。

    返回 (src, fully_done)：fully_done=True 表示该源 3 分类都已采到末页，可标记 done；
    因超单源预算提前收尾时 fully_done=False，续跑点已落盘，下轮继续。
    """
    print(f"▶ 开始回填源 {src}")
    src_start = time.time()
    resume = _state["src_pages"].get(src, {})
    fully_done = True

    for cat in CATS:
        page = resume.get(cat, 1)
        while page <= MAX_PAGE_PER_SRC:
            before = count_rows(cat, src)
            rc, out = collect(src, cat, page, CHUNK)
            after = count_rows(cat, src)
            if DRY:
                delta = CHUNK if page <= 4500 else 0   # 模拟：前 4500 页有新增
            else:
                delta = (after - before) if (before >= 0 and after >= 0) else -1
            tail = "\n".join([l for l in out.strip().splitlines()[-4:]])
            print(f"  [{src}/{cat}] start={page} rc={rc} rows_delta={delta}\n  {tail}")

            # 记录续跑点（本 chunk 末页 +1），下一轮从这儿继续
            with _lock:
                _state["src_pages"].setdefault(src, {})[cat] = page + CHUNK
                save_state(force=True)

            if delta <= 0 and page > 1:
                print(f"  [{src}/{cat}] 0 新增判定末页 -> 该分类完成")
                break
            page += CHUNK
            if page > MAX_PAGE_PER_SRC:
                print(f"  [{src}] 已达 {MAX_PAGE_PER_SRC} 页上限 -> 该分类完成")
                break
            if time.time() - src_start > SRC_BUDGET_MIN * 60:
                print(f"  [{src}] 单源预算 {SRC_BUDGET_MIN}min 到 -> 本轮先收尾，续跑点已存，下轮继续")
                fully_done = False
                return src, fully_done
        # while 正常结束（末页/上限）表示该分类采完；若因超预算 break 则 fully_done 已=False

    if fully_done:
        with _lock:
            if src not in _state["done_srcs"]:
                _state["done_srcs"].append(src)
            _state["src_pages"].pop(src, None)
            save_state(force=True)
        print(f"✅ 源 {src} 回填完毕（tv/anime/variety 全部分类）")
    else:
        print(f"⏸ 源 {src} 本轮到单源预算，续跑点已存，下轮继续")
    return src, fully_done


def main():
    global _state
    _state = load_state()
    if _state["done"]:
        print("✅ 所有源的分集回填已完成（done=true）。无需再采。")
        return

    todo = [s for s in SOURCES if s not in _state["done_srcs"]]
    print(f"▶ 待回填源 {len(todo)}/{len(SOURCES)} 个 | 并发 {WORKERS} | "
          f"全局预算 {TIME_BUDGET_MIN}min | 单源预算 {SRC_BUDGET_MIN}min")
    print(f"  已完成: {_state['done_srcs']}")
    print(f"  续跑中: {list(_state['src_pages'].keys())}")

    deadline = time.time() + TIME_BUDGET_MIN * 60
    ex = ThreadPoolExecutor(max_workers=WORKERS)
    try:
        futs = {ex.submit(collect_source, s): s for s in todo}
        for f in as_completed(futs):
            s = futs[f]
            try:
                f.result()
            except Exception as e:
                print(f"  [!] 源 {s} 异常: {e}")
            print(f"✅ 进度: done_srcs={len(_state['done_srcs'])}/{len(SOURCES)} | "
                  f"续跑中={list(_state['src_pages'].keys())}")
            if time.time() > deadline:
                print("⏰ 全局时间预算耗尽，停止等待在跑任务，立即收尾...")
                break
    finally:
        # 关键修复：先强杀在跑子进程（避免继续写 media.db），再非阻塞关闭 executor，
        # **绝不等待在跑源** —— 否则整体越过 360min workflow 超时 -> run 被取消、进度全丢。
        kill_all()
        ex.shutdown(wait=False, cancel_futures=True)

    if len(_state["done_srcs"]) >= len(SOURCES):
        _state["done"] = True
    save_state(force=True)
    done = "ALL DONE ✅" if _state["done"] else \
        f"续跑待办 {len([s for s in SOURCES if s not in _state['done_srcs']])} 个"
    print(f"✅ 本批次结束。done_srcs={len(_state['done_srcs'])}/{len(SOURCES)} | {done}")


if __name__ == "__main__":
    main()
