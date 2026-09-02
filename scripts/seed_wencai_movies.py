# -*- coding: utf-8 -*-
"""测试：用文采搜索接口定向抓几部电影入库，验证播放链路。
仅用于小批量测试，全量入库仍走 collect 命令。
"""
import sys
import sqlite3
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config
import urllib.parse
import collector.cc0cd as cc
from collector.base import ResourceItem
from collector.manager import Database

API = "https://api.zeqaht.com/api.php/provide/vod/"
MOVIES = [
    "流浪地球", "满江红", "长津湖", "阿凡达", "复仇者联盟",
    "速度与激情", "孤注一掷", "消失的她", "封神", "八角笼中",
    "你好李焕英", "流浪地球2",
]


def main():
    db = Database()
    inserted = 0
    for name in MOVIES:
        try:
            lst = cc.http_get_json(API + "?ac=list&wd=" + urllib.parse.quote(name) + "&pg=1")
        except Exception as e:
            print(f"[{name}] 搜索失败: {e}")
            continue
        vod_list = lst.get("list") or []
        if not vod_list:
            print(f"[{name}] 无结果")
            continue
        # 取第一个可归类为 movie 的
        target = None
        for v in vod_list:
            cls = cc.classify_category(v.get("type_name") or "", "")
            if cls and cls[0] == "movie":
                target = v
                break
        if not target:
            print(f"[{name}] 搜索结果无电影类: {[v.get('type_name') for v in vod_list[:3]]}")
            continue
        vid = target.get("vod_id")
        try:
            detail = cc.http_get_json(API + "?ac=detail&ids=" + str(vid))
        except Exception as e:
            print(f"[{name}] 详情失败: {e}")
            continue
        dl = (detail.get("list") or [])
        if not dl:
            print(f"[{name}] 详情空")
            continue
        item = cc.CC0CDCollector._to_item(dl[0], "movie", name, source_type_name="",
                                          forced_category="movie", forced_media_type="")
        if not item:
            print(f"[{name}] _to_item 返回空")
            continue
        d = item.to_dict()
        d["source"] = "cc0cd"
        rid = db.add_resource(**d)
        if rid:
            inserted += 1
            print(f"[OK] {name} -> #{rid} {d['name']} {d['region']} {d['year']} {d['quality']}")
            print(f"     url: {d['url'][:90]}")
        else:
            print(f"[跳过] {name} 已存在")
    print(f"\n共入库 {inserted} 部电影")


if __name__ == "__main__":
    main()
