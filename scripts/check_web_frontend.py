#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""前端分片加载 / 搜索逻辑的本地回归验证（改 generator/web.py 后务必先跑这个）。

背景（为什么需要它）：
  * 网页数据按体积切成 data_{cat}_{N}.js，首屏只同步加载第 0 片，其余由前台
    ensureCat() 动态注入；
  * 只要 ensureCat 出问题，表现都很隐蔽且都在线上才暴露：
      - 加载期间被重复调用 → 同一分片注入两次 → 数据翻倍（每部片出现 2 张卡）；
      - 搜索/筛选只作用于「已加载分片」→ 排在后面的分片永远搜不到
        （线上真实案例：搜「功夫女足」搜不到，它在 movie 第 4 片）。
  * 这类 bug 单看代码很难断言，必须跑行为仿真：用**真实的** __RES__ / ensureCat /
    搜索监听器（直接从 web.py 抽取，不是重写版）在 node 里跑一遍。

检查项：
  1. 内联 <script> 的 JS 语法（防止推上线白屏）
  2. 三次并发调用 ensureCat：不重复注入、不翻倍、在途数 <= PART_CONC
  3. 搜索「功夫女足」：必须触发全库加载，末片能加载到，且无重复卡
  4. 慢网络下搜索要先给即时反馈（而不是等到 120MB 下完）
  5. 手工重复注入同一片：幂等键兜得住

用法：
    python scripts/check_web_frontend.py
退出码 0 = 全部 PASS，1 = 有 FAIL（不要在这种状态下部署）。
"""
import ast
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
WEBPY = ROOT / "generator" / "web.py"
NODE = r"C:\Users\win11\.workbuddy\binaries\node\versions\22.22.2-2\node.exe"
if not pathlib.Path(NODE).exists():
    NODE = shutil.which("node") or "node"

HARNESS = r"""
import { readFileSync } from 'node:fs';
const WEBPY = process.env.WEBPY, RESJS = process.env.RESJS;
const src = readFileSync(WEBPY, 'utf8');
const PARTS = Array.from({ length: 8 }, (_, i) => 'data_movie_' + i + '.js');
const PER_PART = 10, FIRST_LOADED = 1, EXPECT_TOTAL = PARTS.length * PER_PART;
const PART_CONC = Number(process.env.PART_CONC), PART_RETRY = Number(process.env.PART_RETRY);

const g = globalThis;
g.window = g;
g.window.__DATAMANIFEST__ = { movie: PARTS.slice() };
g.window.__DVER__ = 'v1';
const stats = { injected: {}, pending: 0, pendingMax: 0 };
let PART_DELAY = 2, resCalls = 0;
const mkPart = (name) => {
  const i = PARTS.indexOf(name);
  const arr = Array.from({ length: PER_PART }, (_, k) => ({ name: 'P' + i + '_' + k, url: 'http://x/' + i + '/' + k }));
  return 'window.__RES__("movie",' + JSON.stringify(arr) + ');';
};
g.document = {
  createElement: () => ({}),
  head: { appendChild(s) {
    const name = s.src.split('/').pop().split('?')[0];
    stats.injected[name] = (stats.injected[name] || 0) + 1;
    stats.pending++; stats.pendingMax = Math.max(stats.pendingMax, stats.pending);
    setTimeout(() => {
      try { new Function(mkPart(name)).call(g); } catch (e) { console.error('分片注入失败', name, e.message); }
      resCalls++; stats.pending--;
      try { s.onload && s.onload(); } catch (e) { console.error('onload 抛错', e.message); }
    }, PART_DELAY);
  } }
};

function grabBlock(from, marker) {
  const i = src.indexOf(marker, from);
  if (i < 0) throw new Error('未找到 ' + marker);
  let d = 0, started = false;
  for (let p = src.indexOf('{', i); p < src.length; p++) {
    if (src[p] === '{') { d++; started = true; }
    else if (src[p] === '}') { d--; if (started && d === 0) return src.slice(i, p + 1); }
  }
  throw new Error('括号不配平 ' + marker);
}
const fnEnsure = grabBlock(0, 'function ensureCat(');
const fnPending = grabBlock(0, 'function hasPendingParts(');
new Function('PART_CONC', 'PART_RETRY', fnEnsure + '\n' + fnPending +
  '\nglobalThis.ensureCat=ensureCat; globalThis.hasPendingParts=hasPendingParts;')(PART_CONC, PART_RETRY);
const arrow = grabBlock(src.indexOf("$('search').addEventListener('input'"), 'e => {');
const RES_CODE = readFileSync(RESJS, 'utf8');
g.window.__RESOURCES__ = g.window.__RESOURCES__ || {};   // 先建好，供下面搜索harness抓引用

// ---------- 搜索：从 web.py 现抽真实实现（服务端检索 + 本地兜底）----------
const SEARCH_FNS = [
  'function matchSearch(', 'function filterItems(', 'function sortItems(', 'function popScore(',
  'function catLabel(', 'function searchCatsForView(', 'function localSearchHits(',
  'function rankSearch(', 'function runSearch(', 'function finishSearch(', 'function ensureView(',
  'function searchList(', 'function applyHomeSearchView(', 'function appendSearchHint(',
  'function renderGridOnly(',
];
const searchSrc = SEARCH_FNS.map(m => grabBlock(0, m)).join('\n');
// ⚠️ new Function 建的函数运行在**全局作用域**，看不到模块作用域变量 →
// 测试桩必须挂在 globalThis 上（SRV / FETCH_MODE），否则报 "SRV is not defined"。
const SRV = g.SRV = {};               // cat -> 服务端要返回的条目
g.FETCH_MODE = 'ok';                  // ok | fail | slow
const makeSearch = new Function(`
  var SEARCH_CATS = ['movie', 'tv', 'anime', 'variety'];
  var searchResults = null, searchPending = 0, searchTotal = 0, searchSeq = 0, searchServerFailed = false;
  var searchQuery = '', currentCat = 'home', displayLimit = 200, PAGE_SIZE = 200, currentSort = 'pop';
  var activeFilters = { media_type: '', region: '', year: '' };
  var RESOURCES = globalThis.__RESOURCES__;
  var CATEGORIES = { home: {label:'首页'}, movie: {label:'电影'}, tv: {label:'剧集'},
                     anime: {label:'动漫'}, variety: {label:'综艺'}, live: {label:'直播'} };
  var els = {}, rendered = [], homeCalls = 0, fetchCalls = [];
  function el() { return { innerHTML: '', textContent: '', className: '', style: {}, kids: [],
                           appendChild(c) { this.kids.push(c); } }; }
  function $(id) { return els[id] || (els[id] = el()); }
  function htmlEscape(s) { return String(s); }
  function initSortGroup() {}
  function renderHome() { homeCalls++; }
  function renderGrid(items) { rendered.push(items.slice()); }
  function renderLiveGrid() {}
  function sortLive(x) { return x; }
  function filterLive() { return []; }
  function fetch(url) {
    fetchCalls.push(String(url));
    const cat = (String(url).match(/cat=([a-z]+)/) || [])[1];
    if (globalThis.FETCH_MODE === 'fail') return Promise.reject(new Error('boom'));
    const list = (globalThis.SRV[cat] || []).map(m => Object.assign({}, m));
    const mk = () => ({ ok: true, json: () => Promise.resolve({ ok: true, cat: cat, total: list.length, movies: list }) });
    if (globalThis.FETCH_MODE === 'slow') return new Promise(r => setTimeout(() => r(mk()), 60));
    return Promise.resolve(mk());
  }
  ${searchSrc}
  return {
    set(o) {
      if (o.cat !== undefined) currentCat = o.cat;
      if (o.q !== undefined) searchQuery = o.q;
      if (o.fetchMode !== undefined) globalThis.FETCH_MODE = o.fetchMode;
      fetchCalls = []; rendered = []; homeCalls = 0;
      for (const k of Object.keys(els)) delete els[k];
    },
    run: () => runSearch(),
    state: () => ({ pending: searchPending, total: searchTotal, failed: searchServerFailed,
                    results: (searchResults || []).slice(), rendered: rendered.slice(),
                    homeCalls: homeCalls, fetchCalls: fetchCalls.slice() }),
    el: (id) => $(id),
  };
`)();

const reset = () => {
  // ⚠️ 必须「就地清空」而不是重新赋值：搜索测试那一组脚本里把 __RESOURCES__ 抓成了
  // 变量引用，重新赋值会让它指向旧对象（表现为「搜索永远 0 条」这种诡异现象）。
  const R = g.window.__RESOURCES__ = g.window.__RESOURCES__ || {};
  for (const k of Object.keys(R)) delete R[k];
  g.window.__RESSEEN__ = {};
  g.window.__READY__ = {}; g.window.__INJECTED__ = {}; g.window.__PENDING__ = {};
  g.window.__LOADING__ = {}; g.window.__QUEUE__ = {}; g.window.__RETRY__ = {}; g.window.__FAILED__ = {};
  g.window.__LOADED_PARTS__ = { movie: FIRST_LOADED };
  stats.injected = {}; stats.pending = 0; stats.pendingMax = 0; resCalls = 0;
  new Function(RES_CODE).call(g);
  new Function(mkPart(PARTS[0])).call(g);      // 模拟首屏静态 <script> 已注入第 0 片
};
let lines = [], allPass = true;
const say = (s) => lines.push(s);
const mark = (ok, label) => { if (!ok) allPass = false; say('  -> ' + (ok ? 'PASS' : 'FAIL') + (label ? '  ' + label : '')); };
// 搜索监听器必须把活交给 runSearch（服务端全库检索）；旧实现只 renderGridOnly()，
// 于是「只在已加载分片里找」——首页更是渲染进 display:none 的 #grid，点了完全没反应。
const listenerCallsRunSearch = /runSearch\(\)/.test(arrow);

// A: 并发调用不重复注入
reset();
let doneA = 0;
ensureCat('movie', () => doneA++); ensureCat('movie', () => doneA++); ensureCat('movie', () => doneA++);
await new Promise(r => setTimeout(r, 80));
const dup = Object.entries(stats.injected).filter(([, n]) => n > 1);
const totalA = (window.__RESOURCES__.movie || []).length;
say('=== A 三次并发 ensureCat ===');
say('  回调数 ' + doneA + ' | 动态注入 ' + Object.keys(stats.injected).length + '/' + (PARTS.length - FIRST_LOADED) +
    ' | 重复注入 ' + (dup.length ? JSON.stringify(dup) : '无') + ' | 条目 ' + totalA + '/' + EXPECT_TOTAL +
    ' | 在途峰值 ' + stats.pendingMax + ')');
mark(doneA === 3 && dup.length === 0 && totalA === EXPECT_TOTAL && stats.pendingMax <= PART_CONC);
// __RES__ 必须给每条打上自身分类（首页跨分类搜索的徽标/续播键/播放器分类都靠它）
const catStamped = (window.__RESOURCES__.movie || [])[0]._cat;
say('  __RES__ 打的 _cat: ' + JSON.stringify(catStamped) + '（期望 "movie"）');
mark(catStamped === 'movie');

// B0: 搜索监听器接线
say('=== B0 搜索监听器接线 ===');
say('  监听器调用 runSearch: ' + listenerCallsRunSearch);
mark(listenerCallsRunSearch);

// B1: 首页搜索（旧实现渲染进 display:none 的 #grid，点了等于没反应）
reset();
const mkItem = (name, cat) => ({ name: name, year: '2026', url: 'http://x/' + name, _cat: cat,
                                 score: 7.5, hits: 100, sources: [{ src: '虎牙', url: 'http://x/' + name }] });
SRV.movie = [mkItem('功夫女足', 'movie'), mkItem('功夫女足之外星人篇', 'movie')];
SRV.tv = [mkItem('功夫女足 剧版', 'tv')];
SRV.anime = []; SRV.variety = [];
makeSearch.set({ cat: 'home', q: '功夫女足', fetchMode: 'ok' });
makeSearch.run();
await new Promise(r => setTimeout(r, 40));
let S1 = makeSearch.state();
const lastB1 = S1.rendered[S1.rendered.length - 1] || [];
say('=== B1 首页搜索「功夫女足」（服务端 /site/search）===');
say('  请求分类 ' + S1.fetchCalls.length + ' 个 | 结果 ' + lastB1.length + ' 条' +
    ' | 各条分类 ' + JSON.stringify(lastB1.map(x => x._cat)));
say('  homeView 显示 ' + JSON.stringify(makeSearch.el('homeView').style.display) +
    ' | grid 显示 ' + JSON.stringify(makeSearch.el('grid').style.display) +
    ' | 标题 ' + JSON.stringify(makeSearch.el('sectionName').textContent));
mark(S1.fetchCalls.length === 4 &&                                   // 首页 = 跨全部分类检索
     S1.fetchCalls.every(u => u.indexOf('/site/search') === 0) &&
     lastB1.length === 3 &&
     lastB1.filter(x => x._cat === 'movie').length === 2 &&
     lastB1.filter(x => x._cat === 'tv').length === 1 &&
     makeSearch.el('homeView').style.display === 'none' &&
     makeSearch.el('grid').style.display === '' &&
     makeSearch.el('sectionName').textContent === '搜索结果' && S1.pending === 0);

// B2: 检索中先给反馈，响应到达再补画（渐进）
reset();
makeSearch.set({ cat: 'home', q: '功夫女足', fetchMode: 'slow' });
makeSearch.run();
await new Promise(r => setTimeout(r, 20));
const midHtml = makeSearch.el('grid').innerHTML, midRendered = makeSearch.state().rendered.length;
await new Promise(r => setTimeout(r, 120));
const finB2 = makeSearch.state();
say('=== B2 慢响应（60ms）===');
say('  20ms 时 grid 文案 ' + JSON.stringify(midHtml) + ' | 已渲染批次 ' + midRendered +
    ' | 最终结果 ' + ((finB2.rendered[finB2.rendered.length - 1] || []).length));
mark(/正在检索全库/.test(midHtml) && finB2.pending === 0 &&
     (finB2.rendered[finB2.rendered.length - 1] || []).length === 3);

// B3: 清空搜索词 → 回到首页多板块
reset();
makeSearch.set({ cat: 'home', q: '', fetchMode: 'ok' });
makeSearch.run();
await new Promise(r => setTimeout(r, 20));
const S3 = makeSearch.state();
say('=== B3 清空搜索词 ===');
say('  renderHome 调用 ' + S3.homeCalls + ' 次 | homeView ' + JSON.stringify(makeSearch.el('homeView').style.display) +
    ' | grid ' + JSON.stringify(makeSearch.el('grid').style.display) + ' | 请求数 ' + S3.fetchCalls.length);
mark(S3.homeCalls === 1 && makeSearch.el('homeView').style.display === 'block' &&
     makeSearch.el('grid').style.display === 'none' && S3.fetchCalls.length === 0);

// B4: 分类 Tab 内搜索只查该分类
reset();
makeSearch.set({ cat: 'movie', q: '功夫女足', fetchMode: 'ok' });
makeSearch.run();
await new Promise(r => setTimeout(r, 40));
const S4 = makeSearch.state();
const lastB4 = S4.rendered[S4.rendered.length - 1] || [];
say('=== B4 分类 Tab(movie) 内搜索 ===');
say('  请求 ' + JSON.stringify(S4.fetchCalls) + ' | 结果 ' + lastB4.length + ' 条');
mark(S4.fetchCalls.length === 1 && /cat=movie/.test(S4.fetchCalls[0]) &&
     lastB4.length === 2 && lastB4.every(x => x._cat === 'movie'));

// B5: 服务端不可用 → 降级本地分片扫描（分享站网关没有该路由）
reset();
makeSearch.set({ cat: 'home', q: 'P0_', fetchMode: 'fail' });
makeSearch.run();
await new Promise(r => setTimeout(r, 50));
const S5 = makeSearch.state();
await new Promise(r => setTimeout(r, 600));   // 等 ensureView 把分片拉齐后重渲染
const S5b = makeSearch.state();
const lastB5 = S5b.rendered[S5b.rendered.length - 1] || [];
say('=== B5 服务端搜索失败 → 本地兜底 ===');
say('  failed=' + S5.failed + ' | 降级后抽到分片 ' + Object.keys(stats.injected).length +
    ' | 本地命中 ' + lastB5.length + ' 条');
mark(S5.failed === true && Object.keys(stats.injected).length >= PARTS.length - FIRST_LOADED &&
     lastB5.length > 0 && lastB5.every(x => /^P0_/.test(x.name)));

// C: 幂等兜底
const beforeC = (window.__RESOURCES__.movie || []).length;
new Function(mkPart(PARTS[3])).call(g);
const afterC = (window.__RESOURCES__.movie || []).length;
say('=== C 手工重复注入同一片 ===');
say('  注入前 ' + beforeC + ' -> 注入后 ' + afterC);
mark(beforeC === afterC);

// D: 「不能播的线路默认隐藏」（renderSources 真代码）
const fnDead = grabBlock(0, 'function isDeadSource(');
const fnRenderSrc = grabBlock(0, 'function renderSources(');
const makeRs = new Function(`
  var currentSources = [], currentSourceIdx = 0, showAllSources = false;
  var box = { innerHTML: '', textContent: '', kids: [], appendChild(el) { this.kids.push(el); } };
  var titleEl = { textContent: '' };   // $('pvSrcTitle') 必须返回同一个对象，否则读不到写进去的标题
  function $(id) { return id === 'pvSources' ? box : titleEl; }
  function htmlEscape(s) { return String(s); }
  function updatePvLine() {}
  function renderEpisodes() {}
  return (function () {
    var document = { createElement: () => ({ className: '', innerHTML: '', textContent: '', onclick: null }) };
    ${fnDead}
    ${fnRenderSrc}
    return {
      run(sources, idx, all) {
        currentSources = sources; currentSourceIdx = idx; showAllSources = !!all;
        box.kids = []; box.innerHTML = ''; box.textContent = ''; titleEl.textContent = '';
        renderSources();
        return box;
      },
      title() { return titleEl.textContent; }
    };
  })();
`)();
const S = (src, probe, failed) => ({ src, url: 'http://x/' + src, _probe: probe, _failed: failed });
say('=== D 线路列表隐藏不可用 ===');
let rsBox = makeRs.run([S('虎牙', true), S('爱坤', false), S('红牛', true), S('豆瓣', true, true)], 0, false);
const labels = rsBox.kids.map(k => k.textContent || k.innerHTML).join(' | ');
const moreBtn = rsBox.kids.find(k => (k.className || '').indexOf('more') >= 0);
say('  4 条线路(2 可用/1 探测死/1 失败) -> 渲染 ' + rsBox.kids.length + ' 个按钮 | 标题 ' + JSON.stringify(makeRs.title()));
say('    按钮: ' + labels);
mark(rsBox.kids.length === 3 && !!moreBtn && /另有 2 条/.test(moreBtn.textContent || '') &&
     makeRs.title() === '播放源（2）');
rsBox = makeRs.run([S('虎牙', true), S('爱坤', false), S('红牛', true), S('豆瓣', true, true)], 0, true);
say('  展开全部 -> 渲染 ' + rsBox.kids.length + ' 个按钮 | 标题 ' + JSON.stringify(makeRs.title()));
mark(rsBox.kids.length === 5 && /收起不可用线路/.test(rsBox.kids[4].textContent || '') &&
     makeRs.title() === '播放源（4）');
rsBox = makeRs.run([S('虎牙', false), S('爱坤', false)], 0, false);
say('  全部探测失败但当前正在播第 0 条 -> 渲染 ' + rsBox.kids.length + ' 个按钮（必须留住当前线路）');
mark(rsBox.kids.length >= 1 && rsBox.kids[0].className.indexOf('active') >= 0);

// E: 画面冻结自愈（stallEvaluate 真代码）
const fnFrames = grabBlock(0, 'function videoFrameCount(');
const fnStall = grabBlock(0, 'function stallEvaluate(');
const stallEval = new Function('document', `
  var document = arguments[0];
  ${fnFrames}
  ${fnStall}
  return stallEvaluate;
`)({ hidden: false });
const mkV = (t, frames, over) => Object.assign({
  currentTime: t, paused: false, seeking: false, ended: false, readyState: 4,
  getVideoPlaybackQuality: () => ({ totalVideoFrames: frames })
}, over || {});
const newSt = () => ({ lastT: 0, frames: null, hits: 0, fixStep: 0 });
let st = newSt(), seq = [];
seq.push(stallEval(mkV(1, 30), st));    // 建立基线
seq.push(stallEval(mkV(2.5, 75), st));  // 帧数增长 → 正常
seq.push(stallEval(mkV(4, 120), st));
seq.push(stallEval(mkV(5.5, 120), st)); // 时间走、帧不动 → 第 1 次
seq.push(stallEval(mkV(7, 120), st));   // 第 2 次 → 触发一级自愈
seq.push(stallEval(mkV(8.5, 120), st));
seq.push(stallEval(mkV(10, 120), st));  // → 二级
seq.push(stallEval(mkV(11.5, 200), st));// 画面恢复（帧数重新增长）→ 自愈级别归零
seq.push(stallEval(mkV(13, 200), st));  // 冻结重新计数 1
seq.push(stallEval(mkV(14.5, 200), st));// 第 2 次 → **重新从一级开始**（而不是直接跳到三级换线路）
seq.push(stallEval(mkV(16, 200), st));
seq.push(stallEval(mkV(17.5, 200), st));// → 二级
const wantE = ['', '', '', '', 'fix1', '', 'fix2', '', '', 'fix1', '', 'fix2'];
say('=== E 画面冻结自愈判定 ===');
say('  期望 ' + JSON.stringify(wantE));
say('  实际 ' + JSON.stringify(seq));
mark(JSON.stringify(seq) === JSON.stringify(wantE));
const st2 = newSt();
stallEval(mkV(1, 30), st2);
stallEval(mkV(2.5, 30), st2);
const pausedAct = stallEval(mkV(4, 30, { paused: true }), st2);
const seekAct = stallEval(mkV(5, 30, { seeking: true }), st2);
say('  暂停/拖动中不累积: paused=' + JSON.stringify(pausedAct) + ' seeking=' + JSON.stringify(seekAct) + ' hits=' + st2.hits);
mark(pausedAct === '' && seekAct === '' && st2.hits === 0);
const st3 = newSt();
const noQ = { currentTime: 1, paused: false, seeking: false, ended: false, readyState: 4 };
stallEval(noQ, st3);
say('  浏览器不给帧统计时: 采样 ' + JSON.stringify(st3) + '（不启用检测）');
mark(st3.hits === 0 && st3.frames === null);

// F: 跨分类搜索结果逐条按「条目自己的分类」渲染（真 renderGrid）
//    首页搜索是混合列表，徽标/续播键/播放器分类都必须取 it._cat —— 用 currentCat 会全错。
const fnRenderGrid = grabBlock(0, 'function renderGrid(');
const fnCatLabelF = grabBlock(0, 'function catLabel(');
const fnHtmlEsc = grabBlock(0, 'function htmlEscape(');
const opened = [], progressKeys = [];
const fx = new Function('state', `
  var state = arguments[0];
  var searchQuery = state.q, currentCat = state.cat, displayLimit = 50, PAGE_SIZE = 50;
  var CATEGORIES = { home:{label:'首页'}, movie:{label:'电影'}, tv:{label:'剧集'}, anime:{label:'动漫'}, variety:{label:'综艺'}, live:{label:'直播'} };
  var grid = { className: '', innerHTML: '', kids: [], appendChild(el) { this.kids.push(el); } };
  var meta = { textContent: '' }, count = { textContent: '' };
  function $(id) { return id === 'grid' ? grid : (id === 'sectionName' ? meta : count); }
  function cardProgress(k) { state.progressKeys.push(k); return { t: 0, d: 0 }; }
  function openPlayer(it, cat) { state.opened.push([it.name, cat]); }
  function openDetail(it, cat) { state.opened.push([it.name, 'D:' + cat]); }
  function document_createEl() {
    const el = { className: '', innerHTML: '', textContent: '', href: '', style: {}, kids: [],
                 appendChild(c) { this.kids.push(c); }, querySelector() { return this._poster; } };
    el._poster = { appendChild(c) { this.kids = (this.kids || []).concat([c]); }, style: {} };
    return el;
  }
  var document = { createElement: document_createEl };
  ${fnRenderGrid}
  ${fnCatLabelF}
  ${fnHtmlEsc}
  return { grid: grid, meta: meta, count: count, renderGrid: renderGrid };
`)({ q: '功夫女足', cat: 'home', opened: opened, progressKeys: progressKeys });
const fg = fx.grid;
fx.renderGrid([
  { name: '功夫女足', year: '2026', _cat: 'movie', score: 7.5, region: '内地', quality: 'HD',
    cover: 'c1.jpg', url: 'http://x/1', sources: [{ src: '虎牙', url: 'http://x/1' }] },
  { name: '功夫女足外传', year: '2026', _cat: 'anime', score: 8.6, region: '日本',
    cover: 'c2.jpg', url: 'http://x/2', sources: [{ src: '爱坤', url: 'http://x/2' }] },
]);
const fCards = fg.kids.filter(k => (k.className || '').indexOf('card') >= 0);
say('=== F 跨分类结果的逐条分类 ===');
say('  渲染 ' + fCards.length + ' 张卡 | 标题 ' + JSON.stringify(fx.meta.textContent) +
    ' | 续播键 ' + JSON.stringify(progressKeys));
say('  卡片1 meta ' + JSON.stringify((fCards[0].innerHTML.match(/class="meta">([^<]*)</) || [])[1]) +
    ' | 卡片2 meta ' + JSON.stringify((fCards[1].innerHTML.match(/class="meta">([^<]*)</) || [])[1]));
const card1 = fCards[0].innerHTML, card2 = fCards[1].innerHTML;
mark(fCards.length === 2 &&
     fx.meta.textContent === '搜索结果' &&
     /电影 ·/.test((card1.match(/class="meta">([^<]*)</) || [])[1] || '') &&
     /动漫 ·/.test((card2.match(/class="meta">([^<]*)</) || [])[1] || '') &&
     card1.indexOf('ep-badge') < 0 && card2.indexOf('ep-badge') >= 0 &&   // 「全集」只该出现在动漫那条
     progressKeys[0] === 'movie|功夫女足|2026' && progressKeys[1] === 'anime|功夫女足外传|2026');
// 点击卡片进播放器：分类必须取条目自身的，否则续播进度会记到错误的分类下
fCards[0].onclick({ preventDefault() {} });
fCards[1].onclick({ preventDefault() {} });
say('  点击后 openPlayer 收到 ' + JSON.stringify(opened));
mark(opened.length === 2 && opened[0][1] === 'movie' && opened[1][1] === 'anime');

console.log(lines.join('\n'));
console.log('\nVERDICT: ' + (allPass ? 'PASS' : 'FAIL'));
process.exit(allPass ? 0 : 1);
"""


def load_src():
    return WEBPY.read_text(encoding="utf-8")


def const_eval(node):
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return const_eval(node.left) + const_eval(node.right)
    raise ValueError(ast.dump(node)[:80])


def extract_template(src):
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "HTML_TEMPLATE" for t in node.targets):
            return const_eval(node.value)
    raise SystemExit("未找到 HTML_TEMPLATE")


def extract_res(src):
    """__RES__ 在 Python 侧 header 里由 f-string + json.dumps 拼出，无法整体常量折叠，
    故按行取「整行就是一个双引号字面量」的行，逐个解码后拼回 JS。
    ⚠️ 不能用跨行 re.findall('"..."')：它会把「上一行收尾引号 + 间隔 + 下一行起首引号」
    当成一个整体匹配掉，抽出来全是空白（踩过）。"""
    start = src.rindex("\n", 0, src.index("window.__RES__ = function")) + 1
    end = src.index('"    };\\n"', start) + len('"    };\\n"')
    region = src[start:src.index("\n", end)]
    line_re = re.compile(r'^\s*"((?:[^"\\]|\\.)*)"\s*,?\s*$')
    out = []
    for line in region.splitlines():
        m = line_re.match(line)
        if not m:
            break
        out.append(ast.literal_eval('"' + m.group(1) + '"'))
    js = "".join(out).strip()
    if "window.__RES__ = function" not in js:
        raise SystemExit("__RES__ 抽取失败：web.py 里 header 的写法可能变了")
    return js


def main():
    src = load_src()
    tpl = extract_template(src)
    res_js = extract_res(src)

    # ---- 1) JS 语法检查 ----
    blocks = re.findall(r"<script>(.*?)</script>", tpl, re.S)
    print(f"HTML_TEMPLATE {len(tpl):,} 字符 / 内联 script {len(blocks)} 块 / __RES__ {len(res_js)} 字符")
    ok = True
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="probe_websim_"))
    try:
        for i, b in enumerate(blocks):
            p = tmp / f"tpl_s{i}.js"
            p.write_text(b, encoding="utf-8")
            r = subprocess.run([NODE, "--check", str(p)], capture_output=True, text=True)
            print(f"  语法 block#{i} {len(b):,} 字符 -> {'OK' if r.returncode == 0 else 'FAIL'}")
            if r.returncode:
                ok = False
                print(r.stderr[:1500])
        # 关键标记必须在位（__RESSEEN__ 由 Python 侧 header 拼出，不在 HTML 模板里）
        for pat, where, label in [("__INJECTED__", tpl, "模板"), ("__PENDING__", tpl, "模板"),
                                  ("__LOADING__", tpl, "模板"), ("__READY__", tpl, "模板"),
                                  ("PART_CONC", tpl, "模板"), ("function ensureCat", tpl, "模板"),
                                  ("function hasPendingParts", tpl, "模板"),
                                  ("function isDeadSource", tpl, "模板"),
                                  ("function stallEvaluate", tpl, "模板"),
                                  ("function runSearch", tpl, "模板"),
                                  ("function applyHomeSearchView", tpl, "模板"),
                                  ("function appendSearchHint", tpl, "模板"),
                                  ("/site/search?cat=", tpl, "模板"),
                                  (".grid-hint", tpl, "模板"),
                                  ("header-row", tpl, "模板"),
                                  ("__RESSEEN__", src, "源"), ("seen.has(k)", src, "源"),
                                  ("it._cat = c;", src, "源")]:
            if pat not in where:
                ok = False
                print(f"  标记缺失 [{label}]: {pat}")
        # 布局断言：搜索框必须与分类 Tab 同一行（在 <header> 内），且不在 <main> 里重复
        head_html = tpl[tpl.index("<header>"):tpl.index("</header>")]
        body_html = tpl[tpl.index("<main"):]
        for cond, msg in [(('id="search"' in head_html), "搜索框不在 <header> 内（要求与分类 Tab 同一行）"),
                          (('id="tabs"' in head_html and "header-row" in head_html),
                           "分类 Tab / header-row 结构缺失"),
                          (("search-wrap" not in body_html), "<main> 里仍有 search-wrap（搜索框应只保留一份）")]:
            if not cond:
                ok = False
                print(f"  布局检查失败: {msg}")
        print("  关键标记: " + ("全部在位" if ok else "有缺失"))
        m = re.search(r"const PART_CONC = (\d+)", tpl)
        m2 = re.search(r"const PART_RETRY = (\d+)", tpl)
        if not (m and m2):
            raise SystemExit("未能从模板解析 PART_CONC / PART_RETRY")
        conc, retry = m.group(1), m2.group(1)
        print(f"  分片并发 PART_CONC={conc} 重试 PART_RETRY={retry}")

        # ---- 2) 行为仿真 ----
        (tmp / "res.js").write_text(res_js, encoding="utf-8")
        (tmp / "sim.mjs").write_text(HARNESS, encoding="utf-8")
        env = dict(os.environ, WEBPY=str(WEBPY), RESJS=str(tmp / "res.js"),
                   PART_CONC=conc, PART_RETRY=retry)
        r = subprocess.run([NODE, str(tmp / "sim.mjs")], capture_output=True, text=True,
                           env=env, cwd=str(tmp), timeout=180)
        print("\n" + (r.stdout or "").strip())
        if r.stderr.strip():
            print("[node stderr]\n" + r.stderr.strip()[:1500])
        if r.returncode:
            ok = False
        if "VERDICT: PASS" not in (r.stdout or ""):
            ok = False
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n总判定:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
