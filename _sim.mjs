// 仿真 Cloudflare Worker：加载真实 output/_worker.js，mock fetch/caches，
// 关键：统计「单次调用」内的子请求数，验证不触发 Cloudflare 子请求上限（~50）。
import { readFileSync, existsSync } from "fs";
import { fileURLToPath } from "url";
import { dirname, join } from "path";

const __dirname = dirname(fileURLToPath(import.meta.url));
const API_DIR = join(__dirname, "output", "api");

// ---- 子请求计数 ----
let subreq = 0;
function countFetch() { subreq++; }

// ---- 本地静态托管（模拟 ASSETS）----
function serveLocal(urlStr) {
  const u = new URL(urlStr);
  let p = decodeURIComponent(u.pathname);
  if (p.startsWith("/api/")) {
    const fp = join(API_DIR, p.slice("/api/".length));
    if (existsSync(fp)) {
      const buf = readFileSync(fp);
      return new Response(buf, {
        status: 200,
        headers: { "content-type": "application/json; charset=utf-8" },
      });
    }
    return new Response("not found " + p, { status: 404 });
  }
  return new Response("asset " + p, { status: 200 });
}

// ---- caches 模拟（存储字节，每次 match 返回全新可读 Response，避免 body 被复用消耗）----
const _cache = new Map();
globalThis.caches = {
  default: {
    async match(req) {
      const b = _cache.get(req.url);
      return b ? new Response(b, { status: 200, headers: { "content-type": "application/json" } }) : null;
    },
    async put(req, res) {
      const b = await res.arrayBuffer();
      _cache.set(req.url, b);
    },
  },
};

// ---- fetch 模拟：本域走 ASSETS，外域给假 m3u8；每次都计数 ----
globalThis.fetch = async (input, init) => {
  countFetch();
  const url = typeof input === "string" ? input : input.url;
  if (url.includes("qinjin.pages.dev")) return serveLocal(url);
  // 外部源（播放探测）：返回假 m3u8，避免无限递归
  return new Response("#EXTM3U\n#EXTINF:-1,test\nhttps://example.com/a.ts\n", {
    status: 200, headers: { "content-type": "application/vnd.apple.mpegurl" },
  });
};

const worker = (await import("./output/_worker.js")).default;

function req(method, path, body) {
  const u = "https://qinjin.pages.dev" + path;
  const r = new Request(u, {
    method,
    headers: { "content-type": "application/json", "user-agent": "Tubo/1.0" },
    body: body ? JSON.stringify(body) : undefined,
  });
  return r;
}

let pass = 0, fail = 0;
function assert(cond, msg) {
  if (cond) { pass++; console.log("  ✓ " + msg); }
  else { fail++; console.log("  ✗ FAIL: " + msg); }
}

async function invoke(method, path, body) {
  subreq = 0;
  const r = await worker.fetch(req(method, path, body), {}, { waitUntil() {} });
  const txt = await r.text();
  let json = null;
  try { json = JSON.parse(txt); } catch {}
  return { status: r.status, json, text: txt, subreq };
}

const THRESHOLD = 40; // 安全余量：远小于 Cloudflare ~50 上限

console.log("=== 1) 添加链路（握手，不应有子请求）===");
let r;
r = await invoke("GET", "/System/Info/Public"); assert(r.status === 200, "System/Info/Public 200"); assert(r.subreq === 0, "握手 0 子请求 (实际 " + r.subreq + ")");
r = await invoke("POST", "/Users/AuthenticateByName", { Username: "tubo", Password: "" }); assert(r.status === 200 && r.json?.AccessToken, "Authenticate 200 + token");
r = await invoke("GET", "/Users/Me"); assert(r.status === 200, "Users/Me 200");

console.log("=== 2) 电影列表 第一页（StartIndex=0）===");
r = await invoke("GET", "/Items?ParentId=view_cat_movie&StartIndex=0&Limit=60");
assert(r.status === 200, "列表 200 (此前 500)");
assert(r.subreq <= THRESHOLD, "列表子请求 " + r.subreq + " <= " + THRESHOLD);
assert(r.json?.Items?.length === 60, "返回 60 条 (实际 " + (r.json?.Items?.length) + ")");
const movieTpTotal = r.json?.TotalRecordCount;
assert(typeof movieTpTotal === "number" && movieTpTotal > 1000, "TotalRecordCount 为途播可播数 " + movieTpTotal);
assert(r.json.Items.every(it => it.Id?.startsWith("m_")), "均为电影 id");

console.log("=== 3) 跨页对齐（StartIndex=0 Limit=600 == tp页0+页1）===");
r = await invoke("GET", "/Items?ParentId=view_cat_movie&StartIndex=0&Limit=600");
const p0 = JSON.parse(readFileSync(join(API_DIR, "cat_movie_tp_p0.json"))).movies;
const p1 = JSON.parse(readFileSync(join(API_DIR, "cat_movie_tp_p1.json"))).movies;
assert(r.json.Items.length === 600, "600 条 (实际 " + r.json.Items.length + ")");
assert(r.json.Items[300].Id === p1[0].id, "第301条 == tp页1首条 (对齐正确)");
assert(r.json.Items[0].Id === p0[0].id, "首条 == tp页0首条");

console.log("=== 4) 深层分页（接近末尾的安全深页）===");
const mvTp = JSON.parse(readFileSync(join(API_DIR, "all.json"))).cats.movie.tpCount;
const deepStart = Math.max(0, mvTp - 100); // 末尾附近，仍在范围内
r = await invoke("GET", "/Items?ParentId=view_cat_movie&StartIndex=" + deepStart + "&Limit=60");
assert(r.status === 200, "深层列表 200");
assert(r.subreq <= THRESHOLD, "深层子请求 " + r.subreq + " <= " + THRESHOLD);
assert(r.json.Items.length === 60, "深层返回 60 条 (实际 " + r.json.Items.length + ")");

console.log("=== 5) 详情 + PlaybackInfo（应为可播）===");
const sampleId = p0[0].id;
r = await invoke("GET", "/Items/" + sampleId); assert(r.status === 200 && r.json?.Id === sampleId, "详情 200");
r = await invoke("GET", "/Items/" + sampleId + "/PlaybackInfo");
assert(r.status === 200, "PlaybackInfo 200");
assert(Array.isArray(r.json?.MediaSources) && r.json.MediaSources.length >= 1, "至少 1 条可播线路 (实际 " + r.json?.MediaSources?.length + ")");

console.log("=== 6) 搜索（限定扫描页数，子请求可控）===");
const q = (p0[5].name || "a").slice(0, 2);
r = await invoke("GET", "/Items?ParentId=view_cat_movie&searchTerm=" + encodeURIComponent(q) + "&Limit=20");
assert(r.status === 200, "搜索 200");
assert(r.subreq <= THRESHOLD, "搜索子请求 " + r.subreq + " <= " + THRESHOLD + " (实际 " + r.subreq + ")");

console.log("=== 7) 直播列表 ===");
r = await invoke("GET", "/Items?ParentId=view_cat_live&StartIndex=0&Limit=10");
assert(r.status === 200, "直播列表 200");
assert(r.json?.Items?.length === 10, "直播 10 条");

console.log("=== 8) 跨库搜索（无 ParentId，必须覆盖全部分类）===");
// 回归护栏：旧实现把无 ParentId 的搜索硬回退到 movie，
// 导致只在剧集收录的片子（如《庆余年》）搜出来是 0 条。
const tvIdx = readFileSync(join(API_DIR, "search_tv.txt"), "utf8");
const mvIdx = readFileSync(join(API_DIR, "search_movie.txt"), "utf8");
const tvLines = tvIdx.split("\n").filter(Boolean);
// 找一个只在剧集出现、电影索引里没有的词
let probe = null;
for (const line of tvLines.slice(0, 400)) {
  const nm = (line.split("\t")[2] || "").trim();
  if (nm.length < 2) continue;
  const token = nm.slice(0, 2);
  if (token && !mvIdx.includes(token) && tvIdx.includes(token)) { probe = { token, name: nm }; break; }
}
if (!probe) { console.log("  (跳过：找不到只在剧集出现的探测词)"); }
else {
  console.log("  探测词「" + probe.token + "」（来自剧集《" + probe.name + "》，电影索引无）");
  r = await invoke("GET", "/Items?searchTerm=" + encodeURIComponent(probe.token) + "&Limit=20");
  assert(r.status === 200, "全局搜索 200");
  assert(r.subreq <= THRESHOLD, "全局搜索子请求 " + r.subreq + " <= " + THRESHOLD);
  assert((r.json?.TotalRecordCount ?? 0) >= 1,
    "全局搜索能搜到剧集条目 (Total=" + r.json?.TotalRecordCount + ")");
  assert((r.json?.Items ?? []).some(it => it.Id?.startsWith("t_")),
    "结果包含剧集 id (t_ 前缀)");
  // 限定分类的搜索不应越界
  const r2 = await invoke("GET", "/Items?ParentId=view_cat_movie&searchTerm=" + encodeURIComponent(probe.token) + "&Limit=20");
  assert((r2.json?.TotalRecordCount ?? -1) === 0,
    "限定电影分类时仍为 0（不越界）(实际 " + r2.json?.TotalRecordCount + ")");
}

console.log("\n结果: PASS=" + pass + " FAIL=" + fail);
process.exit(fail ? 1 : 0);
