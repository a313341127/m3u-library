# -*- coding: utf-8 -*-
"""采集编排：抓取 → 入库（去重 + 字段更新）

采集器只负责 fetch() 返回数据，入库统一走这里，方便统一日志与统计。

入库策略（对应「自动去重 + 自动更新数据库」）:
- 同分类 + 同名 + 同播放地址 视为同一资源
- 不存在        -> 新增
- 已存在        -> 用本次抓取的数据刷新字段（类型/地区/年份/封面/简介/清晰度）
                  并自动更新 updated_at（如剧集更新、封面变化都会同步）
- 多集资源（tv/anime/variety）按 (分类, 名称, 线路名) 合并选集，避免每集一行
"""
import json
import re
from typing import Dict, List, Tuple

from collector.registry import get
from core.database import Database

# 采集场景下允许刷新的字段（不含 name/category/url 这些"身份键"）
_UPDATE_FIELDS = ("media_type", "region", "year", "cover", "description",
                  "quality", "line_name", "raw_type_name", "hits", "score", "episodes")


def _merge_items(items: list) -> list:
    """预合并：同一资源同一线路的多个选集合并成一条。

    电影（episodes 为空）保持原样；剧集/动漫/综艺把 (category,name,line_name)
    相同的条目合并，episodes 取并集并按 label 中的数字排序，url 固定为第一集。
    """
    from collector.base import ResourceItem
    key_groups: Dict[Tuple[str, str, str], List[ResourceItem]] = {}
    single: List[ResourceItem] = []
    for it in items:
        eps = getattr(it, "episodes", None) or []
        if len(eps) <= 1:
            single.append(it)
            continue
        key = (it.category, it.name, it.line_name or '')
        key_groups.setdefault(key, []).append(it)
    merged = single[:]
    for group in key_groups.values():
        base = group[0]
        seen = set()
        all_eps = []
        for it in group:
            for ep in (it.episodes or []):
                u = ep.get("url")
                if not u or u in seen:
                    continue
                seen.add(u)
                all_eps.append(ep)
        if not all_eps:
            continue
        # 按 label 中的数字排序（第1集/第2集/...）
        def _ep_sort_key(ep):
            m = re.search(r'\d+', ep.get("label", "") or "")
            return int(m.group()) if m else 0
        all_eps.sort(key=_ep_sort_key)
        base.url = all_eps[0]["url"]
        base.episodes = all_eps
        merged.append(base)
    return merged


def run_collector(name: str, **kwargs) -> Dict[str, object]:
    """执行一次采集并入库。

    返回: {"collector": 展示名, "fetched": 抓取条数,
           "inserted": 新增条数, "updated": 更新条数,
           "movies": 电影条数, "tvs": 电视剧条数, "animes": 动漫条数,
           "duplicates": 重复条数(已存在被刷新),
           "filtered": 过滤条数(黑名单分类/无播放地址被丢弃),
           "failed": 失败条数(请求失败页数)}
    """
    collector = get(name)
    items = collector.fetch(**kwargs)
    items = _merge_items(items)
    cstats = getattr(collector, "stats", {}) or {}

    db = Database()
    inserted = 0
    updated = 0
    for item in items:
        d = item.to_dict()
        # ResourceItem 已含 source 字段，但采集器不填（默认空）；
        # 此处用采集器注册名覆盖，避免与下方 **d 展开冲突（079afc4 给
        # ResourceItem 加了 source 字段后，若仍写 source=collector.name 会
        # 触发 "got multiple values for keyword argument 'source'" 崩溃）。
        d["source"] = collector.name
        # episodes 是 list，入库前序列化为 JSON 字符串
        if d.get("episodes"):
            d["episodes"] = json.dumps(d["episodes"], ensure_ascii=False)
        else:
            d["episodes"] = ''
        rid = db.add_resource(**d)
        if rid:
            inserted += 1
            continue
        # 已存在 → 刷新可变字段
        exist_id = db.find_resource_id(d["name"], d["category"], d["url"])
        if exist_id:
            changed = {k: d[k] for k in _UPDATE_FIELDS}
            if db.update_resource(exist_id, **changed):
                updated += 1

    return {
        "collector": collector.display_name,
        "fetched": len(items),
        "inserted": inserted,
        "updated": updated,
        "movies": sum(1 for i in items if i.category == "movie"),
        "tvs": sum(1 for i in items if i.category == "tv"),
        "animes": sum(1 for i in items if i.category == "anime"),
        "varieties": sum(1 for i in items if i.category == "variety"),
        "duplicates": updated,
        "filtered": int(cstats.get("dropped", 0)),
        "failed": int(cstats.get("failed_pages", 0)),
    }
