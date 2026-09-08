"""一次性诊断：定位 media.db 体积膨胀根因。
- 各类目字节占用（用 length() 估算各行文本列总字节）
- (name,year,source) 完全重复行数（验证“每次重采重复插入”假设）
- episodes 字段平均/总字节
只读，不改数据。
"""
import os, sqlite3, sys

path = sys.argv[1] if len(sys.argv) > 1 else "data/media.db"
print(f"数据库: {path}  ({os.path.getsize(path)/1024/1024:.1f} MB)")
c = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
cols = [r[1] for r in c.execute("PRAGMA table_info(resources)")]
print("字段:", ", ".join(cols))
print()

total = c.execute("SELECT COUNT(*) FROM resources").fetchone()[0]
print(f"总行数: {total:,}")
print()

# 各类目行数 + 字节估算（用 length() 累加所有 TEXT/BLOB 列）
text_cols = [x for x in cols if x not in ("id", "category", "created_at")]
expr = "+".join(f"COALESCE(LENGTH({x}),0)" for x in text_cols) or "0"
print("=== 类目：行数 / 估算字节 / 占比 ===")
rows = c.execute(
    f"SELECT category, COUNT(*), SUM({expr}) FROM resources GROUP BY category ORDER BY 3 DESC"
).fetchall()
grand = sum(r[2] or 0 for r in rows)
for cat, n, b in rows:
    b = b or 0
    print(f"  {cat:<10} 行 {n:>9,} | 字节 {b/1024/1024:>9.1f} MB | {100*b/grand:5.1f}%")
print(f"  {'合计':<10} {'':>9} | 字节 {grand/1024/1024:>9.1f} MB")
print()

# source 分布（前 20）
if "source" in cols:
    print("=== source 分布（前 20）===")
    for s, n in c.execute("SELECT source, COUNT(*) n FROM resources GROUP BY source ORDER BY n DESC LIMIT 20"):
        print(f"  {str(s):<24} {n:>9,}")
    print()

# 重复 (name,year,source) —— 若大量重复说明每轮重采重复插入
if "source" in cols:
    print("=== 重复检测 (name,year,source) ===")
    dup_groups = c.execute(
        "SELECT COUNT(*) FROM (SELECT 1 FROM resources GROUP BY name, year, source HAVING COUNT(*)>1)"
    ).fetchone()[0]
    dup_rows = c.execute(
        "SELECT SUM(cnt)-COUNT(*) FROM (SELECT COUNT(*) cnt FROM resources GROUP BY name, year, source)"
    ).fetchone()[0]
    print(f"  存在重复的行组数: {dup_groups:,}")
    print(f"  因重复多出来的冗余行: {dup_rows:,}  ({100*dup_rows/total:.1f}% of all rows)")
    print()

# episodes 字节占比
if "episodes" in cols:
    print("=== episodes 字段字节 ===")
    ep_total_rows = c.execute(
        "SELECT COUNT(*) FROM resources WHERE episodes IS NOT NULL AND episodes<>'' AND episodes<>'[]'"
    ).fetchone()[0]
    ep_bytes = c.execute(
        "SELECT SUM(LENGTH(episodes)) FROM resources WHERE episodes IS NOT NULL"
    ).fetchone()[0] or 0
    print(f"  带 episodes 行数: {ep_total_rows:,}")
    print(f"  episodes 总字节: {ep_bytes/1024/1024:.1f} MB  ({100*ep_bytes/grand:.1f}% of 估算总字节)")
    avg = (ep_bytes / ep_total_rows) if ep_total_rows else 0
    print(f"  平均每条 episodes 字节: {avg/1024:.1f} KB")
    print()

# 若加 UNIQUE(name,year,source) 能砍到多少行
if "source" in cols:
    uni = c.execute("SELECT COUNT(*) FROM (SELECT 1 FROM resources GROUP BY name, year, source)").fetchone()[0]
    print(f"=== 若按 (name,year,source) 去重 ===")
    print(f"  {total:,} 行 -> {uni:,} 行  (可减 {total-uni:,} 行, {100*(total-uni)/total:.1f}%)")
    uni2 = c.execute("SELECT COUNT(*) FROM (SELECT 1 FROM resources GROUP BY name, year)").fetchone()[0]
    print(f"  若按 (name,year) 去重 -> {uni2:,} 行  (可减 {total-uni2:,} 行, {100*(total-uni2)/total:.1f}%)")
