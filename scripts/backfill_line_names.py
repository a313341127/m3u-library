# -*- coding: utf-8 -*-
"""一次性回填：根据 URL 域名给已有 resources 记录补 line_name。

新采集器已会写入 line_name，旧数据靠此脚本按域名反推源名，
让网页/途播在增量更新覆盖前也能显示真实线路。
"""
import sqlite3
from urllib.parse import urlparse

ROOT = __import__("os").path.dirname(__import__("os").path.dirname(__file__))
DB_PATH = __import__("os").path.join(ROOT, "data", "media.db")

# 域名/关键字 -> 线路名（优先级：写在前面的优先匹配）
LINE_HINTS = [
    ("文采", ["hhuus.com", "hhwenjian.com", "6g9ba6.com"]),
    ("最大", ["zuidazym3u8.com", "zuidapi.com", "zuidapic.com", "zuidazy.com"]),
    ("暴风", ["bfvvs.com", "fengbao11.com", "fengbao12.com", "fengbaovod.com", "baofeng11.com", "baofeng12.com"]),
    ("猫眼", ["maoyanplay.top", "maowushi.com", "maoyanvod.com"]),
    ("极速", ["jisuzyv.com", "jisuyun", "jisuyunvod.com", "jisucdn"]),
    ("量子", ["lziapi.com", "lzcdn", "lz-cdn", "lzcdn", "lzod"]),
    ("西瓜", ["xgzyapi.com", "xgplay", "xluuss.com", "xgcdn", "yzzy.play-cdn"]),
    ("魔都", ["mdzyapi.com", "mdyun", "mdvod"]),
    ("爱奇艺", ["iqiyizyapi.com", "iqiyivod.com", "iqiyicdn"]),
    ("索尼", ["suonizy.net", "suonizy.com", "snzyapi"]),
    ("金鹰", ["jyzyapi.com", "jinyingvod.com", "jyzycnd"]),
    ("红牛", ["hongniuzy2.com", "hongniuzy.com", "hnzyapi"]),
    ("非凡", ["ffzy-online", "ffzym3u8.com", "ffzyapi.com"]),
    ("无尽", ["wjm3u8.com", "wjvod.com", "wjzyapi"]),
    ("速播", ["subozy.com", "subozy.com", "subocdn"]),
    ("火狐", ["hohuzy.com", "hohucdn.com", "hohuvod.com"]),
    ("优酷", ["youkuzy.net", "youkucdn.com", "youkuvod.com"]),
    ("百度", ["baiduzy.com", "baiducdn.com"]),
    ("豆瓣", ["doubanzy.com", "doubancdn.com"]),
    ("星球", ["xingqiuzy.com", "xingqiucdn.com", "xingqiuvod.com"]),
    ("茅台", ["maotai1.com", "maotaicdn.com", "maotaivod.com", "mtzyapi"]),
    ("360", ["360zy.com", "360zyw.com", "360vod.com"]),
    ("旺旺", ["wangwangzy.com", "wangwangvod.com", "wwzyapi"]),
    ("如意", ["ryiplay", "ryplay", "ruyi", "ryzyapi"]),
    ("率率", ["llyja.com", "llyun", "lvvot.com", "luluvod.com"]),
]


def guess_line_name(url: str) -> str:
    u = (url or "").lower()
    host = urlparse(u).netloc.lower()
    for line_name, hints in LINE_HINTS:
        for h in hints:
            if h in host or h in u:
                return line_name
    return ""


def main():
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("SELECT id, url FROM resources WHERE line_name = '' OR line_name IS NULL")
    rows = cur.fetchall()
    updated = 0
    for rid, url in rows:
        name = guess_line_name(url)
        if name:
            cur.execute("UPDATE resources SET line_name = ? WHERE id = ?", (name, rid))
            updated += 1
    con.commit()
    con.close()
    print(f"回填完成：共 {len(rows)} 条无 line_name，成功识别 {updated} 条")


if __name__ == "__main__":
    main()
