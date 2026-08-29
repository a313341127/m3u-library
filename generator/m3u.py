# -*- coding: utf-8 -*-
"""M3U / TXT 生成器

M3U 条目格式（途播可导入）:
    #EXTM3U
    #EXTINF:-1 tvg-logo="封面URL" group-title="电影-类型-科幻",流浪地球2 | 中国大陆 | 2023 | 4K
    http://example.com/play.m3u8

多维分组（类似 App 的筛选）:
    每个资源会按「类型 / 地区 / 年份」三个维度各生成一条分组入口：
      - 电影-类型-科幻
      - 电影-地区-中国大陆
      - 电影-年份-2023
    途播导入后，group-title 相同的条目会聚合在一个分类下，实现截图里的
    「电影 / 剧集 / 动漫 / 综艺 + 类型 / 地区 / 年份」交叉筛选效果。

生成前自动处理（prepare_items）:
    1. 标题清洗: 去掉 1080p/720p/HD/全集/高清 等标记（清晰度由 quality 字段单独保存）
    2. 多线路保留: 同资源（名称+年份+地区）的不同播放地址全部保留，
       相同 URL 才去重；同一资源的多条线路中「国内可直连源」排最前，
       播放器里同名多条 = 不同线路（某条超时可换另一条）

TXT 文本源格式（config.TXT_LINE_FORMAT 可配）:
    流浪地球2,http://example.com/play.m3u8
"""
import re
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import config
from core.database import Database
from generator import health as _health  # noqa: E402


# ---------------------------------------------------------------- 标题清洗
_TITLE_CLEAN_RE = re.compile(
    r"(?i)(?<![a-z0-9])\b(1080p|720p|2160p|4k|hd|bd)\b(?![a-z0-9])"
    r"|高清|超清|全集|蓝光|连载中|更新至|大结局"
)


def clean_title(name: str) -> str:
    """去掉名称里的清晰度/状态标记（1080p/720p/HD/全集/高清...），
    这些信息由 quality 字段单独保存，避免标题出现『xxx 1080p高清全集』噪音"""
    n = _TITLE_CLEAN_RE.sub(" ", name or "")
    # 清理残留括号/分隔符与多余空格
    n = re.sub(r"[\[\]【】()（）・·…]", " ", n)
    n = re.sub(r"\s+", " ", n).strip()
    return n


def display_title(item: dict, clean_name: str) -> str:
    """条目标题 = 名称 | 地区 | 年份 | 清晰度（空字段自动省略）"""
    parts = [clean_name]
    for k in ("region", "year", "quality"):
        v = item.get(k)
        if v:
            parts.append(str(v))
    return config.ENTRY_TITLE_JOIN.join(parts)


def _era_label(year: Optional[int]) -> str:
    """把具体年份映射到年代区间"""
    if not year:
        return ""
    for start, end, label in config.YEAR_BUCKETS:
        if start <= year <= end:
            return label
    return ""


def _region_bucket(region: str) -> str:
    """把原始地区字符串合并到主要地区桶。

    规则：
    1. 优先匹配主要地区（中国大陆/香港/台湾/美国/日本/韩国/英国/印度/泰国）
    2. 然后匹配欧美桶
    3. 都没命中统一归入「其他」，避免细碎国家各自成组
    """
    r = (region or "").strip().lower()
    if not r or r in ("其它", "其他"):
        return config.GROUP_FALLBACK
    # 1. 主要地区（按配置顺序优先匹配）
    for bucket, keywords in config.REGION_BUCKETS.items():
        if bucket == "欧美":
            continue
        for kw in keywords:
            if kw.lower() in r:
                return bucket
    # 2. 欧美桶
    for kw in config.REGION_BUCKETS["欧美"]:
        if kw.lower() in r:
            return "欧美"
    # 3. 兜底：统一归入「其他」
    return config.GROUP_FALLBACK


def _group_value(item: dict, field: str) -> str:
    """取分组维度值，year 映射为年代区间，region 合并为地区桶，
    无意义类型（如电影分类下 type=电影）归入「其他」，空值返回空字符串"""
    if field == "year":
        return _era_label(item.get("year"))
    if field == "region":
        return _region_bucket(item.get("region") or "")
    if field == "media_type":
        mt = (item.get(field) or "").strip()
        # 把「电影/电视剧/动漫/综艺」这种等于大分类本身的无意义类型归并
        if mt in ("电影", "电视剧", "剧集", "动漫", "综艺", ""):
            return config.GROUP_FALLBACK
        return mt
    return (item.get(field) or "").strip()


def group_titles(item: dict) -> List[str]:
    """返回该资源应归属的所有 group-title 列表。

    按 config.GROUP_TITLE_RULES 配置的维度展开，例如：
      电影 -> ['电影-类型-科幻', '电影-地区-中国大陆', '电影-年份-2023']
    空值维度会被跳过。
    """
    rule = config.GROUP_TITLE_RULES[item["category"]]
    label = rule["label"]
    groups = []
    for dim in rule["dimensions"]:
        value = _group_value(item, dim)
        if not value:
            continue
        dim_label = config.GROUP_DIMENSION_LABELS.get(dim, dim)
        groups.append(f"{label}-{dim_label}-{value}")
    return groups


# ---------------------------------------------------------------- 去重
# 国内可直连线路的域名特征（这些源国内直连延迟低，多线路时排最前）
_DOMESTIC_HINTS = ("bfvvs.com", ".cn/", "aliyun", "cdnd", "huya", "qncdn",
                   "upyun", "wsdns", "gtimg", "126.net", "163.com",
                   "mgtv.com", "qq.com", "youku.com", "iqiyi.com",
                   "sohu.com", "bilibili.com", "b23.tv", "1905.com",
                   # 实测国内直连快: 量子 lzcdn / 暴风 fengbao,baofeng
                   "lzcdn", "fengbao", "baofeng")


def _is_domestic(url: str) -> bool:
    """粗判播放地址是否偏向国内可直连（按域名特征匹配，命中即视为国内源优先）"""
    u = (url or "").lower()
    return any(k in u for k in _DOMESTIC_HINTS)


def prepare_items(category: str) -> Tuple[List[dict], Dict[str, int]]:
    """加载 + 标题清洗 + 多线路保留，返回 (条目列表, 统计)

    多线路策略（方案 A）:
    - 同一资源（名称+年份+地区）来自不同采集站的多个播放地址【全部保留】
    - 只有完全相同的 URL 才视为重复剔除
    - 同一资源的多条线路中，国内可直连源排最前（不开 VPN 也能看的优先）

    统计: {"raw": 原始条数, "dropped_no_url": 无播放地址被剔除,
           "duplicates": 同URL重复剔除条数, "lines": 最终线路条数}
    """
    db = Database()
    raw = [dict(r) for r in db.list_resources(category=category)]
    items = [i for i in raw if i.get("url")]
    stats = {"raw": len(items), "dropped_no_url": len(raw) - len(items),
             "duplicates": 0, "lines": 0, "dead_dropped": 0}

    # 先按资源聚合（名称+年份+地区），组内再做 URL 去重 + 国内优先
    groups: Dict[tuple, List[dict]] = defaultdict(list)
    for it in items:
        clean = clean_title(it["name"]) or it["name"]
        it["_clean_name"] = clean
        key = (clean, it.get("year") or "", it.get("region") or "")
        groups[key].append(it)

    result: List[dict] = []
    for gitems in groups.values():
        seen_url: set = set()
        uniq: List[dict] = []
        for it in gitems:
            u = (it.get("url") or "").strip()
            if not u or u in seen_url:
                stats["duplicates"] += 1
                continue
            seen_url.add(u)
            uniq.append(it)
        # 线路体检：剔除域名级已确认失效的线路（源站 CDN 跑路，任何播放器都放不出来）
        if _health.enabled():
            kept = [it for it in uniq if _health.playable(it.get("url") or "")]
            stats["dead_dropped"] += len(uniq) - len(kept)
            uniq = kept
        # 组内：可用度（直连优先）→ 国内可直连线路，其次保持原顺序
        uniq.sort(key=lambda it: (_health.rank(it.get("url")),
                                  0 if _is_domestic(it.get("url")) else 1))
        result.extend(uniq)
        stats["lines"] += len(uniq)
    return result, stats


# ---------------------------------------------------------------- 条目构建
def build_entry(item: dict, group: str) -> str:
    """单条 M3U 条目（2 行：EXTINF 头 + 播放地址）"""
    title = display_title(item, item.get("_clean_name") or clean_title(item["name"]))
    attrs = f'group-title="{group}"'
    if item.get("cover"):
        attrs = f'tvg-logo="{item["cover"]}" {attrs}'
    return f'#EXTINF:-1 {attrs},{title}\n{item["url"]}'


# ---------------------------------------------------------------- 生成
def _merge_small_groups(groups: Dict[str, List[dict]]) -> Dict[str, List[dict]]:
    """把条目数 < MIN_GROUP_SIZE 的小分类并入「分类-维度-其他」"""
    final: Dict[str, List[dict]] = defaultdict(list)
    for g, gitems in groups.items():
        prefix = g.rsplit("-", 1)[0]          # 例如 "电影-类型"
        if len(gitems) < config.MIN_GROUP_SIZE:
            final[f"{prefix}-{config.GROUP_FALLBACK}"].extend(gitems)
        else:
            final[g].extend(gitems)
    return dict(final)


def generate_m3u(category: str, output_dir: Path = None) -> Path:
    """生成单个分类的 M3U 文件（清洗/去重/小分类合并/多维分组后输出），返回文件路径"""
    items, stats = prepare_items(category)
    out = (output_dir or config.OUTPUT_DIR) / config.M3U_OUTPUT[category]
    out.parent.mkdir(parents=True, exist_ok=True)

    # 1. 先统计每个 group 的原始条目数
    raw_groups: Dict[str, List[dict]] = defaultdict(list)
    for it in items:
        for g in group_titles(it):
            raw_groups[g].append(it)

    # 2. 小分类合并（注意：同一个资源在不同维度可能分别被合并或保留，这是允许的）
    final_groups = _merge_small_groups(raw_groups)

    # 3. 组排序：按维度顺序（类型 -> 地区 -> 年份），「其他」兜底组放最后
    dim_order = {d: i for i, d in enumerate(config.GROUP_DIMENSION_LABELS.values())}

    def sort_key(kv: Tuple[str, List[dict]]) -> tuple:
        g, _ = kv
        parts = g.split("-")
        label = parts[0] if parts else ""
        dim = parts[1] if len(parts) > 1 else ""
        value = parts[2] if len(parts) > 2 else ""
        is_other = value == config.GROUP_FALLBACK
        return (label, dim_order.get(dim, 99), is_other, g)

    lines = ["#EXTM3U"]
    for g, gitems in sorted(final_groups.items(), key=sort_key):
        for it in gitems:
            lines.append(build_entry(it, g))
    out.write_text("\n".join(lines) + "\n", encoding=config.M3U_ENCODING)

    problems = verify_m3u(out)
    if problems:
        for p in problems:
            print(f"[自检失败] {out.name}: {p}")
    else:
        print(f"[自检通过] {out.name}: {len(items)} 条资源 / {len(final_groups)} 个分组"
              f"（原始 {stats['raw']} 条，去重 {stats['duplicates']} 条）")
    return out


def generate_txt(category: str, output_dir: Path = None) -> Path:
    """生成单个分类的 TXT 文本源（同样清洗去重），返回文件路径"""
    items, stats = prepare_items(category)
    out = (output_dir or config.OUTPUT_DIR) / config.TXT_OUTPUT[category]
    out.parent.mkdir(parents=True, exist_ok=True)
    lines = [config.TXT_LINE_FORMAT.format(
        name=it["_clean_name"] or it["name"], url=it["url"]) for it in items]
    out.write_text("\n".join(lines) + "\n", encoding=config.M3U_ENCODING)
    return out


def _flat_best_items(items: List[dict]) -> List[dict]:
    """从已排序的多线路条目中，每部影片只保留一条最优线路。

    聚合键为（名称, 年份），忽略不同来源对地区/类型的不一致标注，
    从而实现搜索列表里「一部影片只出现一次」。
    由于 prepare_items 已经把国内可直连源排最前，这里保留的第一条就是最优线路。
    """
    seen: dict = {}
    for it in items:
        key = (it["_clean_name"], it.get("year") or "")
        if key in seen:
            continue
        seen[key] = it
    # 按名称+年份排序，输出稳定
    return sorted(seen.values(), key=lambda it: (it["_clean_name"], it.get("year") or 0))


def generate_best_m3u(category: str, output_dir: Path = None) -> Path:
    """生成单条最优版 M3U：每部影片只保留一条线路（国内源优先），且只出现一次"""
    items, stats = prepare_items(category)
    best_items_list = _flat_best_items(items)
    out = (output_dir or config.OUTPUT_DIR) / config.BEST_M3U_OUTPUT[category]
    out.parent.mkdir(parents=True, exist_ok=True)

    # 平面列表：统一归到一个 group-title，避免多维分组导致同一影片反复出现
    group = config.CATEGORIES[category]["label"]

    lines = ["#EXTM3U"]
    for it in best_items_list:
        lines.append(build_entry(it, group))
    out.write_text("\n".join(lines) + "\n", encoding=config.M3U_ENCODING)

    problems = verify_m3u(out)
    if problems:
        for p in problems:
            print(f"[自检失败] {out.name}: {p}")
    else:
        print(f"[自检通过] {out.name}: {len(best_items_list)} 条最优资源 "
              f"（完整版 {len(items)} 条）")
    return out


def generate_best_txt(category: str, output_dir: Path = None) -> Path:
    """生成单条最优版 TXT 文本源，返回文件路径"""
    items, stats = prepare_items(category)
    best_items_list = _flat_best_items(items)
    out = (output_dir or config.OUTPUT_DIR) / config.BEST_TXT_OUTPUT[category]
    out.parent.mkdir(parents=True, exist_ok=True)
    lines = [config.TXT_LINE_FORMAT.format(
        name=it["_clean_name"] or it["name"], url=it["url"]) for it in best_items_list]
    out.write_text("\n".join(lines) + "\n", encoding=config.M3U_ENCODING)
    return out


def generate_all(output_dir: Path = None, include_txt: bool = False,
                 include_best: bool = False) -> Dict[str, Path]:
    """生成全部分类的源文件，返回 {文件名: 路径}"""
    results: Dict[str, Path] = {}
    for cat in config.M3U_OUTPUT:
        results[config.M3U_OUTPUT[cat]] = generate_m3u(cat, output_dir)
        if include_txt:
            results[config.TXT_OUTPUT[cat]] = generate_txt(cat, output_dir)
        if include_best:
            results[config.BEST_M3U_OUTPUT[cat]] = generate_best_m3u(cat, output_dir)
            if include_txt:
                results[config.BEST_TXT_OUTPUT[cat]] = generate_best_txt(cat, output_dir)
    return results


# ---------------------------------------------------------------- 自检
def verify_m3u(path: Path) -> list:
    """自检 M3U 文件是否为标准纯文本格式（非 Markdown）。

    检查项:
      1. 首行必须是 #EXTM3U（允许 UTF-8 BOM）
      2. 内容中不得出现 Markdown 链接语法 [url](url)
      3. 不得出现 Markdown 表格竖线分隔（行首/行尾的 |；行中标题分隔符不受影响）
    返回问题列表，空列表表示通过。
    """
    problems = []
    try:
        raw = path.read_bytes()
        text = raw.decode("utf-8-sig", errors="replace")
    except Exception as e:
        return [f"读取失败: {e}"]
    lines = [ln.rstrip("\r\n") for ln in text.split("\n") if ln.strip()]
    if not lines or lines[0] != "#EXTM3U":
        problems.append(f"首行不是 #EXTM3U（实际: {lines[0] if lines else '空文件'}）")
    for idx, line in enumerate(lines[1:], start=2):
        if "](" in line and ("http" in line or "://" in line):
            problems.append(f"第 {idx} 行含 Markdown 链接: {line[:70]}")
        if line.startswith("|") or line.endswith("|"):
            problems.append(f"第 {idx} 行疑似 Markdown 表格: {line[:70]}")
    return problems
