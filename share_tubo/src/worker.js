// 途播时效分享层 (Cloudflare Workers) · 秦哥影视
// ---------------------------------------------------------------
// 用途：把 m3u-jellyfin（真实 Jellyfin 后端）包装成「带时效的分享码」给
// 途播用户，且**不暴露真实后端地址**。
//
// 架构（零 KV）：
//   分享码 = AES-GCM 加密的 JSON {t:真实后端, e:过期时间戳, n:随机id}
//   密钥存在 Worker Secret（SHARE_KEY），接收方拿到码也无法解出真实地址。
//   Worker 收到 /j/{code}/* 请求时解出目标 → 校验时效 → 流式反代。
//   过期信息编在码内，无需任何存储；码无法篡改（GCM 认证）。
//
// 路由：
//   GET  /admin            管理页（口令 + 选时长 → 生成分享码）
//   POST /admin/generate   {pass, days, target?} → {code, url, expire_at}
//   ANY  /j/{code}/**      反代（含 WebSocket / Range / 流式视频）
//
// 安全边界：
//   - /admin/generate 仅口令（ADMIN_PASS secret）持有者可用
//   - 分享码只含目标+过期时间，无任何库数据；失效靠时效（无吊销，零 KV 取舍）

const DEFAULT_TARGET = "https://m3u-jellyfin.a313341127.workers.dev";
const ALLOWED_DAYS = [1, 3, 7, 30, 90, 365];

// ---------- base64url ----------
function b64uEnc(buf) {
  const b = new Uint8Array(buf);
  let s = "";
  for (let i = 0; i < b.length; i++) s += String.fromCharCode(b[i]);
  return btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}
function b64uDec(str) {
  str = str.replace(/-/g, "+").replace(/_/g, "/");
  while (str.length % 4) str += "=";
  const bin = atob(str);
  const b = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) b[i] = bin.charCodeAt(i);
  return b;
}

// ---------- 码：AES-GCM 加解密 ----------
async function getKey(env) {
  return crypto.subtle.importKey("raw", b64uDec(env.SHARE_KEY), "AES-GCM", false,
    ["encrypt", "decrypt"]);
}

async function mintCode(env, days, target) {
  const key = await getKey(env);
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const payload = {
    t: target,
    e: Math.floor(Date.now() / 1000) + days * 86400,
    n: b64uEnc(crypto.getRandomValues(new Uint8Array(6))),
  };
  const ct = await crypto.subtle.encrypt({ name: "AES-GCM", iv }, key,
    new TextEncoder().encode(JSON.stringify(payload)));
  return "v1." + b64uEnc(iv) + "." + b64uEnc(ct);
}

// 返回 {t,e} 或 {expired:true} 或 null（无效）
async function openCode(env, code) {
  const parts = code.split(".");
  if (parts.length !== 3 || parts[0] !== "v1") return null;
  try {
    const key = await getKey(env);
    const pt = await crypto.subtle.decrypt(
      { name: "AES-GCM", iv: b64uDec(parts[1]) }, key, b64uDec(parts[2]));
    const info = JSON.parse(new TextDecoder().decode(pt));
    if (!info.t || !info.e) return null;
    if (Math.floor(Date.now() / 1000) > info.e) return { expired: true };
    return info;
  } catch (_) {
    return null;
  }
}

// ---------- 反代 ----------
const HOP_BY_HOP = new Set([
  "host", "connection", "keep-alive", "proxy-authenticate",
  "proxy-authorization", "te", "trailer", "transfer-encoding", "upgrade",
]);

function cleanHeaders(h) {
  const out = new Headers();
  for (const [k, v] of h.entries()) {
    if (!HOP_BY_HOP.has(k.toLowerCase()) && !k.toLowerCase().startsWith("cf-")) {
      out.set(k, v);
    }
  }
  return out;
}

async function handleProxy(request, env, code, subpath, search) {
  const info = await openCode(env, code);
  if (!info) return text(403, "分享码无效");
  if (info.expired) return text(403, "分享码已过期，请联系分享者重新生成");

  const target = info.t.replace(/\/+$/, "") + subpath + search;

  // WebSocket（Jellyfin /embywebsocket 会话通道）：作为客户端连上游，双向桥接
  if ((request.headers.get("Upgrade") || "").toLowerCase() === "websocket") {
    let up;
    try {
      up = await fetch(target, { headers: cleanHeaders(request.headers) });
    } catch (_) {
      return text(502, "upstream websocket error");
    }
    if (!up.webSocket) return text(502, "upstream websocket refused");
    up.webSocket.accept();
    const pair = new WebSocketPair();
    pair[1].accept();
    const bridge = (from, to) => {
      from.addEventListener("message", (e) => {
        try { to.send(e.data); } catch (_) {}
      });
      const bye = () => { try { to.close(1000); } catch (_) {} };
      from.addEventListener("close", bye);
      from.addEventListener("error", bye);
    };
    bridge(pair[1], up.webSocket);
    bridge(up.webSocket, pair[1]);
    return new Response(null, { status: 101, webSocket: pair[0] });
  }

  // 普通 HTTP：流式透传（视频分片不在内存缓冲）
  const body = (request.method === "GET" || request.method === "HEAD")
    ? undefined : request.body;
  let resp;
  try {
    resp = await fetch(target, {
      method: request.method, headers: cleanHeaders(request.headers),
      body, redirect: "follow",
    });
  } catch (e) {
    return text(502, "upstream error");
  }
  return new Response(resp.body, {
    status: resp.status, statusText: resp.statusText, headers: cleanHeaders(resp.headers),
  });
}

// ---------- 管理页 ----------
function adminPage() {
  return new Response(ADMIN_HTML, {
    headers: { "Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store" },
  });
}

async function handleGenerate(request, env) {
  if (!env.ADMIN_PASS || !env.SHARE_KEY) return json(500, { error: "服务端密钥未配置" });
  let body;
  try { body = await request.json(); } catch (_) { return json(400, { error: "参数错误" }); }
  const { pass, days, target } = body || {};
  if (pass !== env.ADMIN_PASS) return json(403, { error: "管理口令不对" });
  if (!ALLOWED_DAYS.includes(days)) return json(400, { error: "时长不合法" });
  const t = typeof target === "string" && /^https:\/\//.test(target)
    ? target : DEFAULT_TARGET;
  const code = await mintCode(env, days, t);
  const expireAt = new Date((Math.floor(Date.now() / 1000) + days * 86400) * 1000)
    .toISOString().replace("T", " ").slice(0, 16) + " UTC";
  const origin = new URL(request.url).origin;
  return json(200, {
    code, expire_at: expireAt,
    url: `${origin}/j/${code}`,
    user: "tubo", pass_hint: "任意非空密码",
  });
}

// ---------- 小工具 ----------
function json(status, obj) {
  return new Response(JSON.stringify(obj), {
    status, headers: { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store" },
  });
}
function text(status, msg) {
  return new Response(msg, {
    status, headers: { "Content-Type": "text/plain; charset=utf-8", "Cache-Control": "no-store" },
  });
}

// ---------- 入口 ----------
export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const path = url.pathname;

    if (path === "/admin" && request.method === "GET") return adminPage();
    if (path === "/admin/generate" && request.method === "POST") return handleGenerate(request, env);

    const m = path.match(/^\/j\/([A-Za-z0-9_.-]+)(\/.*)?$/);
    if (m) return handleProxy(request, env, m[1], m[2] || "/", url.search);

    if (path === "/") return text(200, "tubo share alive");
    return text(404, "not found");
  },
};

const ADMIN_HTML = `<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>途播分享码生成</title>
<style>
  :root { --ink:#2b2b2b; --paper:#f7f4ec; --line:#ddd6c6; --accent:#1B4B6B; --red:#C4502E; }
  * { box-sizing:border-box; }
  body { margin:0; min-height:100vh; background:var(--paper); color:var(--ink);
         font:16px/1.7 "PingFang SC","Microsoft YaHei",sans-serif;
         display:flex; justify-content:center; padding:48px 16px; }
  .card { width:100%; max-width:460px; }
  h1 { font-size:22px; font-weight:600; letter-spacing:2px; margin:0 0 6px; }
  .sub { color:#8a8574; font-size:13px; margin-bottom:28px; }
  label { display:block; font-size:14px; color:#6d6858; margin:18px 0 6px; }
  input[type=text], input[type=password] { width:100%; padding:10px 12px; border:1px solid var(--line);
         border-radius:6px; background:#fff; font-size:15px; outline:none; }
  input:focus { border-color:var(--accent); }
  .days { display:flex; flex-wrap:wrap; gap:8px; }
  .days label { margin:0; }
  .days input { display:none; }
  .days span { display:inline-block; padding:7px 14px; border:1px solid var(--line);
         border-radius:999px; font-size:14px; background:#fff; cursor:pointer; }
  .days input:checked + span { background:var(--accent); color:#fff; border-color:var(--accent); }
  button { width:100%; margin-top:26px; padding:12px; border:0; border-radius:6px;
         background:var(--accent); color:#fff; font-size:16px; letter-spacing:2px; cursor:pointer; }
  button:active { opacity:.85; }
  .result { display:none; margin-top:26px; padding:16px; border:1px solid var(--line);
         border-radius:8px; background:#fff; }
  .result .row { margin:10px 0; font-size:14px; word-break:break-all; }
  .result .k { color:#8a8574; margin-right:8px; }
  .warn { margin-top:12px; font-size:13px; color:var(--red); }
  .ok { margin-top:16px; font-size:14px; color:#3a7d44; display:none; }
  .err { margin-top:14px; font-size:14px; color:var(--red); display:none; }
</style></head><body>
<div class="card">
  <h1>途播 · 分享码生成</h1>
  <div class="sub">生成带时效的临时服务器地址，真实后端地址不对外暴露</div>

  <label>管理口令</label>
  <input type="password" id="pass" placeholder="输入管理口令">

  <label>有效时长</label>
  <div class="days" id="days"></div>

  <label>目标后端（留空即默认，无需填写）</label>
  <input type="text" id="target" value="" placeholder="留空 = 默认后端（地址不落页面）">

  <button id="go">生 成 分 享 码</button>
  <div class="err" id="err"></div>

  <div class="result" id="result">
    <div class="row"><span class="k">有效至</span><span id="expire"></span></div>
    <div class="row"><span class="k">服务器地址</span><span id="url"></span></div>
    <div class="row"><span class="k">用户名</span>tubo</div>
    <div class="row"><span class="k">密码</span>任意非空</div>
    <button id="copy" style="background:#6d6858">复制配置</button>
    <div class="ok" id="copied">已复制，去途播里粘贴即可</div>
    <div class="warn">⚠️ 分享码含完整使用权，请只发给要给的人；到期自动失效。</div>
  </div>
</div>
<script>
  var DAYS = [1,3,7,30,90,365];
  var box = document.getElementById('days');
  DAYS.forEach(function(d, i) {
    var l = document.createElement('label');
    l.innerHTML = '<input type="radio" name="days" value="' + d + '"' + (i===2?' checked':'') +
                  '><span>' + (d===1?'1 天':d===365?'1 年':d+' 天') + '</span>';
    box.appendChild(l);
  });
  document.getElementById('go').onclick = function() {
    var err = document.getElementById('err');
    err.style.display = 'none';
    fetch('/admin/generate', { method:'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({
        pass: document.getElementById('pass').value,
        days: parseInt(document.querySelector('input[name=days]:checked').value, 10),
        target: document.getElementById('target').value.trim()
      })
    }).then(function(r){ return r.json(); }).then(function(d) {
      if (d.error) { err.textContent = d.error; err.style.display = 'block'; return; }
      document.getElementById('expire').textContent = d.expire_at;
      document.getElementById('url').textContent = d.url;
      document.getElementById('copied').style.display = 'none';
      document.getElementById('result').style.display = 'block';
      window.__CFG__ = '服务器地址：' + d.url + '\\n用户名：tubo\\n密码：任意非空';
    }).catch(function(e) { err.textContent = '网络错误：' + e; err.style.display = 'block'; });
  };
  document.getElementById('copy').onclick = function() {
    var self = this;
    navigator.clipboard.writeText(window.__CFG__ || '').then(function() {
      document.getElementById('copied').style.display = 'block';
      self.textContent = '已复制';
    }).catch(function() {});
  };
</script></body></html>`;
