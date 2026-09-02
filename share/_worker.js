// 秦哥影视资源 - 分享站访问网关（一人一码 + IP 异常监测）
// 管理面板：https://qs-agcl2.pages.dev/__admin
const ADMIN_PASSWORD = "031985";   // 管理面板登录口令
const SITE_URL = "https://qs-agcl2.pages.dev";
const COOKIE = "qgcode";           // 用户身份 Cookie（值为邀请码）
const NOTE_COOKIE = "qgnote";      // 首次弹窗已确认 Cookie
const ADMIN_COOKIE = "qgadmin";    // 管理员 Cookie
const MAX_AGE = 7776000;           // 90 天
const ANOMALY_IPS = 3;             // 24h 内超过 N 个不同 IP → 异常
const BLOCK_IPS = 5;               // 24h 内超过 N 个不同 IP → 自动封禁
const MAX_LOGS = 30;               // 每人保留最近 N 条访问日志
const CODE_CHARS = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"; // 无易混淆字符

// ---------- 工具函数 ----------
function generateCode() {
  let c = "";
  for (let i = 0; i < 6; i++) c += CODE_CHARS[Math.floor(Math.random() * CODE_CHARS.length)];
  return c;
}
function getClientIP(request) {
  return request.headers.get("cf-connecting-ip") || "unknown";
}
function nowISO() { return new Date().toISOString(); }
function getCookie(request, name) {
  const m = (request.headers.get("cookie") || "").match(new RegExp(`${name}=([^;]+)`));
  return m ? m[1] : "";
}
function cookieStr(name, value) {
  return `${name}=${value}; Path=/; Max-Age=${MAX_AGE}; Secure; HttpOnly; SameSite=Lax`;
}
function json(data, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "content-type": "application/json; charset=utf-8", "cache-control": "no-store" },
  });
}

// ---------- 码表（静态 JSON via ASSETS，零 KV）----------
// 码表文件：仓库内 share/codes.json，部署后由 env.ASSETS 提供（路径 /codes.json）。
// 管理面板增删改 → 通过 GitHub Contents API 写回仓库（需 GITHUB_TOKEN + GITHUB_REPO 环境变量/密钥），
//   同时更新内存缓存立即生效；下次部署后 ASSETS 自动同步。
// 访问遥测（ips24h / 访问次数等）仅存内存，worker 重启后重置（无 KV/D1 写入）。
// 迁移期：设 MIGRATE_FROM_KV=1 且 KV 仍绑定，则 codes.json 为空时回退读 KV（迁移完成后关掉并解绑 KV）。

const CODES_FILE = "/codes.json";
let SELF_ORIGIN = SITE_URL;
let codeCache = null;            // { codes: [...] }
let codeCacheAt = 0;
const CODE_TTL = 60_000;         // 内存缓存 60s，自动拾取新部署
const accessState = new Map();   // code -> { ips24h, totalAccess, lastAccess, lastIP, recentLogs, softBlocked, softAnomaly }

function b64encode(str) { return btoa(unescape(encodeURIComponent(str))); }
function b64decode(str) { return decodeURIComponent(escape(atob(str))); }

async function loadCodes(env) {
  const now = Date.now();
  if (codeCache && now - codeCacheAt < CODE_TTL) return codeCache;
  let table = null;
  try {
    const res = await env.ASSETS.fetch(new URL(CODES_FILE, SELF_ORIGIN));
    if (res.ok) {
      const txt = await res.text();
      if (txt && txt.trim()) table = JSON.parse(txt);
    }
  } catch {}
  // 迁移期回退：codes.json 为空且开启 MIGRATE_FROM_KV 且 KV 仍绑定 → 读 KV
  if ((!table || !table.codes || !table.codes.length) && env.MIGRATE_FROM_KV === "1" && env.CODES) {
    try {
      const raw = await env.CODES.get("users_index");
      const idx = raw ? JSON.parse(raw) : [];
      const codes = [];
      for (const code of idx) {
        const u = await env.CODES.get(`user:${code}`);
        if (u) codes.push(JSON.parse(u));
      }
      table = { codes, migrated: true };
    } catch {}
  }
  codeCache = table && table.codes ? table : { codes: [] };
  codeCacheAt = now;
  return codeCache;
}

async function getUser(env, code) {
  const t = await loadCodes(env);
  return t.codes.find(c => c.code === code) || null;
}
async function getIndex(env) {
  const t = await loadCodes(env);
  return t.codes.map(c => c.code);
}

// ---- GitHub Contents API（把码表写回仓库，实现零 KV 持久化）----
async function ghRead(env) {
  if (!env.GITHUB_TOKEN || !env.GITHUB_REPO) return null;
  const url = `https://api.github.com/repos/${env.GITHUB_REPO}/contents/share/codes.json`;
  try {
    const r = await fetch(url, { headers: { Authorization: `Bearer ${env.GITHUB_TOKEN}`, Accept: "application/vnd.github+json", "User-Agent": "qs-agcl2" } });
    if (!r.ok) return null;
    const d = await r.json();
    return { data: JSON.parse(b64decode(d.content)), sha: d.sha };
  } catch { return null; }
}
async function ghWrite(env, content, sha, message) {
  if (!env.GITHUB_TOKEN || !env.GITHUB_REPO) return false;
  const url = `https://api.github.com/repos/${env.GITHUB_REPO}/contents/share/codes.json`;
  const body = JSON.stringify({ message, content: b64encode(JSON.stringify(content, null, 2)), sha });
  try {
    const r = await fetch(url, {
      method: "PUT",
      headers: { Authorization: `Bearer ${env.GITHUB_TOKEN}`, Accept: "application/vnd.github+json", "content-type": "application/json", "User-Agent": "qs-agcl2" },
      body,
    });
    return r.ok;
  } catch { return false; }
}

// 把内存缓存里的某个码替换为 latest（立即生效）
function patchCache(code, user) {
  if (!codeCache) return;
  const i = codeCache.codes.findIndex(c => c.code === code);
  if (i >= 0) codeCache.codes[i] = user; else if (user) codeCache.codes.push(user);
  codeCacheAt = Date.now();
}

function isBlocked(user, code) {
  if (user && user.status === "blocked") return true;
  const st = accessState.get(code);
  return !!(st && st.softBlocked);
}
function isAnomaly(code) {
  const st = accessState.get(code);
  return !!(st && (st.softAnomaly || st.softBlocked));
}

// 记录访问 + IP 异常检测（内存态，失败不阻塞访问）
async function logAccess(env, code, request, path) {
  let user = await getUser(env, code);
  if (!user) return null;
  let st = accessState.get(code) || { ips24h: [], totalAccess: 0, recentLogs: [] };
  try {
    const ip = getClientIP(request);
    const ua = (request.headers.get("user-agent") || "").substring(0, 120);
    const ts = nowISO();
    const cutoff = Date.now() - 86400000;
    // 清理 24h 以外的 IP 记录
    st.ips24h = (st.ips24h || []).filter(e => new Date(e.ts).getTime() > cutoff);
    const hit = st.ips24h.find(e => e.ip === ip);
    if (hit) { hit.ts = ts; hit.count = (hit.count || 1) + 1; }
    else st.ips24h.push({ ip, ts, count: 1 });
    // 异常判定（手动 blocked 不自动降级）
    const n = st.ips24h.length;
    if (user.status !== "blocked") {
      if (n > BLOCK_IPS) st.softBlocked = true;
      else if (n > ANOMALY_IPS) st.softAnomaly = true;
      else st.softAnomaly = false;
    }
    st.totalAccess = (st.totalAccess || 0) + 1;
    st.lastAccess = ts;
    st.lastIP = ip;
    st.recentLogs = [...(st.recentLogs || []), { ts, ip, ua: ua.substring(0, 80), path }].slice(-MAX_LOGS);
    accessState.set(code, st);
  } catch (e) { /* 记录失败不阻塞访问 */ }
  return user;
}

// ---------- 页面模板 ----------
const LOGIN_HTML = `<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="robots" content="noindex,nofollow">
<title>秦哥影视资源</title>
<style>
  *{box-sizing:border-box;margin:0;padding:0}
  body{min-height:100vh;display:flex;align-items:center;justify-content:center;
    background:#101318;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
  .box{width:92%;max-width:340px;padding:32px 28px;background:#1a1e24;border-radius:16px;
    box-shadow:0 8px 32px rgba(0,0,0,.4)}
  .t{font-size:20px;font-weight:700;color:#e8eaed;text-align:center;margin-bottom:4px}
  .sub{font-size:13px;color:#9aa0a6;text-align:center;margin-bottom:24px}
  input{width:100%;height:48px;border:1px solid #2a2f37;border-radius:10px;
    background:#101318;color:#e8eaed;font-size:20px;letter-spacing:4px;
    text-align:center;outline:none;padding:0 12px;text-transform:uppercase}
  input:focus{border-color:#ff2d55}
  button{width:100%;height:48px;margin-top:14px;border:none;border-radius:10px;
    background:#ff2d55;color:#fff;font-size:16px;font-weight:600;cursor:pointer}
  button:active{opacity:.85}
  .err{color:#ff6160;font-size:13px;text-align:center;margin-top:12px;min-height:18px}
  .foot{margin-top:18px;padding-top:14px;border-top:1px solid #22262e;
    font-size:11px;color:#6b7280;text-align:center;line-height:1.7}
</style></head><body>
<form class="box" method="POST" action="/__login" autocomplete="off">
  <div class="t">秦哥影视资源</div>
  <div class="sub">请输入你的专属邀请码</div>
  <input name="code" type="text" maxlength="6" autofocus required placeholder="邀请码">
  <button type="submit">进入</button>
  <div class="err" id="e"><!--ERR--></div>
  <div class="foot">邀请码仅限本人使用 · 严禁转发分享<br>多 IP 访问将被自动封禁</div>
</form></body></html>`;

const BLOCKED_HTML = `<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow">
<title>访问受限</title>
<style>
  *{box-sizing:border-box;margin:0;padding:0}
  body{min-height:100vh;display:flex;align-items:center;justify-content:center;
    background:#101318;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
  .box{width:88%;max-width:340px;padding:36px 28px;background:#1a1e24;border-radius:16px;text-align:center}
  .icon{font-size:44px;margin-bottom:12px}
  .t{font-size:18px;font-weight:700;color:#e8eaed;margin-bottom:8px}
  .d{font-size:13px;color:#9aa0a6;line-height:1.7}
</style></head><body>
<div class="box">
  <div class="icon">🔒</div>
  <div class="t">邀请码已被停用</div>
  <div class="d">你的邀请码检测到异常使用（短时间多个不同网络访问），已自动停用保护。<br>请联系分享者重新获取。</div>
</div></body></html>`;

const ADMIN_LOGIN_HTML = `<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>管理面板</title>
<style>
  *{box-sizing:border-box;margin:0;padding:0}
  body{min-height:100vh;display:flex;align-items:center;justify-content:center;
    background:#101318;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
  .box{width:92%;max-width:340px;padding:32px 28px;background:#1a1e24;border-radius:16px}
  .t{font-size:20px;font-weight:700;color:#e8eaed;text-align:center;margin-bottom:24px}
  input{width:100%;height:48px;border:1px solid #2a2f37;border-radius:10px;
    background:#101318;color:#e8eaed;font-size:18px;letter-spacing:4px;
    text-align:center;outline:none;padding:0 12px}
  input:focus{border-color:#ff2d55}
  button{width:100%;height:48px;margin-top:14px;border:none;border-radius:10px;
    background:#ff2d55;color:#fff;font-size:16px;font-weight:600;cursor:pointer}
  .err{color:#ff6160;font-size:13px;text-align:center;margin-top:12px;min-height:18px}
</style></head><body>
<form class="box" method="POST" action="/__admin/api/login" autocomplete="off">
  <div class="t">管理面板登录</div>
  <input name="password" type="password" maxlength="20" autofocus required placeholder="管理口令">
  <button type="submit">登录</button>
  <div class="err"><!--ERR--></div>
</form></body></html>`;

// 管理面板（单页应用，数据走 API）
const ADMIN_HTML = `<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>分享管理面板</title>
<style>
  *{box-sizing:border-box;margin:0;padding:0}
  body{background:#101318;color:#e8eaed;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;padding:16px;max-width:1100px;margin:0 auto}
  h1{font-size:20px;margin-bottom:4px}
  .sub{color:#9aa0a6;font-size:13px;margin-bottom:20px}
  .stats{display:flex;gap:10px;margin-bottom:20px;flex-wrap:wrap}
  .stat{background:#1a1e24;border-radius:10px;padding:12px 18px;min-width:100px}
  .stat .n{font-size:24px;font-weight:700;color:#ff2d55}
  .stat .l{font-size:12px;color:#9aa0a6;margin-top:2px}
  .addbar{display:flex;gap:10px;margin-bottom:20px}
  .addbar input{flex:1;height:42px;border:1px solid #2a2f37;border-radius:10px;background:#1a1e24;color:#e8eaed;padding:0 14px;outline:none}
  .addbar input:focus{border-color:#ff2d55}
  .addbar button{height:42px;padding:0 22px;border:none;border-radius:10px;background:#ff2d55;color:#fff;font-size:14px;font-weight:600;cursor:pointer}
  table{width:100%;border-collapse:collapse;font-size:13px}
  th{text-align:left;color:#9aa0a6;font-weight:600;padding:10px 8px;border-bottom:1px solid #2a2f37;white-space:nowrap}
  td{padding:10px 8px;border-bottom:1px solid #22262e;vertical-align:top}
  .badge{display:inline-block;padding:2px 8px;border-radius:6px;font-size:11px;font-weight:700}
  .b-active{background:#0f3820;color:#4ade80}
  .b-anomaly{background:#3a2a0a;color:#fbbf24}
  .b-blocked{background:#3a1518;color:#ff6160}
  .code{font-family:monospace;font-size:15px;font-weight:700;color:#7dd3fc;letter-spacing:2px}
  .btn{border:none;border-radius:8px;padding:5px 10px;font-size:12px;cursor:pointer;margin:2px}
  .btn-sm{background:#2a2f37;color:#e8eaed}
  .btn-danger{background:#3a1518;color:#ff6160}
  .btn-warn{background:#3a2a0a;color:#fbbf24}
  .ips{font-size:11px;color:#9aa0a6}
  .actions{white-space:nowrap}
  .modal{position:fixed;inset:0;background:rgba(0,0,0,.7);display:none;align-items:center;justify-content:center;z-index:99}
  .modal .card{background:#1a1e24;border-radius:16px;padding:24px;width:92%;max-width:380px;max-height:88vh;overflow:auto}
  .modal h3{font-size:16px;margin-bottom:12px}
  .modal img{display:block;margin:12px auto;border-radius:12px}
  .linkbox{background:#101318;border-radius:10px;padding:10px;margin-top:8px;font-size:12px;word-break:break-all}
  .linkbox .lb-t{color:#9aa0a6;margin-bottom:4px}
  .linkbox code{color:#7dd3fc;font-family:monospace}
  .copy{margin-top:8px;width:100%;height:38px;border:none;border-radius:8px;background:#2a2f37;color:#e8eaed;font-size:13px;cursor:pointer}
  .empty{color:#9aa0a6;text-align:center;padding:40px 0;font-size:14px}
  .logout{float:right;background:none;border:1px solid #2a2f37;color:#9aa0a6;border-radius:8px;padding:6px 14px;font-size:12px;cursor:pointer;text-decoration:none}
</style></head><body>
<h1>分享管理面板 <a class="logout" href="/__admin/api/logout">退出</a></h1>
<div class="sub">一人一码 · 超过 ${ANOMALY_IPS} 个 IP(24h) 标异常 · 超过 ${BLOCK_IPS} 个自动封禁</div>
<div class="stats" id="stats"></div>
<div class="addbar">
  <input id="newName" placeholder="朋友名字（如：张三）" maxlength="12">
  <button onclick="addUser()">生成邀请码</button>
</div>
<table>
  <thead><tr>
    <th>名字</th><th>邀请码</th><th>状态</th><th>访问次数</th>
    <th>24h IP数</th><th>最近访问</th><th>操作</th>
  </tr></thead>
  <tbody id="tbody"></tbody>
</table>
<div class="empty" id="empty" style="display:none">还没有添加任何人，输入名字生成第一个邀请码</div>

<div class="modal" id="modal" onclick="if(event.target===this)closeModal()">
  <div class="card" id="modalCard"></div>
</div>

<script>
const $ = id => document.getElementById(id);
const SITE = location.origin;

function fmtTime(iso) {
  if (!iso) return '-';
  const d = new Date(iso);
  const pad = n => String(n).padStart(2, '0');
  return pad(d.getMonth()+1)+'-'+pad(d.getDate())+' '+pad(d.getHours())+':'+pad(d.getMinutes());
}

async function loadUsers() {
  const r = await fetch('/__admin/api/users');
  if (r.status === 401) { location.reload(); return; }
  const data = await r.json();
  const users = data.users || [];
  const active = users.filter(u => u.status === 'active').length;
  const anomaly = users.filter(u => u.status === 'anomaly').length;
  const blocked = users.filter(u => u.status === 'blocked').length;
  const today = users.filter(u => u.lastAccess && new Date(u.lastAccess).toDateString() === new Date().toDateString()).length;
  $('stats').innerHTML =
    stat(users.length, '总人数') + stat(active, '正常') + stat(anomaly, '异常') +
    stat(blocked, '已封禁') + stat(today, '今日活跃');
  const tb = $('tbody');
  $('empty').style.display = users.length ? 'none' : 'block';
  tb.innerHTML = users.map(u => {
    const badge = u.status === 'active' ? '<span class="badge b-active">正常</span>'
      : u.status === 'anomaly' ? '<span class="badge b-anomaly">异常</span>'
      : '<span class="badge b-blocked">已封禁</span>';
    const ips = (u.ips24h || []).map(e => e.ip).join('<br>');
    return '<tr>' +
      '<td><b>' + esc(u.name) + '</b></td>' +
      '<td><span class="code">' + u.code + '</span></td>' +
      '<td>' + badge + '</td>' +
      '<td>' + (u.totalAccess || 0) + '</td>' +
      '<td class="ips">' + (u.ips24h || []).length + ' 个<br>' + ips + '</td>' +
      '<td class="ips">' + fmtTime(u.lastAccess) + '</td>' +
      '<td class="actions">' +
        '<button class="btn btn-sm" onclick="showCode(\\'' + u.code + '\\')">二维码</button>' +
        (u.status === 'blocked'
          ? '<button class="btn btn-warn" onclick="blockUser(\\'' + u.code + '\\', false)">解封</button>'
          : '<button class="btn btn-danger" onclick="blockUser(\\'' + u.code + '\\', true)">封禁</button>') +
        '<button class="btn btn-sm" onclick="resetIPs(\\'' + u.code + '\\')">重置IP</button>' +
        '<button class="btn btn-danger" onclick="delUser(\\'' + u.code + '\\')">删除</button>' +
      '</td></tr>';
  }).join('');
}

function stat(n, label) {
  return '<div class="stat"><div class="n">' + n + '</div><div class="l">' + label + '</div></div>';
}

function esc(s) {
  return String(s || '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
}

async function addUser() {
  const name = $('newName').value.trim();
  if (!name) { $('newName').focus(); return; }
  const r = await fetch('/__admin/api/add', {
    method: 'POST',
    headers: {'content-type': 'application/json'},
    body: JSON.stringify({ name })
  });
  const data = await r.json();
  if (data.code) {
    $('newName').value = '';
    await loadUsers();
    showCode(data.code);
  }
}

async function blockUser(code, blocked) {
  if (blocked && !confirm('确定封禁该邀请码？此人将立即无法访问')) return;
  await fetch('/__admin/api/block', {
    method: 'POST',
    headers: {'content-type': 'application/json'},
    body: JSON.stringify({ code, blocked })
  });
  loadUsers();
}

async function resetIPs(code) {
  if (!confirm('清空该码的 IP 记录？异常状态将恢复正常')) return;
  await fetch('/__admin/api/reset', {
    method: 'POST',
    headers: {'content-type': 'application/json'},
    body: JSON.stringify({ code })
  });
  loadUsers();
}

async function delUser(code) {
  if (!confirm('确定删除？该邀请码立即失效，访问记录一并清除')) return;
  await fetch('/__admin/api/delete', {
    method: 'POST',
    headers: {'content-type': 'application/json'},
    body: JSON.stringify({ code })
  });
  loadUsers();
}

function showCode(code) {
  const qrUrl = 'https://api.qrserver.com/v1/create-qr-code/?size=220x220&margin=8&data=' + encodeURIComponent(SITE + '/?pwd=' + code);
  const m3u = f => SITE + '/' + f + '.m3u?key=' + code;
  $('modalCard').innerHTML =
    '<h3>邀请码 <span class="code">' + code + '</span></h3>' +
    '<div style="color:#9aa0a6;font-size:12px">扫码直接进入，无需输入邀请码</div>' +
    '<img src="' + qrUrl + '" width="220" height="220" alt="二维码">' +
    '<div class="linkbox"><div class="lb-t">网页链接（扫码或复制到浏览器）</div><code>' + SITE + '/?pwd=' + code + '</code></div>' +
    '<div class="linkbox"><div class="lb-t">播放器 M3U 地址（带专属码）</div>' +
      '<code>' + m3u('movie') + '</code><br><code>' + m3u('tv') + '</code><br>' +
      '<code>' + m3u('anime') + '</code><br><code>' + m3u('variety') + '</code><br>' +
      '<code>' + m3u('live') + '</code><br>' +
      '<div class="lb-t" style="margin-top:6px">单条最优版（同名只留一条最快线路）</div>' +
      '<code>' + m3u('movie.best') + '</code> 等</div>' +
    '<button class="copy" onclick="copyAll(\\'' + code + '\\')">复制全部链接</button>' +
    '<button class="copy" onclick="closeModal()">关闭</button>';
  $('modal').style.display = 'flex';
}

function copyAll(code) {
  const SITE2 = location.origin;
  const text =
    '秦哥影视资源\\n' +
    '网页（扫码/点击进入）：' + SITE2 + '/?pwd=' + code + '\\n' +
    '播放器 M3U 源地址：\\n' +
    '电影：' + SITE2 + '/movie.m3u?key=' + code + '\\n' +
    '剧集：' + SITE2 + '/tv.m3u?key=' + code + '\\n' +
    '动漫：' + SITE2 + '/anime.m3u?key=' + code + '\\n' +
    '综艺：' + SITE2 + '/variety.m3u?key=' + code + '\\n' +
    '直播：' + SITE2 + '/live.m3u?key=' + code + '\\n' +
    '（单条最优版：movie.best.m3u / tv.best.m3u 等同理）';
  navigator.clipboard.writeText(text).then(() => {
    alert('已复制，直接粘贴发给朋友');
  });
}

function closeModal() { $('modal').style.display = 'none'; }

loadUsers();
</script></body></html>`;

// ---------- 首次访问弹窗（注入到首页 HTML）----------
function escHtml(s) {
  return String(s || "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}
function popupHTML(code, user) {
  const ips = (user.ips24h || []).length;
  const isAnomaly = user.status === "anomaly";
  const banner = isAnomaly
    ? `<div class="qg-pn-warn">警告：你的邀请码 24 小时内已在 <b>${ips}</b> 个不同 IP 上使用，已被标记为异常。请立即停止分享，再扩散将自动封禁。</div>`
    : "";
  return `
<style>
#qg-pn-ov{position:fixed;inset:0;z-index:99998;background:rgba(0,0,0,.62);
  display:flex;align-items:center;justify-content:center;padding:24px}
#qg-pn-ov.qg-hide{display:none}
#qg-pn-box{width:100%;max-width:400px;background:#1f232a;border:1px solid #333945;
  border-radius:16px;padding:26px 24px 20px;text-align:center;animation:qg-pop .28s ease}
@keyframes qg-pop{from{transform:scale(.92);opacity:0}to{transform:scale(1);opacity:1}}
#qg-pn-box.qg-warnbox{border-color:#8a6a2a}
.qg-pn-logo{width:52px;height:52px;margin:0 auto 14px;border-radius:14px;background:#C4502E;
  color:#fff;font-size:20px;font-weight:700;display:flex;align-items:center;justify-content:center}
.qg-warnbox .qg-pn-logo{background:#b8862b}
.qg-pn-t{font-size:18px;font-weight:700;color:#f2f2f2;margin-bottom:4px}
.qg-pn-s{font-size:12px;color:#8a909a;margin-bottom:18px}
.qg-pn-code{background:#14171c;border:1px dashed #4a5160;border-radius:10px;
  padding:12px 10px;margin-bottom:16px}
.qg-pn-code .l{font-size:12px;color:#9aa0aa;margin-bottom:6px}
.qg-pn-code .c{font-size:26px;font-weight:700;letter-spacing:6px;color:#e8b56a;
  font-family:Consolas,monospace}
.qg-pn-rules{text-align:left;background:#181b20;border-radius:10px;padding:12px 14px;
  margin-bottom:16px;list-style:none}
.qg-pn-rules li{font-size:13px;color:#c3c8d0;line-height:1.9;display:flex;gap:8px}
.qg-pn-rules li::before{content:"·";color:#C4502E;font-weight:700}
.qg-pn-rules b{color:#e88a6a;font-weight:600}
.qg-pn-warn{background:rgba(232,181,106,.12);border:1px solid rgba(232,181,106,.4);
  color:#e8b56a;font-size:13px;border-radius:10px;padding:10px 12px;margin-bottom:14px;
  line-height:1.6;text-align:left}
.qg-pn-ip{display:flex;justify-content:space-between;align-items:center;font-size:12px;
  color:#9aa0aa;padding:0 4px 16px}
.qg-pn-ip .v{color:#7fc79a;font-weight:700}
.qg-warnbox .qg-pn-ip .v{color:#e8b56a}
#qg-pn-ok{width:100%;padding:13px 0;border:none;border-radius:10px;background:#C4502E;
  color:#fff;font-size:15px;font-weight:600;cursor:pointer}
#qg-pn-ok:active{transform:translateY(1px)}
</style>
<div id="qg-pn-ov">
  <div id="qg-pn-box"${isAnomaly ? ' class="qg-warnbox"' : ""}>
    <div class="qg-pn-logo">秦</div>
    <div class="qg-pn-t">秦哥影视资源</div>
    <div class="qg-pn-s">专属邀请码 · 仅限本人使用</div>
    ${banner}
    <div class="qg-pn-code">
      <div class="l">你的专属邀请码（绑定 ${escHtml(user.name)}）</div>
      <div class="c">${escHtml(code)}</div>
    </div>
    <ul class="qg-pn-rules">
      <li>本邀请码<b>仅限本人使用，严禁转发</b>给他人</li>
      <li>系统实时监测访问 IP，超过 <b>${ANOMALY_IPS} 个 IP</b> 标记异常</li>
      <li>超过 <b>${BLOCK_IPS} 个 IP</b> 将<b>自动封禁</b>，无法访问</li>
      <li>封禁后需联系分享者人工解封</li>
    </ul>
    <div class="qg-pn-ip">
      <span>当前 24h 已监测到</span>
      <span class="v">${ips} / ${ANOMALY_IPS} 个 IP</span>
    </div>
    <button id="qg-pn-ok">我已知晓</button>
  </div>
</div>
<script>
(function(){
  var ov=document.getElementById('qg-pn-ov');
  document.getElementById('qg-pn-ok').addEventListener('click',function(){
    document.cookie='${NOTE_COOKIE}=1; Path=/; Max-Age=${MAX_AGE}; Secure; SameSite=Lax';
    ov.parentNode&&ov.parentNode.removeChild(ov);
  });
})();
</script>`;
}

// ---------- 静态资源 ----------
function contentTypeFor(path) {
  if (path.endsWith(".m3u") || path.endsWith(".m3u8")) return "application/vnd.apple.mpegurl";
  if (path.endsWith(".txt")) return "text/plain; charset=utf-8";
  if (path.endsWith(".html")) return "text/html; charset=utf-8";
  return null;
}
function wrapAsset(r, path, setCookie) {
  const res = new Response(r.body, r);
  res.headers.set("X-Robots-Tag", "noindex");
  const ct = contentTypeFor(path);
  if (ct) res.headers.set("Content-Type", ct);
  if (/\.(m3u|txt|html)$/.test(path)) res.headers.set("Cache-Control", "max-age=0, must-revalidate");
  if (setCookie) res.headers.set("Set-Cookie", setCookie);
  return res;
}

// ---------- 主处理器 ----------
export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const path = url.pathname;
    SELF_ORIGIN = url.origin;

    // ===== 管理面板路由 =====
    if (path === "/__admin") {
      if (getCookie(request, ADMIN_COOKIE) === "1") {
        return new Response(ADMIN_HTML, { headers: { "content-type": "text/html; charset=utf-8", "cache-control": "no-store" } });
      }
      return new Response(ADMIN_LOGIN_HTML.replace("<!--ERR-->", ""), { headers: { "content-type": "text/html; charset=utf-8", "cache-control": "no-store" } });
    }
    if (path === "/__admin/api/login" && request.method === "POST") {
      const form = await request.formData();
      const pwd = (form.get("password") || "").toString();
      if (pwd === ADMIN_PASSWORD) {
        return new Response(null, {
          status: 302,
          headers: { Location: "/__admin", "Set-Cookie": `${ADMIN_COOKIE}=1; Path=/__admin; Max-Age=${MAX_AGE}; Secure; HttpOnly; SameSite=Lax` },
        });
      }
      return new Response(ADMIN_LOGIN_HTML.replace("<!--ERR-->", "口令错误"), {
        status: 401,
        headers: { "content-type": "text/html; charset=utf-8", "cache-control": "no-store" },
      });
    }
    if (path === "/__admin/api/logout") {
      return new Response(null, { status: 302, headers: { Location: "/__admin", "Set-Cookie": `${ADMIN_COOKIE}=; Path=/__admin; Max-Age=0` } });
    }
    // 迁移导出：返回当前码表（codes.json 或 KV 回退），供粘贴进 codes.json
    if (path === "/__admin/api/export" && request.method === "GET") {
      const t = await loadCodes(env);
      return json({ codes: t.codes || [] });
    }
    // 管理 API（需管理员 Cookie）
    if (path.startsWith("/__admin/api/")) {
      if (getCookie(request, ADMIN_COOKIE) !== "1") return json({ error: "unauthorized" }, 401);

      if (path === "/__admin/api/users" && request.method === "GET") {
        const idx = await getIndex(env);
        const users = [];
        for (const code of idx) {
          const u = await getUser(env, code);
          if (!u) continue;
          const st = accessState.get(code);
          users.push({
            code, name: u.name, status: u.status,
            totalAccess: st ? (st.totalAccess || 0) : (u.totalAccess || 0),
            ips24h: st ? (st.ips24h || []) : (u.ips24h || []),
            lastAccess: st ? (st.lastAccess || u.lastAccess) : u.lastAccess,
            created: u.created,
          });
        }
        users.sort((a, b) => new Date(b.created || 0) - new Date(a.created || 0));
        return json({ users });
      }
      if (path === "/__admin/api/add" && request.method === "POST") {
        const body = await request.json();
        const name = (body.name || "").toString().trim().substring(0, 12);
        if (!name) return json({ error: "名字不能为空" }, 400);
        let code = generateCode();
        let idx = await getIndex(env);
        while (idx.includes(code)) code = generateCode(); // 防撞码
        const user = {
          name, code, status: "active",
          created: nowISO(), totalAccess: 0,
          ips24h: [], recentLogs: [],
        };
        const gh = await ghRead(env);
        let persisted = false;
        if (gh) {
          const t = gh.data && gh.data.codes ? gh.data : { codes: [] };
          t.codes.push(user);
          persisted = await ghWrite(env, t, gh.sha, `share: add code ${code} (${name})`);
        }
        // 立即生效（内存）
        codeCache = codeCache && codeCache.codes ? codeCache : { codes: [] };
        codeCache.codes.push(user);
        codeCacheAt = Date.now();
        return json({ code, name, persisted });
      }
      if (path === "/__admin/api/delete" && request.method === "POST") {
        const { code } = await request.json();
        const idx = await getIndex(env);
        if (!idx.includes(code)) return json({ error: "not found" }, 404);
        const gh = await ghRead(env);
        if (gh) {
          const t = gh.data && gh.data.codes ? gh.data : { codes: [] };
          t.codes = t.codes.filter(c => c.code !== code);
          await ghWrite(env, t, gh.sha, `share: delete code ${code}`);
        }
        if (codeCache && codeCache.codes) codeCache.codes = codeCache.codes.filter(c => c.code !== code);
        codeCacheAt = Date.now();
        accessState.delete(code);
        return json({ ok: true });
      }
      if (path === "/__admin/api/block" && request.method === "POST") {
        const { code, blocked } = await request.json();
        const u = await getUser(env, code);
        if (!u) return json({ error: "not found" }, 404);
        u.status = blocked ? "blocked" : "active";
        if (!blocked) { u.ips24h = []; accessState.delete(code); }
        const gh = await ghRead(env);
        if (gh) {
          const t = gh.data && gh.data.codes ? gh.data : { codes: [] };
          const i = t.codes.findIndex(c => c.code === code);
          if (i >= 0) { t.codes[i] = u; await ghWrite(env, t, gh.sha, `share: ${blocked ? "block" : "unblock"} ${code}`); }
        }
        patchCache(code, u);
        return json({ ok: true });
      }
      if (path === "/__admin/api/reset" && request.method === "POST") {
        const { code } = await request.json();
        const u = await getUser(env, code);
        if (!u) return json({ error: "not found" }, 404);
        u.ips24h = [];
        u.status = "active";
        accessState.delete(code);
        const gh = await ghRead(env);
        if (gh) {
          const t = gh.data && gh.data.codes ? gh.data : { codes: [] };
          const i = t.codes.findIndex(c => c.code === code);
          if (i >= 0) { t.codes[i] = u; await ghWrite(env, t, gh.sha, `share: reset ${code}`); }
        }
        patchCache(code, u);
        return json({ ok: true });
      }
      return json({ error: "not found" }, 404);
    }

    // ===== 用户登录（扫码 ?pwd= 或表单 POST /__login）=====
    const pwdParam = url.searchParams.get("pwd");
    if (pwdParam) {
      const code = pwdParam.toUpperCase();
      const user = await getUser(env, code);
      if (!user) {
        return new Response(LOGIN_HTML.replace("<!--ERR-->", "邀请码无效"), {
          status: 401,
          headers: { "content-type": "text/html; charset=utf-8", "cache-control": "no-store" },
        });
      }
      if (isBlocked(user, code)) {
        return new Response(BLOCKED_HTML, { status: 403, headers: { "content-type": "text/html; charset=utf-8", "cache-control": "no-store" } });
      }
      await logAccess(env, code, request, "/");
      return new Response(null, {
        status: 302,
        headers: { Location: "/", "Set-Cookie": cookieStr(COOKIE, code) },
      });
    }

    if (path === "/__login" && request.method === "POST") {
      const form = await request.formData();
      const code = ((form.get("code") || form.get("password") || "").toString()).toUpperCase().trim();
      const user = await getUser(env, code);
      if (!user) {
        return new Response(LOGIN_HTML.replace("<!--ERR-->", "邀请码无效"), {
          status: 401,
          headers: { "content-type": "text/html; charset=utf-8", "cache-control": "no-store" },
        });
      }
      if (isBlocked(user, code)) {
        return new Response(BLOCKED_HTML, { status: 403, headers: { "content-type": "text/html; charset=utf-8", "cache-control": "no-store" } });
      }
      await logAccess(env, code, request, "/");
      return new Response(null, {
        status: 302,
        headers: { Location: "/", "Set-Cookie": cookieStr(COOKIE, code) },
      });
    }

    // ===== M3U / TXT 播放器访问（?key= 专属码）=====
    const keyParam = url.searchParams.get("key");
    if (keyParam) {
      const code = keyParam.toUpperCase();
      const user = await getUser(env, code);
      if (!user || isBlocked(user, code)) return new Response("Forbidden", { status: 403 });
      await logAccess(env, code, request, path);
      const r = await env.ASSETS.fetch(new Request(url.origin + path, request));
      return wrapAsset(r, path, cookieStr(COOKIE, code));
    }

    // ===== Cookie 会话访问 =====
    const cookieCode = getCookie(request, COOKIE);
    if (cookieCode) {
      let user = await getUser(env, cookieCode);
      if (user && !isBlocked(user, cookieCode)) {
        const isPage = path === "/" || path === "/index.html";
        // 网页访问也记录 IP（此前仅登录/M3U 请求记录）；用更新后的数据渲染弹窗
        if (isPage) user = (await logAccess(env, cookieCode, request, path)) || user;
        const r = await env.ASSETS.fetch(request);
        // 首次访问（无 qgnote）或异常状态 → 注入弹窗
        const needPopup = getCookie(request, NOTE_COOKIE) !== "1" || isAnomaly(cookieCode) || user.status === "anomaly";
        const isHtml = (r.headers.get("content-type") || "").includes("text/html");
        if (isPage && needPopup && isHtml) {
          let html = await r.text();
          const idx = html.toLowerCase().lastIndexOf("</body>");
          if (idx >= 0) html = html.slice(0, idx) + popupHTML(cookieCode, user) + html.slice(idx);
          else html += popupHTML(cookieCode, user);
          const res = new Response(html, r);
          res.headers.set("cache-control", "no-store");
          res.headers.set("X-Robots-Tag", "noindex");
          return res;
        }
        return wrapAsset(r, path, null);
      }
    }

    // ===== 未登录 → 登录页 / 403 =====
    if (path === "/" || path === "/index.html") {
      return new Response(LOGIN_HTML.replace("<!--ERR-->", ""), {
        headers: { "content-type": "text/html; charset=utf-8", "cache-control": "no-store" },
      });
    }
    return new Response("Forbidden", { status: 403 });
  },
};
