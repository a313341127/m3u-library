-- qs-agcl2 分享站 D1 遥测库（方案 B：跨边缘实例一致的设备计数，使自动封禁真正可靠）
-- 绑定名固定为 DB（worker 中通过 env.DB 访问）。
-- 应用方式（首次）：
--   wrangler d1 execute m3u-share --remote --file=share/schema.sql
CREATE TABLE IF NOT EXISTS access_log (
  id   INTEGER PRIMARY KEY AUTOINCREMENT,
  code TEXT    NOT NULL,   -- 邀请码
  did  TEXT    NOT NULL,   -- 设备指纹（qgdev Cookie 值）
  ip   TEXT,               -- 客户端 IP（仅审计，不计入判定）
  ts   INTEGER NOT NULL    -- 访问时间戳(ms)
);
CREATE INDEX IF NOT EXISTS idx_access_log_code_ts ON access_log(code, ts);
CREATE INDEX IF NOT EXISTS idx_access_log_ts ON access_log(ts);
