"""只读分析 media.db 采集/入库概况（不改任何数据）。

用法:
    python scripts/analyze_db.py                      # 默认 data/media.db
    python scripts/analyze_db.py data/_remote_media.db
"""
import os
import sys
import sqlite3

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DB = os.path.join(REPO, "data", "media.db")


def human(n):
    return f"{n/1024/1024:.1f} MB" if n >= 1024 * 1024 else f"{n/1024:.1f} KB"


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_DB
    if not os.path.exists(path):
        print("不存在:", path)
        return 1

    print(f"数据库: {path}")
    print(f"体积  : {human(os.path.getsize(path))}")
    print()

    c = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    cols = [r[1] for r in c.execute("PRAGMA table_info(resources)")]
    print("字段:", ", ".join(cols))
    print()

    total = c.execute("SELECT COUNT(*) FROM resources").fetchone()[0]
    print(f"总行数: {total:,}")
    print()

    print("=== 分类分布 ===")
    for cat, n in c.execute(
            "SELECT category, COUNT(*) n FROM resources GROUP BY category ORDER BY n DESC"):
        print(f"  {cat:<12} {n:>9,}")
    print()

    if "source" in cols:
        print("=== source 分布（前 15）===")
        for s, n in c.execute(
                "SELECT source, COUNT(*) n FROM resources GROUP BY source ORDER BY n DESC LIMIT 15"):
            print(f"  {str(s):<22} {n:>9,}")
        print()

    if "created_at" in cols:
        oldest = c.execute("SELECT MIN(created_at) FROM resources").fetchone()[0]
        newest = c.execute("SELECT MAX(created_at) FROM resources").fetchone()[0]
        print(f"最早 created_at: {oldest}")
        print(f"最新 created_at: {newest}   (UTC)")
        print()
        print("=== 入库趋势（按天，最近 14 天）===")
        for d, n in c.execute(
                "SELECT substr(created_at,1,10) d, COUNT(*) n FROM resources "
                "GROUP BY d ORDER BY d DESC LIMIT 14"):
            print(f"  {d}  {n:>9,}")
        print()

        print("=== 最近 48 小时（按小时，UTC）===")
        rows = list(c.execute(
            "SELECT substr(created_at,1,13) h, COUNT(*) n FROM resources "
            "GROUP BY h ORDER BY h DESC LIMIT 48"))
        for h, n in reversed(rows):
            print(f"  {h}:00  {n:>8,}")
        print()

    if "episodes" in cols:
        print("=== 多集覆盖（episodes 非空）===")
        for cat, tot, ep in c.execute(
                "SELECT category, COUNT(*), SUM(CASE WHEN episodes IS NOT NULL "
                "AND episodes<>'' AND episodes<>'[]' THEN 1 ELSE 0 END) "
                "FROM resources GROUP BY category ORDER BY 2 DESC"):
            ep = ep or 0
            print(f"  {cat:<12} 总 {tot:>8,} | 带集数 {ep:>8,}  ({100*ep/tot:.1f}%)")
        print()

    if "line_name" in cols:
        print("=== 线路覆盖（line_name 非空）===")
        for cat, tot, ln in c.execute(
                "SELECT category, COUNT(*), SUM(CASE WHEN line_name IS NOT NULL "
                "AND line_name<>'' THEN 1 ELSE 0 END) FROM resources GROUP BY category ORDER BY 2 DESC"):
            ln = ln or 0
            print(f"  {cat:<12} 总 {tot:>8,} | 有线路名 {ln:>8,} ({100*ln/tot:.1f}%)")
        print()

    print("=== 跨源重合度（按 name+year 归一）===")
    uni = c.execute("SELECT COUNT(*) FROM (SELECT 1 FROM resources GROUP BY name, year)").fetchone()[0]
    print(f"  原始行 {total:,} | 唯一(name,year) {uni:,} | 重合 {100*(1-uni/total):.1f}%")
    for cat, in c.execute("SELECT DISTINCT category FROM resources ORDER BY category"):
        t = c.execute("SELECT COUNT(*) FROM resources WHERE category=?", (cat,)).fetchone()[0]
        u = c.execute(
            "SELECT COUNT(*) FROM (SELECT 1 FROM resources WHERE category=? GROUP BY name, year)",
            (cat,)).fetchone()[0]
        print(f"    {cat:<12} 行 {t:>8,} -> 唯一 {u:>8,}  (重合 {100*(1-u/t):.1f}%)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
