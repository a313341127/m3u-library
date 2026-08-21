# -*- coding: utf-8 -*-
"""采集器注册表

新采集站写好类后，在 collector/__init__.py 里 import 一次即完成注册。
"""
from typing import Dict, List, Type

from collector.base import BaseCollector

_REGISTRY: Dict[str, Type[BaseCollector]] = {}


def register(cls: Type[BaseCollector]) -> Type[BaseCollector]:
    """类装饰器：把采集器注册进注册表"""
    if not cls.name:
        raise ValueError(f"采集器 {cls.__name__} 缺少 name")
    if cls.name in _REGISTRY:
        raise ValueError(f"采集器名称重复: {cls.name}")
    _REGISTRY[cls.name] = cls
    return cls


def get(name: str) -> BaseCollector:
    """按注册名获取采集器实例"""
    if name not in _REGISTRY:
        raise KeyError(f"未注册的采集器: {name}，可用: {list(_REGISTRY)}")
    return _REGISTRY[name]()


def list_collectors() -> List[str]:
    """返回所有已注册采集器名（排序）"""
    return sorted(_REGISTRY)
