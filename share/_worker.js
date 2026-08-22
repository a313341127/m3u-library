// 秦哥影视资源 - 分享站访问网关（Cloudflare Pages Advanced Mode _worker.js）
// 口令/密钥泄露后改这里三处常量，重新 build + deploy 即可令旧的全部失效
const PASSWORD = "031985";        // 网页登录口令
const ACCESS_KEY = "1334813c6f7c2fddf77a";  // M3U/TXT 链接 ?key= 参数
const AUTH_TOKEN = "8d5b86d1985d99355f1f8bf578ba1753"; // 登录后下发的 Cookie 值
const COOKIE = "qgauth";
const MAX_AGE = 7776000; // 90 天

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
    background:#101318;color:#e8eaed;font-size:18px;letter-spacing:4px;
    text-align:center;outline:none;padding:0 12px}
  input:focus{border-color:#ff2d55}
  button{width:100%;height:48px;margin-top:14px;border:none;border-radius:10px;
    background:#ff2d55;color:#fff;font-size:16px;font-weight:600;cursor:pointer}
  button:active{opacity:.85}
  .err{color:#ff6160;font-size:13px;text-align:center;margin-top:12px;min-height:18px}
</style></head><body>
<form class="box" method="POST" action="/__login" autocomplete="off">
  <div class="t">秦哥影视资源</div>
  <div class="sub">请输入访问口令</div>
  <input name="password" type="password" inputmode="numeric" maxlength="6" autofocus required>
  <button type="submit">进入</button>
  <div class="err" id="e">${"<!--ERR-->"}</div>
</form></body></html>`;

function setAuth(loc) {
  const res = new Response(null, {
    status: 302,
    headers: {
      Location: loc,
      "Set-Cookie": `${COOKIE}=${AUTH_TOKEN}; Path=/; Max-Age=${MAX_AGE}; Secure; HttpOnly; SameSite=Lax`,
    },
  });
  return res;
}

function contentTypeFor(path) {
  if (path.endsWith(".m3u") || path.endsWith(".m3u8")) return "application/vnd.apple.mpegurl";
  if (path.endsWith(".txt")) return "text/plain; charset=utf-8";
  if (path.endsWith(".html")) return "text/html; charset=utf-8";
  return null;
}

// 包装静态资源响应：统一加 noindex + 正确 Content-Type + 禁缓存（m3u/txt/html）
function wrapAsset(r, path, setCookie) {
  const res = new Response(r.body, r);
  res.headers.set("X-Robots-Tag", "noindex");
  const ct = contentTypeFor(path);
  if (ct) res.headers.set("Content-Type", ct);
  if (/\.(m3u|txt|html)$/.test(path)) res.headers.set("Cache-Control", "max-age=0, must-revalidate");
  if (setCookie) res.headers.set("Set-Cookie", setCookie);
  return res;
}

const COOKIE_VAL = `${COOKIE}=${AUTH_TOKEN}; Path=/; Max-Age=${MAX_AGE}; Secure; HttpOnly; SameSite=Lax`;

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const path = url.pathname;
    const cookieHdr = request.headers.get("cookie") || "";
    const authed = cookieHdr.includes(`${COOKIE}=${AUTH_TOKEN}`);

    // 1) 带 key 参数的请求（M3U/TXT 播放器拉取）→ 鉴权并下发 Cookie
    if (url.searchParams.get("key") === ACCESS_KEY) {
      const r = await env.ASSETS.fetch(new Request(url.origin + path, request));
      return wrapAsset(r, path, COOKIE_VAL);
    }

    // 2) 已登录 Cookie → 直接放行
    if (authed) {
      const r = await env.ASSETS.fetch(request);
      return wrapAsset(r, path, null);
    }

    // 3) 登录提交
    if (path === "/__login" && request.method === "POST") {
      const form = await request.formData();
      const pwd = (form.get("password") || "").toString();
      if (pwd === PASSWORD) return setAuth("/");
      return new Response(LOGIN_HTML.replace("<!--ERR-->", "口令错误，请重试"), {
        status: 401,
        headers: { "content-type": "text/html; charset=utf-8", "cache-control": "no-store" },
      });
    }

    // 4) 未鉴权访问 → 回登录页（首页放行到登录页，其他一律 403）
    if (path === "/" || path === "/index.html") {
      return new Response(LOGIN_HTML.replace("<!--ERR-->", ""), {
        headers: { "content-type": "text/html; charset=utf-8", "cache-control": "no-store" },
      });
    }
    return new Response("Forbidden", { status: 403 });
  },
};
