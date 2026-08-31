# -*- coding: utf-8 -*-
"""采集器抽象接口

采集模块与主程序完全解耦，设计目标：后续无侵入接入多个采集站。

- 每个采集站 = 一个继承 BaseCollector 的类
- 只需实现 fetch()，返回 List[ResourceItem]
- 数据入库由 collector.manager.run_collector() 统一编排，采集器自身不碰数据库
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, asdict, field
from typing import List, Optional, Dict


@dataclass
class ResourceItem:
    """采集器与主程序之间的统一资源结构（字段与数据库 resources 表对应）"""
    name: str                       # 名称（必填）
    category: str                   # 分类 movie/tv/anime（必填）
    media_type: str = ""            # 类型: 动作 / 科幻 / 古装 ...
    region: str = ""                # 地区: 中国大陆 / 美国 / 韩剧 ...
    year: Optional[int] = None      # 年份
    cover: str = ""                 # 封面 URL
    description: str = ""           # 简介
    url: str = ""                   # 播放地址（默认第一集/第一线路）
    quality: str = ""               # 清晰度: 4K / 1080p / 720p
    raw_type_name: str = ""         # 采集站原始分类名（用于排查分类错误）
    source: str = ""                # 采集器注册名（由 manager 入库时填充）
    line_name: str = ""             # 播放线路名（如 文采/暴风/最大）
    hits: int = 0                   # 人气（源站播放量，用于排序）
    score: float = 0.0              # 评分（豆瓣等，0-10，0 表示无评分）
    episodes: List[Dict[str, str]] = field(default_factory=list)  # 多集选集 [{label, url}]

    def to_dict(self) -> dict:
        return asdict(self)


class BaseCollector(ABC):
    """所有采集站的基类。

    子类必须定义：
        name         = "zy"          采集器注册名（唯一，CLI 触发用）
        display_name = "资源采集站"    展示用名称
    子类必须实现：
        fetch()      -> List[ResourceItem]
    """

    name: str = ""
    display_name: str = ""

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        if not getattr(cls, "name", None):
            raise TypeError(f"采集器 {cls.__name__} 必须定义 name 属性")

    @abstractmethod
    def fetch(self, **kwargs) -> List[ResourceItem]:
        """从采集站抓取资源。

        子类在此调用采集站 API / 抓取页面并解析。
        常用 kwargs:
            category   按分类筛选 (movie/tv/anime)
            keyword    搜索关键词
            page       页码
        """
        raise NotImplementedError
