# -*- coding: utf-8 -*-
"""全局配置

分类体系、输出文件命名、编码等集中在这里。
以后要调整分类/类型/地区，只改这个文件，不用动代码。
"""
from pathlib import Path

# ---------- 路径 ----------
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"          # SQLite 数据库目录
OUTPUT_DIR = BASE_DIR / "output"      # 生成的 M3U / TXT 目录

# ---------- 数据库 ----------
DB_FILE = "media.db"                  # 数据库文件名
DB_PATH = DATA_DIR / DB_FILE

# ---------- 分类体系 ----------
# category 取值: movie / tv / anime / variety
# label   -> M3U group-title 里的分类标签
# types   -> 该分类下的类型列表（add 时做提示性校验）
# regions -> 该分类下的地区列表（add 时做提示性校验）
CATEGORIES = {
    "movie": {
        "label": "电影",
        "types": ["动作", "喜剧", "爱情", "科幻", "恐怖", "悬疑", "犯罪", "战争", "动画", "纪录片"],
        "regions": ["中国大陆", "香港", "台湾", "美国", "日本", "韩国", "英国", "印度", "泰国"],
    },
    "tv": {
        "label": "剧集",
        "types": ["古装", "都市", "爱情", "悬疑", "刑侦", "战争", "谍战", "科幻", "青春"],
        "regions": ["国产", "美剧", "韩剧", "日剧", "港剧", "泰剧"],
    },
    "anime": {
        "label": "动漫",
        "types": ["冒险", "奇幻", "科幻", "武侠", "悬疑", "热血", "恋爱", "治愈", "搞笑", "运动"],
        "regions": ["中国大陆", "日本", "欧美"],
    },
    "variety": {
        "label": "综艺",
        "types": ["真人秀", "音乐", "脱口秀", "歌舞", "爱情", "搞笑", "访谈", "美食", "旅游", "竞技", "选秀"],
        "regions": ["中国大陆", "香港", "台湾", "美国", "日本", "韩国"],
    },
}

# ---------- 输出文件 ----------
M3U_OUTPUT = {"movie": "movie.m3u", "tv": "tv.m3u", "anime": "anime.m3u", "variety": "variety.m3u"}
TXT_OUTPUT = {"movie": "movie.txt", "tv": "tv.txt", "anime": "anime.txt", "variety": "variety.txt"}

# 单条最优版输出：同部影片只保留排序后的第一条线路（国内源优先），
# 适合只想在播放器里看到一个条目的场景。
BEST_M3U_OUTPUT = {"movie": "movie.best.m3u", "tv": "tv.best.m3u",
                   "anime": "anime.best.m3u", "variety": "variety.best.m3u"}
BEST_TXT_OUTPUT = {"movie": "movie.best.txt", "tv": "tv.best.txt",
                   "anime": "anime.best.txt", "variety": "variety.best.txt"}

# 输出编码：Windows 上部分播放器对无 BOM 的 UTF-8 识别不稳，
# 默认带 BOM（utf-8-sig）；如播放器兼容性好可改为 "utf-8"
M3U_ENCODING = "utf-8-sig"

# TXT 文本源单行格式（途播等播放器的纯文本源）
# 可用占位符: {name} 名称 / {url} 播放地址
TXT_LINE_FORMAT = "{name},{url}"

# ---------- M3U 生成规则 ----------
# 为每个分类生成「类型 / 地区 / 年份」三个筛选维度，类似 App 的多维分类。
# group-title 格式：
#   电影-类型-科幻
#   电影-地区-美国
#   电影-年份-2024
#   剧集-类型-古装
#   剧集-地区-国产
#   综艺-类型-真人秀
#
# dimensions: 按顺序生成哪些维度（可调顺序）
#   - media_type: 类型（vod 中的 media_type 字段）
#   - region:     地区
#   - year:       年份（字符串）
# group-labels: 每个维度在 group-title 里的显示名
GROUP_TITLE_RULES = {
    "movie":   {"label": "电影", "dimensions": ["media_type", "region", "year"]},
    "tv":      {"label": "剧集", "dimensions": ["media_type", "region", "year"]},
    "anime":   {"label": "动漫", "dimensions": ["media_type", "region", "year"]},
    "variety": {"label": "综艺", "dimensions": ["media_type", "region", "year"]},
}
GROUP_DIMENSION_LABELS = {
    "media_type": "类型",
    "region":     "地区",
    "year":       "年份",
}
GROUP_FALLBACK = "其他"      # 分组字段为空 / 小分类合并后的兜底组名（如 电影-类型-其他）
MIN_GROUP_SIZE = 1           # 分组内条目数小于此值的小分类，并入「其他」不单独生成
                             # 改为 1：数据量较小时也保持真实分类名，避免正常地区/年份被归并

# 地区合并：把细碎国家/合拍片合并到主要地区桶，减少无意义分组
# key 为桶名，value 为命中关键词（大小写不敏感）
REGION_BUCKETS = {
    "中国大陆": ["中国大陆", "大陆", "中国", "国产"],
    "香港": ["香港", "中国香港"],
    "台湾": ["台湾", "中国台湾"],
    "美国": ["美国"],
    "日本": ["日本"],
    "韩国": ["韩国"],
    "英国": ["英国"],
    "印度": ["印度"],
    "泰国": ["泰国"],
    "欧美": ["法国", "德国", "意大利", "西班牙", "加拿大", "澳大利亚",
             "俄罗斯", "荷兰", "比利时", "瑞典", "丹麦", "挪威", "芬兰",
             "爱尔兰", "瑞士", "奥地利", "波兰", "捷克", "匈牙利", "希腊",
             "葡萄牙", "卢森堡", "冰岛", "爱沙尼亚", "立陶宛", "拉脱维亚",
             "乌克兰", "罗马尼亚", "保加利亚", "塞尔维亚", "克罗地亚",
             "墨西哥", "巴西", "阿根廷", "智利", "哥伦比亚", "秘鲁",
             "南非", "埃及", "土耳其", "伊朗", "伊拉克", "以色列",
             "新西兰", "斐济", "加纳"],
}

# 年份合并为年代区间，避免每一年都成一个独立分组
YEAR_BUCKETS = [
    (2020, 2029, "2020年代"),
    (2010, 2019, "2010年代"),
    (2000, 2009, "2000年代"),
    (1990, 1999, "90年代"),
    (1980, 1989, "80年代"),
    (0, 1979, "更早"),
]

# 条目标题格式: 名称 | 地区 | 年份 | 清晰度（空字段自动省略）
ENTRY_TITLE_JOIN = " | "

# 标题清洗: 从名称中移除的清晰度/状态标记词（大小写不敏感），
# 这些信息由 quality 字段单独保存，避免标题出现「xxx 1080p高清全集」噪音
TITLE_CLEAN_TOKENS = (
    "1080p", "720p", "2160p", "4k", "hd",
    "高清", "超清", "全集", "蓝光", "bd",
)

# ---------- 直播源配置 ----------
# 聚合直播源（公开 M3U），每天更新一次，全量刷新 live 表。
# 下载走系统代理（本机 HTTPS_PROXY），测速始终直连（贴近真实观看网络）。
LIVE_SOURCES = {
    # Guovin iptv-api：每天自动测速优选（gd=广东视角），量大含港澳台
    "guovin": "https://raw.githubusercontent.com/Guovin/iptv-api/gd/output/result.m3u",
    # vbskycn：分组规整（央视/卫视/地方）
    "vbskycn": "https://raw.githubusercontent.com/vbskycn/iptv/master/tv/iptv4.m3u",
    # iptv-org 港澳台补充（含 Geo-blocked 标记的会自动剔除）
    "iptvorg-hk": "https://iptv-org.github.io/iptv/countries/hk.m3u",
    "iptvorg-tw": "https://iptv-org.github.io/iptv/countries/tw.m3u",
    "iptvorg-mo": "https://iptv-org.github.io/iptv/countries/mo.m3u",
}

# 直播分类体系：key -> M3U group-title / Web 筛选标签
LIVE_CATEGORIES = {
    "cctv": "央视频道",
    "satellite": "卫视频道",
    "local": "地方频道",
    "hmt": "港澳台",
}
LIVE_CATEGORY_ORDER = ["cctv", "satellite", "local", "hmt"]

LIVE_KEEP_PER_CHANNEL = 3   # 每频道保留的线路数（按延迟升序）
LIVE_SPEED_TIMEOUT = 3.0    # TCP 测速超时（秒）
LIVE_SPEED_WORKERS = 64     # 测速并发线程数
LIVE_SPEED_RETRY = 1        # 测速失败重试次数

# 直播输出文件
LIVE_M3U_OUTPUT = "live.m3u"
LIVE_TXT_OUTPUT = "live.txt"

# ---------- 采集配置 ----------
# cc0cd = TVBox 聚合配置中心（https://tv.cc0cd.cc.cd 根路径返回一份 JSON，
# 内含数百个影视采集站 API）。sources 为启用的白名单源 key（对应配置中心 sites[].key）。
# 只放内容干净的大站；配置中心里的成人/擦边源一律不启用。
COLLECTORS = {
    "cc0cd": {
        "center_url": "https://tv.cc0cd.cc.cd",   # 配置中心地址
        "sources": {                              # 白名单源(配置中心 key): key -> 展示名
            "360":  "360影视",
            "旺旺": "旺旺影视",
            "如意": "如意影视",
            "率率": "率率影视",
        },
        # 直接 API 地址源：不依赖配置中心 key，URL 经过实测可用。
        # 来源：饭太硬/摸鱼儿/王二小等知名 TVBox 配置背后的核心苹果CMS采集站
        # （这些知名接口本身是 spider 配置，标准采集器无法直接执行 JS 爬虫，
        #  取其底层标准 JSON API 即可拿到同等资源）。
        # 已剔除纯短剧站(魔都/爱奇艺/牛牛/鸭鸭)与成人站。
        "direct_sources": {
            "索尼": "https://suoniapi.com/api.php/provide/vod",
            "金鹰": "https://jyzyapi.com/provide/vod/",
            "红牛": "https://www.hongniuzy2.com/api.php/provide/vod/",
            "猫眼": "https://api.maoyanapi.top/api.php/provide/vod",
            "樱花": "https://m3u8.apiyhzy.com/api.php/provide/vod/",
            "非凡": "https://cj.ffzyapi.com/api.php/provide/vod/",
            "光速": "https://api.guangsuapi.com/api.php/provide/vod/",
            "无尽": "https://api.wujinapi.com/api.php/provide/vod/",
            "速播": "https://subocaiji.com/api.php/provide/vod/",
            "极速": "https://jszyapi.com/api.php/provide/vod/at/json",
            "火狐": "https://hhzyapi.com/api.php/provide/vod/at/json",
            "西瓜": "https://caiji.xgzyapi.com/api.php/provide/vod/",
            "优酷": "https://api.ukuapi.com/api.php/provide/vod/",
            "百度": "https://api.apibdzy.com/api.php/provide/vod/",
            "豆瓣": "https://caiji.dbzy5.com/api.php/provide/vod/at/json/",
            "暴风": "https://bfzyapi.com/api.php/provide/vod/",
            "星球": "https://www.ysxq.cc/api.php/provide/vod",
            # 量子：国内可直连源（播放域名 47ms 实测），150898 条大站
            "量子": "https://cj.lziapi.com/api.php/provide/vod/",
            # 茅台：国内节点直连快（vodcnd02.uvjtih.cn 53ms 实测），141390 条大站
            "茅台": "https://caiji.maotaizy.cc/api.php/provide/vod/",
        },
        "default_pages": 3,        # 每源默认采集页数（每页 20 条）
            "detail_batch": 20,        # 单次 ac=detail 批量查询的 vod_id 数
            "request_delay": 0.25,     # 两次请求之间的间隔秒数（防止被限流）
            "timeout": 10,             # 单次请求超时秒数
    },
}

# 采集黑名单：type_name（采集站分类名）命中任一关键词即跳过
# 用于拦截成人/伦理/擦边内容，以及用户不需要的短剧/直播/演唱会等
COLLECT_BLOCK_TYPES = (
    "伦理", "成人", "色情", "福利", "萝莉", "av", "番号", "写真",
    "短剧", "演唱会", "体育", "直播",
    # 低价值/非点播内容
    "预告", "反转", "爽剧", "有声", "解说", "花絮", "片花", "采访",
    "发布会", "开机", "探班", "ai漫", "漫剧", "动态漫",
    "理论", "里番", "伦理剧", "情色",
)
