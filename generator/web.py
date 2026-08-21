# -*- coding: utf-8 -*-
"""卡片式 Web 浏览页生成器

生成一个独立的 index.html，内嵌所有资源数据，提供：
- 顶部大分类 Tab（电影 / 剧集 / 动漫 / 综艺）
- 横向滚动筛选标签（类型 / 地区 / 年代）
- 卡片网格（一行多个卡片）
- 搜索框
- 点击卡片播放 / 复制链接

该页面随 M3U 一起部署到 Cloudflare Pages，可作为资源库首页。
"""
import json
import html
from pathlib import Path
from typing import Dict, List

import config
from generator.m3u import clean_title, prepare_items, _flat_best_items


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
  <title>影视资源库</title>
  <style>
    :root {
      --bg: #f5f5f7;
      --card: #ffffff;
      --text: #1d1d1f;
      --text-secondary: #6e6e73;
      --accent: #ff2d55;
      --accent-light: #ffe5ea;
      --border: rgba(0,0,0,0.08);
      --shadow: 0 4px 20px rgba(0,0,0,0.08);
    }
    * { box-sizing: border-box; -webkit-tap-highlight-color: transparent; }
    body {
      margin: 0;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
      background: var(--bg);
      color: var(--text);
      padding-bottom: 40px;
    }
    header {
      position: sticky;
      top: 0;
      z-index: 100;
      background: rgba(255,255,255,0.92);
      backdrop-filter: saturate(180%) blur(20px);
      border-bottom: 1px solid var(--border);
    }
    .header-inner {
      max-width: 1200px;
      margin: 0 auto;
      padding: 12px 16px;
    }
    .title {
      font-size: 20px;
      font-weight: 700;
      margin: 0 0 12px;
      display: flex;
      align-items: center;
      gap: 8px;
    }
    .title-dot {
      width: 10px; height: 10px;
      border-radius: 50%;
      background: var(--accent);
    }
    .tabs {
      display: flex;
      gap: 8px;
      overflow-x: auto;
      scrollbar-width: none;
    }
    .tabs::-webkit-scrollbar { display: none; }
    .tab {
      flex: 0 0 auto;
      padding: 8px 18px;
      border-radius: 18px;
      font-size: 14px;
      font-weight: 600;
      color: var(--text-secondary);
      background: transparent;
      border: 1px solid transparent;
      cursor: pointer;
      transition: all .2s;
    }
    .tab.active {
      color: #fff;
      background: var(--accent);
    }
    .main {
      max-width: 1200px;
      margin: 0 auto;
      padding: 16px;
    }
    .search-wrap {
      position: relative;
      margin-bottom: 16px;
    }
    .search-wrap svg {
      position: absolute;
      left: 14px; top: 50%;
      transform: translateY(-50%);
      width: 18px; height: 18px;
      fill: var(--text-secondary);
    }
    .search {
      width: 100%;
      padding: 12px 16px 12px 42px;
      border-radius: 14px;
      border: none;
      background: var(--card);
      font-size: 15px;
      outline: none;
      box-shadow: var(--shadow);
    }
    .filter-section {
      margin-bottom: 16px;
    }
    .filter-label {
      font-size: 12px;
      color: var(--text-secondary);
      margin-bottom: 8px;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.5px;
    }
    .filter-tags {
      display: flex;
      gap: 8px;
      overflow-x: auto;
      padding-bottom: 4px;
      scrollbar-width: none;
    }
    .filter-tags::-webkit-scrollbar { display: none; }
    .tag {
      flex: 0 0 auto;
      padding: 7px 14px;
      border-radius: 16px;
      font-size: 13px;
      color: var(--text);
      background: var(--card);
      border: 1px solid var(--border);
      cursor: pointer;
      transition: all .2s;
    }
    .tag.active {
      color: var(--accent);
      background: var(--accent-light);
      border-color: var(--accent-light);
      font-weight: 600;
    }
    .section-title {
      font-size: 18px;
      font-weight: 700;
      margin: 24px 0 14px;
      display: flex;
      align-items: center;
      justify-content: space-between;
    }
    .count {
      font-size: 13px;
      color: var(--text-secondary);
      font-weight: 500;
    }
    .grid {
      display: grid;
      grid-template-columns: repeat(2, 1fr);
      gap: 12px;
    }
    @media (min-width: 640px) { .grid { grid-template-columns: repeat(3, 1fr); } }
    @media (min-width: 900px) { .grid { grid-template-columns: repeat(4, 1fr); } }
    @media (min-width: 1200px) { .grid { grid-template-columns: repeat(5, 1fr); } }
    .card {
      background: var(--card);
      border-radius: 16px;
      overflow: hidden;
      box-shadow: var(--shadow);
      cursor: pointer;
      transition: transform .2s, box-shadow .2s;
      text-decoration: none;
      color: inherit;
      display: block;
    }
    .card:active { transform: scale(0.97); }
    .poster {
      position: relative;
      width: 100%;
      padding-top: 140%;
      background: #e5e5ea;
      overflow: hidden;
    }
    .poster img {
      position: absolute;
      inset: 0;
      width: 100%;
      height: 100%;
      object-fit: cover;
      transition: transform .3s;
    }
    .card:hover .poster img { transform: scale(1.05); }
    .play-icon {
      position: absolute;
      inset: 0;
      display: flex;
      align-items: center;
      justify-content: center;
      background: rgba(0,0,0,0.2);
      opacity: 0;
      transition: opacity .2s;
    }
    .card:hover .play-icon { opacity: 1; }
    .play-icon svg { width: 44px; height: 44px; fill: #fff; }
    .quality-badge {
      position: absolute;
      top: 8px; right: 8px;
      padding: 3px 7px;
      border-radius: 6px;
      font-size: 11px;
      font-weight: 700;
      color: #fff;
      background: rgba(0,0,0,0.65);
    }
    .info {
      padding: 12px;
    }
    .name {
      font-size: 14px;
      font-weight: 600;
      line-height: 1.35;
      overflow: hidden;
      text-overflow: ellipsis;
      display: -webkit-box;
      -webkit-line-clamp: 2;
      -webkit-box-orient: vertical;
      min-height: 38px;
    }
    .meta {
      margin-top: 6px;
      font-size: 12px;
      color: var(--text-secondary);
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .empty {
      grid-column: 1 / -1;
      text-align: center;
      padding: 60px 20px;
      color: var(--text-secondary);
    }
    .empty svg { width: 64px; height: 64px; fill: #d1d1d6; margin-bottom: 12px; }
    .toast {
      position: fixed;
      bottom: 30px;
      left: 50%;
      transform: translateX(-50%);
      padding: 12px 22px;
      border-radius: 24px;
      background: rgba(0,0,0,0.85);
      color: #fff;
      font-size: 14px;
      opacity: 0;
      pointer-events: none;
      transition: opacity .3s;
      z-index: 200;
    }
    .toast.show { opacity: 1; }
  </style>
</head>
<body>
  <header>
    <div class="header-inner">
      <h1 class="title"><span class="title-dot"></span>影视资源库</h1>
      <nav class="tabs" id="tabs"></nav>
    </div>
  </header>
  <main class="main">
    <div class="search-wrap">
      <svg viewBox="0 0 24 24"><path d="M15.5 14h-.79l-.28-.27A6.471 6.471 0 0 0 16 9.5 6.5 6.5 0 1 0 9.5 16c1.61 0 3.09-.59 4.23-1.57l.27.28v.79l5 4.99L20.49 19l-4.99-5zM9.5 14A4.5 4.5 0 1 1 14 9.5 4.5 4.5 0 0 1 9.5 14z"/></svg>
      <input class="search" id="search" type="text" placeholder="搜索片名...">
    </div>
    <div class="filter-section">
      <div class="filter-label">类型</div>
      <div class="filter-tags" id="typeTags"></div>
    </div>
    <div class="filter-section">
      <div class="filter-label">地区</div>
      <div class="filter-tags" id="regionTags"></div>
    </div>
    <div class="filter-section">
      <div class="filter-label">年代</div>
      <div class="filter-tags" id="eraTags"></div>
    </div>
    <div class="section-title">
      <span id="sectionName">全部</span>
      <span class="count" id="resultCount">0</span>
    </div>
    <div class="grid" id="grid"></div>
  </main>
  <div class="toast" id="toast"></div>

  <script>
    const CATEGORIES = __CATEGORIES__;
    const RESOURCES = __RESOURCES__;
    const DIM_LABELS = { media_type: '类型', region: '地区', year: '年代' };

    let currentCat = 'movie';
    let activeFilters = { media_type: '', region: '', year: '' };
    let searchQuery = '';

    const $ = id => document.getElementById(id);

    function initTabs() {
      const tabs = $('tabs');
      tabs.innerHTML = '';
      Object.entries(CATEGORIES).forEach(([key, info]) => {
        const btn = document.createElement('button');
        btn.className = 'tab' + (key === currentCat ? ' active' : '');
        btn.textContent = info.label;
        btn.onclick = () => { currentCat = key; activeFilters = { media_type: '', region: '', year: '' }; render(); };
        tabs.appendChild(btn);
      });
    }

    function makeTags(containerId, dim, values) {
      const c = $(containerId);
      c.innerHTML = '';
      const all = document.createElement('span');
      all.className = 'tag' + (!activeFilters[dim] ? ' active' : '');
      all.textContent = '全部';
      all.onclick = () => { activeFilters[dim] = ''; render(); };
      c.appendChild(all);
      values.forEach(v => {
        const span = document.createElement('span');
        span.className = 'tag' + (activeFilters[dim] === v ? ' active' : '');
        span.textContent = v;
        span.onclick = () => { activeFilters[dim] = v; render(); };
        c.appendChild(span);
      });
    }

    function getFilterValues(cat, dim) {
      const set = new Set();
      RESOURCES[cat].forEach(it => {
        const v = it[dim];
        if (v) set.add(v);
      });
      return Array.from(set).sort((a, b) => {
        // 年代按数字区间排序
        if (dim === 'year') {
          const order = { '2020年代':1, '2010年代':2, '2000年代':3, '90年代':4, '80年代':5, '更早':6 };
          return (order[a] || 99) - (order[b] || 99);
        }
        return a.localeCompare(b, 'zh-CN');
      });
    }

    function filterItems(items) {
      return items.filter(it => {
        if (activeFilters.media_type && it.media_type !== activeFilters.media_type) return false;
        if (activeFilters.region && it.region !== activeFilters.region) return false;
        if (activeFilters.year && it.year !== activeFilters.year) return false;
        if (searchQuery) {
          const q = searchQuery.toLowerCase();
          return (it.name && it.name.toLowerCase().includes(q)) ||
                 (it.media_type && it.media_type.toLowerCase().includes(q)) ||
                 (it.region && it.region.toLowerCase().includes(q));
        }
        return true;
      });
    }

    function openUrl(url) {
      // 尝试用外部播放器打开；移动端可复制链接
      if (/iPhone|iPad|iPod|Android/i.test(navigator.userAgent)) {
        window.location.href = url;
      } else {
        copyToClipboard(url);
      }
    }

    function copyToClipboard(text) {
      if (navigator.clipboard) {
        navigator.clipboard.writeText(text).then(showToast);
      } else {
        const ta = document.createElement('textarea');
        ta.value = text;
        document.body.appendChild(ta);
        ta.select();
        document.execCommand('copy');
        document.body.removeChild(ta);
        showToast();
      }
    }

    function showToast() {
      const t = $('toast');
      t.textContent = '播放链接已复制';
      t.classList.add('show');
      setTimeout(() => t.classList.remove('show'), 2000);
    }

    function renderGrid(items) {
      const grid = $('grid');
      grid.innerHTML = '';
      $('resultCount').textContent = items.length + ' 部';
      $('sectionName').textContent = CATEGORIES[currentCat].label;
      if (!items.length) {
        grid.innerHTML = `<div class="empty">
          <svg viewBox="0 0 24 24"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm0 18c-4.41 0-8-3.59-8-8s3.59-8 8-8 8 3.59 8 8-3.59 8-8 8zm-1-13h2v6h-2zm0 8h2v2h-2z"/></svg>
          <div>没有找到相关资源</div>
        </div>`;
        return;
      }
      items.forEach(it => {
        const card = document.createElement('a');
        card.className = 'card';
        card.href = it.url;
        card.target = '_blank';
        card.onclick = e => {
          e.preventDefault();
          openUrl(it.url);
        };
        const meta = [it.region, it.year, it.quality].filter(Boolean).join(' · ');
        card.innerHTML = `
          <div class="poster">
            <img src="${it.cover || ''}" alt="${htmlEscape(it.name)}" loading="lazy" onerror="this.style.display='none'">
            <div class="play-icon"><svg viewBox="0 0 24 24"><path d="M8 5v14l11-7z"/></svg></div>
            ${it.quality ? `<span class="quality-badge">${htmlEscape(it.quality)}</span>` : ''}
          </div>
          <div class="info">
            <div class="name">${htmlEscape(it.name)}</div>
            <div class="meta">${htmlEscape(meta)}</div>
          </div>
        `;
        grid.appendChild(card);
      });
    }

    function htmlEscape(s) {
      return String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    }

    function render() {
      initTabs();
      makeTags('typeTags', 'media_type', getFilterValues(currentCat, 'media_type'));
      makeTags('regionTags', 'region', getFilterValues(currentCat, 'region'));
      makeTags('eraTags', 'year', getFilterValues(currentCat, 'year'));
      const items = filterItems(RESOURCES[currentCat]);
      renderGrid(items);
    }

    $('search').addEventListener('input', e => {
      searchQuery = e.target.value.trim();
      renderGrid(filterItems(RESOURCES[currentCat]));
    });

    render();
  </script>
</body>
</html>
"""


def _item_to_json(it: dict) -> dict:
    """把数据库条目转成页面需要的轻量字段"""
    return {
        "name": it.get("_clean_name") or clean_title(it["name"]),
        "media_type": it.get("media_type") or "",
        "region": it.get("region") or "",
        "year": str(it.get("year") or ""),
        "quality": it.get("quality") or "",
        "cover": it.get("cover") or "",
        "url": it.get("url") or "",
    }


def generate_index(output_dir: Path = None) -> Path:
    """生成卡片式首页 index.html"""
    out = (output_dir or config.OUTPUT_DIR) / "index.html"
    out.parent.mkdir(parents=True, exist_ok=True)

    resources: Dict[str, List[dict]] = {}
    for cat in config.M3U_OUTPUT:
        items, _ = prepare_items(cat)
        # Web 首页同样去重：每部影片只展示一条最优线路，避免搜索时满屏重复
        resources[cat] = [_item_to_json(it) for it in _flat_best_items(items)]

    categories = {
        cat: {"label": info["label"]}
        for cat, info in config.CATEGORIES.items()
    }

    html_text = HTML_TEMPLATE
    html_text = html_text.replace("__CATEGORIES__", json.dumps(categories, ensure_ascii=False))
    html_text = html_text.replace("__RESOURCES__", json.dumps(resources, ensure_ascii=False))

    out.write_text(html_text, encoding="utf-8")
    print(f"[OK] 已生成 {out}")
    return out
