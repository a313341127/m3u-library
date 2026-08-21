# -*- coding: utf-8 -*-
"""示例采集器（模板 / 占位）

接入新采集站的步骤：
1. 复制本文件为 collector/xxx.py
2. 实现 fetch()：调用采集站 API / 抓页面，解析出 List[ResourceItem]
3. 在 collector/__init__.py 底部 import xxx 即完成注册
4. 运行: python main.py collect -n xxx

本示例不产出真实数据，仅演示接口用法。
"""
from collector.base import BaseCollector, ResourceItem
from collector.registry import register


@register
class ExampleCollector(BaseCollector):
    name = "example"
    display_name = "示例采集器（占位，不产出数据）"

    def fetch(self, **kwargs) -> list[ResourceItem]:
        # TODO: 在这里实现真实采集逻辑，例如:
        #   category = kwargs.get("category", "movie")
        #   keyword  = kwargs.get("keyword")
        #   resp = requests.get(API_URL, params={...})
        #   ... 解析后组装 ResourceItem 列表返回
        return []
