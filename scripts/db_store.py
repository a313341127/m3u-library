#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 media.db 存到 GitHub Release 资产，彻底绕开 git 单文件 100MB 硬限制。

背景：media.db 是采集库，会涨过 100MB，直接 git add 提交会被 GitHub 的
GH001 大文件钩子拒绝（pre-receive hook declined），导致所有包含它的 push 失败、
采集成果永远推不上去、并触发「限额」邮件。

改为：media.db 不再进 git，而是作为 Release 资产（tag=db-store）托管。
GitHub Release 资产单文件上限 2GB、无带宽配额，契合「无限制」诉求。

★ 压缩存储（2026-09-05 起）：资产以 gzip 形式存为 `media.db.gz`。
  SQLite 文本字段（分集 JSON 等）压缩比高，688MB → 约 150-200MB：
  1) 延缓 2GB 资产上限（约 8-10 天倒计时 → 数月）；
  2) 缩短每轮 download/upload 传输耗时，间接提升 30 分钟采集轮的有效产能。
  旧版未压缩 `media.db` 资产在首次 download 时自动回退迁移，无需手工处理。

★ 原子上传：先传临时名 `media.db.gz.tmp`，成功后再「删旧 → 改名 → 清旧版」，
  避免「先删旧的、新的又上传失败」导致整库丢失（2026-09-02 空库覆盖事故的前车之鉴）。

用法（CI 内，需 GITHUB_TOKEN 环境变量，且仓库 permissions 含 contents:write）：
  python scripts/db_store.py download   # 工作流开头：拉回上次采集的 media.db
  python scripts/db_store.py upload     # 工作流结尾：把更新后的 media.db 存回

退出码约定：
  download: 0=成功(或首次运行无资产,从空库开始)  2=网络/API 异常(应让工作流失败, 避免误清空)
  upload:   0=成功/跳过(库过小)  1=API/上传异常
"""
import json
import os
import sys
import time
import gzip
import urllib.request
import urllib.error

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(REPO, "data", "media.db")
OWNER = "a313341127"
REPO_NAME = "m3u-library"
TAG = "db-store"
ASSET_NAME = "media.db.gz"        # 压缩资产名（2026-09-05 起）
RAW_ASSET_NAME = "media.db"       # 旧版未压缩资产名（兼容迁移）
TMP_NAME = ASSET_NAME + ".tmp"    # 原子上传用的临时名
GZIP_LEVEL = 6                    # 压缩级别：6 在体积/速度间较平衡

# 安全阈值：库小于此值不上传，避免「下载失败→空库→误覆盖好库」导致数据清空。
# 正常库 90MB+，空库仅几十 KB，5MB 阈值足够区分。
MIN_UPLOAD_BYTES = 5 * 1024 * 1024

API = "https://api.github.com"


def _request(method, path, token, data=None, accept="application/vnd.github+json",
             extra_headers=None, timeout=60, raw=False):
    url = API + path
    if data is not None and not isinstance(data, (bytes, bytearray)):
        data = json.dumps(data).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", accept)
    req.add_header("User-Agent", "m3u-db-store")
    if data is not None and not raw:
        req.add_header("Content-Type", "application/json")
    if extra_headers:
        for k, v in extra_headers.items():
            req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read().decode("utf-8", "replace")
            return r.status, (json.loads(body) if body and not raw else body)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")[:600]
    except Exception as e:  # 网络异常等
        return 0, str(e)


def get_release(token):
    """返回 (status, release_dict_or_error)。404 表示尚未建 Release。"""
    return _request("GET", f"/repos/{OWNER}/{REPO_NAME}/releases/tags/{TAG}", token)


def ensure_release(token):
    status, rel = get_release(token)
    if status == 200:
        return rel
    if status == 404:
        status, rel = _request(
            "POST", f"/repos/{OWNER}/{REPO_NAME}/releases",
            token,
            {"tag_name": TAG, "name": "Data Store (media.db)",
             "body": "media.db 自动托管（gzip 压缩），由 CI 采集工作流读写，勿手动编辑。",
             "draft": False, "prerelease": False},
        )
        if status in (200, 201):
            return rel
    raise RuntimeError(f"无法获取/创建 Release: {status} {rel}")


def _download_asset_bytes(asset, token):
    """下载某个资产原始字节（Accept: octet-stream 触发到签名 URL 的 302，urllib 自动跟随）。"""
    url = asset["url"]
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/octet-stream")
    req.add_header("User-Agent", "m3u-db-store")
    try:
        with urllib.request.urlopen(req, timeout=600) as r:
            return r.read()
    except Exception as e:
        print(f"DB_STORE_ERR: 下载资产 {asset.get('name')} 失败: {e}")
        return None


def cmd_download(token):
    status, rel = get_release(token)
    if status == 404:
        print("DB_STORE: 无 Release 资产（首次运行），从空库开始采集")
        return 0
    if status != 200:
        print(f"DB_STORE_ERR: 查询 Release 失败 {status} {rel}")
        return 2  # 网络/API 异常 → 让工作流失败，避免误清空

    assets = rel.get("assets", [])
    # 优先压缩资产；缺失时回退旧版未压缩 media.db（一次性迁移）
    asset = next((a for a in assets if a.get("name") == ASSET_NAME), None)
    is_gz = True
    if not asset:
        legacy = next((a for a in assets if a.get("name") == RAW_ASSET_NAME), None)
        if legacy:
            print("DB_STORE: 未找到压缩资产，回退下载旧版未压缩 media.db（下次上传将迁移为 gz）")
            asset = legacy
            is_gz = False

    if not asset:
        print("DB_STORE: Release 存在但无 media.db 资产，从空库开始")
        return 0

    data = _download_asset_bytes(asset, token)
    if data is None:
        return 2
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    if is_gz:
        try:
            raw = gzip.decompress(data)
        except Exception as e:
            print(f"DB_STORE_ERR: 解压 {ASSET_NAME} 失败: {e}")
            return 2
        with open(DB_PATH, "wb") as f:
            f.write(raw)
        print(f"DB_STORE: 已恢复 media.db（压缩包 {len(data)/1024/1024:.1f} MB → 解压 {len(raw)/1024/1024:.1f} MB）")
    else:
        with open(DB_PATH, "wb") as f:
            f.write(data)
        print(f"DB_STORE: 已恢复 media.db（{len(data)/1024/1024:.1f} MB，未压缩/迁移）")
    return 0


def cmd_upload(token):
    if not os.path.exists(DB_PATH):
        print("DB_STORE: 本地无 media.db，跳过上传")
        return 0
    size = os.path.getsize(DB_PATH)
    if size < MIN_UPLOAD_BYTES:
        print(f"DB_STORE: media.db 仅 {size/1024/1024:.2f} MB（< 阈值），疑似空库，跳过上传以免误清空好库")
        return 0

    rel = ensure_release(token)

    # 压缩后再上传：SQLite 文本字段（分集 JSON）压缩比高，688MB→约 150-200MB，
    # 既延缓 2GB Release 资产上限，又缩短每轮传输耗时。
    with open(DB_PATH, "rb") as f:
        raw = f.read()
    blob = gzip.compress(raw, GZIP_LEVEL)
    print(f"DB_STORE: 压缩 media.db {size/1024/1024:.1f} MB → {len(blob)/1024/1024:.1f} MB (gzip-{GZIP_LEVEL})")

    # 原子上传：先传临时名，成功后再删旧+改名，避免「删旧的、新的又失败」丢数据
    upload_base = rel["upload_url"].split("{")[0]
    req = urllib.request.Request(f"{upload_base}?name={TMP_NAME}", data=blob, method="POST")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Content-Type", "application/octet-stream")
    req.add_header("User-Agent", "m3u-db-store")
    try:
        with urllib.request.urlopen(req, timeout=600) as r:
            new_asset = json.loads(r.read().decode())
            new_id = new_asset["id"]
            print(f"DB_STORE: 已上传临时资产 {TMP_NAME}（{len(blob)/1024/1024:.1f} MB，HTTP {r.status}）")
    except urllib.error.HTTPError as e:
        print(f"DB_STORE_ERR: 上传失败 {e.code} {e.read().decode()[:300]}")
        return 1
    except Exception as e:
        print(f"DB_STORE_ERR: 上传异常 {e}")
        return 1

    # 上传成功后再切换：删旧（压缩版+旧版未压缩）→ 改名临时资产为正式名
    for a in rel.get("assets", []):
        if a.get("name") in (ASSET_NAME, RAW_ASSET_NAME):
            _request("DELETE", f"/repos/{OWNER}/{REPO_NAME}/releases/assets/{a['id']}", token)
    st, body = _request("PATCH", f"/repos/{OWNER}/{REPO_NAME}/releases/assets/{new_id}",
                        token, data={"name": ASSET_NAME})
    if st in (200, 201):
        print(f"DB_STORE: 已发布 {ASSET_NAME}（{len(blob)/1024/1024:.1f} MB）")
        return 0
    else:
        print(f"DB_STORE_ERR: 重命名资产失败 {st} {body}")
        return 1


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in ("download", "upload"):
        print("用法: python scripts/db_store.py [download|upload]")
        sys.exit(3)
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not token:
        print("DB_STORE_ERR: 缺少 GITHUB_TOKEN 环境变量")
        sys.exit(3)
    cmd = sys.argv[1]
    t0 = time.time()
    if cmd == "download":
        rc = cmd_download(token)
    else:
        rc = cmd_upload(token)
    print(f"DB_STORE: {cmd} 完成，耗时 {time.time()-t0:.1f}s，退出码 {rc}")
    sys.exit(rc)


if __name__ == "__main__":
    main()
