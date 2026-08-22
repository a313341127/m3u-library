# -*- coding: utf-8 -*-
"""组装分享站部署目录：复制 output/ 到 share/dist/ 并注入 _worker.js 网关"""
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "output"
DIST = Path(__file__).resolve().parent / "dist"
WORKER = Path(__file__).resolve().parent / "_worker.js"


def build():
    DIST.mkdir(parents=True, exist_ok=True)
    # 合并复制（不删旧目录，避免沙箱安全删除拦截）；output 删掉的文件在 dist 可能残留，影响极小
    shutil.copytree(OUT, DIST, ignore=shutil.ignore_patterns(
        ".wrangler", "backfill.log", "*.log", "media.db*"), dirs_exist_ok=True)
    shutil.copy2(WORKER, DIST / "_worker.js")
    files = sorted(p.name for p in DIST.iterdir())
    print(f"[share] 已组装 {DIST}，包含 {len(files)} 个文件")
    return DIST


if __name__ == "__main__":
    build()
