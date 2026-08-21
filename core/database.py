# -*- coding: utf-8 -*-
"""SQLite 数据库访问层

表结构: resources（资源表）
索引:   category / media_type / region / year（M3U 分组与筛选加速）

设计要点:
- 同分类下「名称 + 播放地址」完全相同时视为重复，add 自动跳过
- updated_at 在新增/更新时自动维护，对应需求的「更新时间」
"""
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import List, Optional

import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS resources (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,              -- 名称
    category    TEXT NOT NULL,              -- 分类: movie / tv / anime
    media_type  TEXT DEFAULT '',            -- 类型: 动作 / 科幻 / 古装 ...
    region      TEXT DEFAULT '',            -- 地区: 中国大陆 / 美国 / 韩剧 ...
    year        INTEGER,                    -- 年份
    cover       TEXT DEFAULT '',            -- 封面 URL
    description TEXT DEFAULT '',            -- 简介
    url           TEXT NOT NULL,              -- 播放地址
    quality       TEXT DEFAULT '',            -- 清晰度: 4K / 1080p / 720p
    source        TEXT DEFAULT 'manual',      -- 来源: manual=手动, 其他=采集器注册名
    raw_type_name TEXT DEFAULT '',            -- 采集站原始分类名（用于排查分类错误）
    updated_at    TEXT NOT NULL,              -- 更新时间
    created_at    TEXT NOT NULL               -- 创建时间
);

CREATE INDEX IF NOT EXISTS idx_resources_category   ON resources(category);
CREATE INDEX IF NOT EXISTS idx_resources_media_type ON resources(media_type);
CREATE INDEX IF NOT EXISTS idx_resources_region     ON resources(region);
CREATE INDEX IF NOT EXISTS idx_resources_year       ON resources(year);
"""

# update_resource 允许更新的字段白名单
UPDATEABLE_FIELDS = {"name", "category", "media_type", "region", "year",
                     "cover", "description", "url", "quality", "source",
                     "raw_type_name"}


class Database:
    """资源库操作封装"""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = Path(db_path) if db_path else config.DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    # ---------------- 基础 ----------------

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_schema(self):
        with self._connect() as conn:
            conn.executescript(SCHEMA)
            self._migrate(conn)

    @staticmethod
    def _migrate(conn: sqlite3.Connection):
        """兼容升级：老表缺少 raw_type_name 时自动追加"""
        cols = {r[1] for r in conn.execute("PRAGMA table_info(resources)")}
        if "raw_type_name" not in cols:
            conn.execute("ALTER TABLE resources ADD COLUMN raw_type_name TEXT DEFAULT ''")

    @staticmethod
    def _now() -> str:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # ---------------- 增删改查 ----------------

    def add_resource(self, name: str, category: str, media_type: str = "",
                     region: str = "", year: Optional[int] = None,
                     cover: str = "", description: str = "", url: str = "",
                     quality: str = "", source: str = "manual",
                     raw_type_name: str = "") -> Optional[int]:
        """新增资源，返回新 id；重复（同分类+同名+同地址）返回 None。"""
        now = self._now()
        with self._connect() as conn:
            dup = conn.execute(
                "SELECT id FROM resources WHERE category=? AND name=? AND url=?",
                (category, name, url),
            ).fetchone()
            if dup:
                return None
            cur = conn.execute(
                """INSERT INTO resources
                   (name, category, media_type, region, year, cover,
                    description, url, quality, source, raw_type_name,
                    updated_at, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (name, category, media_type, region, year, cover, description,
                 url, quality, source, raw_type_name, now, now),
            )
            return cur.lastrowid

    def find_resource_id(self, name: str, category: str, url: str) -> Optional[int]:
        """按「分类+名称+播放地址」查已存在资源 id，用于采集场景的更新定位"""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id FROM resources WHERE category=? AND name=? AND url=?",
                (category, name, url),
            ).fetchone()
            return row["id"] if row else None

    def update_resource(self, resource_id: int, **fields) -> bool:
        """按 id 更新字段（白名单校验），自动刷新 updated_at。"""
        sets, vals = [], []
        for k, v in fields.items():
            if k in UPDATEABLE_FIELDS:
                sets.append(f"{k}=?")
                vals.append(v)
        if not sets:
            return False
        sets.append("updated_at=?")
        vals.append(self._now())
        vals.append(resource_id)
        with self._connect() as conn:
            cur = conn.execute(
                f"UPDATE resources SET {', '.join(sets)} WHERE id=?", vals)
            return cur.rowcount > 0

    def remove_resource(self, resource_id: int) -> bool:
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM resources WHERE id=?", (resource_id,))
            return cur.rowcount > 0

    def get_resource(self, resource_id: int) -> Optional[sqlite3.Row]:
        with self._connect() as conn:
            return conn.execute(
                "SELECT * FROM resources WHERE id=?", (resource_id,)).fetchone()

    def list_resources(self, category: Optional[str] = None,
                       media_type: Optional[str] = None,
                       region: Optional[str] = None,
                       year: Optional[int] = None,
                       keyword: Optional[str] = None,
                       source: Optional[str] = None) -> List[sqlite3.Row]:
        """按条件筛选，按更新时间倒序返回。"""
        sql = "SELECT * FROM resources WHERE 1=1"
        params: list = []
        if category:
            sql += " AND category=?"
            params.append(category)
        if media_type:
            sql += " AND media_type=?"
            params.append(media_type)
        if region:
            sql += " AND region=?"
            params.append(region)
        if year:
            sql += " AND year=?"
            params.append(year)
        if source:
            sql += " AND source=?"
            params.append(source)
        if keyword:
            sql += " AND (name LIKE ? OR description LIKE ?)"
            params += [f"%{keyword}%", f"%{keyword}%"]
        sql += " ORDER BY updated_at DESC, id DESC"
        with self._connect() as conn:
            return conn.execute(sql, params).fetchall()

    def count(self) -> int:
        with self._connect() as conn:
            return conn.execute("SELECT COUNT(*) FROM resources").fetchone()[0]
