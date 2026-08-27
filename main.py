# -*- coding: utf-8 -*-
"""M3U 影视资源库 —— 命令行入口（后台程序，无界面）

命令:
  add       手动添加资源
  list      查看 / 筛选资源
  remove    按 id 删除资源
  generate  生成 M3U / TXT 源文件
  collect   运行采集器（第一阶段为占位，验证采集模块接口）

示例:
  python main.py add -c movie -n "流浪地球2" -t 科幻 -r 中国大陆 -y 2023 \
      -u "http://example.com/stream.m3u8" -q "4K" -d "简介文字"
  python main.py list -c movie
  python main.py generate
  python main.py generate --txt
  python main.py collect --list
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config


# ---------------------------------------------------------------- add
def cmd_add(args):
    from core.database import Database

    cat_cfg = config.CATEGORIES.get(args.category)
    if cat_cfg is None:
        print(f"[错误] 未知分类: {args.category}，可选: {list(config.CATEGORIES)}")
        sys.exit(1)
    if args.type and args.type not in cat_cfg["types"]:
        print(f"[警告] 类型不在预设列表 {cat_cfg['types']} 中，仍将保存")
    if args.region and args.region not in cat_cfg["regions"]:
        print(f"[警告] 地区不在预设列表 {cat_cfg['regions']} 中，仍将保存")
    if not args.url:
        print("[错误] 播放地址 --url 不能为空")
        sys.exit(1)

    db = Database()
    rid = db.add_resource(
        name=args.name, category=args.category, media_type=args.type or "",
        region=args.region or "", year=args.year, cover=args.cover or "",
        description=args.description or "", url=args.url, quality=args.quality or "",
    )
    if rid:
        tags = "/".join(x for x in [cat_cfg["label"], args.type or "-",
                                    args.region or "-", str(args.year or "-")] if x)
        print(f"[OK] 已添加 #{rid}  {args.name}  ({tags})")
    else:
        print(f"[跳过] 已存在相同资源（同分类+同名+同地址）: {args.name}")


# ---------------------------------------------------------------- list
def cmd_list(args):
    from core.database import Database

    db = Database()
    rows = db.list_resources(
        category=args.category, media_type=args.type or None,
        region=args.region or None, year=args.year, keyword=args.keyword or None,
    )
    if not rows:
        print("（空）没有符合条件的资源")
        return
    print(f"共 {len(rows)} 条资源：")
    for r in rows:
        cat_label = config.CATEGORIES.get(r["category"], {}).get("label", r["category"])
        tags = "/".join(x for x in [cat_label, r["media_type"], r["region"],
                                    str(r["year"] or "")] if x)
        print(f"  #{r['id']:<4} [{tags}] {r['name']}  {r['quality']}  更新:{r['updated_at']}")
        print(f"        {r['url']}")


# ---------------------------------------------------------------- remove
def cmd_remove(args):
    from core.database import Database

    db = Database()
    if db.remove_resource(args.id):
        print(f"[OK] 已删除 #{args.id}")
    else:
        print(f"[错误] 未找到 #{args.id}")


# ---------------------------------------------------------------- generate
def cmd_generate(args):
    from generator import m3u as m3u_gen
    from generator import live as live_gen
    from generator.web import generate_index

    if args.category:
        p = m3u_gen.generate_m3u(args.category)
        print(f"[OK] 已生成 {p}")
        if args.txt:
            p2 = m3u_gen.generate_txt(args.category)
            print(f"[OK] 已生成 {p2}")
        if args.best:
            p3 = m3u_gen.generate_best_m3u(args.category)
            print(f"[OK] 已生成 {p3}")
            if args.txt:
                p4 = m3u_gen.generate_best_txt(args.category)
                print(f"[OK] 已生成 {p4}")
    else:
        results = m3u_gen.generate_all(include_txt=args.txt, include_best=args.best)
        for path in results.values():
            print(f"[OK] 已生成 {path}")
        # 直播源（live 表有数据时顺带生成）
        from collector.live import list_live
        if list_live():
            live_gen.generate_live_m3u()
            if args.txt:
                live_gen.generate_live_txt()
    if not args.no_web:
        generate_index()


# ---------------------------------------------------------------- collect
def cmd_collect(args):
    from collector import manager, registry
    from generator import m3u as m3u_gen

    if args.list:
        names = registry.list_collectors()
        if not names:
            print("（空）还没有注册采集器，接入方法见 README")
            return
        print("已注册采集器：")
        for n in names:
            print(f"  - {n}: {registry.get(n).display_name}")
        return

    if not args.name:
        print("[错误] 请用 -n 指定采集器名（或 --list 查看可用）")
        sys.exit(1)

    kwargs = {}
    if args.keyword:
        kwargs["keyword"] = args.keyword
    if args.pages:
        kwargs["pages"] = args.pages
    if args.sources:
        kwargs["sources"] = args.sources
    if args.category:
        kwargs["category"] = args.category

    result = manager.run_collector(args.name, **kwargs)
    print(f"[OK] {result['collector']}: 抓取 {result['fetched']} 条")
    print(f"  电影数量: {result['movies']} | 电视剧数量: {result['tvs']} "
          f"| 动漫数量: {result['animes']} | 综艺数量: {result['varieties']}")
    print(f"  新增: {result['inserted']} | 重复(已存在): {result['duplicates']}"
          f" | 过滤(黑名单/无效): {result['filtered']} | 请求失败: {result['failed']}")

    # 采集后自动重新生成 M3U（默认开启，--no-generate 关闭）
    if not args.no_generate:
        gen_results = m3u_gen.generate_all(include_txt=args.txt, include_best=args.best)
        for path in gen_results.values():
            print(f"[OK] 已重新生成 {path}")


# ---------------------------------------------------------------- collect-live
def cmd_collect_live(args):
    from collector.live import collect_live
    from generator import m3u as m3u_gen
    from generator import live as live_gen
    from generator.web import generate_index

    stats = collect_live()
    print(f"[OK] 直播采集完成: 源可用 {stats.get('sources_ok')}/{stats.get('sources_ok', 0) + stats.get('sources_fail', 0)}"
          f" | 解析 {stats.get('parsed')} 条 | 频道 {stats.get('kept')} 个"
          f" | 入库线路 {stats.get('rows', 0)} 条")
    if args.no_generate:
        return
    for path in m3u_gen.generate_all(include_txt=args.txt, include_best=args.best).values():
        print(f"[OK] 已重新生成 {path}")
    live_gen.generate_live_m3u()
    if args.txt:
        live_gen.generate_live_txt()
    generate_index()


# ---------------------------------------------------------------- 参数定义
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="main.py", description="M3U 影视资源库")
    sub = parser.add_subparsers(dest="command", required=True)

    p_add = sub.add_parser("add", help="手动添加资源")
    p_add.add_argument("-c", "--category", required=True, choices=list(config.CATEGORIES), help="分类: movie/tv/anime")
    p_add.add_argument("-n", "--name", required=True, help="资源名称")
    p_add.add_argument("-t", "--type", dest="type", default="", help="类型，如 科幻 / 古装 / 热血")
    p_add.add_argument("-r", "--region", default="", help="地区，如 中国大陆 / 美剧 / 日本")
    p_add.add_argument("-y", "--year", type=int, help="年份")
    p_add.add_argument("-u", "--url", required=True, help="播放地址")
    p_add.add_argument("-q", "--quality", default="", help="清晰度，如 4K / 1080p")
    p_add.add_argument("--cover", default="", help="封面 URL")
    p_add.add_argument("-d", "--description", default="", help="简介")
    p_add.set_defaults(func=cmd_add)

    p_list = sub.add_parser("list", help="查看 / 筛选资源")
    p_list.add_argument("-c", "--category", choices=list(config.CATEGORIES), help="按分类筛选")
    p_list.add_argument("-t", "--type", dest="type", default="", help="按类型筛选")
    p_list.add_argument("-r", "--region", default="", help="按地区筛选")
    p_list.add_argument("-y", "--year", type=int, help="按年份筛选")
    p_list.add_argument("-k", "--keyword", default="", help="按名称/简介关键字搜索")
    p_list.set_defaults(func=cmd_list)

    p_rm = sub.add_parser("remove", help="按 id 删除资源（id 用 list 查看）")
    p_rm.add_argument("id", type=int, help="资源 id")
    p_rm.set_defaults(func=cmd_remove)

    p_gen = sub.add_parser("generate", help="生成 M3U / TXT 源文件")
    p_gen.add_argument("-c", "--category", choices=list(config.CATEGORIES), help="只生成指定分类")
    p_gen.add_argument("--txt", action="store_true", help="同时生成 TXT 文本源")
    p_gen.add_argument("--best", action="store_true", help="同时生成单条最优版（同影片只保留一条线路）")
    p_gen.add_argument("--no-web", action="store_true", help="不生成卡片式 Web 首页")
    p_gen.set_defaults(func=cmd_generate)

    p_live = sub.add_parser("collect-live", help="采集聚合直播源（下载/分类/测速择优），完成后自动重新生成全部源")
    p_live.add_argument("--txt", action="store_true", help="同时生成 TXT 文本源")
    p_live.add_argument("--best", action="store_true", help="同时生成单条最优版 M3U/TXT")
    p_live.add_argument("--no-generate", action="store_true", help="采集后不自动重新生成")
    p_live.set_defaults(func=cmd_collect_live)

    p_col = sub.add_parser("collect", help="运行采集器，采集后自动重新生成 M3U")
    p_col.add_argument("-n", "--name", default="", help="采集器注册名")
    p_col.add_argument("-k", "--keyword", default="", help="可选搜索关键字")
    p_col.add_argument("-c", "--category", choices=list(config.CATEGORIES), help="只采集指定分类: movie/tv/anime/variety")
    p_col.add_argument("--pages", type=int, help="每个采集源采集页数（每页约 20 条，默认 3；0 = 全量采集到最后一页）")
    p_col.add_argument("--sources", default="", help="只采集指定源（逗号分隔，如 360,旺旺；默认白名单全部）")
    p_col.add_argument("--txt", action="store_true", help="采集后同时生成 TXT 文本源")
    p_col.add_argument("--best", action="store_true", help="采集后同时生成单条最优版 M3U/TXT")
    p_col.add_argument("--no-generate", action="store_true", help="采集后不自动重新生成 M3U")
    p_col.add_argument("--list", action="store_true", help="列出已注册采集器")
    p_col.set_defaults(func=cmd_collect)

    return parser


def main():
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
