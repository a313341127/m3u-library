# -*- coding: utf-8 -*-
"""cc0cd 聚合采集器

采集源: https://tv.cc0cd.cc.cd （TVBox 聚合配置中心，根路径返回 JSON）

工作原理:
1. 拉取配置中心 JSON，得到数百个采集站 API（苹果CMS 标准接口）
2. 按 config.COLLECTORS["cc0cd"]["sources"] 白名单挑选内容干净的大站
3. 对每个源:
   a. ac=list&pg=N    拉列表（拿 vod_id / type_name）
   b. ac=detail&ids=.. 按 ID 批量拉完整字段（封面/简介/播放地址/地区/年份）
4. 解析、分类（movie/tv/anime）、清洗后返回 List[ResourceItem]

设计说明:
- 采集器只做"拿数据"，不碰数据库；入库/去重由 manager 统一编排
- 白名单机制天然屏蔽成人/擦边源；type_name 黑名单二次拦截
- 播放地址取第一播放源的第一集（M3U 一条目对应一个直链）

CLI:
  python main.py collect -n cc0cd                 # 默认采全部白名单源
  python main.py collect -n cc0cd --pages 5       # 每源多采几页
  python main.py collect -n cc0cd --sources 360   # 只采 360
  python main.py collect -n cc0cd -c movie        # 只要电影
"""
import json
import re
import time
import urllib.request
import urllib.parse
import ssl
from typing import Dict, List, Optional, Tuple

import config
from collector.base import BaseCollector, ResourceItem
from collector.registry import register

_CTX = ssl.create_default_context()
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"


# ---------------------------------------------------------------- HTTP 工具
def http_get_json(url: str, timeout: int = 20) -> dict:
    """GET 请求并解析 JSON；失败抛异常（由调用方决定是否跳过）"""
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=timeout, context=_CTX) as resp:
        raw = resp.read()
    return json.loads(raw)


def join_params(api: str, **params) -> str:
    """给采集 API 追加查询参数。api 可能自带 ?ac=list 或尾部 ?，统一处理
    参数值含中文时自动 URL 编码。"""
    base = api.rstrip("?&")
    sep = "&" if "?" in base else "?"
    qs = "&".join(f"{k}={urllib.parse.quote(str(v), safe='')}" for k, v in params.items())
    return f"{base}{sep}{qs}"


# ---------------------------------------------------------------- 字段清洗
_REGION_MAP = {
    "中国香港": "香港", "香港": "香港", "中国澳门": "澳门", "澳门": "澳门",
    "中国台湾": "台湾", "台湾省": "台湾", "台湾": "台湾",
    "中国大陆": "中国大陆", "内地": "中国大陆", "大陆": "中国大陆", "国产": "中国大陆",
    "美国": "美国", "日本": "日本", "韩国": "韩国", "英国": "英国",
    "印度": "印度", "泰国": "泰国", "法国": "法国", "德国": "德国",
    "意大利": "意大利", "西班牙": "西班牙", "俄罗斯": "俄罗斯",
    "加拿大": "加拿大", "澳大利亚": "澳大利亚", "新加坡": "新加坡",
    "马来西亚": "马来西亚", "越南": "越南", "菲律宾": "菲律宾",
    "欧美": "欧美", "日韩": "日韩", "国漫": "中国大陆",
    "日漫": "日本", "美漫": "欧美",
}

_QUALITY_PATTERN = [
    (re.compile(r"4k|2160p", re.I), "4K"),
    (re.compile(r"1080p|超清|fhd", re.I), "1080p"),
    (re.compile(r"720p|hd|高清", re.I), "720p"),
    (re.compile(r"标清|sd", re.I), "标清"),
]

_URL_RE = re.compile(r"https?://[^\s$#]+", re.I)


def norm_region(area: str) -> str:
    """地区规范化: 中国香港 -> 香港 / 中国台湾 -> 台湾 / 内地 -> 中国大陆
    部分站 vod_area 形如 "国产: 中国大陆"（带子分类冒号前缀），取冒号后段"""
    a = (area or "").strip()
    if not a:
        return ""
    if ":" in a:
        a = a.split(":")[-1].strip()
    if a in _REGION_MAP:
        return _REGION_MAP[a]
    # 兜底：去"中国"前缀
    if a.startswith("中国"):
        return a[2:]
    return a


def extract_quality(text: str) -> str:
    """从 vod_remarks / 封面等文本提取清晰度，返回统一格式"""
    s = text or ""
    for pattern, label in _QUALITY_PATTERN:
        if pattern.search(s):
            return label
    return ""


def extract_first_url(play_url: str) -> str:
    """播放地址格式: 第01集$https://a.m3u8#第02集$https://b.m3u8
    多个播放源用 $$$ 分隔。取第一个源的第一集直链。"""
    if not play_url:
        return ""
    first_source = play_url.split("$$$")[0]
    for seg in first_source.split("#"):
        if "$" in seg:
            url = seg.split("$", 1)[1].strip()
        else:
            url = seg.strip()
        if url.startswith(("http://", "https://")):
            return url
    m = _URL_RE.search(play_url)
    return m.group(0) if m else ""


# ---------------------------------------------------------------- 分类映射
def classify_category(type_name: str, description: str = "") -> Optional[Tuple[str, str]]:
    """采集站分类名 -> (category, media_type)；无法归类返回 None

    规则:
    - 简介明确出现「纪录片/documentary」-> movie/纪录片（优先修正源站错标）
    - 含 综艺/真人秀/脱口秀/访谈/选秀/歌舞  -> variety（去后缀作类型）
    - 含 动漫/动画       -> anime（去后缀作类型）
    - 以 剧 结尾        -> tv（国产剧/韩剧/美剧 这类"地区剧"类型留空）
    - 以 片 结尾/含电影  -> movie
    - 短剧/伦理等黑名单  -> None（跳过）
    """
    t = (type_name or "").strip()
    if not t:
        return None
    # 简介关键词强修正（解决源站把纪录片标成科幻等错误）
    desc = (description or "").lower()
    if "纪录片" in desc or "documentary" in desc:
        return ("movie", "纪录片")
    if any(b.lower() in t.lower() for b in config.COLLECT_BLOCK_TYPES):
        return None
    if any(k in t for k in ("综艺", "真人秀", "脱口秀", "访谈", "选秀", "歌舞")):
        for k in ("综艺", "真人秀", "脱口秀", "访谈", "选秀", "歌舞"):
            t = t.replace(k, "")
        mt = t.strip().rstrip("片")      # "综艺片" -> 类型留空
        # 大陆综艺/港台综艺/日韩综艺：地区词不进入类型
        if mt in _REGION_MAP or (mt + "国") in _REGION_MAP or mt in ("国产", "内地", "大陆", "海外", "欧美", "日韩", "港台"):
            return ("variety", "")
        return ("variety", mt or "综艺")
    if "动漫" in t or "动画" in t:
        mt = t.replace("动漫", "").replace("动画", "").strip().rstrip("片")   # "动漫片" -> 类型留空
        # 国产动漫/日本动漫/欧美动漫：地区词不进入类型
        if mt in _REGION_MAP or (mt + "国") in _REGION_MAP or mt in ("国产", "内地", "大陆", "海外", "欧美", "日韩"):
            return ("anime", "")
        return ("anime", mt or "动漫")
    # 电影细分类型（文采/文才等站用细分名，如 科幻/喜剧/动作/战争/剧情/悬疑）
    # 注意「剧情」以"剧"结尾，必须先于下面的"剧"分支识别为电影
    _MOVIE_TYPES = ("科幻", "喜剧", "动作", "战争", "爱情", "犯罪", "恐怖",
                    "悬疑", "剧情", "冒险", "惊悚", "武侠", "奇幻", "灾难",
                    "运动", "历史", "传记", "伦理")
    if any(k in t for k in _MOVIE_TYPES):
        mt = t.strip().rstrip("片")      # "科幻片" -> 科幻
        if mt in _REGION_MAP or (mt + "国") in _REGION_MAP or mt in ("国产", "内地", "大陆", "海外", "欧美", "日韩"):
            return ("movie", "")
        return ("movie", mt or "电影")
    if t.endswith("剧"):
        mt = t[:-1]
        # 地区复合剧种（日本剧/泰国剧/港剧/泰剧/国产剧...）：
        # 地区信息由 vod_area 表达，类型留空避免 group-title 冗余
        region_like = (
            mt in _REGION_MAP
            or (mt + "国") in _REGION_MAP      # 泰->泰国, 韩->韩国
            or mt in ("港", "澳", "台", "日", "韩", "美", "英", "法", "德",
                      "泰", "越", "印", "俄", "意", "西")            # 单字地区
            or mt in ("国产", "内地", "大陆", "海外", "欧美", "日韩")
        )
        if region_like:
            return ("tv", "")
        # "漫剧/漫画改剧" 本质多为短剧/动态漫，不纳入剧集库
        if mt in ("漫", "漫改", "漫画", "漫画改"):
            return None
        return ("tv", mt)
    if t.endswith("片") or "电影" in t:
        mt = t[:-1] if t.endswith("片") else t.replace("电影", "").strip()
        # "电影解说"这类短视频内容不纳入片库
        if "解说" in mt or mt == "解说":
            return None
        # 纪录/记录 -> 纪录片；地区词/等于大分类 -> 类型留空（由地区维度表达）
        if mt in ("纪录", "记录"):
            return ("movie", "纪录片")
        if mt in ("", "中国", "国产", "内地", "大陆", "港台", "港澳", "马泰",
                  "其他", "电影", "短") or mt in _REGION_MAP:
            return ("movie", "")
        return ("movie", mt)
    return None


# ---------------------------------------------------------------- 采集器
@register
class CC0CDCollector(BaseCollector):
    name = "cc0cd"
    display_name = "cc0cd 聚合采集"

    def fetch(self, **kwargs) -> List[ResourceItem]:
        cfg = config.COLLECTORS["cc0cd"]
        want_category = kwargs.get("category")          # movie/tv/anime
        # --pages 0 表示全量（采到 pagecount）；显式判断避免 0 被 `or` 吞掉
        pages = int(kwargs["pages"]) if kwargs.get("pages") is not None else cfg["default_pages"]
        start_page = int(kwargs.get("start_page") or 1)
        keyword = (kwargs.get("keyword") or "").strip()
        timeout = cfg["timeout"]

        direct = cfg.get("direct_sources") or {}        # {展示名: API URL}
        items: List[ResourceItem] = []
        stats: List[str] = []
        self.stats = {"dropped": 0, "failed_pages": 0}

        # 0. 解析本次要采的源名单：--sources 优先，否则全量
        selected = kwargs.get("sources")
        if selected:
            keys = [k.strip() for k in str(selected).split(",") if k.strip()]
            direct_keys = [k for k in keys if k in direct]
            center_keys = [k for k in keys if k not in direct]
        else:
            direct_keys = list(direct)
            center_keys = list(cfg["sources"])

        # 1. 直接 API 源（URL 实测可用，不走配置中心）
        if direct_keys:
            print(f"[cc0cd] 直接 API 源 {len(direct_keys)} 个: {', '.join(direct_keys)}")
            for key in direct_keys:
                api = direct[key]
                n = self._collect_from_site(api, key, pages, want_category,
                                            keyword, timeout, items, start_page)
                stats.append(f"{key}:{n}")

        # 2. 配置中心白名单源
        if center_keys:
            print(f"[cc0cd] 拉取配置中心 {cfg['center_url']} ...")
            try:
                center = http_get_json(cfg["center_url"], timeout=timeout)
            except Exception as e:
                print(f"[cc0cd] [错误] 配置中心不可达: {e}")
                center = None
            if center:
                sites = {s.get("key"): s for s in center.get("sites", []) if s.get("api")}
                print(f"[cc0cd] 配置中心共 {len(sites)} 个源，本次启用 {len(center_keys)} 个白名单源")
                for key in center_keys:
                    site = sites.get(key)
                    if not site:
                        print(f"[cc0cd] [警告] 配置中心未找到源 '{key}'，跳过")
                        continue
                    if site.get("type") != 1:
                        print(f"[cc0cd] [警告] 源 '{key}' 是 type={site.get('type')} 接口"
                              f"（仅支持 type=1 JSON 接口），跳过；可在配置中心改选 JSON 源")
                        continue
                    api = site["api"]
                    n = self._collect_from_site(api, key, pages, want_category,
                                                keyword, timeout, items)
                    stats.append(f"{key}:{n}")

        if items:
            print(f"[cc0cd] 完成，共抓取 {len(items)} 条可入库资源（各源 {', '.join(stats)}）")
        else:
            print("[cc0cd] 完成，本次没有抓到可入库资源")
        return items

    # --------------------------------------------------------------
    def _map_source_types(self, api: str, timeout: int) -> List[Tuple[Optional[int], str, str, str]]:
        """获取源站分类映射：[(type_id, type_name, category, media_type), ...]

        调用 ac=list（不传分页/类型）拿到 class 列表，再把每个源站类型名映射到
        movie/tv/anime/variety。若接口未返回 class 则返回空列表，由调用方 fallback。
        """
        try:
            data = http_get_json(join_params(api, ac="list"), timeout=timeout)
        except Exception:
            return []
        classes = data.get("class") or []
        mapped = []
        for cls in classes:
            tid = cls.get("type_id")
            tname = (cls.get("type_name") or "").strip()
            if not tname:
                continue
            classified = classify_category(tname)
            if classified is None:
                continue
            cat, mt = classified
            try:
                tid = int(tid) if tid is not None else None
            except (TypeError, ValueError):
                tid = None
            mapped.append((tid, tname, cat, mt))
        return mapped

    # --------------------------------------------------------------
    def _collect_from_site(self, api: str, site_name: str, pages: int,
                           want_category: Optional[str], keyword: str,
                           timeout: int, items: List[ResourceItem],
                           start_page: int = 1) -> int:
        """采一个源，返回本源的产出条数。

        先拉取源站 class 列表，把源站类型 ID 映射到 movie/tv/anime/variety，
        再按目标分类的 type_id 分页采集（ac=list&t=type_id），避免跨类型错采。
        """
        cfg = config.COLLECTORS["cc0cd"]
        batch = cfg["detail_batch"]
        delay = cfg["request_delay"]
        total = 0

        # 1. 获取源站类型映射
        type_map = self._map_source_types(api, timeout)
        if not type_map:
            print(f"[cc0cd] [{site_name}] 未获取到类型列表，fallback 为全量混采")
            type_map = [(None, "", want_category or None, "")]
        else:
            mapped_summary = {}
            for _, tname, cat, _ in type_map:
                mapped_summary.setdefault(cat, []).append(tname)
            summary = " | ".join(
                f"{config.CATEGORIES.get(cat, {}).get('label', cat)}:{','.join(names[:3])}"
                for cat, names in mapped_summary.items()
            )
            print(f"[cc0cd] [{site_name}] 类型映射 {summary}")

        # 1.1 探测 t=type_id 是否生效：部分源（如文采/文才影视）的 API 忽略
        #     type 参数，ac=list&t=N 返回空。此时改为全量混采（不加 t），
        #     由 _to_item 在入库时按 type_name 重新分类。
        first_tid = next((t[0] for t in type_map if t[0] is not None), None)
        if first_tid is not None:
            probe_url = join_params(api, ac="list", t=first_tid, pg=1)
            try:
                probe = http_get_json(probe_url, timeout=timeout)
                if not probe.get("list"):
                    print(f"[cc0cd] [{site_name}] 源不支持按类型筛选(t={first_tid} 返回空)，"
                          f"改用全量混采模式")
                    # forced_category 置 None：由 _to_item 按 type_name 重新分类，
                    # 同时受 want_category 过滤，避免把混合数据全部强行标成 movie
                    type_map = [(None, "", None, "")]
            except Exception:
                pass

        # 2. 只保留目标分类
        if want_category:
            # 全量混采 fallback 的条目 category 为 None，由 _to_item 按 type_name
            # 再分类并受 want_category 过滤，此处不能把它过滤掉
            type_map = [t for t in type_map if t[2] == want_category or t[2] is None]
        if not type_map:
            print(f"[cc0cd] [{site_name}] 无匹配的目标分类，跳过")
            return 0

        for tid, tname, cat, mt in type_map:
            cat_label = config.CATEGORIES.get(cat, {}).get("label", cat)
            # 全量模式：pages<=0 时自动采到 pagecount（由首屏响应的 pagecount 决定上限）
            max_pages = pages if (pages and pages > 0) else 10 ** 9
            pg = start_page
            retry_limit = 4
            while pg <= max_pages:
                params = {"ac": "list", "pg": pg}
                if tid is not None:
                    params["t"] = tid
                if keyword:
                    params["wd"] = keyword
                list_url = join_params(api, **params)
                # 列表页拉取具备重试与跳过能力：单次 429/网络抖动不再中断整源采集
                data = None
                for attempt in range(retry_limit):
                    try:
                        data = http_get_json(list_url, timeout=timeout)
                        break
                    except Exception as e:
                        wait = delay * (2 ** attempt) + 2
                        print(f"[cc0cd] [{site_name}][{cat_label}][{tname or '?'}] "
                              f"列表第{pg}页第{attempt + 1}次失败: {e}；{wait:.0f}s 后重试")
                        time.sleep(wait)
                if data is None:
                    print(f"[cc0cd] [{site_name}][{cat_label}][{tname or '?'}] "
                          f"列表第{pg}页重试{retry_limit}次仍失败，跳过本页")
                    self.stats["failed_pages"] += 1
                    pg += 1
                    continue
                vod_list = data.get("list") or []
                if not vod_list:
                    break
                total_pages = data.get("pagecount") or 0
                print(f"[cc0cd] [{site_name}][{cat_label}][{tname or '?'}] 列表第{pg}/{total_pages}页，{len(vod_list)} 条")
                # 全量模式：首屏拿到 pagecount 后扩展上限，确保采到该源最后一页
                if (not pages or pages <= 0) and total_pages:
                    max_pages = total_pages

                # 按 ID 批量拉详情
                ids = [str(v.get("vod_id")) for v in vod_list if v.get("vod_id")]
                for i in range(0, len(ids), batch):
                    chunk = ids[i:i + batch]
                    detail_url = join_params(api, ac="detail", ids=",".join(chunk))
                    try:
                        detail = http_get_json(detail_url, timeout=timeout)
                    except Exception as e:
                        print(f"[cc0cd] [{site_name}][{cat_label}] 详情批量失败: {e}")
                        self.stats["failed_pages"] += 1
                        continue
                    for v in detail.get("list") or []:
                        item = self._to_item(
                            v, want_category, keyword,
                            source_type_name=tname,
                            forced_category=cat,
                            forced_media_type=mt,
                        )
                        if item:
                            items.append(item)
                            total += 1
                        else:
                            self.stats["dropped"] += 1
                    time.sleep(delay)
                time.sleep(delay)
                pg += 1
        return total

    # --------------------------------------------------------------
    @staticmethod
    def _to_item(v: dict, want_category: Optional[str],
                 keyword: str,
                 source_type_name: str = "",
                 forced_category: Optional[str] = None,
                 forced_media_type: Optional[str] = None) -> Optional[ResourceItem]:
        """单条 vod 数据 -> ResourceItem；不符合要求返回 None

        当通过源站类型 ID 采集时，传入 forced_category/forced_media_type 以类型 ID
        为准，避免源站在详情页把类型写错（如纪录片被标成科幻片）。
        """
        raw_type_name = source_type_name or (v.get("type_name") or "").strip()
        desc = re.sub(r"<[^>]+>", "", v.get("vod_content") or "").strip()

        if forced_category:
            category = forced_category
            media_type = forced_media_type or ""
        else:
            classified = classify_category(raw_type_name, desc)
            if classified is None:
                return None
            category, media_type = classified
            if want_category and category != want_category:
                return None

        # 简介关键词二次修正（即使类型 ID 对了，media_type 仍可能被标错）
        if "纪录片" in desc or "documentary" in desc.lower():
            media_type = "纪录片"

        name = (v.get("vod_name") or "").strip()
        url = extract_first_url(v.get("vod_play_url") or "")
        if not name or not url:
            return None
        if keyword and keyword not in name and keyword not in desc:
            return None

        remarks = v.get("vod_remarks") or ""
        quality = extract_quality(remarks) or extract_quality(v.get("vod_pic") or "")

        year = v.get("vod_year")
        try:
            year = int(year) if str(year).strip().isdigit() else None
        except (TypeError, ValueError):
            year = None

        # 人气（源站播放量）与评分（0-10，脏值归 0）
        try:
            hits = int(v.get("vod_hits") or 0)
        except (TypeError, ValueError):
            hits = 0
        try:
            score = float(v.get("vod_score") or 0)
        except (TypeError, ValueError):
            score = 0.0
        if not (0 < score <= 10):
            score = 0.0

        return ResourceItem(
            name=name,
            category=category,
            media_type=media_type,
            region=norm_region(v.get("vod_area") or ""),
            year=year,
            cover=(v.get("vod_pic") or "").strip(),
            description=desc,
            url=url,
            quality=quality,
            raw_type_name=raw_type_name,
            hits=hits,
            score=score,
        )
