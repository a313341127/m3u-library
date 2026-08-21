# -*- coding: utf-8 -*-
"""采集编排：抓取 → 入库（去重 + 字段更新）

采集器只负责 fetch() 返回数据，入库统一走这里，方便统一日志与统计。

入库策略（对应「自动去重 + 自动更新数据库」）:
- 同分类 + 同名 + 同播放地址 视为同一资源
- 不存在        -> 新增
- 已存在        -> 用本次抓取的数据刷新字段（类型/地区/年份/封面/简介/清晰度）
                  并自动更新 updated_at（如剧集更新、封面变化都会同步）
"""
from typing import Dict

from collector.registry import get
from core.database import Database

# 采集场景下允许刷新的字段（不含 name/category/url 这些"身份键"）
_UPDATE_FIELDS = ("media_type", "region", "year", "cover", "description",
                  "quality", "raw_type_name")


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
    cstats = getattr(collector, "stats", {}) or {}

    db = Database()
    inserted = 0
    updated = 0
    for item in items:
        d = item.to_dict()
        rid = db.add_resource(source=collector.name, **d)
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
