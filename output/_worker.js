// 秦哥影视 · Jellyfin 兼容后端 (Cloudflare Pages Functions)
// 部署在 qinjin.pages.dev

const DATA_ORIGIN = "https://qinjin.pages.dev";
const DATA_VERSION = "20260827b";
// 数据源（cc0cd 苹果CMS）不提供真实时长字段，故不返回 RunTimeTicks，
// 避免途播显示统一的虚假“1小时30分”。若日后采集到真实时长再补。
// 统一库分类：电影/直播/剧集/综艺/动漫
const CAT_LABELS = { movie: "电影", live: "直播", tv: "剧集", variety: "综艺", anime: "动漫" };
const CAT_ORDER = ["movie", "live", "tv", "variety", "anime"];

let CACHE_ALL = null;
let CACHE_ALL_LOADING = null;

function dataUrl(origin) {
  return origin + "/api/all.json?v=" + DATA_VERSION;
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

async function loadAll(origin, ctx) {
  if (CACHE_ALL && CACHE_ALL.movies) return CACHE_ALL;
  if (CACHE_ALL_LOADING) return CACHE_ALL_LOADING;

  CACHE_ALL_LOADING = (async () => {
    try {
      const res = await cachedFetch(dataUrl(origin), ctx);
      if (!res.ok) throw new Error("all.json " + res.status);
      const manifest = await res.json();
      let movies = [];
      if (manifest.sharded && manifest.cats) {
        // 分片模式：按分类加载各自 cat_{cat}_{i}.json，合并为全量
        for (const c of CAT_ORDER) {
          const entry = manifest.cats[c];
          if (!entry) continue;
          const files = entry.files || (entry.file ? [entry.file] : []);
          for (const f of files) {
            try {
              const r = await cachedFetch(origin + "/api/" + f + "?v=" + DATA_VERSION, ctx);
              if (!r.ok) { console.warn("shard miss", f, r.status); continue; }
              const j = await r.json();
              if (j && j.movies) movies = movies.concat(j.movies);
            } catch (e) {
              console.warn("shard err", f, e);
            }
          }
        }
      } else if (manifest.movies) {
        // 兼容旧版单文件 all.json
        movies = manifest.movies;
      }
      const json = Object.assign({}, manifest, { movies });
      json.byId = new Map(json.movies.map((m) => [m.id, m]));
      // 按分类预建索引，分类视图直接取，避免每次全量过滤
      json.byCat = {};
      for (const c of CAT_ORDER) json.byCat[c] = [];
      for (const m of json.movies) {
        const c = m.cat || "movie";
        (json.byCat[c] || (json.byCat[c] = [])).push(m);
      }
      CACHE_ALL = json;
      return CACHE_ALL;
    } finally {
      CACHE_ALL_LOADING = null;
    }
  })();
  return CACHE_ALL_LOADING;
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
  const sortBy = url.searchParams.get("SortBy") || "pop";
  const sortOrder = url.searchParams.get("SortOrder") || "Descending";
  return DATA_VERSION + "_list_" + [parentId, searchTerm, start, limit, sortBy, sortOrder].map(encodeURIComponent).join("_");
}

function emptyList(request, etag = DATA_VERSION + "_empty") {
  return cacheJson(request, { Items: [], TotalRecordCount: 0 }, 200, etag, 3600, 86400);
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
  const cat = m.cat || "movie";
  return {
    Id: m.id,
    Name: m.name,
    Type: "Movie",
    MediaType: "Video",
    ProductionYear: m.year || null,
    Overview: m.overview || "",
    Genres: [CAT_LABELS[cat] || "电影"],
    ImageTags: { Primary: "cover" },
    ServerId: "c0a8f7e2-1b3c-4d5e-9f0a-2b6c4d8e1f02",
    SortName: m.sort || m.name,
    OfficialRating: m.quality || "",
    CommunityRating: m.score || 0,
  };
}

function views(data) {
  // 统一库：五分类（电影/直播/剧集/综艺/动漫），无「全部」
  const items = [];
  for (const c of CAT_ORDER) {
    items.push({
      Id: "view_cat_" + c,
      Name: CAT_LABELS[c] || c,
      Type: "CollectionFolder",
      CollectionType: "movies",
      ImageTags: { Primary: "cover" },
      ServerId: "c0a8f7e2-1b3c-4d5e-9f0a-2b6c4d8e1f02",
    });
  }
  return { Items: items, TotalRecordCount: items.length };
}

function itemsList(data, url) {
  const parentId = url.searchParams.get("ParentId");
  let scope = "all";
  if (parentId && parentId.startsWith("view_cat_")) {
    scope = parentId.slice("view_cat_".length);
  }
  // 按分类取数据：分类=预建索引
  let items = data.byCat[scope] || [];
  if (scope === "all" || !items.length) {
    // 兼容旧客户端或无 ParentId 的列表请求：返回全量
    items = data.movies || [];
  }

  const searchTerm =
    url.searchParams.get("searchTerm") ||
    url.searchParams.get("SearchTerm") ||
    url.searchParams.get("Search") ||
    "";
  const hasSearch = searchTerm.trim().length > 0;
  if (hasSearch) {
    const q = searchTerm.trim().toLowerCase();
    items = items.filter(
      (m) =>
        (m.name || "").toLowerCase().includes(q) ||
        (m.sort || "").toLowerCase().includes(q)
    );
  }

  // 途播侧只暴露“有可播线路”的影片：过滤掉所有源都被封的片。
  items = items.filter((m) => tuboSources(m).length > 0);

  const rawSortBy = (url.searchParams.get("SortBy") || "pop").split(",")[0];
  const rawSortOrder = (url.searchParams.get("SortOrder") || "Descending").toLowerCase();

  // 途播客户端默认按 SortName 字母排序，导致首页总是显示“100天/100道/10间…”这类固定内容；
  // 另外若客户端发 DateCreated/升序，会把冷门老片顶到最前。
  // 首页/分类视图（无搜索）强制按热度降序；搜索场景仍按名称排序最直观。
  let sortBy = rawSortBy;
  let forceDesc = false;
  if (!hasSearch) {
    if (sortBy === "SortName" || sortBy === "ProductionYear" || sortBy === "" ||
        sortBy === "DateCreated" || sortBy === "PremiereDate" || sortBy === "Default") {
      sortBy = "pop";
    }
    forceDesc = true;  // 首页永远热度降序，避免老片/冷门置顶
  }
  const dir = forceDesc ? -1 : (rawSortOrder === "ascending" ? 1 : -1);

  items = items.slice().sort((a, b) => {
    let av, bv;
    if (sortBy === "SortName") {
      av = a.sort || "";
      bv = b.sort || "";
      return av < bv ? -1 * dir : av > bv ? 1 * dir : 0;
    } else if (sortBy === "ProductionYear") {
      av = a.year || 0;
      bv = b.year || 0;
    } else if (sortBy === "CommunityRating") {
      av = a.score || 0;
      bv = b.score || 0;
    } else {
      // 默认 pop 热度（hits * (score/10)^2）
      av = a.pop || 0;
      bv = b.pop || 0;
    }
    let cmp = av < bv ? -1 : av > bv ? 1 : 0;
    // 同热度时新片优先（年份降序）
    if (cmp === 0 && sortBy === "pop") {
      const ya = a.year || 0, yb = b.year || 0;
      cmp = ya > yb ? 1 : ya < yb ? -1 : 0;
    }
    return cmp * dir;
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
  // 封面可能是外站 URL（电影海报）或本仓库生成的相对路径（/covers/live_xxx.jpg 直播台标）。
  // 相对路径补成绝对地址，交给 ASSETS 静态托管。
  let coverUrl = m.cover;
  if (!/^https?:\/\//i.test(coverUrl)) {
    coverUrl = origin + (coverUrl.startsWith("/") ? "" : "/") + coverUrl;
  }
  try {
    const upstream = await fetch(coverUrl, {
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

  const doFetch = async (referer, ua) => {
    const fetchInit = {
      redirect: "follow",
      headers: {
        "User-Agent": ua || "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
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
    // 反盗链策略矩阵：源站可能对 Referer/UA 有严格要求，依次尝试多种组合。
    // 若某个组合返回 200，则直接采用，避免后续无效重试。
    const strategies = [
      { referer: ownRef },
      ...(refOverride && refOverride !== ownRef ? [{ referer: refOverride }] : []),
      { referer: "" },
      { referer: "https://www.cc0cd.cc.cd/" },
      { referer: "https://tv.cc0cd.cc.cd/" },
      { referer: ownRef, ua: "Mozilla/5.0 (Linux; Android 10; SM-G960U) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.120 Mobile Safari/537.36" },
    ];
    let upstream = null;
    const seen = new Set();
    for (const s of strategies) {
      const key = (s.referer || "") + "|" + (s.ua || "");
      if (seen.has(key)) continue;
      seen.add(key);
      upstream = await doFetch(s.referer, s.ua);
      if (upstream.ok) break;
    }
    if (!upstream || !upstream.ok) return new Response("upstream " + (upstream && upstream.status), { status: 502 });
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
  const cat = m.cat || "movie";
  const srcIdx = parseInt(url.searchParams.get("src") || "0", 10) || 0;
  const sourcesRaw = (m.sources && m.sources.length)
    ? m.sources
    : (m.url ? [m.url] : []);
  if (!sourcesRaw.length) return new Response("no source", { status: 404 });
  const tryIdx = Math.min(srcIdx, sourcesRaw.length - 1);

  // 直播直接 302 给客户端，不经过 worker 代理（playlist 实时更新、分片动态）
  if (cat === "live") {
    return redirect302(sourcesRaw[tryIdx]);
  }

  // 与 playbackInfo 一致：途播侧只用过滤后的源（索引对齐）
  const sources = tuboSources(m);
  if (!sources.length) return new Response("no source for tubo", { status: 404 });
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

// 线路预检：开播前并发探测所有线路是否可取到流，前端据此挑第一条可用线路，
// 避免用户对着死链空等 8 秒超时。只读取前 1KB，成本极低。
async function probeRoute(url) {
  const u = url.searchParams.get("u");
  if (!u) return json({ ok: false, error: "missing u" }, 400);
  let target;
  try { target = new URL(u); } catch (_) { return json({ ok: false, error: "bad url" }, 400); }
  const rfOverride = url.searchParams.get("rf");
  const ownRef = extractBaseReferer(u);
  const strategies = [
    ...(rfOverride ? [rfOverride] : []),
    ownRef,
    "",
    "https://www.cc0cd.cc.cd/",
    "https://tv.cc0cd.cc.cd/",
  ];
  const isStreamCt = ct =>
    ct.includes("mpegurl") || ct.includes("vnd.apple") || ct.includes("x-mpegurl")
    || ct.includes("mp4") || ct.includes("video/") || ct.includes("octet-stream");
  const looksStream = /\.(m3u8|m3u|mp4|ts|flv)(\?|$)/i.test(target.pathname);

  let last = 0, lastCt = "";
  const seen = new Set();
  for (const ref of strategies) {
    if (seen.has(ref)) continue;
    seen.add(ref);
    try {
      const r = await fetch(u, {
        redirect: "follow",
        headers: {
          "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
          "Referer": ref,
          "Range": "bytes=0-1023",
          "Accept": "*/*",
        },
      });
      const ct = (r.headers.get("content-type") || "").toLowerCase();
      last = r.status; lastCt = ct;
      const ok = (r.status === 200 || r.status === 206) && (isStreamCt(ct) || looksStream);
      try { await r.arrayBuffer(); } catch (_) {}
      if (ok) {
        return json({ ok: true, status: r.status, ref: ref || "", cdn: target.host }, 200,
          { "Access-Control-Allow-Origin": "*", "Cache-Control": "private, max-age=120" });
      }
    } catch (e) {
      last = 0; lastCt = "fetch-error";
    }
  }
  return json({ ok: false, status: last, ct: lastCt, cdn: target.host }, 200,
    { "Access-Control-Allow-Origin": "*", "Cache-Control": "private, max-age=120" });
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
  const cat = m.cat || "movie";
  const sourcesRaw = (m.sources && m.sources.length)
    ? m.sources
    : (m.url ? [m.url] : []);
  if (!sourcesRaw.length) return { MediaSources: [] };

  // 直播：不过滤、不代理，直接给原始 m3u8 地址让客户端大陆网络直连
  if (cat === "live") {
    const mediaSources = sourcesRaw.map((u, i) => {
      const container = detectContainer(u);
      return {
        Protocol: "Http",
        Id: m.id + "-" + i,
        Path: u,
        DirectStreamUrl: u,
        Type: "Default",
        Container: container,
        IsRemote: true,
        SupportsDirectStream: true,
        SupportsDirectPlay: true,
        SupportsTranscoding: false,
        Name: i === 0 ? "主线路" : "线路" + (i + 1),
        Size: 0,
        MediaStreams: makeStreams(),
      };
    });
    return { MediaSources: mediaSources, PlaySessionId: "sess-" + m.id };
  }

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
    p === "/probe" ||             // 线路预检：前端开播前并发探测，避免死链空等
    p === "/Views" ||             // 直接浏览器/调试入口
    p === "/Genres" ||
    p === "/MusicGenres" ||
    p === "/Studios" ||
    p === "/Persons" ||
    p === "/Years" ||
    p === "/Artists"
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
      if (p === "/probe") return probeRoute(url);

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
        const data = await loadAll(url.origin, ctx);
        return cacheJson(request, views(data), 200, DATA_VERSION + "_views", 3600, 86400);
      }

      // 途播「风格/标签/演员/年份」Tab 会请求这些端点；返回空列表避免「格式不正确」报错。
      if (p === "/Genres" || p === "/MusicGenres" || p === "/Studios" ||
          p === "/Persons" || p === "/Years" || p === "/Artists") {
        return emptyList(request, DATA_VERSION + p.replace("/", "_"));
      }

      let m = p.match(/^\/Users\/[^\/]+\/Items\/([^\/]+)$/);
      if (m) {
        const data = await loadAll(url.origin, ctx);
        return cacheJson(request, toDto(data.byId.get(m[1]) || {}), 200, DATA_VERSION + "_item_" + m[1], 3600, 86400);
      }
      m = p.match(/^\/Items\/([^\/]+)\/Images\/Primary/);
      if (m) {
        const data = await loadAll(url.origin, ctx);
        return imagePrimary(data, m[1], url.origin);
      }
      m = p.match(/^\/Items\/([^\/]+)\/PlaybackInfo/);
      if (m) {
        const data = await loadAll(url.origin, ctx);
        return cacheJson(request, playbackInfo(data, m[1], url.origin), 200, DATA_VERSION + "_play_" + m[1], 60, 300);
      }
      m = p.match(/^\/Videos\/([^\/]+)(\/stream)?/);
      if (m) {
        const data = await loadAll(url.origin, ctx);
        return streamProxy(data, m[1], url, request, ctx);
      }
      m = p.match(/^\/Items\/([^\/]+)$/);
      if (m) {
        const data = await loadAll(url.origin, ctx);
        const it = data.byId.get(m[1]);
        return it
          ? cacheJson(request, toDto(it), 200, DATA_VERSION + "_item_" + m[1], 3600, 86400)
          : json({ error: "not found" }, 404);
      }

      if (p.endsWith("/Items")) {
        const data = await loadAll(url.origin, ctx);
        return cacheJson(request, itemsList(data, url), 200, listEtag(url), 3600, 86400);
      }

      return json({ error: "not found", path: p }, 404);
    } catch (e) {
      return json({ error: String(e), stack: e.stack }, 500);
    }
  },
};
