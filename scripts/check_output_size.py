# -*- coding: utf-8 -*-
"""部署前体积闸门：扫描 output/ 里是否有文件突破 Cloudflare Pages 单文件 25 MiB 上限。

为什么需要这个脚本
------------------
Cloudflare Pages 有一条**硬限制：单个文件最大 25 MiB**。只要 output/ 里有任意
一个文件超限，`wrangler pages deploy` 就会**整个部署失败**，而 Pages 会继续提供
**上一次成功部署**的内容。站点不报错、不变红，只是内容「静止在旧版本」——
如果上次成功部署恰好数据不全，表现就是「全站 + 途播内容为空」，极易被误判成
「数据库丢了 / 采集挂了」，排查方向完全跑偏。

历史上已经踩过三次同类问题（都是「按条数分片」而没有「按体积分片」）：
  1. index.html 内联全量数据超限   -> 改为 output/web/data_*.js 体积分片
  2. movie.m3u 50.3 MiB            -> generator/m3u.py 加体积保护、自适应降维
  3. cat_movie_0.json ~27 MiB      -> generate_movies_json.py 按体积分片
  4. web/desc_movie.js 25.4 MiB    -> generator/web.py 按体积分片
每次都是「部署失败 → 站点静止在旧版本」，且只能等 wrangler 报错才发现。

本脚本在**部署之前**跑，把超限文件直接点名并以非 0 退出，让 CI 在生成阶段就红，
错误信息一眼可见，不必再去翻 wrangler 日志倒推。
"""
import os
import sys

# Cloudflare Pages 硬上限 25 MiB；留 1 MiB 安全边界，避免边界抖动
HARD_LIMIT = 25 * 1024 * 1024
WARN_LIMIT = 24 * 1024 * 1024


def human(n: int) -> str:
    return "%.1f MiB" % (n / 1024 / 1024)


def main() -> int:
    out_dir = sys.argv[1] if len(sys.argv) > 1 else "output"
    if not os.path.isdir(out_dir):
        print("[体积闸门] 目录不存在: %s" % out_dir)
        return 0

    over, warn, biggest = [], [], []
    for root, _dirs, files in os.walk(out_dir):
        for fn in files:
            fp = os.path.join(root, fn)
            try:
                size = os.path.getsize(fp)
            except OSError:
                continue
            rel = os.path.relpath(fp, out_dir).replace("\\", "/")
            biggest.append((size, rel))
            if size > HARD_LIMIT:
                over.append((size, rel))
            elif size > WARN_LIMIT:
                warn.append((size, rel))

    biggest.sort(reverse=True)
    print("[体积闸门] %s 下最大的 10 个文件：" % out_dir)
    for size, rel in biggest[:10]:
        print("    %10s  %s" % (human(size), rel))

    for size, rel in warn:
        print("::warning::[体积闸门] %s 已达 %s，逼近 Pages 25 MiB 上限，请尽快加体积分片"
              % (rel, human(size)))

    if over:
        print("")
        print("::error::[体积闸门] 以下文件超过 Cloudflare Pages 单文件 25 MiB 上限，"
              "若继续部署会导致【整个部署失败 + 站点回退到上次成功部署（表现为内容为空）】：")
        for size, rel in over:
            print("::error::    %s  ->  %s" % (rel, human(size)))
        print("")
        print("修复方向：该文件所在的生成器必须改为「按序列化体积分片」，而不是按条数分片。")
        print("  - output/*.m3u / *.txt   -> generator/m3u.py（PAGES_MAX_FILE_BYTES 体积保护）")
        print("  - output/api/*.json      -> generate_movies_json.py（MAX_SHARD_BYTES / PAGE_SIZE）")
        print("  - output/web/data_*.js   -> generator/web.py（SHARD_MAX_BYTES）")
        print("  - output/web/desc_*.js   -> generator/web.py（DESC_MAX_BYTES）")
        return 1

    print("[体积闸门] 通过：无文件超过 25 MiB。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
