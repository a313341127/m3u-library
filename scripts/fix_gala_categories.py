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
    # 修复 BLOB 片名脏数据（2026-11-23）：个别源站把 bytes 原样入库，is_tv_gala / clean_title
    # 等正则消费方在全表扫描时会抛 TypeError("cannot use a string pattern on a bytes-like
    # object")，未被下方 except OperationalError 捕获 -> 每日任务固定在此步失败、后续全部
    # skipped（2026-11-05 起连续复发）。精准定点修复：typeof(name)='blob' 只扫坏行，
    # decode 后写回，幂等。
    try:
        blobs = con.execute(
            "SELECT id, name FROM resources WHERE typeof(name)='blob'").fetchall()
    except sqlite3.DatabaseError as e:
        print(f"[fix_gala] BLOB 片名检查失败: {e}")
        blobs = []
    if blobs:
        fixed = []
        for r in blobs:
            try:
                n = r["name"].decode("utf-8", "replace").strip()
            except Exception:
                n = ""
            fixed.append((n or "未知", r["id"]))
        con.executemany("UPDATE resources SET name=? WHERE id=?", fixed)
        con.commit()
        print(f"[fix_gala] 已修复 {len(blobs)} 条 BLOB 片名（decode utf-8），"
              f"示例: {repr(blobs[0]['name'])[:80]}")
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

    # 逐行改判（2026-11-23 实证真凶）：唯一索引 uq_resources_cat_name_url(category,name,url)
    # 下，若库中已存在同 (name,url) 的 variety 行，一把 executemany UPDATE 会让任意一行
    # 撞键即 IntegrityError 整体失败 -> 此前每日任务连续 18 天卡死在此。
    # 处理：改判撞键时说明已有同源 variety 行，直接删除被误标的源行（去重）。
    # resources 表无任何外键引用其 id，删除安全；幂等：跑第二遍命中 0 行。
    changed = deleted = 0
    for r in hits:
        try:
            con.execute(
                "UPDATE resources SET category='variety', media_type='' WHERE id=?",
                (r["id"],),
            )
            changed += 1
        except sqlite3.IntegrityError:
            con.execute("DELETE FROM resources WHERE id=?", (r["id"],))
            deleted += 1
    con.commit()
    con.close()
    print(f"[fix_gala] 已改判 {changed} 行为 variety（media_type 置空），"
          f"去重删除 {deleted} 行（已存在同 (name,url) 的 variety 行）")
    return changed + deleted


def main():
    ap = argparse.ArgumentParser(description="修正被误标为电影的电视晚会（幂等）")
    ap.add_argument("--apply", action="store_true", help="真正写库；缺省只统计")
    ap.add_argument("--db", default=os.environ.get("MEDIA_DB") or str(DEFAULT_DB))
    args = ap.parse_args()
    if not os.path.exists(args.db):
        print(f"[fix_gala] 库不存在，跳过: {args.db}")
        return 0
    try:
        return fix(args.db, apply=args.apply)
    except Exception as e:  # 兜底：任何意外（坏库/脏数据）都降级跳过，绝不拖垮每日流水线
        import traceback
        print(f"[fix_gala] 执行异常（已降级跳过，不阻断流水线）: {type(e).__name__}: {e}")
        traceback.print_exc()
        return 0


if __name__ == "__main__":
    raise SystemExit(0 if main() or True else 0)
