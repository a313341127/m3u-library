#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
qinjin DNS 污染看门狗
=====================
每日定时运行：检查 qinjin 各自定义域是否被 DNS 污染 / Cloudflare 绑定是否掉线，
发现异常自动处置（重新添加自定义域）并告警。

判定逻辑
--------
1. 污染 = 从「干净解析器」(223.5.5.5 / 8.8.8.8) 查到的 IP 不属于 Cloudflare 官方
   边缘段（即落到了沉洞/劫持 IP，如 183.192.65.101）。
2. 绑定健康 = Cloudflare Pages 项目里该自定义域 status == active、SSL 已签发。
3. 自动处置 = 若自定义域被解绑，调用 API 重新添加；若广泛污染（连干净解析器都
   返回沉洞），输出告警并给出建议（换备用域 / 引导收件人用干净 DNS）。

仅「干净解析器」的结果用于判定是否污染；本机默认解析器（往往是用户自家 ISP，
可能已被污染）只作信息参考，不触发误报。
"""
import os
import re
import sys
import json
import ipaddress
import subprocess
import urllib.request
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------
PROJECT = "qinjin"
# 需要监控的自定义域（Cloudflare Pages 自定义域）
DOMAINS = ["qinjin.ccwu.cc", "qinjin.cc.cd"]
# 已知反诈沉洞 IP（出现即视为被劫持）
SINKHOLE_IPS = {"183.192.65.101", "183.192.65.102", "183.192.65.103"}
# 干净解析器（用于判定「是否广泛污染」）。注意：不要用本机默认解析器判定。
CLEAN_RESOLVERS = ["223.5.5.5", "8.8.8.8"]
# Cloudflare 权威 NS（用于取「期望 IP」做对照）
AUTHORITATIVE_NS = "gigi.ns.cloudflare.com"
# Cloudflare 官方 IP 段兜底（网络不可达时也能判定）
CF_RANGES_FALLBACK = [
    "104.16.0.0/12", "172.64.0.0/13", "131.0.72.0/22", "141.101.64.0/18",
    "162.158.0.0/15", "173.245.48.0/20", "188.114.96.0/20", "190.93.240.0/20",
    "197.234.240.0/22", "198.41.128.0/17",
    "2400:cb00::/32", "2606:4700::/32", "2803:f800::/32", "2405:b500::/32",
    "2405:8100::/32", "2a06:98c0::/29", "2c0f:f248::/32",
]

# ---------------------------------------------------------------------------
# 路径：脚本在 scripts/ 下，project root 为父目录
# ---------------------------------------------------------------------------
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
LOG_FILE = SCRIPT_DIR / "dns_check.log"


def load_env():
    """从项目根目录 .env 读取 Cloudflare Token / Account（也接受环境变量覆盖）。"""
    token = os.environ.get("CLOUDFLARE_API_TOKEN")
    acct = os.environ.get("CLOUDFLARE_ACCOUNT_ID")
    env_path = PROJECT_ROOT / ".env"
    if (not token or not acct) and env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("CLOUDFLARE_API_TOKEN=") and not token:
                token = line.split("=", 1)[1].strip().strip('"').strip("'")
            elif line.startswith("CLOUDFLARE_ACCOUNT_ID=") and not acct:
                acct = line.split("=", 1)[1].strip().strip('"').strip("'")
    return token, acct


# ---------------------------------------------------------------------------
# Cloudflare API
# ---------------------------------------------------------------------------
def cf_request(method, path, token, body=None):
    url = "https://api.cloudflare.com/client/v4" + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", "Bearer " + token)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode())
        except Exception:
            return e.code, {"success": False, "errors": [{"message": e.reason}]}
    except Exception as e:
        return "ERR", {"success": False, "errors": [{"message": str(e)}]}


# ---------------------------------------------------------------------------
# DNS 记录自愈（尽力而为，仅当 Token 拥有 Zone:DNS 权限时有效）
# ---------------------------------------------------------------------------
def self_heal_dns_record(domain, token):
    """若自定义域在 Cloudflare zone 内的 DNS 记录未开启代理(橙色云)，
    则自动重新开启。记录漂移（解析到非 Cloudflare IP）时调用。
    无 Zone:DNS 权限时记录提示并跳过，不报错。"""
    s, j = cf_request("GET", "/zones?name=" + domain, token)
    if s != 200 or not j.get("success"):
        print(f"[WARN] 自愈DNS记录：查询 zone 失败（可能无 Zone:DNS 权限）：HTTP {s}")
        return
    zones = j.get("result") or []
    if not zones:
        print(f"[WARN] 自愈DNS记录：{domain} 非本账户托管 zone，跳过")
        return
    zid = zones[0]["id"]
    s, j = cf_request("GET", f"/zones/{zid}/dns_records?name={domain}", token)
    if s != 200:
        print(f"[WARN] 自愈DNS记录：读取记录失败（可能无 Zone:DNS 权限）：HTTP {s}")
        return
    for rec in (j.get("result") or []):
        if rec.get("proxied") is False:
            body = dict(rec)
            body["proxied"] = True
            s2, j2 = cf_request("PATCH", f"/zones/{zid}/dns_records/{rec['id']}", token, body)
            if s2 == 200 and j2.get("success"):
                print(f"[FIXED] 已将 {domain} 的 DNS 记录({rec.get('type')})重新开启 Cloudflare 代理(橙色云)")
            else:
                print(f"[WARN] 自愈DNS记录：开启代理失败：HTTP {s2} {j2.get('errors')}")


# ---------------------------------------------------------------------------
# DNS 解析（指定解析器）
# ---------------------------------------------------------------------------
def resolve(host, resolver=None):
    """用 nslookup 查询 host；resolver 为 None 时用本机默认解析器。返回 IP 集合。"""
    cmd = ["nslookup"]
    if resolver:
        cmd += ["-type=A", host, resolver]
    else:
        cmd += ["-type=A", host]
    try:
        res = subprocess.run(cmd, capture_output=True, timeout=15)
        out = res.stdout.decode("utf-8", "ignore")
    except Exception:
        return set()
    ips = set()
    for m in re.findall(r"(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})", out):
        ips.add(m)
    for m in re.findall(r"((?:[A-Fa-f0-9]{1,4}:){2,7}[A-Fa-f0-9]{1,4})", out):
        ips.add(m)
    if resolver and resolver in ips:
        ips.discard(resolver)
    return ips


# ---------------------------------------------------------------------------
# Cloudflare IP 段
# ---------------------------------------------------------------------------
def load_cf_ranges():
    ranges = set()
    for url in ["https://www.cloudflare.com/ips-v4", "https://www.cloudflare.com/ips-v6"]:
        try:
            data = urllib.request.urlopen(url, timeout=10).read().decode()
            for line in data.splitlines():
                line = line.strip()
                if line:
                    try:
                        ranges.add(ipaddress.ip_network(line))
                    except ValueError:
                        pass
        except Exception:
            pass
    if not ranges:
        for c in CF_RANGES_FALLBACK:
            ranges.add(ipaddress.ip_network(c))
    return ranges


def is_cloudflare(ip, ranges):
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    for n in ranges:
        if addr in n:
            return True
    return False


# ---------------------------------------------------------------------------
# 主逻辑
# ---------------------------------------------------------------------------
def main():
    token, acct = load_env()
    problems = []
    info = []

    if not token or not acct:
        print("[FAIL] 未找到 CLOUDFLARE_API_TOKEN / CLOUDFLARE_ACCOUNT_ID（检查 .env）")
        return 1

    cf_ranges = load_cf_ranges()
    print(f"[info] Cloudflare IP 段已加载：{len(cf_ranges)} 条")

    # ---- 1. Cloudflare 侧绑定健康 ----
    s, j = cf_request("GET", f"/accounts/{acct}/pages/projects/{PROJECT}/domains", token)
    if s != 200 or not j.get("success"):
        problems.append(f"[FAIL] 无法读取 Pages 自定义域列表：HTTP {s} {j.get('errors')}")
    bound = {d["name"]: d for d in (j.get("result") or [])}

    for target in DOMAINS:
        if target not in bound:
            # 自动处置：重新添加自定义域
            s2, j2 = cf_request(
                "POST", f"/accounts/{acct}/pages/projects/{PROJECT}/domains",
                token, {"name": target},
            )
            if s2 == 200 and j2.get("success"):
                problems.append(f"[FIXED] {target} 已被解绑，已自动重新添加（状态待生效）")
            else:
                problems.append(f"[FAIL] {target} 已被解绑且重新添加失败：HTTP {s2} {j2.get('errors')}")
            continue
        st = bound[target].get("status")
        if st != "active":
            problems.append(f"[WARN] {target} 状态={st}（期望 active），可能 SSL/验证未完成")
        else:
            info.append(f"{target}: Cloudflare 侧 status=active ✓")

    # ---- 2. DNS 污染检测（仅用干净解析器判定）----
    expected = resolve(DOMAINS[0], AUTHORITATIVE_NS) if DOMAINS else set()
    info.append(f"权威 NS({AUTHORITATIVE_NS}) 期望 IP 样例: {sorted(expected)[:3]}")

    for target in DOMAINS:
        for r in CLEAN_RESOLVERS:
            got = resolve(target, r)
            if not got:
                info.append(f"{target} <- {r}: 查询无结果（可能网络不通，跳过）")
                continue
            polluted = [ip for ip in got if not is_cloudflare(ip, cf_ranges)]
            if polluted:
                sink = [ip for ip in polluted if ip in SINKHOLE_IPS]
                tag = "沉洞" if sink else "非Cloudflare"
                problems.append(
                    f"[ALERT:BROAD] {target} 经干净解析器 {r} 解析到 {tag} IP {polluted} "
                    f"（期望 Cloudflare 边缘）。请改用备用域名或引导收件人设置干净 DNS(223.5.5.5/DoH)"
                )
                if not sink:
                    # 非沉洞（疑似记录漂移/未代理）→ 尝试云端自愈
                    self_heal_dns_record(target, token)
            else:
                info.append(f"{target} <- {r}: 全部为 Cloudflare IP ✓")

    # ---- 3. 本机默认解析器（仅信息，不告警）----
    for target in DOMAINS:
        got = resolve(target)  # 本机默认
        polluted = [ip for ip in got if not is_cloudflare(ip, cf_ranges)]
        if polluted:
            info.append(
                f"[参考] {target} 经本机默认解析器得到 {polluted}（疑似本地 ISP 污染，"
                f"建议改路由器 DNS 为 223.5.5.5；不影响他人，仅本机视角）"
            )

    # ---- 汇总 ----
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = [f"===== DNS 巡检 {ts} ====="]
    lines.append(f"项目: {PROJECT} | 监控域: {', '.join(DOMAINS)}")
    for i in info:
        lines.append("  · " + i)
    alert_count = sum(1 for p in problems if p.startswith(("[ALERT", "[FAIL", "[WARN")))
    if problems:
        lines.append("----- 需要处理的问题 -----")
        for p in problems:
            lines.append("  " + p)
    else:
        lines.append("  无异常，全部健康 ✓")

    report = "\n".join(lines)
    print(report)

    # 写日志（追加）
    try:
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(report + "\n\n")
    except Exception:
        pass

    # 退出码：有 FAIL/ALERT 返回非 0，便于自动化识别
    return 1 if any(p.startswith(("[ALERT", "[FAIL")) for p in problems) else 0


if __name__ == "__main__":
    sys.exit(main())
