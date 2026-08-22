# -*- coding: utf-8 -*-
"""聚合直播源采集器

流程：下载公开 M3U 聚合源 -> 解析频道 -> 归类（央视/卫视/地方/港澳台）
-> 同频道多线路聚合 -> TCP 直连测速 -> 每频道保留延迟最低的 N 条 -> 入库。

直播源时效性强，每次采集全量替换 live 表（不做增量）。
下载走系统代理（本机 HTTPS_PROXY 环境变量），测速始终直连。
"""
import re
import socket
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse

import config

LIVE_TABLE = """
CREATE TABLE IF NOT EXISTS live (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,               -- 规范化频道名（聚合 key）
    display     TEXT DEFAULT '',             -- 原始显示名
    category    TEXT NOT NULL,               -- cctv / satellite / local / hmt
    logo        TEXT DEFAULT '',             -- 频道图标
    url         TEXT NOT NULL,               -- 播放地址（m3u8）
    latency     INTEGER DEFAULT 0,           -- TCP 延迟 ms，0=未知
    source      TEXT DEFAULT '',             -- 来源源名
    updated_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_live_category ON live(category);
"""

# ---------------- 频道名规范化 ----------------

# 括号噪音：分辨率 / 地理锁 / 播放提示
_PAREN_NOISE = re.compile(
    r"[\[\(]\s*(?:\d{3,4}[pi]?|geo[- ]?blocked|not 24/7|hd|fhd|uhd|4k|8k|"
    r"标清|高清|超清|蓝光|付费|测试)\s*[\]\)]",
    re.IGNORECASE,
)
_TRAILING_NOISE = re.compile(
    r"\s*(?:超清4k|超清|高清|蓝光|标清|4k|8k|fhd|uhd)\s*$", re.IGNORECASE)

# CCTV 系列：CCTV-1 / CCTV 1 / CCTV1综合 / CCTV5+体育 -> CCTV1 / CCTV5+
_CCTV_RE = re.compile(r"^cctv[\s-]*(\d+)\s*(\+)?", re.IGNORECASE)
_CCTV_KEEP = {"cctv5+": "CCTV5+", "cctv17": "CCTV17"}


def normalize_channel_name(raw: str) -> str:
    """频道名规范化：去噪音标记、统一 CCTV 编号格式"""
    name = (raw or "").strip()
    name = _PAREN_NOISE.sub("", name)
    name = _TRAILING_NOISE.sub("", name)
    name = re.sub(r"\s+", " ", name).strip()
    m = _CCTV_RE.match(name)
    if m:
        num = m.group(1)
        plus = "+" if m.group(2) else ""
        return f"CCTV{num}{plus}"
    # CGTN / 央视其他写法保持原样（CGTN、CGTN纪录 等）
    return name


# ---------------- 分类规则 ----------------

# 港澳台关键词（先于卫视判断，香港卫视/凤凰卫视归此类）
_HMT_NAME_WORDS = (
    "TVB", "翡翠台", "明珠台", "凤凰", "香港", "澳门", "澳视", "澳亚",
    "港台", "无线卫视", "台视", "中天", "东森", "民视", "华视", "三立",
    "八大", "TVBS", "大爱", "寰宇", "龙华", "八大", "纬来", "MOMO",
    "翡翠", "J2", "ViuTV", "HOY", "RTHK", "开电视",
)
_CCTV_WORDS = ("CCTV", "CGTN", "央视", "中央电视台")

PROVINCES = (
    "北京", "上海", "天津", "重庆", "河北", "山西", "辽宁", "吉林",
    "黑龙江", "江苏", "浙江", "安徽", "福建", "江西", "山东", "河南",
    "湖北", "湖南", "广东", "海南", "四川", "贵州", "云南", "陕西",
    "甘肃", "青海", "内蒙古", "广西", "西藏", "宁夏", "新疆",
)
# 这些「xx频道」不属于地方频道，直接丢弃
_DROP_GROUP_WORDS = (
    "电影", "纪录", "体育", "少儿", "儿童", "戏曲", "音乐", "新闻",
    "教育", "财经", "生活", "时装", "汽车", "高尔夫", "网球", "春晚",
    "整理", "收藏", "其他", "国际",
)


def classify_channel(name: str, group: str) -> Optional[str]:
    """归入 cctv/satellite/local/hmt，不在这四类的返回 None（丢弃）"""
    text = f"{group}|{name}"
    # 1) 港澳台：分组名含 港/澳/台 或名称命中港澳台台标
    if re.search(r"[港澳台]", group or ""):
        return "hmt"
    for w in _HMT_NAME_WORDS:
        if w and w in name:
            return "hmt"
    # 2) 央视
    for w in _CCTV_WORDS:
        if w in name.upper() or w in name:
            return "cctv"
    if _CCTV_RE.match(name):
        return "cctv"
    # 3) 卫视（含「卫视」但非港澳台）
    if "卫视" in name:
        return "satellite"
    # 4) 地方：分组是「地方频道」/「xx频道(省名)」，或名称含省市名
    g = re.sub(r"[^\u4e00-\u9fa5a-zA-Z]", "", group or "")
    if "地方频道" in g:
        return "local"
    for p in PROVINCES:
        if p in g:
            # 省名频道但主题是丢弃类的除外
            for d in _DROP_GROUP_WORDS:
                if d in g and d not in p:
                    return None
            return "local"
    for p in PROVINCES:
        if p in name:
            return "local"
    # 名称带「频道」的（如「都市频道」），分组又是地方性的
    if "频道" in name and not any(d in name for d in _DROP_GROUP_WORDS):
        return "local"
    return None


def channel_sort_key(cat: str, name: str) -> Tuple:
    """频道排序键：CCTV 按台号，其余按拼音"""
    if cat == "cctv":
        m = re.match(r"CCTV(\d+)(\+)?", name)
        if m:
            return (0, int(m.group(1)), 1 if m.group(2) else 0, "")
        return (1, 0, 0, name)
    return (2, 0, 0, name)


# ---------------- 下载与解析 ----------------

def _download(url: str, timeout: int = 30) -> str:
    """下载 M3U 文本（走系统代理，Actions 上自然直连）"""
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    ctx = ssl_ctx()
    opener = urllib.request.build_opener(
        urllib.request.HTTPSHandler(context=ctx))
    with opener.open(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "ignore")


def ssl_ctx():
    import ssl
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def parse_m3u(text: str) -> List[dict]:
    """解析 M3U -> [{name, group, logo, url}]"""
    items: List[dict] = []
    name, group, logo = "", "", ""
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("#EXTINF"):
            name = line.rsplit(",", 1)[1].strip() if "," in line else ""
            if 'tvg-name="' in line:
                name = line.split('tvg-name="')[1].split('"')[0].strip()
            group = ""
            if 'group-title="' in line:
                group = line.split('group-title="')[1].split('"')[0].strip()
            logo = ""
            if 'tvg-logo="' in line:
                logo = line.split('tvg-logo="')[1].split('"')[0].strip()
        elif line and not line.startswith("#") and name:
            items.append({"name": name, "group": group, "logo": logo, "url": line})
            name, group, logo = "", "", ""
    return items


# ---------------- TCP 测速 ----------------

def tcp_latency(url: str) -> Optional[int]:
    """对播放地址 host:port 做 TCP 直连测速，返回毫秒；连不上返回 None"""
    try:
        p = urlparse(url)
        host = p.hostname
        if not host:
            return None
        port = p.port or (443 if p.scheme == "https" else 80)
    except ValueError:
        return None
    best = None
    for _ in range(config.LIVE_SPEED_RETRY + 1):
        t0 = time.perf_counter()
        try:
            with socket.create_connection((host, port), timeout=config.LIVE_SPEED_TIMEOUT):
                ms = int((time.perf_counter() - t0) * 1000)
                best = ms if best is None else min(best, ms)
                break
        except OSError:
            continue
    return best


def speed_test_all(urls: List[str]) -> Dict[str, Optional[int]]:
    """并发测速一批 URL -> {url: latency_ms}"""
    result: Dict[str, Optional[int]] = {}
    uniq = list(dict.fromkeys(urls))
    with ThreadPoolExecutor(max_workers=config.LIVE_SPEED_WORKERS) as pool:
        futs = {pool.submit(tcp_latency, u): u for u in uniq}
        for fut in as_completed(futs):
            result[futs[fut]] = fut.result()
    return result


# ---------------- 主流程 ----------------

def collect_live() -> dict:
    """采集全部直播源 -> 分类 -> 测速 -> 入库（全量替换 live 表）"""
    from core.database import Database

    db = Database()
    with db._connect() as conn:
        conn.executescript(LIVE_TABLE)

    # 1) 下载 + 解析 + 分类
    channels: Dict[str, dict] = {}   # key: (category, name)
    stats = {"sources_ok": 0, "sources_fail": 0, "parsed": 0, "kept": 0}
    for sname, surl in config.LIVE_SOURCES.items():
        try:
            text = _download(surl)
            items = parse_m3u(text)
            stats["sources_ok"] += 1
        except Exception as e:
            print(f"[失败] 直播源 {sname}: {type(e).__name__}: {e}")
            stats["sources_fail"] += 1
            continue
        # 同一源内条目顺序即该源的测速优选顺序（guovin 已按广东延迟排序）
        order = 0
        for it in items:
            stats["parsed"] += 1
            url = it["url"]
            if not url.lower().startswith(("http://", "https://")):
                continue
            # 排除明显非流媒体（直播源里有大量短链/PHP转发，实际可播，需保留）
            if re.search(r"[.](jpe?g|png|gif|webp|html?)([?#]|$)", url, re.I):
                continue
            raw_name = it["name"]
            if "geo-blocked" in raw_name.lower():
                continue
            name = normalize_channel_name(raw_name)
            if not name:
                continue
            cat = classify_channel(name, it["group"])
            if cat is None:
                continue
            key = f"{cat}|{name}"
            ch = channels.setdefault(key, {
                "name": name, "display": raw_name, "category": cat,
                "logo": it["logo"], "urls": {},
            })
            if url not in ch["urls"]:
                ch["urls"][url] = {"source": sname, "order": order}
                if not ch["logo"] and it["logo"]:
                    ch["logo"] = it["logo"]
            order += 1
        print(f"[OK] 直播源 {sname}: 解析 {len(items)} 条，累计频道 {len(channels)}")

    stats["kept"] = len(channels)
    if not channels:
        print("[警告] 没有可用直播频道，跳过入库（保留旧数据）")
        return stats

    # 2) 全量测速
    all_urls = [u for ch in channels.values() for u in ch["urls"]]
    print(f"[测速] 开始 TCP 直连测速 {len(all_urls)} 条线路 "
          f"(并发 {config.LIVE_SPEED_WORKERS}，超时 {config.LIVE_SPEED_TIMEOUT}s)...")
    latencies = speed_test_all(all_urls)
    reachable = sum(1 for v in latencies.values() if v is not None)
    print(f"[测速] 可连通 {reachable}/{len(all_urls)}")

    # 3) 每频道择优：延迟升序（连不上的排最后仍可做备胎）-> 源内优选序
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    rows = []
    for key, ch in channels.items():
        ranked = sorted(
            ch["urls"].items(),
            key=lambda kv: (latencies.get(kv[0]) is None,
                            latencies.get(kv[0]) or 10**6,
                            kv[1]["order"]),
        )
        for url, meta in ranked[: config.LIVE_KEEP_PER_CHANNEL]:
            rows.append({
                "name": ch["name"], "display": ch["display"],
                "category": ch["category"], "logo": ch["logo"],
                "url": url, "latency": latencies.get(url) or 0,
                "source": meta["source"], "updated_at": now,
            })

    # 4) 全量替换入库
    with db._connect() as conn:
        conn.executescript(LIVE_TABLE)
        conn.execute("DELETE FROM live")
        conn.executemany(
            "INSERT INTO live (name, display, category, logo, url, latency, source, updated_at) "
            "VALUES (:name, :display, :category, :logo, :url, :latency, :source, :updated_at)",
            rows,
        )
    stats["rows"] = len(rows)
    by_cat: Dict[str, int] = {}
    for r in rows:
        by_cat[r["category"]] = by_cat.get(r["category"], 0) + 1
    stats["by_category"] = by_cat
    print(f"[OK] 直播入库 {len(rows)} 条线路 / {len(channels)} 个频道: "
          + ", ".join(f"{config.LIVE_CATEGORIES.get(k, k)} {v}"
                      for k, v in sorted(by_cat.items())))
    return stats


def list_live() -> List[dict]:
    """读取 live 表（按分类顺序 + 频道序 + 延迟）"""
    from core.database import Database

    db = Database()
    with db._connect() as conn:
        conn.executescript(LIVE_TABLE)
        rows = [dict(r) for r in conn.execute(
            "SELECT name, display, category, logo, url, latency, source "
            "FROM live")]
    order = {c: i for i, c in enumerate(config.LIVE_CATEGORY_ORDER)}
    rows.sort(key=lambda r: (order.get(r["category"], 99),
                             channel_sort_key(r["category"], r["name"]),
                             r["latency"] or 10**6))
    return rows


if __name__ == "__main__":
    collect_live()
