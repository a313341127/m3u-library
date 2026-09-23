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

const reset = () => {
  g.window.__RESOURCES__ = {}; g.window.__RESSEEN__ = {};
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
let scope;
const makeScope = () => ({
  searchQuery: '', searchTimer: null, displayLimit: 9, PAGE_SIZE: 200, currentCat: 'movie',
  renderCalls: 0, renderGridOnly() { scope.renderCalls++; }, ensureCat,
});
const fireSearch = new Function('scope',
  'with(scope){ return (' + arrow + ')({target:{value:"功夫女足"}}); }');

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

// B1: 搜索触发全库加载
reset(); scope = makeScope();
const beforeParts = Object.keys(stats.injected).length;
fireSearch(scope);
await new Promise(r => setTimeout(r, 1000));
const totalB = (window.__RESOURCES__.movie || []).length;
const uniqB = new Set((window.__RESOURCES__.movie || []).map(x => x.name)).size;
const lastInjected = !!window.__INJECTED__[PARTS[PARTS.length - 1]];
say('=== B1 搜索「功夫女足」正常网速 ===');
say('  搜索前动态注入 ' + beforeParts + ' | 条目 ' + totalB + '/' + EXPECT_TOTAL + ' | 唯一片名 ' + uniqB +
    ' | 末片已加载 ' + lastInjected + ' | 渲染 ' + scope.renderCalls + ' 次');
mark(beforeParts === 0 && totalB === EXPECT_TOTAL && uniqB === totalB && lastInjected && scope.renderCalls >= 2);

// B2: 慢网络下先给即时反馈
reset(); PART_DELAY = 160; scope = makeScope();
fireSearch(scope);
await new Promise(r => setTimeout(r, 360));
const midCalls = scope.renderCalls, midTotal = (window.__RESOURCES__.movie || []).length;
await new Promise(r => setTimeout(r, 1500));
const lateTotal = (window.__RESOURCES__.movie || []).length, lateCalls = scope.renderCalls;
PART_DELAY = 2;
say('=== B2 慢网络(每片160ms) ===');
say('  360ms 时渲染 ' + midCalls + ' 次 / 条目 ' + midTotal + ' | 最终条目 ' + lateTotal + ' | 最终渲染 ' + lateCalls + ' 次');
mark(midCalls === 1 && midTotal < EXPECT_TOTAL && lateTotal === EXPECT_TOTAL && lateCalls >= 2);

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
                                  ("header-row", tpl, "模板"),
                                  ("__RESSEEN__", src, "源"), ("seen.has(k)", src, "源")]:
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
