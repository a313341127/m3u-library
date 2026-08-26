// 秦哥影视 · Jellyfin 兼容后端 (Cloudflare Pages Functions)
// 部署在 qinjin.pages.dev

const DATA_ORIGIN = "https://qinjin.pages.dev";
const DATA_VERSION = "20260826x";
// 数据源（cc0cd 苹果CMS）不提供真实时长字段，故不返回 RunTimeTicks，
// 避免途播显示统一的虚假“1小时30分”。若日后采集到真实时长再补。
const REGION_ORDER = [
  "中国大陆", "香港", "台湾", "美国", "日本", "韩国",
  "英国", "法国", "泰国", "印度", "欧美", "其他",
];

let CACHE_ALL = null;
let CACHE_ALL_LOADING = null;
const CACHE_REGION = new Map();
const CACHE_REGION_LOADING = new Map();

function dataUrl(origin, region) {
  if (!region) return origin + "/api/movies.json?v=" + DATA_VERSION;
  const safe = region.replace(/[ /]/g, "_");
  return origin + "/api/movies_" + safe + ".json?v=" + DATA_VERSION;
}

async function cachedFetch(url, ctx) {
  try {
    const cache = caches.default;
    const req = new Request(url, { headers: { "User-Agent": "Mozilla/5.0" } });
    let res = await cache.match(req);
    if (res) return res;
    res = await fetch(url, {
      cf: { cacheTtl: 3600 },
      headers: { "User-Agent": "Mozilla/5.0" },
    });
    if (res.ok && ctx) ctx.waitUntil(cache.put(req, res.clone()));
    return res;
  } catch (_) {
    return fetch(url, {
      cf: { cacheTtl: 3600 },
      headers: { "User-Agent": "Mozilla/5.0" },
    });
  }
}

async function loadRegion(origin, region, ctx) {
  if (CACHE_REGION.has(region)) return CACHE_REGION.get(region);
  if (CACHE_REGION_LOADING.has(region)) return CACHE_REGION_LOADING.get(region);

  const promise = (async () => {
    try {
      const url = dataUrl(origin, region);
      const res = await cachedFetch(url, ctx);
      if (!res.ok) throw new Error("movies_" + region + ".json " + res.status);
      const json = await res.json();
      json.byId = new Map(json.movies.map((m) => [m.id, m]));
      CACHE_REGION.set(region, json);
      return json;
    } finally {
      CACHE_REGION_LOADING.delete(region);
    }
  })();
  CACHE_REGION_LOADING.set(region, promise);
  return promise;
}

async function loadAll(origin, ctx) {
  if (CACHE_ALL && CACHE_ALL.movies) return CACHE_ALL;
  if (CACHE_ALL_LOADING) return CACHE_ALL_LOADING;

  CACHE_ALL_LOADING = (async () => {
    try {
      const res = await cachedFetch(dataUrl(origin, null), ctx);
      if (!res.ok) throw new Error("movies.json " + res.status);
      const json = await res.json();
      const regions = new Set();
      for (const m of json.movies) regions.add(m.region || "其他");
      const ordered = REGION_ORDER.filter((r) => regions.has(r));
      const extra = [...regions].filter((r) => !REGION_ORDER.includes(r));
      json.regions = ordered.concat(extra);
      json.byId = new Map(json.movies.map((m) => [m.id, m]));
      CACHE_ALL = json;
      return CACHE_ALL;
    } finally {
      CACHE_ALL_LOADING = null;
    }
  })();
  return CACHE_ALL_LOADING;
}

async function loadData(origin, region, ctx) {
  // 搜索/无地区：用全量；有地区：优先用分片
  if (!region) return loadAll(origin, ctx);
  return loadRegion(origin, region, ctx);
}

function json(obj, status = 200, extraHeaders = {}) {
  const headers = {
    "content-type": "application/json; charset=utf-8",
    "access-control-allow-origin": "*",
    "cache-control": "public, max-age=300, stale-while-revalidate=3600",
    "cdn-cache-control": "public, max-age=600",
    "vary": "Accept-Encoding",
    ...extraHeaders,
  };
  return new Response(JSON.stringify(obj), { status, headers });
}

function cacheJson(request, obj, status = 200, etag = DATA_VERSION, ttl = 300, cdnTtl = 600) {
  const match = request.headers.get("If-None-Match");
  if (match && match === etag) {
    return new Response(null, {
      status: 304,
      headers: {
        "ETag": etag,
        "Cache-Control": "public, max-age=" + ttl + ", stale-while-revalidate=3600",
      },
    });
  }
  const headers = {
    "content-type": "application/json; charset=utf-8",
    "access-control-allow-origin": "*",
    "cache-control": "public, max-age=" + ttl + ", stale-while-revalidate=3600",
    "cdn-cache-control": "public, max-age=" + cdnTtl,
    "vary": "Accept-Encoding",
    "ETag": etag,
  };
  return new Response(JSON.stringify(obj), { status, headers });
}

function listEtag(url) {
  const parentId = url.searchParams.get("ParentId") || "";
  const searchTerm = url.searchParams.get("searchTerm") || url.searchParams.get("SearchTerm") || "";
  const start = url.searchParams.get("StartIndex") || "0";
  const limit = url.searchParams.get("Limit") || "60";
  const sortBy = url.searchParams.get("SortBy") || "ProductionYear";
  return DATA_VERSION + "_list_" + [parentId, searchTerm, start, limit, sortBy].map(encodeURIComponent).join("_");
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
  if (!m || !m.id) return {};
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
  };
}

function views(data) {
  // 只展示“含至少一部可播影片”的地区，避免空分类
  const items = data.regions
    .filter((r) => data.movies.some((m) => (m.region || "其他") === r && tuboSources(m).length > 0))
    .map((r) => ({
      Id: "view_" + r,
      Name: "电影-" + r,
      Type: "CollectionFolder",
      CollectionType: "movies",
      ImageTags: { Primary: "cover" },
      ServerId: "c0a8f7e2-1b3c-4d5e-9f0a-2b6c4d8e1f02",
    }));
  return { Items: items, TotalRecordCount: items.length };
}

function itemsList(data, url) {
  const parentId = url.searchParams.get("ParentId");
  const region = parentId && parentId.startsWith("view_") ? parentId.slice(5) : null;
  let items = data.movies;
  if (region) {
    items = items.filter((m) => (m.region || "其他") === region);
  }

  const searchTerm =
    url.searchParams.get("searchTerm") ||
    url.searchParams.get("SearchTerm") ||
    url.searchParams.get("Search") ||
    "";
  if (searchTerm.trim()) {
    const q = searchTerm.trim().toLowerCase();
    items = items.filter(
      (m) =>
        (m.name || "").toLowerCase().includes(q) ||
        (m.sort || "").toLowerCase().includes(q)
    );
  }

  // 途播侧只暴露“有可播线路”的影片：过滤掉所有源都被封的片（如九尾狐/文采片），
  // 不让用户点到注定失败的条目。网页端走静态数据，不受影响。
  items = items.filter((m) => tuboSources(m).length > 0);

  const sortBy = (url.searchParams.get("SortBy") || "ProductionYear").split(",")[0];
  const sortOrder = (url.searchParams.get("SortOrder") || "Descending").toLowerCase();
  const dir = sortOrder === "ascending" ? 1 : -1;
  items = items.slice().sort((a, b) => {
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

async function imagePrimary(data, id, origin) {
  // 地区视图封面（兼容 pathname 编码/未编码）
  if (id.startsWith("view_")) {
    let region = id.slice(5);
    try { region = decodeURIComponent(region); } catch (_) {}
    return fetch(origin + "/covers/view_" + encodeURIComponent(region) + ".jpg", {
      headers: {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
      },
    });
  }
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
    headers.set("Cache-Control", "public, max-age=86400");
    headers.set("CDN-Cache-Control", "public, max-age=86400");
    return new Response(upstream.body, {
      status: upstream.status,
      statusText: upstream.statusText,
      headers,
    });
  } catch (e) {
    return new Response("fetch failed: " + e, { status: 502 });
  }
}

function rewriteM3u8(text, base, origin) {
  let baseUrl;
  try { baseUrl = new URL(base); } catch (_) { baseUrl = null; }
  // 把 m3u8 来源站 origin 烙进每个 /proxy URL，供分片/密钥代理按防盗链要求回带 Referer
  const rf = baseUrl ? encodeURIComponent(baseUrl.origin + "/") : "";
  return text.split("\n").map((line) => {
    if (line.startsWith("#")) {
      return line.replace(/URI="([^"]+)"/g, (mt, u) => {
        try {
          const abs = baseUrl ? new URL(u, baseUrl).href : u;
          if (typeof abs === "string" && abs.startsWith("http"))
            return `URI="${origin}/proxy?u=${encodeURIComponent(abs)}&rf=${rf}"`;
          return mt;
        } catch (_) { return mt; }
      });
    }
    const t = line.trim();
    if (!t) return line;
    let abs;
    try { abs = baseUrl ? new URL(t, baseUrl).href : t; } catch (_) { return line; }
    if (typeof abs === "string" && abs.startsWith("http")) {
      return `${origin}/proxy?u=${encodeURIComponent(abs)}&rf=${rf}`;
    }
    return line;
  }).join("\n");
}

// 仅把相对地址解析为“原始 CDN 的绝对地址”，不做 /proxy 改写——
// 客户端据此直连各原始 CDN 拉 key/分片（规避 worker 出口 IP 被分片 CDN 封禁）。
function absolutizeM3u8(text, base) {
  let baseUrl;
  try { baseUrl = new URL(base); } catch (_) { baseUrl = null; }
  return text.split("\n").map((line) => {
    if (line.startsWith("#")) {
      return line.replace(/URI="([^"]+)"/g, (mt, u) => {
        try {
          const abs = baseUrl ? new URL(u, baseUrl).href : u;
          return `URI="${abs}"`;
        } catch (_) { return mt; }
      });
    }
    const t = line.trim();
    if (!t) return line;
    try {
      const abs = baseUrl ? new URL(t, baseUrl).href : t;
      return abs;
    } catch (_) { return line; }
  }).join("\n");
}

function m3u8Response(text) {
  return new Response(text, {
    status: 200,
    headers: {
      "content-type": "application/vnd.apple.mpegurl; charset=utf-8",
      "access-control-allow-origin": "*",
      "cache-control": "public, max-age=600",
      "cdn-cache-control": "public, max-age=600",
    },
  });
}

function corsHeaders(upstreamHeaders, keepLength = false) {
  const h = new Headers(upstreamHeaders);
  h.set("Access-Control-Allow-Origin", "*");
  h.delete("content-encoding");
  if (!keepLength) h.delete("content-length");
  h.set("Cache-Control", "public, max-age=86400");
  h.set("CDN-Cache-Control", "public, max-age=86400");
  return h;
}

async function pipeOrRewrite(upstream, target, origin) {
  const ct = (upstream.headers.get("content-type") || "").toLowerCase();
  if (ct.includes("mpegurl") || ct.includes("vnd.apple") || ct.includes("x-mpegurl")) {
    const text = await upstream.text();
    return m3u8Response(rewriteM3u8(text, target, origin));
  }
  if (upstream.body) {
    const [peekStream, passStream] = upstream.body.tee();
    const reader = peekStream.getReader();
    let isM3u8 = false;
    try {
      const { value } = await reader.read();
      if (value) {
        const head = new TextDecoder().decode(value.slice(0, 64));
        if (head.includes("#EXTM3U")) isM3u8 = true;
      }
    } finally {
      reader.cancel().catch(() => {});
    }
    if (isM3u8) {
      const full = await new Response(passStream).text();
      return m3u8Response(rewriteM3u8(full, target, origin));
    }
    const keepLength = upstream.status === 206;
    return new Response(passStream, {
      status: upstream.status,
      statusText: upstream.statusText,
      headers: corsHeaders(upstream.headers, keepLength),
    });
  }
  return new Response(null, { status: upstream.status });
}

function resolveUrl(candidate, pageUrl) {
  if (!candidate) return null;
  candidate = candidate.trim();
  if (candidate.toLowerCase().startsWith("http")) return candidate;
  if (candidate.startsWith("//")) return "https:" + candidate;
  try { return new URL(candidate, pageUrl).href; } catch (_) { return candidate; }
}

function extractVideoUrl(html, pageUrl) {
  // 1. iframe / video 标签
  const tag = html.match(/<(iframe|video|audio|source)[^>]+src=["']([^"']+)["']/i);
  if (tag && tag[2]) {
    const url = resolveUrl(tag[2], pageUrl);
    if (url && /\.(m3u8|mp4|ts|flv)(\?|$)/i.test(url)) return url;
  }

  // 2. 常见 JSON / JS 变量里的播放地址
  const patterns = [
    /["'](?:url|src|video|file|link|play_url|video_url|m3u8_url|source)["']\s*:\s*["']([^"']+\.(?:m3u8|mp4|ts|flv)(?:\?[^"']*)?)["']/i,
    /(?:const|var|let|window\.)[A-Za-z_]*(?:vid|url|src|file|video|m3u8|mp4)[A-Za-z_]*\s*=\s*["']([^"']+\.(?:m3u8|mp4|ts|flv)(?:\?[^"']*)?)["']/i,
    /player\.src\(["']([^"']+)["']\)/i,
    /(?:src|file)\s*=\s*["']([^"']+\.(?:m3u8|mp4|ts|flv)(?:\?[^"']*)?)["']/i,
    // 采集站常用变量：var main = "..."; const url = "...";
    /(?:var|const|let)\s+(?:main|url|src|video|m3u8|playUrl|videoUrl)\s*=\s*["']([^"']+\.(?:m3u8|mp4|ts|flv)(?:\?[^"']*)?)["']/i,
    /(?:var|const|let)\s+(?:main|url|src|video|m3u8|playUrl|videoUrl)\s*=\s*["']([^"']+)["']/i,
    /["']([^"']*\.(?:m3u8|mp4|ts|flv)(?:\?[^"']*)?)["']/i,
  ];
  for (const p of patterns) {
    const m = html.match(p);
    if (m && m[1]) {
      const url = resolveUrl(m[1], pageUrl);
      if (url && /\.(m3u8|mp4|ts|flv)(\?|$)/i.test(url)) return url;
    }
  }

  // 3. 页面里裸写的 m3u8/mp4 链接
  const m = html.match(/https?:\/\/[^\s"'`<>]+?\.(?:m3u8|mp4|ts|flv)(?:\?[^\s"'`<>]*)?/i);
  if (m) return m[0];

  // 4. base64 编码的 m3u8 数据 URI
  const b64 = html.match(/data:application\/vnd\.apple\.mpegurl[^;]*;base64,([A-Za-z0-9+/=]+)/i);
  if (b64) {
    try {
      const text = atob(b64[1]);
      if (text.includes("#EXTM3U")) return "data:application/vnd.apple.mpegurl;base64," + b64[1];
    } catch (_) {}
  }

  // 5. 形如 var player_aaaa = {..."url":"..."} 的 JSON 对象
  const jsonM = html.match(/player_[a-z0-9_]+\s*=\s*(\{[\s\S]{0,800}?\})/i);
  if (jsonM) {
    try {
      const obj = JSON.parse(jsonM[1].replace(/'/g, '"'));
      for (const k of ["url", "video", "src", "file", "m3u8"]) {
        if (obj[k]) {
          const url = resolveUrl(obj[k], pageUrl);
          if (url) return url;
        }
      }
    } catch (_) {}
  }

  return null;
}

function extractBaseReferer(target) {
  try { return new URL(target).origin + "/"; } catch (_) { return "https://qinjin.pages.dev/"; }
}

async function proxyFetch(target, origin, request, depth = 0, refOverride = null) {
  if (depth > 3) return new Response("parse too deep", { status: 502 });

  // base64 data URI 直接返回
  if (target.startsWith("data:")) {
    try {
      const text = await new Response(target).text();
      if (text.includes("#EXTM3U")) return m3u8Response(rewriteM3u8(text, origin + "/", origin));
    } catch (_) {}
    return new Response("invalid data uri", { status: 502 });
  }

  const doFetch = async (referer) => {
    const fetchInit = {
      redirect: "follow",
      headers: {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": referer,
        "Accept": "*/*",
      },
    };
    if (request) {
      const range = request.headers.get("Range");
      if (range) fetchInit.headers["Range"] = range;
      const ifRange = request.headers.get("If-Range");
      if (ifRange) fetchInit.headers["If-Range"] = ifRange;
      const accept = request.headers.get("Accept");
      if (accept) fetchInit.headers["Accept"] = accept;
    }
    return fetch(target, fetchInit);
  };

  try {
    const ownRef = extractBaseReferer(target);
    let upstream = await doFetch(ownRef);
    // 防盗链：自身 origin 作 Referer 被拒(如文采分片 CDN p.hhwenjian.com)时，
    // 改用 m3u8 来源站 referer 重试，通常即可取到分片。
    if (!upstream.ok && refOverride && refOverride !== ownRef) {
      const retry = await doFetch(refOverride);
      if (retry.ok) upstream = retry;
    }
    if (!upstream.ok) return new Response("upstream " + upstream.status, { status: 502 });
    const ct = (upstream.headers.get("content-type") || "").toLowerCase();
    if (ct.includes("text/html") || ct.includes("html")) {
      const html = await upstream.text();
      const real = extractVideoUrl(html, target);
      if (real) {
        return await proxyFetch(real, origin, request, depth + 1, refOverride);
      }
      return new Response("cannot parse play page", { status: 502 });
    }
    return pipeOrRewrite(upstream, target, origin);
  } catch (e) {
    return new Response("proxy error: " + e, { status: 502 });
  }
}

// 解析出真实可播放地址：直链流直接返回；播放页/分享页则服务端解析出内部 m3u8/mp4 后返回。
// 仅做“解析”，不代理媒体——媒体交给客户端大陆网络直连（规避 worker 出口 IP 被分片 CDN 封禁）。
async function resolvePlayUrl(target, request, depth = 0) {
  if (!target || depth > 4) return null;
  try {
    const referer = extractBaseReferer(target);
    const upstream = await fetch(target, {
      redirect: "follow",
      headers: {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": referer,
        "Accept": "*/*",
      },
    });
    if (!upstream.ok) return null;
    const ct = (upstream.headers.get("content-type") || "").toLowerCase();
    const isStream = ct.includes("mpegurl") || ct.includes("vnd.apple") || ct.includes("x-mpegurl")
      || ct.includes("mp4") || /\.(m3u8|m3u|mp4|ts|flv)(\?|$)/i.test(target.split("?")[0]);
    if (isStream) return target;
    if (ct.includes("html") || ct.includes("text/plain")) {
      const text = await upstream.text();
      const real = extractVideoUrl(text, target);
      if (real) return await resolvePlayUrl(real, request, depth + 1);
    }
    return null;
  } catch (_) {
    return null;
  }
}

function redirect302(loc) {
  return new Response(null, {
    status: 302,
    headers: {
      "Location": loc,
      "Access-Control-Allow-Origin": "*",
      "Cache-Control": "private, max-age=60",
    },
  });
}

async function streamProxy(data, id, url, request, ctx) {
  const m = data.byId.get(id);
  if (!m) return new Response("not found", { status: 404 });
  const srcIdx = parseInt(url.searchParams.get("src") || "0", 10) || 0;
  // 与 playbackInfo 一致：途播侧只用过滤后的源（索引对齐）
  const sources = tuboSources(m);
  if (!sources.length) return new Response("no source for tubo", { status: 404 });
  const tryIdx = Math.min(srcIdx, sources.length - 1);
  const order = [];
  for (let i = tryIdx; i < sources.length; i++) order.push(i);
  for (let i = 0; i < tryIdx; i++) order.push(i);

  // 全代理模式（20260826p 起）：worker 在海外侧把 m3u8 / key / 分片全部拉取并转发，
  // 途播只与 pages.dev 通信，彻底不打国内 CDN——根治途播播放内核不走 Shadowrocket 分流的问题。
  // 实测 Cloudflare 出口可稳定取回量子(lzcdn)/茅台(uvjtih)/暴风(fengbao)/bfvvs 等绝大多数国内源。
  const origin = url.origin;
  for (const i of order) {
    const real = await resolvePlayUrl(sources[i], request);
    if (!real) continue;
    // 直链媒体文件（mp4/ts/flv）直接经 /proxy 转发，不走 m3u8 改写
    if (/\.(mp4|ts|flv)(\?|$)/i.test(real.split("?")[0])) {
      return proxyFetch(real, origin, request);
    }
    try {
      const upstream = await fetch(real, {
        redirect: "follow",
        headers: {
          "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
          "Referer": extractBaseReferer(real),
          "Accept": "*/*",
        },
      });
      if (upstream.ok) {
        const text = await upstream.text();
        if (text.includes("#EXTM3U")) return m3u8Response(rewriteM3u8(text, real, origin));
      }
    } catch (_) {}
  }

  // 极少数源 worker 出口取不到（如文采反爬 530）→ 兜底 302 给客户端直连
  // （途播可能仍失败，但浏览器/Safari 能放）
  return redirect302(sources[tryIdx]);
}

function proxyRoute(url, request) {
  const u = url.searchParams.get("u");
  if (!u) return new Response("missing u", { status: 400 });
  const rf = url.searchParams.get("rf");
  return proxyFetch(u, url.origin, request, 0, rf || null);
}

function detectContainer(url) {
  const lower = url.toLowerCase();
  if (lower.includes(".m3u8") || lower.includes(".m3u")) return "m3u8";
  if (lower.endsWith(".mp4") || lower.includes(".mp4?")) return "mp4";
  if (lower.endsWith(".ts") || lower.includes(".ts?")) return "ts";
  if (lower.endsWith(".flv") || lower.includes(".flv?")) return "flv";
  return "m3u8";
}

// 这类 URL 是采集站/分享页/播放器页，服务端在海外经常 403，
// 途播客户端在大陆网络下通常能自行解析出真实 m3u8。
// 让客户端直接连原始地址，比服务端代理更可靠。
function shouldClientParse(url) {
  if (!url) return false;
  const lower = url.toLowerCase();
  if (/\/(share|play|player)\//i.test(url)) return true;
  if (lower.includes("ffzy")) return true;
  if (lower.includes("lzcdn")) return false; // 量子是国内直链 CDN
  if (lower.includes("bfvvs") || lower.includes("baofeng") || lower.includes("fengbao")) return false;
  // 不以常见流后缀结尾，且不是已知直链域名 → 视为需要客户端解析的页面
  const hasStreamExt = /\.(m3u8|mp4|ts|flv)(\?|$)/i.test(url.split("?")[0]);
  return !hasStreamExt;
}

function makeStreams() {
  return [
    { Type: "Video", Codec: "h264", IsDefault: true, IsExternal: false, Index: 0, Level: 0 },
    { Type: "Audio", Codec: "aac", IsDefault: true, IsExternal: false, Index: 1, Level: 0 },
  ];
}

// 途播不可用源域名黑名单（CF 出口取不到 / 海外封禁）。
// 文采 6g9ba6/hhuus/hhwenjian：CF 出口 530/1010 反爬封禁；
// 非凡 ffzy-online*：CF 出口 403 海外封禁。这些源网页端（本机直连）能放，
// 但途播经 CF Worker 取不到 → 在途播侧过滤掉，只暴露“途播可播”的线路。
const TUBO_BLOCKED = [
  "6g9ba6.com",    // 文采播放链（530 反爬）
  "hhuus.com",     // 文采真实 m3u8 CDN（海外）
  "hhwenjian.com", // 文采分片 CDN（海外封禁）
  "ffzy-online",   // 非凡分享站（403 海外封禁）
];

function isTuboBlocked(url) {
  try {
    const host = new URL(url).hostname.toLowerCase();
    return TUBO_BLOCKED.some(b => host.includes(b));
  } catch (_) { return false; }
}

// 途播侧可见的源：过滤掉黑名单域名。过滤后为空则返回 []（途播显示无可用线路），
// 不退回全量——避免途播点到注定失败的源。网页端走静态数据，不受此影响。
function tuboSources(m) {
  const srcs = (m.sources && m.sources.length)
    ? m.sources
    : (m.url ? [m.url] : []);
  return srcs.filter(u => !isTuboBlocked(u));
}

function playbackInfo(data, id, origin) {
  const m = data.byId.get(id);
  if (!m) return { MediaSources: [] };
  // 途播侧只暴露“途播可播”的源（过滤黑名单域名）；网页端走静态数据不受影响
  const sources = tuboSources(m);
  if (!sources.length) return { MediaSources: [] };
  const mediaSources = sources.map((u, i) => {
    const container = detectContainer(u);
    // 所有线路统一走 worker 代理：worker 能解析的分享页返回真实 m3u8，
    // 解析失败再 302 让客户端大陆网络自行解析，体验最稳。
    const path = origin + "/Videos/" + m.id + "/stream?src=" + i;
    return {
      Protocol: "Http",
      Id: m.id + "-" + i,
      Path: path,
      DirectStreamUrl: path,
      Type: "Default",
      Container: container,
      IsRemote: false,
      SupportsDirectStream: true,
      SupportsDirectPlay: true,
      SupportsTranscoding: false,
      Name: i === 0 ? "主线路" : "线路" + (i + 1),
      Size: 0,
      MediaStreams: makeStreams(),
    };
  });
  return {
    MediaSources: mediaSources,
    PlaySessionId: "sess-" + m.id,
  };
}

function isJellyfinPath(p) {
  if (p === "/" || p === "") return false;
  return (
    p.startsWith("/System/") ||
    p.startsWith("/Users/") ||
    p.startsWith("/Items") ||      // /Items 列表 + /Items/{id} 详情/播放信息
    p.startsWith("/Videos/") ||
    p === "/proxy" ||
    p === "/Views"                 // 直接浏览器/调试入口
  );
}

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    const p = url.pathname;

    if (!isJellyfinPath(p)) {
      return env.ASSETS.fetch(request);
    }

    try {
      if (p === "/System/Info/Public" || p === "/System/Info")
        return cacheJson(request, systemInfo(), 200, DATA_VERSION + "_sys", 3600, 86400);
      if (p === "/System/Ping") return new Response("pong", { status: 200 });
      if (p === "/proxy") return proxyRoute(url, request);

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
        return cacheJson(request, userObject(), 200, DATA_VERSION + "_user", 3600, 86400);

      if (p.endsWith("/Views")) {
        const data = await loadData(url.origin, null, ctx);
        return cacheJson(request, views(data), 200, DATA_VERSION + "_views_" + data.regions.length, 3600, 86400);
      }

      let m = p.match(/^\/Users\/[^\/]+\/Items\/([^\/]+)$/);
      if (m) {
        const data = await loadData(url.origin, null, ctx);
        return cacheJson(request, toDto(data.byId.get(m[1]) || {}), 200, DATA_VERSION + "_item_" + m[1], 3600, 86400);
      }
      m = p.match(/^\/Items\/([^\/]+)\/Images\/Primary/);
      if (m) {
        const data = await loadData(url.origin, null, ctx);
        return imagePrimary(data, m[1], url.origin);
      }
      m = p.match(/^\/Items\/([^\/]+)\/PlaybackInfo/);
      if (m) {
        const data = await loadData(url.origin, null, ctx);
        return cacheJson(request, playbackInfo(data, m[1], url.origin), 200, DATA_VERSION + "_play_" + m[1], 60, 300);
      }
      m = p.match(/^\/Videos\/([^\/]+)(\/stream)?/);
      if (m) {
        const data = await loadData(url.origin, null, ctx);
        return streamProxy(data, m[1], url, request, ctx);
      }
      m = p.match(/^\/Items\/([^\/]+)$/);
      if (m) {
        const data = await loadData(url.origin, null, ctx);
        const it = data.byId.get(m[1]);
        return it
          ? cacheJson(request, toDto(it), 200, DATA_VERSION + "_item_" + m[1], 3600, 86400)
          : json({ error: "not found" }, 404);
      }

      if (p.endsWith("/Items")) {
        const parentId = url.searchParams.get("ParentId");
        const region = parentId && parentId.startsWith("view_") ? parentId.slice(5) : null;
        const searchTerm = url.searchParams.get("searchTerm") || url.searchParams.get("SearchTerm") || "";
        // 搜索或无地区：加载全量；有地区：加载分片（小得多）
        const data = await loadData(url.origin, searchTerm.trim() ? null : region, ctx);
        return cacheJson(request, itemsList(data, url), 200, listEtag(url), 3600, 86400);
      }

      return json({ error: "not found", path: p }, 404);
    } catch (e) {
      return json({ error: String(e), stack: e.stack }, 500);
    }
  },
};
