// 秦哥影视 · Jellyfin 兼容后端 (Cloudflare Workers)
// 让途播等 Jellyfin 客户端连上后显示海报墙 + 按地区分类 + 播放。
// 数据来自主库 Pages 上预生成的 JSON（运行时 fetch + 边缘缓存，不依赖 D1/KV）。
// 播放采用「服务端代取」模式：途播只跟本 Worker 通信，由 Cloudflare 边缘节点去拉
// 外部视频源（含海外源）并流式转发，手机裸网也能播放，不直连外部域名。

const DATA_URL = "https://production.qinjin.pages.dev/api/movies.json?v=20260825d";
const REGION_ORDER = [
  "中国大陆", "香港", "台湾", "美国", "日本", "韩国",
  "英国", "印度", "泰国", "欧美", "其他",
];

let CACHE = null;

// 拉数据：优先用 Cloudflare 边缘 Cache（1h），避免每次冷启动重拉
async function loadData(ctx) {
  if (CACHE && CACHE.movies) return CACHE;
  const cache = caches.default;
  let res = await cache.match(DATA_URL);
  let json;
  if (!res) {
    res = await fetch(DATA_URL, { cf: { cacheTtl: 3600 } });
    json = await res.clone().json();
    const cached = new Response(res.body, res);
    cached.headers.set("Cache-Control", "max-age=3600");
    if (ctx && ctx.waitUntil) ctx.waitUntil(cache.put(DATA_URL, cached.clone()));
    else await cache.put(DATA_URL, cached.clone());
  } else {
    json = await res.json();
  }
  const regions = new Set();
  for (const m of json.movies) regions.add(m.region || "其他");
  const ordered = REGION_ORDER.filter((r) => regions.has(r));
  const extra = [...regions].filter((r) => !REGION_ORDER.includes(r));
  json.regions = ordered.concat(extra);
  json.byId = new Map(json.movies.map((m) => [m.id, m]));
  CACHE = json;
  return CACHE;
}

function json(obj, status = 200) {
  return new Response(JSON.stringify(obj), {
    status,
    headers: {
      "content-type": "application/json; charset=utf-8",
      "access-control-allow-origin": "*",
    },
  });
}

function systemInfo() {
  return {
    ServerName: "秦哥影视",
    Version: "10.8.13",
    ProductName: "Jellyfin",
    ProductVersion: "10.8.13",
    Id: "c0a8f7e2-1b3c-4d5e-9f0a-2b6c4d8e1f02",
    OperatingSystem: "CloudflareWorkers",
    ServerId: "c0a8f7e2-1b3c-4d5e-9f0a-2b6c4d8e1f02",
    StartupWizardCompleted: true,
    LocalAddress: null,
  };
}

function userObject() {
  return {
    Id: "f1a2b3c4-d5e6-4f7a-8b9c-0d1e2f3a4b5c",
    Name: "tubo",
    ServerId: "c0a8f7e2-1b3c-4d5e-9f0a-2b6c4d8e1f02",
    HasPassword: false,
    IsAdministrator: false,
  };
}

function toDto(m) {
  return {
    Id: m.id,
    Name: m.name,
    Type: "Movie",
    MediaType: "Video",
    ProductionYear: m.year || null,
    Overview: m.overview || "",
    ImageTags: { Primary: "cover" },
    ServerId: "c0a8f7e2-1b3c-4d5e-9f0a-2b6c4d8e1f02",
    SortName: m.sort || m.name,
    OfficialRating: m.quality || "",
    CommunityRating: m.score || 0,
    RunTimeTicks: 0,
  };
}

function views(data) {
  const items = data.regions.map((r) => ({
    Id: "view_" + r,
    Name: "电影-" + r,
    Type: "CollectionFolder",
    CollectionType: "movies",
    ImageTags: {},
    ServerId: "c0a8f7e2-1b3c-4d5e-9f0a-2b6c4d8e1f02",
  }));
  return { Items: items, TotalRecordCount: items.length };
}

function itemsList(data, url) {
  const parentId = url.searchParams.get("ParentId");
  const region =
    parentId && parentId.startsWith("view_") ? parentId.slice(5) : null;
  let items = data.movies.filter(
    (m) => !region || (m.region || "其他") === region
  );

  const sortBy = (url.searchParams.get("SortBy") || "ProductionYear").split(",")[0];
  const sortOrder = (url.searchParams.get("SortOrder") || "Descending").toLowerCase();
  const dir = sortOrder === "ascending" ? 1 : -1;
  items.sort((a, b) => {
    let av, bv;
    if (sortBy === "SortName") {
      av = a.sort || "";
      bv = b.sort || "";
      return av < bv ? -1 * dir : av > bv ? 1 * dir : 0;
    } else if (sortBy === "DateCreated" || sortBy === "DatePlayed") {
      av = a.pop || 0;
      bv = b.pop || 0;
    } else {
      av = a.year || 0;
      bv = b.year || 0;
    }
    return av < bv ? -1 * dir : av > bv ? 1 * dir : 0;
  });

  const start = parseInt(url.searchParams.get("StartIndex") || "0", 10);
  const limit = parseInt(url.searchParams.get("Limit") || "60", 10);
  const page = items.slice(start, start + limit);
  return {
    Items: page.map(toDto),
    TotalRecordCount: items.length,
  };
}

// 海报：Worker 流式代取外部图床（不缓冲，避开 Workers 资源限制），
// Referer 用请求方域名动态生成，避免换域名后防盗链失效。
async function imagePrimary(data, id, origin) {
  const m = data.byId.get(id);
  if (!m || !m.cover) return new Response("no cover", { status: 404 });
  try {
    const upstream = await fetch(m.cover, {
      redirect: "follow",
      headers: {
        "User-Agent": "Mozilla/5.0",
        "Referer": origin + "/",
      },
    });
    if (!upstream.ok) return new Response("upstream error", { status: 502 });
    const headers = new Headers(upstream.headers);
    headers.set("Access-Control-Allow-Origin", "*");
    headers.delete("content-length");
    const ext = (m.cover.split("?")[0].split(".").pop() || "").toLowerCase();
    const MAP = { jpg: "image/jpeg", jpeg: "image/jpeg", png: "image/png", webp: "image/webp", gif: "image/gif" };
    const ctype = upstream.headers.get("content-type") || MAP[ext] || "image/jpeg";
    headers.set("Content-Type", ctype);
    return new Response(upstream.body, {
      status: upstream.status,
      statusText: upstream.statusText,
      headers,
    });
  } catch (e) {
    return new Response("fetch failed: " + e, { status: 502 });
  }
}

// ===== 播放代理（核心：手机只跟 Worker 通信，边缘节点代取外部源）=====
// 把 m3u8 文本里的所有外部 URL（ts 分片 / key / 子 m3u8）改写成走本 Worker 的 /proxy
function rewriteM3u8(text, base, origin) {
  let baseUrl;
  try { baseUrl = new URL(base); } catch (_) { baseUrl = null; }
  return text.split("\n").map((line) => {
    if (line.startsWith("#")) {
      // 处理 KEY/MEDIA 等里面的 URI="..."
      return line.replace(/URI="([^"]+)"/g, (mt, u) => {
        try {
          const abs = baseUrl ? new URL(u, baseUrl).href : u;
          if (typeof abs === "string" && abs.startsWith("http"))
            return `URI="${origin}/proxy?u=${encodeURIComponent(abs)}"`;
          return mt;
        } catch (_) { return mt; }
      });
    }
    const t = line.trim();
    if (!t) return line;
    let abs;
    try { abs = baseUrl ? new URL(t, baseUrl).href : t; } catch (_) { return line; }
    if (typeof abs === "string" && abs.startsWith("http")) {
      return `${origin}/proxy?u=${encodeURIComponent(abs)}`;
    }
    return line;
  }).join("\n");
}

function m3u8Response(text) {
  return new Response(text, {
    status: 200,
    headers: {
      "content-type": "application/vnd.apple.mpegurl; charset=utf-8",
      "access-control-allow-origin": "*",
      "cache-control": "no-store",
    },
  });
}

function corsHeaders(upstreamHeaders) {
  const h = new Headers(upstreamHeaders);
  h.set("Access-Control-Allow-Origin", "*");
  h.delete("content-length");
  h.delete("content-encoding");
  return h;
}

// 代取一个外部 URL：m3u8(含无 content-type 的) 改写后返回，其余流式转发（mp4/ts/key 等）
async function pipeOrRewrite(upstream, target, origin) {
  const ct = (upstream.headers.get("content-type") || "").toLowerCase();
  if (ct.includes("mpegurl") || ct.includes("vnd.apple") || ct.includes("x-mpegurl")) {
    const text = await upstream.text();
    return m3u8Response(rewriteM3u8(text, target, origin));
  }
  // 无 content-type 时，用 tee 探测前几个字节是否为 #EXTM3U，避免破坏原流
  if (upstream.body) {
    const [peekStream, passStream] = upstream.body.tee();
    const reader = peekStream.getReader();
    let isM3u8 = false;
    try {
      const { value } = await reader.read();
      if (value) {
        const head = new TextDecoder().decode(value.slice(0, 32));
        if (head.includes("#EXTM3U")) isM3u8 = true;
      }
    } finally {
      reader.cancel().catch(() => {});
    }
    if (isM3u8) {
      const full = await new Response(passStream).text();
      return m3u8Response(rewriteM3u8(full, target, origin));
    }
    return new Response(passStream, {
      status: upstream.status,
      statusText: upstream.statusText,
      headers: corsHeaders(upstream.headers),
    });
  }
  // 无 body（理论不会到这）：原样返回
  return new Response(null, { status: upstream.status });
}

// 从解析播放页 HTML 中抽取真实视频直链（覆盖 bfvvs/ukubf5/ffzy/xgplay 等常见格式）
function extractVideoUrl(html, pageUrl) {
  const patterns = [
    /(?:const|var|let|window\.)[A-Za-z_]*vid[A-Za-z_]*\s*=\s*["']([^"']+)["']/i,
    /(?:const|var|let|window\.)[A-Za-z_]*url[A-Za-z_]*\s*=\s*["']([^"']+\.m3u8[^"']*)["']/i,
    /player\.src\(["']([^"']+)["']\)/i,
    /["']url["']\s*:\s*["']([^"']+\.m3u8[^"']*)["']/i,
    /(?:src|file)\s*=\s*["']([^"']+\.m3u8[^"']*)["']/i,
  ];
  for (const p of patterns) {
    const m = html.match(p);
    if (m && m[1]) {
      try { return new URL(m[1], pageUrl).href; } catch (_) { return m[1]; }
    }
  }
  // 兜底：网页里出现的任意 .m3u8 直链
  const m = html.match(/https?:\/\/[^\s"'<>`]+?\.m3u8[^\s"'<>`]*/);
  return m ? m[0] : null;
}

async function proxyFetch(target, origin, depth = 0) {
  if (depth > 3) return new Response("parse too deep", { status: 502 });
  try {
    let referer = "https://qinjintubo.cc.cd/";
    try { referer = new URL(target).origin + "/"; } catch (_) {}
    const upstream = await fetch(target, {
      redirect: "follow",
      headers: {
        "User-Agent": "Mozilla/5.0",
        "Referer": referer,
        "Accept": "*/*",
      },
    });
    if (!upstream.ok) return new Response("upstream " + upstream.status, { status: 502 });
    const ct = (upstream.headers.get("content-type") || "").toLowerCase();
    // 命中解析播放页(HTML)：抽取真实直链并递归代取
    if (ct.includes("text/html") || ct.includes("html")) {
      const html = await upstream.text();
      const real = extractVideoUrl(html, target);
      if (real) return proxyFetch(real, origin, depth + 1);
      return new Response("cannot parse play page", { status: 502 });
    }
    return pipeOrRewrite(upstream, target, origin);
  } catch (e) {
    return new Response("proxy error: " + e, { status: 502 });
  }
}

// /Videos/{id}/stream?src=N  → 取第 N 条线路，代取并转发
async function streamProxy(data, id, url) {
  const m = data.byId.get(id);
  if (!m) return new Response("not found", { status: 404 });
  const srcIdx = parseInt(url.searchParams.get("src") || "0", 10) || 0;
  const sources = m.sources && m.sources.length ? m.sources : (m.url ? [m.url] : []);
  const target = sources[srcIdx] || sources[0];
  if (!target) return new Response("no source", { status: 404 });
  return proxyFetch(target, url.origin);
}

function proxyRoute(url) {
  const u = url.searchParams.get("u");
  if (!u) return new Response("missing u", { status: 400 });
  return proxyFetch(u, url.origin);
}

function playbackInfo(data, id, origin) {
  const m = data.byId.get(id);
  if (!m) return json({ MediaSources: [] }, 404);
  const sources = (m.sources && m.sources.length)
    ? m.sources
    : (m.url ? [m.url] : []);
  if (!sources.length) return json({ MediaSources: [] }, 404);
  const mediaSources = sources.map((u, i) => {
    const path = origin + "/Videos/" + m.id + "/stream?src=" + i;
    return {
      Protocol: "Http",
      Id: m.id + "-" + i,
      Path: path,
      DirectStreamUrl: path,
      Type: "Default",
      Container: "m3u8",
      IsRemote: false,
      SupportsDirectStream: true,
      SupportsDirectPlay: true,
      Name: i === 0 ? "主线路" : "线路" + (i + 1),
    };
  });
  return json({
    MediaSources: mediaSources,
    PlaySessionId: "sess-" + m.id,
  });
}

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    const p = url.pathname;
    try {
      const data = await loadData(ctx);

      if (p === "/System/Info/Public" || p === "/System/Info")
        return json(systemInfo());
      if (p === "/System/Ping") return new Response("pong", { status: 200 });

      if (p === "/proxy") return proxyRoute(url);

      if (p.endsWith("/Users/AuthenticateByName") && request.method === "POST") {
        let username = "tubo";
        try {
          const b = await request.json();
          username = b.Username || "tubo";
        } catch (_) {}
        return json({
          User: { Id: "f1a2b3c4-d5e6-4f7a-8b9c-0d1e2f3a4b5c", Name: username, ServerId: "c0a8f7e2-1b3c-4d5e-9f0a-2b6c4d8e1f02" },
          AccessToken: "m3u-jellyfin-token",
          SessionInfo: { UserId: "f1a2b3c4-d5e6-4f7a-8b9c-0d1e2f3a4b5c", UserName: username },
        });
      }

      if (p === "/Users/Me" || /^\/Users\/[^\/]+$/.test(p))
        return json(userObject());

      if (p.endsWith("/Views")) return json(views(data));

      let m = p.match(/^\/Users\/[^\/]+\/Items\/([^\/]+)$/);
      if (m) return json(toDto(data.byId.get(m[1]) || {}));
      m = p.match(/^\/Items\/([^\/]+)\/Images\/Primary/);
      if (m) return imagePrimary(data, m[1], url.origin);
      m = p.match(/^\/Items\/([^\/]+)\/PlaybackInfo/);
      if (m) return playbackInfo(data, m[1], url.origin);
      // 播放：途播会请求 /Videos/{id}/stream（或带 src 参数切换线路）
      m = p.match(/^\/Videos\/([^\/]+)(\/stream)?/);
      if (m) return streamProxy(data, m[1], url);
      m = p.match(/^\/Items\/([^\/]+)$/);
      if (m) {
        const it = data.byId.get(m[1]);
        return it ? json(toDto(it)) : json({ error: "not found" }, 404);
      }

      if (p.endsWith("/Items")) return json(itemsList(data, url));

      return json({ error: "not found", path: p }, 404);
    } catch (e) {
      return json({ error: String(e) }, 500);
    }
  },
};
