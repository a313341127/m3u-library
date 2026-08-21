# M3U 影视资源库

私人影视资源整理 + M3U/TXT 源生成工具（后台程序，无界面）。

用 SQLite 维护资源库，手动添加资源后一键生成**途播可导入**的
`movie.m3u` / `tv.m3u` / `anime.m3u` / `variety.m3u`（以及可选的 `.txt` 文本源）。
采集模块独立设计，后续可无侵入地接入多个采集站。

## 技术栈

- Python 3.10+（第一阶段仅用标准库，**零第三方依赖**）
- SQLite（数据库文件 `data/media.db`，WAL 模式）

## 快速开始

```bash
# 1. 添加资源
python main.py add -c movie -n "流浪地球2" -t 科幻 -r 中国大陆 -y 2023 \
    -u "http://example.com/wander2.m3u8" -q "4K" -d "太阳危机下的人类迁徙计划"

# 2. 查看资源
python main.py list                # 全部
python main.py list -c movie       # 只看电影
python main.py list -k 流浪         # 按名称/简介关键字搜索

# 3. 生成源文件（输出到 output/）
python main.py generate            # movie.m3u / tv.m3u / anime.m3u / variety.m3u
python main.py generate --txt      # 同时生成 .txt 文本源
python main.py generate -c movie   # 只生成电影

# 4. 自动采集（cc0cd 聚合源）
python main.py collect -n cc0cd                 # 采全部白名单源，采集后自动重新生成 M3U
python main.py collect -n cc0cd --pages 5       # 每个源采 5 页（每页约 20 条）
python main.py collect -n cc0cd --sources 360   # 只采 360 源
python main.py collect -n cc0cd -c movie        # 只采电影
python main.py collect --list                   # 查看已注册采集器

# 5. 删除资源（id 用 list 查看）
python main.py remove 3
```

## 目录结构

```
m3u-library/
├── main.py                 # 命令行入口（add/list/remove/generate/collect）
├── config.py               # 分类体系、输出路径、编码等配置
├── requirements.txt
├── core/                   # 核心数据层
│   └── database.py         # SQLite 建表 + 增删改查（含去重、自动更新时间）
├── collector/              # 采集模块（独立、可插拔，与主程序解耦）
│   ├── base.py             # ResourceItem 数据契约 + BaseCollector 抽象基类
│   ├── registry.py         # 采集器注册表（@register 装饰器）
│   ├── manager.py          # 采集编排：fetch() → 去重入库 + 字段更新
│   ├── cc0cd.py            # cc0cd 聚合采集器（TVBox 配置中心 + 苹果CMS 标准 API）
│   └── example.py          # 示例采集器模板（占位，不产出数据）
├── generator/
│   └── m3u.py              # M3U / TXT 生成器（group-title 自动拼装）
├── data/                   # SQLite 数据库目录（自动创建）
└── output/                 # 生成的源文件目录（自动创建）
```

## M3U 生成规则

条目格式（与途播兼容）：

```
#EXTM3U
#EXTINF:-1 tvg-logo="封面URL" group-title="电影-类型-科幻",流浪地球2 | 中国大陆 | 2023 | 4K
http://example.com/play.m3u8
```

- **多维分组**（类似 App 的筛选效果）：每个资源会按「类型 / 地区 / 年份」
  三个维度各生成一条分组入口，途播里会出现：
  - `电影-类型-科幻`、`电影-地区-中国大陆`、`电影-年份-2023`
  - `剧集-地区-国产`、`剧集-年份-2026`
  - `综艺-地区-中国大陆`、`综艺-年份-2026`
- **小分类合并**：某个维度下只有 1 条资源的分组，会并入「`分类-维度-其他`」，
  避免播放器里出现一堆单条分类
- **条目标题**为 `名称 | 地区 | 年份 | 清晰度`，空字段自动省略
- **标题清洗**：自动去掉名称中的 `1080p / 720p / HD / 高清 / 全集 / 超清 / 蓝光` 等
  标记（清晰度由 quality 字段单独展示），清洗词表在 `config.py` 的 `TITLE_CLEAN_TOKENS`
- **资源去重**：生成时按 `名称 + 年份 + 地区` 去重，只保留一条
  （保留清晰度更高 / 带封面 / 更新更近的）
- 有封面时自动追加 `tvg-logo="封面URL"` 属性
- 输出默认 UTF-8 带 BOM（`utf-8-sig`），Windows 播放器兼容性更好；如需调整改 `config.py`

## cc0cd 聚合采集器

`https://tv.cc0cd.cc.cd` 是一个 **TVBox 聚合配置中心**：根路径直接返回一份 JSON，
内含 1400+ 个影视采集站 API（苹果CMS 标准接口）。采集器工作流程：

1. 拉取配置中心 JSON，得到所有采集站 API
2. 按 `config.py` 中 `COLLECTORS["cc0cd"]["sources"]` 的**白名单**挑选内容干净的大站
   （当前启用 360 / 旺旺 / 如意 / 率率 四个 JSON 接口源）
3. 对每个源：`ac=list` 拉列表 → `ac=detail` 按 ID 批量拉完整字段
4. 解析映射：类型 → movie / tv / anime / variety 自动归类，地区规范化
   （中国香港→香港、国产: 中国大陆→中国大陆），播放地址取第一播放源第一集
5. 入库：同「分类+名称+地址」视为同一资源，**不存在则新增，已存在则刷新字段**
   （类型/地区/年份/封面/简介/清晰度，并更新 updated_at）
6. 采集完成输出统计：电影数量 / 电视剧数量 / 动漫数量 / 综艺数量 / 新增 /
   重复(已存在) / 过滤(黑名单分类或无效) / 请求失败，并自动重新生成全部 M3U / TXT

### 采集内容过滤

- **源级白名单**：配置中心里混有成人/擦边源，一律不启用（只改 `sources` 白名单即可）
- **分类黑名单**：`COLLECT_BLOCK_TYPES`（伦理/成人/短剧/演唱会等）命中即跳过；
  综艺已单独归类为 `variety`，不再拦截
- 采集站类型名 → 项目分类的映射规则见 `collector/cc0cd.py` 的 `classify_category()`

### 更换 / 新增采集源

```python
# config.py
COLLECTORS = {
    "cc0cd": {
        "sources": {
            "360":  "360影视",        # key 必须是配置中心 sites[].key（用 --list 探测可先用
            "旺旺": "旺旺影视",        # python -c "from collector.cc0cd import http_get_json; ..." 查看）
            # 新增: "如意": "如意影视",
        },
        "default_pages": 3,           # 每源默认页数
    },
}
```

## 接入自定义采集站

采集模块与数据库、主程序完全解耦，接入流程：

1. 复制 `collector/example.py` 为 `collector/xxx.py`
2. 实现 `fetch()`：调用采集站 API / 抓页面，返回 `List[ResourceItem]`
3. 在 `collector/__init__.py` 底部 `import xxx` 即完成注册
4. 运行 `python main.py collect -n xxx`，抓到的资源自动入库（去重 + 字段更新）

采集器只需要关心"怎么拿数据"，入库、去重、字段更新、生成源文件全部由主程序统一处理。

## 数据字段

| 字段 | 说明 |
| ---- | ---- |
| name | 名称 |
| category | 分类（movie / tv / anime / variety） |
| media_type | 类型（科幻 / 古装 / 热血 / 真人秀…） |
| region | 地区（中国大陆 / 美剧 / 日本…） |
| year | 年份 |
| cover | 封面 URL |
| description | 简介 |
| url | 播放地址 |
| quality | 清晰度（4K / 1080p…） |
| updated_at | 更新时间（自动维护） |
| source | 来源（manual=手动，其他=采集器注册名） |

分类/类型/地区列表集中在 `config.py` 的 `CATEGORIES` 中，可按需增删。

## 路线图

- [x] 第一阶段：项目结构、数据库、手动添加资源、生成 M3U
- [x] 第二阶段：cc0cd 聚合采集（配置中心 + 苹果CMS 标准 API）、自动去重与字段更新、采集后自动重新生成
- [x] 分类体系升级：增加综艺、多维分组（类型/地区/年份）、小分类合并、标题清洗
- [ ] 第三阶段：更多采集源、定时更新、批量导出、封面本地缓存
