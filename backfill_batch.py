# -*- coding: utf-8 -*-
"""分批回填 hits/score：按指定源名单跑 cc0cd 采集，直接写库"""
import sys
import config

names = sys.argv[1].split(",")
direct = {k: v for k, v in config.COLLECTORS["cc0cd"]["direct_sources"].items() if k in names}
center = [s for s in config.COLLECTORS["cc0cd"]["sources"] if s in names]

config.COLLECTORS["cc0cd"]["direct_sources"] = direct
config.COLLECTORS["cc0cd"]["sources"] = center

from collector import manager
r = manager.run_collector("cc0cd", pages=1)
print("RESULT:", r)
