#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""分集回填编排器（云端 GitHub Actions 调用，也可本机运行）。

背景：episodes(分集) 功能是 2026-08-31 才加进采集链路，而此前全量回填的大批老数据
episodes 为空。源站其实对电视剧返回完整分集（第01集$url1#第02集$url2#...），只是老数据
没被重新采集刷新。

本脚本每天由 backfill-episodes.yml 调用一次，做「分批全量重采 tv/anime/variety」：
- 进度存 data/backfill_progress.json（idx=当前源下标, page=该源下一批起始页）
- 每 run 消耗 BUDGET 页预算（约 4-5h @ 3s/页，留余量防 6h 超时）
- 单个 (源,分类) 每次最多采 CHUNK 页
- 末页检测：某源三个分类本次均 0 新增且非首页 -> 判定到末页，切下一源
- 所有源跑完自动停止（idx 越界）

需在仓库根目录运行（脚本会自动 chdir 到仓库根）。
"""
import json
import os
import sqlite3
import subprocess
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(REPO)

STATE = "data/backfill_progress.json"
DRY = os.environ.get("BACKFILL_DRYRUN") == "1"   # 本地模拟用，CI 不设置

# 22 个直连源 + 4 个配置中心源（与 update.yml 保持一致）。
# ⚠️ 顺序按「体量从大到小」排列：大源贡献了绝大多数老标题，优先回填它们，
# 头几天就能让分集覆盖大部分片子，而不是像早期那样先磨一堆小源、主流大源排到最后。
SOURCES = [
    "量子", "最大", "茅台", "魔都", "爱奇艺",   # 5 大源（约 15w/12w/14w/8.7w/6.6w 条）前置
    "红牛", "猫眼", "金鹰", "索尼", "非凡", "光速", "无尽", "速播", "极速", "火狐",
    "西瓜", "优酷", "百度", "豆瓣", "暴风", "星球", "樱花",
    "360", "旺旺", "如意", "率率",               # 配置中心源（较小）置后
]
CATS = ["tv", "anime", "variety"]   # 仅剧集类需要分集；movie 一般为整片，跳过省时

BUDGET = 6500   # 每个 run 最多采的页数（≈5h @ 2.5s/页 + 0.25s 间隔，留余量防 6h job 超时）
CHUNK = 1500    # 单个 (源,分类) 每次最多采的页数
MAX_PAGE_PER_SRC = 9000  # 单源安全上限，超过强制切下一源（防异常空转）


def load_state():
    if os.path.exists(STATE):
        try:
            d = json.load(open(STATE, encoding="utf-8"))
            return int(d.get("idx", 0)), int(d.get("page", 1))
        except Exception:
            pass
    return 0, 1


def save_state(idx, page):
    json.dump({"idx": idx, "page": page},
              open(STATE, "w", encoding="utf-8"), ensure_ascii=False)


def count_rows(cat, src):
    if DRY:
        return 0   # 占位；DRY 模式下 delta 由 main() 直接模拟
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
        time.sleep(0.005)
        return 0, f"模拟采集 {src}/{cat} 第{page}页×{pages}\n重复已更新: {pages}\n"
    cmd = [
        sys.executable, "main.py", "collect", "-n", "cc0cd",
        "-c", cat, "--sources", src,
        "--pages", str(pages), "--start-page", str(page), "--no-generate",
    ]
    r = subprocess.run(cmd, capture_output=True, text=True,
                       encoding="utf-8", errors="ignore")
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def main():
    idx, page = load_state()
    if idx >= len(SOURCES):
        print("✅ 所有源的分集回填已完成（idx 已越界）。无需再采。")
        return

    print(f"▶ 起始进度: idx={idx}({SOURCES[idx]}) page={page} 预算={BUDGET}页")
    remaining = BUDGET

    while remaining > 0 and idx < len(SOURCES):
        src = SOURCES[idx]
        deltas = []
        for cat in CATS:
            if remaining <= 0:
                break
            take = min(CHUNK, remaining)
            before = count_rows(cat, src)
            rc, out = collect(src, cat, page, take)
            after = count_rows(cat, src)
            if DRY:
                # 模拟：前 4500 页有新增，之后无（触发末页检测切源）
                delta = take if page <= 4500 else 0
            else:
                delta = (after - before) if (before >= 0 and after >= 0) else -1
            deltas.append(delta)
            # 打印 collect 末尾的摘要行便于排障
            tail = "\n".join([l for l in out.strip().splitlines()[-6:]])
            print(f"[{src}/{cat}] start={page} take={take} rc={rc} rows_delta={delta}\n{tail}")
            remaining -= take

        # 末页检测：三个分类本次都无新增且非首页 -> 判定已到末页，切下一源
        if deltas and all(d <= 0 for d in deltas) and page > 1:
            print(f"[{src}] 三个分类均无新增，判定已到末页 -> 切下一源")
            idx += 1
            page = 1
            continue

        # 推进当前源页码
        page += CHUNK

        # 单源页数安全上限，防异常空转
        if page > MAX_PAGE_PER_SRC:
            print(f"[{src}] 已达 {MAX_PAGE_PER_SRC} 页上限 -> 切下一源")
            idx += 1
            page = 1

        # 预算用尽：保存断点，下次续跑
        if remaining <= 0:
            break

    save_state(idx, page)
    done = "ALL DONE ✅" if idx >= len(SOURCES) else f"next={SOURCES[idx]} page={page}"
    print(f"✅ 本批次完成。进度已保存 -> idx={idx} ({done})。剩余预算={remaining}")


if __name__ == "__main__":
    main()
