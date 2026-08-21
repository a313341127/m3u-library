# -*- coding: utf-8 -*-
"""采集模块：独立、可插拔

接入新采集站：
1. collector/ 下新建文件，实现 BaseCollector 子类并加 @register
2. 在下方 import 该模块（import 即注册）
"""
from collector.base import BaseCollector, ResourceItem
from collector import registry
from collector import manager

# 已实现的采集器
from collector import example
from collector import cc0cd

__all__ = ["BaseCollector", "ResourceItem", "registry", "manager"]
