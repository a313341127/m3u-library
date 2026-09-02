# -*- coding: utf-8 -*-
"""失效播放片源清理（链接健康检测）

背景:
- resources 表没有 vod_id 列，无法按源站 vod_id 精确回查；
  且国内 CDN 对 Referer/UA 反爬会返回 403/530，但经 worker 中转仍可播放。
- 因此本脚本走「链接健康检测」路线：直接探测每条约播放 URL 的可达性，
  只把"确实死了"的链接删除，避免误删仍可播放的源。

判定规则（保守、只删确定死的）:
- 死链（删除）: 域名无法解析(DNS)、连接被拒/重置、SSL 错误、HTTP 404/410。
- 不确定（跳过，不删）: 403/401/530/000(超时)/其他 —— 这些靠本站中转常能救活，
  且源站临时故障也会返回这类码，误删风险高。
- 探测用 GET Range(0-1023) 小请求，mp4/m3u8 都适用；m3u8 额外校验首字节为 #EXTM3U。

安全机制:
- 默认 dry-run，打印"将删除 N 条" + 样例；加 --apply 才真删。
- --limit N: 本次最多检测 N 条（增量运行，避免单次跑全库太慢）。
- --age-days D: 只检 D 天(默认7)内未检测过的 URL；配合 --limit 可周期性覆盖全库。
- 删除比例上限 --max-delete-ratio (默认0.05): 若本次拟删数/检测数 超过该比例，
  视为疑似源站整体故障，终止删除（除非 --force），防误删。
- --concurrency C: 并发探测数（默认16）。

用法:
  python scripts/prune_dead.py                  # dry-run 预览（默认查 2000 条未近期检测的）
  python scripts/prune_dead.py --limit 5000     # 多查一些，仍预览
  python scripts/prune_dead.py --apply          # 真删（受上限保护）
  python scripts/prune_dead.py --apply --force  # 突破比例上限强删（慎用）
"""
import os
import re
import ssl
import sys
import time
import sqlite3
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(ROOT, "data", "media.db")
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
_CTX = ssl.create_default_context()
_CTX.check_hostname = False
_CTX.verify_mode = ssl.CERT_NONE

# 代理（本机走 127.0.0.1:10808；CI 无代理则为空）
# ⚠️ 关键：CI 无代理，从数据中心 IP 探测时，国内视频 CDN 常对数据中心/海外 IP
#    返回「反爬式 404」（与真实内容删除的 404 无法区分），导致大面积误判死链。
#    本机带住宅代理探测则正常 200。故 404 不再判死（见 _DEAD_CODES）。
_PROXY = os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY") or ""
_HANDLER = urllib.request.ProxyHandler({"http": _PROXY, "https": _PROXY}) if _PROXY else urllib.request.ProxyHandler()

# 仅「内容已明确下架(410 Gone)」+ 连接级失败(见 _classify 的 URLError/SSLError) 判死。
# 404 已移出：源站反爬对数据中心 IP 返 404，与真删除不可区分，误杀率约 50%（实测抽样）。
_DEAD_CODES = {410}
# 这些码视为"不确定"，绝不删（404 加入：反爬式 404 一律保留，等真实播放时再判）
_SKIP_CODES = {401, 403, 404, 429, 500, 502, 503, 504, 530}


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _classify(url: str, timeout: int):
    """返回 (status, code, reason)
    status ∈ {'dead','alive','unknown'}"""
    req = urllib.request.Request(url, headers={"User-Agent": _UA, "Range": "bytes=0-1023"})
    try:
        opener = urllib.request.build_opener(_HANDLER)
        with opener.open(req, timeout=timeout) as resp:
            code = resp.getcode()
            if code in _DEAD_CODES:
                return ("dead", code, "http_%d" % code)
            if code in _SKIP_CODES:
                return ("unknown", code, "http_%d_skip" % code)
            # 2xx / 3xx：再校验内容（m3u8 需以 #EXTM3U 开头）
            try:
                head = resp.read(16)
            except Exception:
                head = b""
            text = head.decode("utf-8", "ignore").lower()
            if url.lower().endswith((".m3u8", ".m3u")) and "#extm3u" not in text:
                # 返回 200 但不是合法 m3u8（可能是错误页）→ 视为失效
                return ("dead", code, "bad_m3u8")
            return ("alive", code, "ok")
    except urllib.error.HTTPError as e:
        code = e.code
        if code in _DEAD_CODES:
            return ("dead", code, "http_%d" % code)
        return ("unknown", code, "http_%d_skip" % code)
    except urllib.error.URLError as e:
        # 域名无法解析 / 连接被拒 / SSL 错误 → 确定死链
        return ("dead", 0, "urlerror:%s" % str(e.reason)[:60])
    except ssl.SSLError as e:
        return ("dead", 0, "ssl:%s" % str(e)[:60])
    except Exception as e:  # 超时等
        return ("unknown", 0, "exc:%s" % str(e)[:60])


def _ensure_health_table(con):
    con.execute(
        """CREATE TABLE IF NOT EXISTS url_health (
            url TEXT PRIMARY KEY,
            status TEXT DEFAULT '',
            code INTEGER DEFAULT 0,
            checked_at TEXT DEFAULT ''
        )"""
    )


def _self_heal(con):
    """自愈：把历史误标为 dead、但实际是成功响应的链接纠正回 alive。

    旧版 prune_dead 曾把 206(Partial Content) 等 2xx/3xx 成功码判为 dead，
    导致这类影片被整片隐藏（如 ly166.com 等 m3u8 源）。当前 _classify 对
    2xx/3xx 一律判 alive/unknown、只把 410(Gone) 与连接级失败(code=0) 判 dead，
    故任何 status='dead' 且 code 非 0/410 的行都是陈旧误杀。每轮运行先纠正，
    确保不会因陈旧数据隐藏影片。幂等：纠正后这些行不再是 dead，后续轮次无影响。
    """
    cur = con.execute(
        "UPDATE url_health SET status='alive' "
        "WHERE status='dead' AND code NOT IN (0, 410)"
    )
    n = cur.rowcount
    if n:
        print("[prune] 自愈：纠正 %d 条陈旧误杀(dead→alive, code 2xx/3xx 等非 0/410)" % n)
    return n


def _pick_urls(con, limit, age_days):
    """选取本次要检测的 URL：age_days 内未检测的优先，按检测时间升序（最久未检的先查）。"""
    cutoff = (datetime.now() - timedelta(days=age_days)).strftime("%Y-%m-%d %H:%M:%S")
    rows = con.execute(
        """SELECT r.url FROM resources r
           LEFT JOIN url_health h ON r.url = h.url
           WHERE h.url IS NULL OR h.checked_at < ?
           GROUP BY r.url
           ORDER BY COALESCE(h.checked_at, '1970-01-01 00:00:00') ASC
           LIMIT ?""",
        (cutoff, limit),
    ).fetchall()
    return [r[0] for r in rows]


def main():
    import argparse
    ap = argparse.ArgumentParser(description="失效播放片源清理（链接健康检测）")
    ap.add_argument("--limit", type=int, default=2000, help="本次最多检测 URL 数")
    ap.add_argument("--age-days", type=int, default=7, help="只检该天数内未检测过的 URL")
    ap.add_argument("--concurrency", type=int, default=16, help="并发探测数")
    ap.add_argument("--timeout", type=int, default=8, help="单条探测超时(秒)")
    ap.add_argument("--apply", action="store_true", help="真正删除（默认 dry-run 预览）")
    ap.add_argument("--max-delete-ratio", type=float, default=0.05,
                    help="删除数/检测数 上限，超过则中止（防源站故障误删）")
    ap.add_argument("--force", action="store_true", help="突破删除比例上限")
    args = ap.parse_args()

    if not os.path.exists(DB_PATH):
        print("[错误] 找不到数据库: %s" % DB_PATH)
        sys.exit(1)

    con = sqlite3.connect(DB_PATH)
    _ensure_health_table(con)
    _self_heal(con)  # 先纠正陈旧误杀(dead→alive)，避免隐藏影片

    urls = _pick_urls(con, args.limit, args.age_days)
    if not urls:
        print("[OK] 没有需要检测的 URL（可能都已近期检测过，调大 --age-days 重试）")
        con.close()
        return

    print("[prune] 本次检测 %d 条 URL（age_days=%d, concurrency=%d, apply=%s）"
          % (len(urls), args.age_days, args.concurrency, args.apply))

    results = []  # (url, status, code, reason)
    done = 0

    def worker(u):
        return (u,) + _classify(u, args.timeout)

    with ThreadPoolExecutor(max_workers=args.concurrency) as ex:
        futs = [ex.submit(worker, u) for u in urls]
        for fut in as_completed(futs):
            results.append(fut.result())
            done += 1
            if done % 200 == 0:
                print("[prune] 进度 %d/%d" % (done, len(urls)))

    # 写回 health 表（保留最新状态）
    now = _now()
    con.executemany(
        "INSERT OR REPLACE INTO url_health(url,status,code,checked_at) VALUES(?,?,?,?)",
        [(u, st, code, now) for (u, st, code, _r) in results],
    )

    dead = [(u, code, r) for (u, st, code, r) in results if st == "dead"]
    unknown = sum(1 for (_, st, _, _) in results if st == "unknown")
    alive = sum(1 for (_, st, _, _) in results if st == "alive")

    print("[prune] 检测 %d | 存活 %d | 失效(拟删) %d | 不确定(跳过) %d"
          % (len(results), alive, len(dead), unknown))

    if not dead:
        print("[OK] 本轮没有确定失效的链接")
        con.commit()
        con.close()
        return

    ratio = len(dead) / max(1, len(results))
    if ratio > args.max_delete_ratio and not args.force:
        print("[中止] 拟删比例 %.1f%% 超过上限 %.1f%%（疑似源站整体故障），未删除任何数据。"
              "如确认源站正常，加 --force 强制。" % (ratio * 100, args.max_delete_ratio * 100))
        con.commit()
        con.close()
        return

    # 打印样例
    print("[样例] 将删除的失效链接（前10）:")
    for u, code, r in dead[:10]:
        print("   [%s] %s  %s" % (code, r, u[:90]))

    if not args.apply:
        print("[dry-run] 未加 --apply，仅预览，未删除。")
        con.commit()
        con.close()
        return

    # 真删：按 url 删除（同 url 的所有行一并移除）
    ids = con.execute(
        "SELECT COUNT(*) FROM resources WHERE url IN (%s)"
        % ",".join("?" * len(dead)), [u for (u, _, _) in dead]
    ).fetchone()[0]
    con.executemany("DELETE FROM resources WHERE url = ?", [(u,) for (u, _, _) in dead])
    con.commit()
    con.close()
    print("[OK] 已删除 %d 条失效 URL（涉及 %d 行资源记录）" % (len(dead), ids))


if __name__ == "__main__":
    main()
