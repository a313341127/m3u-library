#!/usr/bin/env python3
"""给 qs-agcl2 Pages 项目设置环境变量（邀请码持久化所需）。

通过 Cloudflare API：
  - GET  /accounts/{acct}/pages/projects/qs-agcl2  读取现有 deployment_configs
  - 合并 env_vars: GITHUB_TOKEN / GITHUB_REPO / MIGRATE_FROM_KV
  - PATCH /accounts/{acct}/pages/projects/qs-agcl2  写回

环境变量来源（CI secrets）：
  CLOUDFLARE_API_TOKEN, CLOUDFLARE_ACCOUNT_ID  -> Cloudflare 凭证
  SHARE_GITHUB_PAT                             -> 即 worker 的 GITHUB_TOKEN（写回 codes.json 用）
  SHARE_GITHUB_REPO                            -> 固定 a313341127/m3u-library
"""
import os
import sys
import json
import urllib.request
import urllib.error

TOKEN = os.environ["CLOUDFLARE_API_TOKEN"]
ACCT = os.environ["CLOUDFLARE_ACCOUNT_ID"]
GH_PAT = os.environ["SHARE_GITHUB_PAT"]
GH_REPO = os.environ.get("SHARE_GITHUB_REPO", "a313341127/m3u-library")
PROJ = "qs-agcl2"

BASE = "https://api.cloudflare.com/client/v4"


def cf(method, path, body=None):
    url = BASE + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "Content-Type": "application/json",
        },
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read().decode()
            return r.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode() or "{}")
        except Exception:
            return e.code, {}


def main():
    # 1) 读取当前项目配置
    st, d = cf("GET", f"/accounts/{ACCT}/pages/projects/{PROJ}")
    if not d.get("success"):
        print("GET project failed:", st, json.dumps(d)[:500])
        sys.exit(1)
    result = d["result"]
    cfg = dict(result.get("deployment_configs", {}) or {})
    prod = dict(cfg.get("production", {}) or {})
    env_vars = dict(prod.get("env_vars", {}) or {})

    # 2) 合并我们要设的变量
    env_vars["GITHUB_TOKEN"] = {"value": GH_PAT}
    env_vars["GITHUB_REPO"] = {"value": GH_REPO}
    env_vars["MIGRATE_FROM_KV"] = {"value": "0"}
    prod["env_vars"] = env_vars
    cfg["production"] = prod

    # 3) 写回
    st2, d2 = cf("PATCH", f"/accounts/{ACCT}/pages/projects/{PROJ}",
                  {"deployment_configs": cfg})
    if not d2.get("success"):
        print("PATCH failed:", st2, json.dumps(d2)[:600])
        sys.exit(1)

    ev = (d2.get("result") or {}).get("deployment_configs", {}).get("production", {}).get("env_vars", {})
    print("PATCH success. qs-agcl2 env vars:")
    for k, v in ev.items():
        val = v.get("value") if isinstance(v, dict) else v
        print(f"  {k} = {'***' if k == 'GITHUB_TOKEN' else val}")


if __name__ == "__main__":
    main()
