# -*- coding: utf-8 -*-
"""从 media.db 抽取所有视频播放域名，生成「厂商级」直连分流规则。

为什么这么做（替代原来手写 270 条子域清单）：
- 每条播放地址取其「注册域(eTLD+1)」，对该注册域发一条 DOMAIN-SUFFIX 直连规则。
  这样同一厂商成千上万个子域(vodcnd00~99.xxx.cn)只需 1 条规则，且未来新子域自动命中，
  不再因为「清单没写新子域」导致该片被 VPN 代理而黑屏。
- 对跨多个注册域的品牌(如 xgplay17/20.com、ffzy-*.com、dytt-*.com)，额外发
  DOMAIN-KEYWORD 规则进一步压缩。
- 后端 qinjin.pages.dev / qs-agcl2.pages.dev / workers.dev 强制 PROXY，置于最前。

输出:
  scripts/shadowrocket_direct_rules.list  (Shadowrocket / 订阅格式)
  scripts/direct_rules_clash.yaml         (Clash 格式)

用法:
  python scripts/gen_direct_rules.py
  python scripts/gen_direct_rules.py --db data/media.db --out-dir scripts
"""
import argparse
import json
import os
import sqlite3
import sys
from urllib.parse import urlparse

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DB = os.path.join(REPO, "data", "media.db")

# 后端域名：必须走代理(海外 Cloudflare)，放最前强制 PROXY
BACKEND_PROXY = [
    "qinjin.pages.dev",
    "qs-agcl2.pages.dev",
    "workers.dev",
]

# 多标签 TLD（注册域取最后 3 段）
MULTI_TLD = {
    "co.uk", "org.uk", "gov.uk", "ac.uk", "me.uk",
    "com.cn", "net.cn", "org.cn", "gov.cn", "edu.cn", "com.tw",
    "com.hk", "com.au", "co.jp", "com.br", "co.nz", "com.sg",
}

# 通用词，不作为品牌关键词（避免 DOMAIN-KEYWORD 过度匹配）
GENERIC = {
    "play", "vod", "cdn", "video", "svip", "vip", "live", "www", "m3u8",
    "m3u", "hls", "api", "player", "static", "img", "upload", "tv", "hd",
    "online", "kan", "see", "read", "hot", "film", "cinema", "movies",
    "cine", "tvs", "luck", "network", "music", "com", "net", "org", "cn",
    "cc", "top", "xyz", "b", "s", "c", "p", "w", "v", "k", "m", "dytt",
    "high", "channel", "stream", "tv", "radio", "news", "app", "web",
    "cloud", "api", "img", "pic", "image", "file", "dl", "down", "css",
    "js", "test", "dev", "demo", "old", "new", "go", "my", "me", "us",
}

# 已知品牌词（跨注册域，作为关键词压缩；DB 缺失时兜底）
SEED_BRANDS = [
    "xgplay", "ryplay", "ffzy", "yzzy", "lzcdn", "lz-cdn", "ukubf",
    "rrcdnbf", "ppqrrs", "rstu6", "wsyzym3u8", "uvjtih", "ajupf",
    "kunyu", "bfvvs", "wgslsw", "maowushi", "guoluche", "hhuus",
    "hhwenjian", "xluuss", "ijycnd", "gsuus", "ryiplay", "bdzybf",
    "ddbbffcdn", "bfllvip", "cdnlz", "play-cdn", "yzzyvip", "yzzyhd",
    "playback-speed", "mgtv", "iqiyi", "youku", "qq",
]


def registrable(host: str) -> str:
    host = host.lower().strip()
    if host.startswith("www."):
        host = host[4:]
    parts = host.split(".")
    if len(parts) <= 2:
        return host
    last2 = ".".join(parts[-2:])
    if last2 in MULTI_TLD and len(parts) >= 3:
        return ".".join(parts[-3:])
    return last2


def extract_hosts(db_path):
    hosts = set()
    if not os.path.exists(db_path):
        print(f"[warn] DB 不存在: {db_path}，仅使用内置 SEED 品牌词", file=sys.stderr)
        return None
    try:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        # resources.url
        try:
            for (u,) in cur.execute("SELECT url FROM resources WHERE url LIKE 'http%'"):
                if u:
                    hosts.add(urlparse(u).netloc.lower().split(":")[0])
        except sqlite3.Error as e:
            print(f"[warn] resources.url 读取失败: {e}", file=sys.stderr)
        # resources.episodes (JSON list of {label,url} 或 list of url)
        try:
            for (e,) in cur.execute(
                "SELECT episodes FROM resources "
                "WHERE episodes IS NOT NULL AND episodes != '' AND episodes != '[]'"
            ):
                try:
                    data = json.loads(e)
                except Exception:
                    continue
                items = data if isinstance(data, list) else []
                for it in items:
                    u = it.get("url") if isinstance(it, dict) else it
                    if isinstance(u, str) and u.startswith("http"):
                        hosts.add(urlparse(u).netloc.lower().split(":")[0])
        except sqlite3.Error as e:
            print(f"[warn] resources.episodes 读取失败: {e}", file=sys.stderr)
        # live.url
        try:
            for (u,) in cur.execute("SELECT url FROM live WHERE url LIKE 'http%'"):
                if u:
                    hosts.add(urlparse(u).netloc.lower().split(":")[0])
        except sqlite3.Error as e:
            print(f"[warn] live.url 读取失败: {e}", file=sys.stderr)
        conn.close()
    except sqlite3.Error as e:
        print(f"[warn] 无法打开 DB: {e}", file=sys.stderr)
        return None
    return hosts


def build_rules(hosts):
    """返回 (suffix_rules, keyword_rules)，均为已排序列表。"""
    backend_set = set(BACKEND_PROXY)
    video_hosts = {h for h in (hosts or set()) if h and h not in backend_set}

    suffix_rules = set()
    brand_stems = {}  # stem -> set(hosts)
    for h in video_hosts:
        if not h or "." not in h or h.startswith("[") or h.replace(".", "").isdigit():
            continue
        reg = registrable(h)
        if reg:
            suffix_rules.add(reg)
        # 提取品牌词干：每个 label 去掉末尾数字，保留字母部分
        for lab in h.split("."):
            stem = ""
            for ch in lab:
                if ch.isalpha():
                    stem += ch
                else:
                    break
            if len(stem) >= 4 and stem not in GENERIC:
                brand_stems.setdefault(stem, set()).add(h)

    # 跨 >=2 个注册域、>=3 个 host 的品牌词 -> 关键词规则
    keyword_rules = set()
    for stem, hs in brand_stems.items():
        if stem in GENERIC:
            continue
        regs = {registrable(x) for x in hs}
        if len(regs) >= 2 and len(hs) >= 3 and len(stem) >= 4:
            keyword_rules.add(stem)

    return sorted(suffix_rules), sorted(keyword_rules)


def render_shadowrocket(suffix, keyword):
    lines = []
    lines.append("# ===========================================================")
    lines.append("# 秦哥影视 / 途播 - 国内视频 CDN 直连规则 (Shadowrocket 订阅)")
    lines.append("# 自动生成自 data/media.db（脚本 scripts/gen_direct_rules.py）")
    lines.append("# 用途：手机开 VPN 访问 qinjin.pages.dev 后端的同时，")
    lines.append("#        让国内视频 CDN 走本地直连（不被 VPN 代理拦截）。")
    lines.append("# 用法：Shadowrocket -> 配置 -> + -> 导入/订阅此文件")
    lines.append("# 关键：出站模式必须选「配置」而非「全局代理」，")
    lines.append("#        DNS 用国内解析（223.5.5.5 / 119.29.29.29）。")
    lines.append("# ---- 后端强制代理（必须最前）----")
    for d in BACKEND_PROXY:
        lines.append(f"DOMAIN-SUFFIX,{d},PROXY")
    lines.append("# ---- 品牌级关键词直连（覆盖跨注册域厂商，如 xgplay*/ffzy*/dytt*）----")
    for k in keyword:
        lines.append(f"DOMAIN-KEYWORD,{k},DIRECT")
    lines.append("# ---- 注册域级直连（同一厂商所有子域自动命中）----")
    for s in suffix:
        lines.append(f"DOMAIN-SUFFIX,{s},DIRECT")
    return "\n".join(lines) + "\n"


def render_clash(suffix, keyword):
    lines = []
    lines.append("# 秦哥影视 / 途播 - 直连规则 (Clash 格式，自动生成)")
    lines.append("# 后端强制代理；其余国内视频 CDN 直连")
    lines.append("rules:")
    for d in BACKEND_PROXY:
        lines.append(f"  - DOMAIN-SUFFIX,{d},Proxy")
    for k in keyword:
        lines.append(f"  - DOMAIN-KEYWORD,{k},DIRECT")
    for s in suffix:
        lines.append(f"  - DOMAIN-SUFFIX,{s},DIRECT")
    lines.append("  - GEOIP,CN,DIRECT")
    lines.append("  - MATCH,Proxy")
    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--out-dir", default=os.path.join(REPO, "scripts"))
    args = ap.parse_args()

    hosts = extract_hosts(args.db)
    suffix, keyword = build_rules(hosts)

    # 兜底：若 DB 无数据，用 SEED 品牌词至少产出可用规则
    if not suffix and not keyword:
        print("[warn] 未从 DB 抽到任何域名，回退到 SEED 品牌词", file=sys.stderr)
        suffix, keyword = [], sorted(SEED_BRANDS)

    os.makedirs(args.out_dir, exist_ok=True)
    sr_path = os.path.join(args.out_dir, "shadowrocket_direct_rules.list")
    clash_path = os.path.join(args.out_dir, "direct_rules_clash.yaml")
    with open(sr_path, "w", encoding="utf-8") as f:
        f.write(render_shadowrocket(suffix, keyword))
    with open(clash_path, "w", encoding="utf-8") as f:
        f.write(render_clash(suffix, keyword))
    print(f"[ok] 生成 {len(suffix)} 条注册域规则 + {len(keyword)} 条品牌关键词规则")
    print(f"     -> {sr_path}")
    print(f"     -> {clash_path}")


if __name__ == "__main__":
    main()
