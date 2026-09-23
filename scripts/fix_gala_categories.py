# -*- coding: utf-8 -*-
"""把采集站误标为「电影 / 剧集 / 动漫」的电视晚会改判为「综艺」（幂等）。

背景
----
春节联欢晚会 / 跨年晚会 / 元宵喜乐会这类**电视晚会**本质是综艺节目，但采集站
常把它们挂在「电影」类目下，且源站的 type_name 本身就是电影类型（「动作片」等），
所以 collect 侧只靠 type_name 判不出来 → 电影墙前排混进大量晚会卡片
（线上实测：2026 春节晚会、北京卫视跨年、湖南卫视元宵喜乐会 全在「电影」Tab）。

修复策略
--------
判定逻辑统一放在 `generator.m3u.is_tv_gala`（与采集侧共用同一函数，避免漂移），
本脚本对**已入库的老数据**做一次性改判 —— 改 DB 而不是改生成代码，好处是
web / 途播 JSON / Jellyfin / M3U / 增量 KV 全部下游自动一致，不会漏掉某一路。

幂等：只有「片名命中晚会特征 且 当前分类不是 variety」的行才会被 UPDATE，
跑第二遍时命中 0 行。可以在每次生成前无脑调用。

用法
----
    python scripts/fix_gala_categories.py            # 只统计（dry-run，默认）
    python scripts/fix_gala_categories.py --apply    # 真正写库
"""
import argparse
import os
import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from generator.m3u import is_tv_gala  # noqa: E402

DEFAULT_DB = REPO / "data" / "media.db"


def fix(db_path: str, apply: bool = False, verbose: bool = True) -> int:
    """返回被改判（或 dry-run 下将被改判）的行数。"""
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute("SELECT id, name, category, media_type FROM resources").fetchall()
    except sqlite3.OperationalError as e:
        print(f"[fix_gala] 读取失败: {e}")
        con.close()
        return 0

    hits = [r for r in rows if is_tv_gala(r["name"]) and (r["category"] or "") != "variety"]

    if verbose:
        by_cat = {}
        for r in hits:
            by_cat[r["category"]] = by_cat.get(r["category"], 0) + 1
        print(f"[fix_gala] 全库 {len(rows)} 行；命中「电视晚会且不在综艺」{len(hits)} 行")
        if by_cat:
            print(f"[fix_gala] 原分类分布: {by_cat}")
            for r in hits[:15]:
                print(f"    {r['category']:<8} {r['name']!r}")
            if len(hits) > 15:
                print(f"    ...（其余 {len(hits) - 15} 行省略）")

    if not hits:
        con.close()
        return 0

    if not apply:
        print("[fix_gala] dry-run，未写库（加 --apply 生效）")
        con.close()
        return len(hits)

    con.executemany(
        "UPDATE resources SET category='variety', media_type='' WHERE id=?",
        [(r["id"],) for r in hits],
    )
    con.commit()
    con.close()
    print(f"[fix_gala] 已改判 {len(hits)} 行为 variety（media_type 置空）")
    return len(hits)


def main():
    ap = argparse.ArgumentParser(description="修正被误标为电影的电视晚会（幂等）")
    ap.add_argument("--apply", action="store_true", help="真正写库；缺省只统计")
    ap.add_argument("--db", default=os.environ.get("MEDIA_DB") or str(DEFAULT_DB))
    args = ap.parse_args()
    if not os.path.exists(args.db):
        print(f"[fix_gala] 库不存在，跳过: {args.db}")
        return 0
    return fix(args.db, apply=args.apply)


if __name__ == "__main__":
    raise SystemExit(0 if main() or True else 0)
