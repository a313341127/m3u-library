#!/usr/bin/env bash
# ============================================================
# 一键部署 M3U 源到 Cloudflare Pages（项目名: qinjin）
# 用法:
#   1) 首次:  cp .env.example .env  并填入 TOKEN / ACCOUNT_ID
#   2) 以后每次采集完:  ./deploy.sh
# 说明: 脚本会先重新生成 M3U/TXT，再上传到 Pages production，
#        自定义域名 qinjin.ccwu.cc 绑定后自动同步生效。
# ============================================================
set -e
cd "$(dirname "$0")"

# ---------- 读取凭据（优先级: 环境变量 > .env 文件） ----------
if [ -f .env ]; then
  set -a; . ./.env; set +a
fi
CF_TOKEN="${CLOUDFLARE_API_TOKEN:-}"
CF_ACCOUNT="${CLOUDFLARE_ACCOUNT_ID:-}"
PROJECT="qinjin"

if [ -z "$CF_TOKEN" ] || [ -z "$CF_ACCOUNT" ]; then
  echo "[错误] 缺少 CLOUDFLARE_API_TOKEN 或 CLOUDFLARE_ACCOUNT_ID"
  echo "       请复制 .env.example 为 .env 并填入，或先 export 环境变量"
  exit 1
fi

# ---------- 定位运行环境 ----------
PY="${PYTHON:-python}"
NODE_BIN="${NODE_BIN:-}"
WRANGLER="${WRANGLER:-}"

# Python（优先用 WorkBuddy 托管版本）
for cand in \
  "C:/Users/win11/.workbuddy/binaries/python/versions/3.13.12/python.exe" \
  "$(command -v python 2>/dev/null)" \
  "$(command -v python3 2>/dev/null)"; do
  [ -n "$cand" ] && [ -x "$cand" ] || [ -n "$cand" ] || continue
  if [ -n "$cand" ]; then PY="$cand"; break; fi
done

# Node / wrangler（优先 WorkBuddy 托管 workspace）
if [ -z "$NODE_BIN" ]; then
  NODE_BIN="C:/Users/win11/.workbuddy/binaries/node/versions/22.22.2/node.exe"
  [ -f "$NODE_BIN" ] || NODE_BIN="$(command -v node 2>/dev/null || echo node)"
fi
if [ -z "$WRANGLER" ]; then
  WRANGLER="C:/Users/win11/.workbuddy/binaries/node/workspace/node_modules/wrangler/bin/wrangler.js"
fi

# ---------- 1) 重新生成 M3U / TXT ----------
echo "[1/3] 重新生成 M3U / TXT ..."
"$PY" main.py generate --txt

# ---------- 2) 生成 _headers（确保 M3U 的 Content-Type 正确） ----------
echo "[2/3] 生成 _headers ..."
cat > output/_headers <<'EOF'
/movie.m3u
  Content-Type: application/vnd.apple.mpegurl
/tv.m3u
  Content-Type: application/vnd.apple.mpegurl
/anime.m3u
  Content-Type: application/vnd.apple.mpegurl
/variety.m3u
  Content-Type: application/vnd.apple.mpegurl
/movie.txt
  Content-Type: text/plain; charset=utf-8
/tv.txt
  Content-Type: text/plain; charset=utf-8
/anime.txt
  Content-Type: text/plain; charset=utf-8
/variety.txt
  Content-Type: text/plain; charset=utf-8
EOF

# ---------- 3) 部署到 Cloudflare Pages ----------
echo "[3/3] 部署到 Cloudflare Pages ($PROJECT) ..."
export CLOUDFLARE_API_TOKEN="$CF_TOKEN"
export CLOUDFLARE_ACCOUNT_ID="$CF_ACCOUNT"
if [ ! -f "$WRANGLER" ]; then
  echo "wrangler 未找到，先安装（首次运行）..."
  npm install --prefix "$HOME/.workbuddy/binaries/node/workspace" wrangler --no-fund --no-audit >/dev/null
fi
"$NODE_BIN" "$WRANGLER" pages deploy output --project-name="$PROJECT" --branch=production

echo ""
echo "✅ 部署完成！"
echo "   Web 首页:   https://production.qinjin.pages.dev/"
echo "   M3U 源:     https://production.qinjin.pages.dev/movie.m3u"
echo "   （自定义域名 qinjin.ccwu.cc 受本地 DNS 劫持影响，建议先用 Pages 域名）"
