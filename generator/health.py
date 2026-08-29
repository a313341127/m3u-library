# -*- coding: utf-8 -*-
"""播放线路健康度（供生成期决策）

data/host_health.json 由 scripts/check_source_health.py 产出：按域名记录三种取流方式
（浏览器直连 / 带 Referer 直连 / Worker 中转）的探测结论。生成页面时据此：

  1. 剔除已确认全线失效的线路（点了必然黑屏，不如不展示）；
  2. 把「浏览器可直接取流」的线路排最前，只有需要中转的才走同源 /proxy；
  3. 某条目所有线路都失效时，整条不进页面（避免 75% 卡片点了不能播）。

体检表缺失或被禁用时全部按 unknown 处理，退化成「原样输出」，不会误杀。
"""
import json
import os
from urllib.parse import urlparse, quote

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PATH = os.path.join(_ROOT, "data", "host_health.json")

_data = None
_enabled = os.environ.get("USE_HOST_HEALTH", "1") != "0"
# 严格模式：只有经过体检且确认可播的源才保留（unknown 也剔除）
_strict = os.environ.get("WEB_STRICT_HEALTH", os.environ.get("STRICT_HEALTH", "0")) == "1"

_DB_PATH = os.path.join(_ROOT, "data", "media.db")
_url_health_cache = None

# 排序权重：能直连的最优先（延迟最低、不占 CF 带宽），中转兜底
_RANK = {"direct": 0, "unknown": 1, "refer": 2, "proxy": 3, "dead": 9}


def _load_url_health():
    """读取 prune_dead.py 写入的 url_health 表，返回 {url: status}。"""
    global _url_health_cache
    if _url_health_cache is not None:
        return _url_health_cache
    out = {}
    try:
        import sqlite3
        con = sqlite3.connect(_DB_PATH)
        try:
            rows = con.execute("SELECT url, status FROM url_health").fetchall()
            out = {u: s for u, s in rows}
        finally:
            con.close()
    except Exception:
        pass
    _url_health_cache = out
    return out


def load(force: bool = False) -> dict:
    global _data
    if _data is None or force:
        try:
            with open(_PATH, "r", encoding="utf-8") as f:
                _data = json.load(f).get("hosts", {})
        except Exception:
            _data = {}
    return _data


def enabled() -> bool:
    return _enabled


def info(url: str) -> dict:
    if not url:
        return {}
    return load().get(urlparse(url).netloc, {})


def mode(url: str) -> str:
    """direct=浏览器可直连 / unknown=未体检 / refer,proxy=需中转 / dead=已失效"""
    if not _enabled:
        return "unknown"
    d = info(url)
    if not d:
        return "unknown"
    if not d.get("ok"):
        return "dead"
    best = d.get("best")
    return best if best in ("direct", "refer", "proxy") else "unknown"


def rank(url: str) -> int:
    return _RANK.get(mode(url), 1)


def playable(url: str) -> bool:
    """是否还有希望播出来。严格模式下未体检(unknown)或已失效均剔除。"""
    if not url:
        return False
    # URL 级：prune_dead.py 已确认死亡的直接剔除
    if _load_url_health().get(url) == "dead":
        return False
    m = mode(url)
    if m == "dead":
        return False
    if _strict:
        # 有体检数据时才对 unknown 严格，避免体检表缺失时把库清空
        if load() and m == "unknown":
            return False
    return True


def proxied(url: str) -> str:
    """同源中转地址：worker 端拉流后返回，规避跨域与防盗链"""
    return "/proxy?u=" + quote(url, safe="")


def play_url(url: str):
    """给网页播放器用的最终地址。dead 返回 None。"""
    if not url:
        return None
    m = mode(url)
    if m == "dead":
        return None
    if m == "direct":
        return url
    # unknown 先按直连给（未体检的域名大多数能直连），播放失败前端会自动切中转
    if m == "unknown":
        return url
    return proxied(url)


def pick_lines(urls):
    """把某条目的多条线路整理成 [(展示地址, 原始地址)]，可用在前、直连优先。"""
    out = []
    for u in urls:
        if not u:
            continue
        p = play_url(u)
        if p is None:
            continue
        out.append((p, u, rank(u)))
    out.sort(key=lambda x: x[2])
    return [(p, u) for p, u, _ in out]


def stats():
    d = load()
    ok = sum(1 for v in d.values() if v.get("ok"))
    return len(d), ok
