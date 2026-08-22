# -*- coding: utf-8 -*-
"""直播 M3U / TXT 生成器

从 live 表读取已测速择优的频道，按 央视/卫视/地方/港澳台 分组输出。
同频道多线路（延迟最低在前）以同名多条输出，播放器内可切换。
"""
from typing import Dict, List

import config
from collector.live import channel_sort_key


def _load_channels() -> Dict[str, List[dict]]:
    """按分类 -> 频道（聚合多线路）读取"""
    from collector.live import list_live

    rows = list_live()
    order = {c: i for i, c in enumerate(config.LIVE_CATEGORY_ORDER)}
    # 频道聚合（list_live 已按延迟排序，同频道线路自然延迟升序）
    cats: Dict[str, Dict[str, List[dict]]] = {}
    for r in rows:
        cat = r["category"]
        ch = cats.setdefault(cat, {}).setdefault(r["name"], [])
        ch.append(r)
    # 分类内频道排序
    result: Dict[str, List[dict]] = {}
    for cat in sorted(cats, key=lambda c: order.get(c, 99)):
        chans = []
        for name, lines in cats[cat].items():
            chans.append({"name": name, "lines": lines})
        chans.sort(key=lambda c: channel_sort_key(cat, c["name"]))
        result[cat] = chans
    return result


def generate_live_m3u(output_dir=None) -> "object":
    """生成 live.m3u"""
    from pathlib import Path

    out = (output_dir or config.OUTPUT_DIR) / config.LIVE_M3U_OUTPUT
    out.parent.mkdir(parents=True, exist_ok=True)
    cats = _load_channels()
    total = 0
    lines = ["#EXTM3U"]
    for cat, chans in cats.items():
        label = config.LIVE_CATEGORIES.get(cat, cat)
        for ch in chans:
            for ln in ch["lines"]:
                logo = f' tvg-logo="{ln["logo"]}"' if ln["logo"] else ""
                lines.append(
                    f'#EXTINF:-1{logo} group-title="{label}",{ch["name"]}')
                lines.append(ln["url"])
                total += 1
    out.write_text("\n".join(lines) + "\n", encoding=config.M3U_ENCODING)
    print(f"[OK] 已生成 {out}（{total} 条线路）")
    return out


def generate_live_txt(output_dir=None):
    """生成 live.txt（途播等纯文本源格式）"""
    from pathlib import Path

    out = (output_dir or config.OUTPUT_DIR) / config.LIVE_TXT_OUTPUT
    out.parent.mkdir(parents=True, exist_ok=True)
    cats = _load_channels()
    total = 0
    lines = []
    for cat, chans in cats.items():
        for ch in chans:
            for ln in ch["lines"]:
                lines.append(config.TXT_LINE_FORMAT.format(
                    name=ch["name"], url=ln["url"]))
                total += 1
    out.write_text("\n".join(lines) + "\n", encoding=config.M3U_ENCODING)
    print(f"[OK] 已生成 {out}（{total} 条线路）")
    return out
