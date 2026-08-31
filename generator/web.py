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
import re
import os
import hashlib
from datetime import datetime
from pathlib import Path
from typing import Dict, List

import config
from generator.m3u import clean_title, prepare_items, _flat_best_items
from generator import health as _health

# 固定的本地台标目录（data/live_logos/，已进 git；部署时同步到 output/covers/live/）。
# 网页与途播优先引用这里的真实台标，彻底摆脱易失效的外链 CDN。
_LIVE_LOGO_ROOT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "live_logos")



HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
  <title>秦哥影视资源</title>
  <meta name="theme-color" content="#101318">
  <!-- 国内 CDN 常因 Referer 反盗链拒绝页面内播放，全局无 Referer 可让 hls.js / video 直接播放 -->
  <meta name="referrer" content="no-referrer">
  <style>
    :root {
      --bg: #101318;
      --card: #1a1e24;
      --text: #e6e8eb;
      --text-secondary: #8a919c;
      --accent: #ff4757;
      --accent-light: rgba(255,71,87,0.16);
      --border: rgba(255,255,255,0.08);
      --shadow: 0 4px 20px rgba(0,0,0,0.4);
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
      background: rgba(16,19,24,0.92);
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
      color: var(--text);
      caret-color: var(--accent);
      outline: none;
      box-shadow: var(--shadow);
    }
    .search::placeholder { color: var(--text-secondary); opacity: 1; }
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
    .home-view { display: none; }
    .home-sec { margin: 0 0 26px; }
    .home-sec-head {
      display: flex; align-items: baseline; justify-content: space-between;
      gap: 12px; margin: 0 0 12px;
    }
    .home-sec-title { font-size: 16px; font-weight: 600; margin: 0; }
    .home-more {
      flex: 0 0 auto; background: none; border: none; padding: 0;
      color: var(--text-secondary); font-size: 13px; cursor: pointer;
    }
    .home-more:hover { color: var(--accent); }
    .home-row {
      display: grid; grid-auto-flow: column; grid-auto-columns: 42%;
      gap: 10px; overflow-x: auto; padding-bottom: 6px;
      scrollbar-width: thin;
    }
    @media (min-width: 640px) { .home-row { grid-auto-columns: 30%; } }
    @media (min-width: 900px) { .home-row { grid-auto-columns: 22%; } }
    @media (min-width: 1200px) {
      .home-row { grid-auto-flow: row; grid-template-columns: repeat(6, 1fr); overflow-x: visible; }
    }
    .hcard { cursor: pointer; }
    .hcard-poster {
      position: relative; width: 100%; aspect-ratio: 2 / 3;
      border-radius: 10px; overflow: hidden; background: var(--card);
    }
    .hcard-poster img {
      width: 100%; height: 100%; object-fit: cover; display: block;
      transition: transform .25s;
    }
    .hcard:hover .hcard-poster img { transform: scale(1.05); }
    .hcard-score {
      position: absolute; top: 6px; right: 6px;
      padding: 2px 7px; border-radius: 8px;
      background: rgba(0,0,0,0.66); color: #ffcc4d;
      font-size: 11px; font-weight: 600;
    }
    .hcard-rank {
      position: absolute; top: 6px; left: 6px;
      width: 22px; height: 22px; border-radius: 6px;
      display: flex; align-items: center; justify-content: center;
      background: var(--accent); color: #fff; font-size: 12px; font-weight: 600;
    }
    .hcard-tag {
      position: absolute; bottom: 6px; left: 6px;
      padding: 2px 7px; border-radius: 8px;
      background: rgba(0,0,0,0.66); color: #fff; font-size: 11px;
    }
    .hcard-title {
      margin: 7px 0 2px; font-size: 13px; font-weight: 500;
      overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
    }
    .hcard-sub {
      font-size: 11.5px; color: var(--text-secondary);
      overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
    }
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
    .card-info-btn {
      position: absolute; top: 8px; right: 8px;
      padding: 3px 8px; border-radius: 8px;
      font-size: 11px; font-weight: 600; color: #fff;
      background: rgba(0,0,0,0.55); border: 1px solid rgba(255,255,255,0.12);
      cursor: pointer; opacity: 0; transition: opacity .2s; z-index: 3;
      pointer-events: auto;
    }
    .card:hover .card-info-btn { opacity: 1; }
    @media (max-width: 640px) { .card-info-btn { opacity: 1; padding: 4px 9px; } }
    .poster {
      position: relative;
      width: 100%;
      padding-top: 150%;
      background: #232a33;
      overflow: hidden;
      pointer-events: none;
    }
    .poster > * { pointer-events: auto; }
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
    .score-badge {
      position: absolute;
      top: 8px; left: 8px;
      padding: 3px 7px;
      border-radius: 6px;
      font-size: 11px;
      font-weight: 700;
      color: #fff;
      background: rgba(255,159,10,0.92);
    }
    .score-badge.high { background: rgba(255,71,87,0.92); }
    .ep-badge {
      position: absolute;
      bottom: 8px; right: 8px;
      padding: 3px 7px;
      border-radius: 6px;
      font-size: 11px;
      font-weight: 700;
      color: #fff;
      background: rgba(0,0,0,0.65);
    }
    .sort-group {
      display: flex;
      gap: 6px;
    }
    .sort-btn {
      padding: 5px 12px;
      border-radius: 14px;
      font-size: 12px;
      color: var(--text-secondary);
      background: var(--card);
      border: 1px solid var(--border);
      cursor: pointer;
      transition: all .2s;
    }
    .sort-btn.active {
      color: var(--accent);
      background: var(--accent-light);
      border-color: var(--accent-light);
      font-weight: 600;
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
    .empty svg { width: 64px; height: 64px; fill: #3a414b; margin-bottom: 12px; }
    /* 直播频道卡 */
    .live-grid {
      display: grid;
      grid-template-columns: repeat(2, 1fr);
      gap: 10px;
    }
    @media (min-width: 640px) { .live-grid { grid-template-columns: repeat(3, 1fr); } }
    @media (min-width: 900px) { .live-grid { grid-template-columns: repeat(4, 1fr); } }
    @media (min-width: 1200px) { .live-grid { grid-template-columns: repeat(5, 1fr); } }
    .live-card {
      display: flex;
      align-items: center;
      gap: 10px;
      background: var(--card);
      border-radius: 14px;
      padding: 10px 12px;
      box-shadow: var(--shadow);
      cursor: pointer;
      transition: transform .2s;
      border: 1px solid var(--border);
    }
    .live-card:active { transform: scale(0.97); }
    .live-logo {
      flex: 0 0 auto;
      width: 44px; height: 44px;
      border-radius: 10px;
      background: #232a33;
      object-fit: cover;
      padding: 0;
    }
    .live-info { flex: 1; min-width: 0; }
    .live-name {
      font-size: 14px;
      font-weight: 600;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .live-meta {
      margin-top: 4px;
      font-size: 12px;
      color: var(--text-secondary);
      display: flex;
      align-items: center;
      gap: 6px;
    }
    .live-dot {
      width: 6px; height: 6px;
      border-radius: 50%;
      background: #2ed573;
      box-shadow: 0 0 6px rgba(46,213,115,0.8);
    }
    .lat-badge {
      flex: 0 0 auto;
      padding: 2px 8px;
      border-radius: 10px;
      font-size: 11px;
      font-weight: 700;
      color: #fff;
      background: rgba(46,213,115,0.85);
    }
    .lat-badge.mid { background: rgba(255,159,10,0.9); }
    .lat-badge.slow { background: rgba(255,71,87,0.9); }
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
    .modal {
      position: fixed;
      inset: 0;
      z-index: 300;
      display: none;
      align-items: center;
      justify-content: center;
    }
    .modal.show { display: flex; }
    .modal-backdrop {
      position: absolute;
      inset: 0;
      background: rgba(0,0,0,0.85);
    }
    .modal-box {
      position: relative;
      width: calc(100% - 32px);
      max-width: 560px;
      background: var(--card);
      border-radius: 16px;
      overflow: hidden;
      box-shadow: var(--shadow);
    }
    .modal-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      padding: 12px 14px 12px 16px;
      border-bottom: 1px solid var(--border);
    }
    .modal-title {
      font-size: 15px;
      font-weight: 600;
      color: var(--text);
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .modal-close {
      flex: 0 0 auto;
      width: 30px;
      height: 30px;
      border-radius: 50%;
      border: none;
      background: var(--bg);
      color: var(--text-secondary);
      font-size: 18px;
      line-height: 1;
      cursor: pointer;
    }
    .modal-video { background: #000; }
    .modal-video video {
      width: 100%;
      aspect-ratio: 16 / 9;
      display: block;
      background: #000;
    }
    .modal-fallback {
      padding: 40px 20px;
      text-align: center;
      color: var(--text-secondary);
    }
    .modal-fallback p { margin: 0 0 18px; font-size: 14px; }
    .modal-fallback .btn {
      display: inline-block;
      margin: 0 6px;
      padding: 10px 22px;
      border-radius: 20px;
      border: none;
      font-size: 14px;
      cursor: pointer;
    }
    .btn-primary { background: var(--accent); color: #fff; }
    .btn-ghost { background: var(--bg); color: var(--text); }

    /* ===== 内嵌式播放视图（dmhyy 风格：播放器嵌入页面中间 + 线路放下方 + 红绿灯） ===== */
    .player-view {
      display: none;
      flex-direction: column;
      background: #0b0d10;
      border-radius: 16px;
      overflow: hidden;
      margin: 0 0 20px;
      box-shadow: var(--shadow);
      border: 1px solid var(--border);
    }
    .player-view.show { display: flex; }
    .pv-top {
      flex: 0 0 auto;
      display: flex;
      align-items: center;
      gap: 12px;
      padding: 12px 16px;
      background: rgba(16,19,24,0.97);
      border-bottom: 1px solid var(--border);
    }
    .pv-title-wrap {
      flex: 1; min-width: 0;
      display: flex; flex-direction: column; gap: 4px;
    }
    .pv-title {
      font-size: 15px; font-weight: 600;
      overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
    }
    .pv-line {
      font-size: 12px; color: var(--text-secondary);
      overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
    }
    .pv-close {
      flex: 0 0 auto;
      width: 32px; height: 32px;
      display: flex; align-items: center; justify-content: center;
      border-radius: 50%; border: 1px solid var(--border);
      background: var(--card); color: var(--text);
      font-size: 20px; line-height: 1; cursor: pointer;
    }
    .pv-close:active { transform: scale(0.96); }
    .pv-stage {
      position: relative;
      width: 100%;
      background: #000;
      aspect-ratio: 16 / 9;
      max-height: min(70vh, 720px);
      overflow: hidden;
    }
    .pv-video {
      width: 100%; height: 100%;
      display: block; background: #000;
      object-fit: contain;
    }
    .pv-resume {
      position: absolute;
      left: 50%; bottom: 18px; transform: translateX(-50%);
      padding: 9px 20px; border-radius: 22px; border: none;
      background: rgba(255,71,87,0.94); color: #fff; font-size: 14px; font-weight: 600; cursor: pointer;
      display: none;
      box-shadow: 0 4px 16px rgba(0,0,0,0.5);
    }
    .pv-resume.show { display: block; }
    .pv-progress-bar {
      position: absolute; left: 0; bottom: 0; height: 3px; background: var(--accent); width: 0;
    }
    /* 线路检测提示：开播前并发探测所有线路，避免用户对着死链空等 */
    .pv-hint {
      position: absolute; left: 0; right: 0; top: 0; padding: 10px 14px;
      font-size: 12.5px; color: #ffd7a1; background: rgba(0,0,0,0.55);
      backdrop-filter: blur(2px); pointer-events: none; display: none; z-index: 3;
    }
    .pv-hint.show { display: block; }
    .pv-hint.err { color: #ffb3b3; }
    /* 加载/缓冲遮罩：点源后立即可见，避免黑屏干等（对齐 dmhyy 的加载反馈） */
    .pv-loading {
      position: absolute; inset: 0; z-index: 4;
      display: none; flex-direction: column; align-items: center; justify-content: center;
      gap: 16px; background: rgba(0,0,0,0.62); pointer-events: none;
    }
    .pv-loading.show { display: flex; }
    .pv-spinner {
      width: 46px; height: 46px; border-radius: 50%;
      border: 4px solid rgba(255,255,255,0.22);
      border-top-color: var(--accent);
      animation: pv-spin 0.9s linear infinite;
    }
    .pv-loading-text {
      font-size: 13.5px; color: #e8edf2; text-align: center; line-height: 1.7;
      max-width: 86%; padding: 0 12px;
    }
    @keyframes pv-spin { to { transform: rotate(360deg); } }
    .pv-info {
      flex: 0 0 auto;
      padding: 16px;
      background: var(--bg);
      border-top: 1px solid var(--border);
    }
    .pv-meta { font-size: 13px; color: var(--text-secondary); margin: 0 0 12px; line-height: 1.7; }
    .pv-desc {
      font-size: 12.5px; line-height: 1.8; color: var(--text-secondary);
      margin: 0 0 16px; max-height: 6.2em; overflow: hidden;
    }
    .pv-desc:empty { display: none; }
    .pv-section-title {
      font-size: 12px; font-weight: 700; color: var(--text-secondary);
      text-transform: uppercase; letter-spacing: .6px; margin: 0 0 10px;
    }
    .pv-sources { display: flex; flex-wrap: wrap; gap: 10px; }
    .pv-episodes { display: none; flex-wrap: wrap; gap: 8px; margin-bottom: 4px; }
    .pv-episodes.show { display: flex; }
    .pv-ep {
      padding: 7px 12px; border-radius: 10px; font-size: 12px;
      background: var(--card); border: 1px solid var(--border); color: var(--text);
      cursor: pointer; transition: all .15s; max-width: 120px;
      overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
    }
    .pv-ep:hover { border-color: var(--accent); }
    .pv-ep.active { background: var(--accent-light); border-color: var(--accent); color: var(--accent); font-weight: 600; }
    .pv-src {
      display: flex; align-items: center; gap: 8px;
      padding: 10px 14px; border-radius: 12px;
      background: var(--card); border: 1px solid var(--border);
      color: var(--text); font-size: 13px; cursor: pointer; text-align: left;
      transition: border-color .15s, background .15s;
    }
    .pv-src:hover { border-color: var(--accent); }
    .pv-src.active { background: var(--accent-light); border-color: var(--accent); color: var(--accent); font-weight: 600; }
    .pv-src .dot {
      width: 8px; height: 8px; border-radius: 50%;
      background: var(--text-secondary); flex: 0 0 auto;
      box-shadow: 0 0 0 0 rgba(138,145,156,0);
      transition: background .2s, box-shadow .2s;
    }
    .pv-src .dot.ok { background: #2ed573; box-shadow: 0 0 6px rgba(46,213,115,0.55); }
    .pv-src .dot.bad { background: #ff4757; box-shadow: 0 0 6px rgba(255,71,87,0.55); }
    .pv-src.active .dot { background: var(--accent); box-shadow: none; }
    .pv-src.active .dot.ok { background: #2ed573; }
    .pv-src.active .dot.bad { background: #ff4757; }
    .pv-src .label { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .pv-src.resolver { border-left: 3px solid #ff9f43; }
    .pv-src.resolver.active { background: rgba(255,159,67,0.14); border-color: #ff9f43; color: #ff9f43; }
    .pv-src.resolver.active .dot { background: #ff9f43; }
    /* 预检判定失效的线路：红色圆点 + 置灰 */
    .pv-src.dead { opacity: .45; }
    .pv-src.dead .dot, .pv-src.dead .dot.ok, .pv-src.dead .dot.bad { background: #ff4757 !important; box-shadow: 0 0 6px rgba(255,71,87,0.55); }
    .pv-src.dead .label::after { content: ' · 已失效'; font-size: 11px; color: var(--text-secondary); }
    @media (max-width: 640px) {
      .pv-src { padding: 9px 12px; font-size: 12.5px; }
      .pv-stage { max-height: 56vw; }
    }
    .detail-view {
      position: fixed;
      inset: 0;
      z-index: 380;
      display: none;
      flex-direction: column;
      background: var(--bg);
    }
    .detail-view.show { display: flex; }
    .dv-top {
      flex: 0 0 auto;
      display: flex; align-items: center; gap: 12px;
      padding: 10px 14px;
      background: var(--card);
      border-bottom: 1px solid var(--border);
    }
    .dv-body {
      flex: 1 1 auto; overflow-y: auto; min-height: 0;
      display: flex; flex-direction: column; gap: 16px;
      padding: 16px;
    }
    .dv-poster {
      flex: 0 0 auto; width: 100%; max-width: 200px; margin: 0 auto;
      border-radius: 12px; overflow: hidden; background: var(--card);
      aspect-ratio: 2 / 3;
    }
    .dv-poster img { width: 100%; height: 100%; object-fit: cover; display: block; }
    .dv-info { flex: 1 1 auto; min-width: 0; }
    .dv-title { font-size: 19px; font-weight: 600; margin: 0 0 8px; line-height: 1.35; }
    .dv-meta { font-size: 13px; color: var(--text-secondary); margin: 0 0 12px; line-height: 1.7; }
    .dv-tags { display: flex; flex-wrap: wrap; gap: 6px; margin: 0 0 14px; }
    .dv-tag {
      padding: 3px 10px; border-radius: 12px; font-size: 12px;
      background: var(--card); border: 1px solid var(--border); color: var(--text-secondary);
    }
    .dv-tag.score { background: var(--accent-light); border-color: transparent; color: var(--accent); font-weight: 600; }
    .dv-desc {
      font-size: 13px; line-height: 1.85; color: var(--text-secondary);
      margin: 0 0 6px; max-height: 7.4em; overflow: hidden;
    }
    .dv-desc.open { max-height: none; }
    .dv-desc-toggle { font-size: 12px; color: var(--accent); cursor: pointer; margin: 0 0 16px; }
    .dv-actions { display: flex; gap: 10px; margin: 0 0 20px; flex-wrap: wrap; }
    .dv-play {
      padding: 11px 26px; border-radius: 22px; border: none;
      background: var(--accent); color: #fff; font-size: 15px; font-weight: 600; cursor: pointer;
    }
    .dv-play:active { transform: scale(0.97); }
    .dv-ghost {
      padding: 11px 18px; border-radius: 22px; border: 1px solid var(--border);
      background: var(--card); color: var(--text); font-size: 14px; cursor: pointer;
    }
    .dv-section-title { font-size: 12px; font-weight: 700; color: var(--text-secondary); }
    .dv-sources { display: grid; grid-template-columns: repeat(2, 1fr); gap: 8px; }
    .dv-src {
      display: flex; align-items: center; gap: 8px;
      padding: 10px 12px; border-radius: 12px;
      background: var(--card); border: 1px solid var(--border);
      color: var(--text); font-size: 13px; cursor: pointer; text-align: left;
    }
    .dv-src:hover { border-color: var(--accent); }
    .dv-src .label { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    @media (min-width: 900px) {
      .dv-body { flex-direction: row; align-items: flex-start; padding: 24px; gap: 24px; }
      .dv-poster { flex: 0 0 240px; max-width: none; margin: 0; }
      .dv-sources { grid-template-columns: repeat(3, 1fr); }
    }
    @media (min-width: 1200px) {
      .dv-body { max-width: 1100px; margin: 0 auto; width: 100%; }
      .dv-poster { flex: 0 0 280px; }
    }
    .continue-badge {
      position: absolute; bottom: 8px; left: 8px;
      padding: 2px 8px; border-radius: 10px;
      font-size: 11px; font-weight: 700; color: #fff; background: rgba(255,71,87,0.92);
      z-index: 2;
    }
    .continue-bar {
      position: absolute; left: 0; bottom: 0; height: 3px; background: var(--accent); z-index: 2;
    }
    .load-more {
      grid-column: 1 / -1;
      padding: 14px;
      border-radius: 14px;
      border: 1px solid var(--border);
      background: var(--card);
      color: var(--text);
      font-size: 14px;
      font-weight: 600;
      cursor: pointer;
      transition: all .2s;
    }
    .load-more:hover { background: var(--accent-light); border-color: var(--accent); color: var(--accent); }
  </style>
</head>
<body>
  <header>
    <div class="header-inner">
      <h1 class="title"><span class="title-dot"></span>秦哥影视资源</h1>
      <nav class="tabs" id="tabs"></nav>
    </div>
  </header>
  <main class="main">
    <div class="search-wrap">
      <svg viewBox="0 0 24 24"><path d="M15.5 14h-.79l-.28-.27A6.471 6.471 0 0 0 16 9.5 6.5 6.5 0 1 0 9.5 16c1.61 0 3.09-.59 4.23-1.57l.27.28v.79l5 4.99L20.49 19l-4.99-5zM9.5 14A4.5 4.5 0 1 1 14 9.5 4.5 4.5 0 0 1 9.5 14z"/></svg>
      <input class="search" id="search" type="text" placeholder="搜索片名...">
    </div>
    <div class="filter-section" id="mediaFilters">
      <div class="filter-label">类型</div>
      <div class="filter-tags" id="typeTags"></div>
    </div>
    <div class="filter-section" id="regionFilterSec">
      <div class="filter-label">地区</div>
      <div class="filter-tags" id="regionTags"></div>
    </div>
    <div class="filter-section" id="mediaFiltersYear">
      <div class="filter-label">年代</div>
      <div class="filter-tags" id="eraTags"></div>
    </div>
    <div class="filter-section" id="liveFilterSec" style="display:none">
      <div class="filter-label">频道分类</div>
      <div class="filter-tags" id="liveTags"></div>
    </div>
    <div class="player-view" id="playerView">
      <div class="pv-top">
        <div class="pv-title-wrap">
          <div class="pv-title" id="pvTitle"></div>
          <div class="pv-line" id="pvLine">请选择播放源</div>
        </div>
        <button class="pv-close" id="pvClose" title="关闭播放器">&times;</button>
      </div>
      <div class="pv-stage" id="pvStage">
        <video class="pv-video" id="pvVideo" controls playsinline referrerpolicy="no-referrer"></video>
        <button class="pv-resume" id="pvResume"></button>
        <div class="pv-progress-bar" id="pvProgressBar"></div>
        <div class="pv-hint" id="pvHint"></div>
        <div class="pv-loading" id="pvLoading">
          <div class="pv-spinner"></div>
          <div class="pv-loading-text" id="pvLoadingText"></div>
        </div>
      </div>
      <div class="pv-info" id="pvInfo">
        <div class="pv-meta" id="pvMeta"></div>
        <div class="pv-desc" id="pvDesc"></div>
        <div class="pv-section-title" id="pvEpTitle" style="display:none">选集</div>
        <div class="pv-episodes" id="pvEpisodes"></div>
        <div class="pv-section-title" id="pvSrcTitle">播放源</div>
        <div class="pv-sources" id="pvSources"></div>
      </div>
    </div>

    <div class="section-title" id="sectionBar">
      <span style="display:flex;align-items:baseline;gap:8px;">
        <span id="sectionName">全部</span>
        <span class="count" id="resultCount">0</span>
      </span>
      <div class="sort-group" id="sortGroup"></div>
    </div>
    <div class="grid" id="grid"></div>
    <div class="home-view" id="homeView"></div>
  </main>
  <div class="toast" id="toast"></div>

  <div class="detail-view" id="detailView">
    <div class="dv-top">
      <button class="pv-back" id="dvBack" aria-label="返回">&lsaquo;</button>
      <div class="pv-title" id="dvTopTitle"></div>
    </div>
    <div class="dv-body">
      <div class="dv-poster">
        <img id="dvPoster" alt="" referrerpolicy="no-referrer" loading="lazy">
      </div>
      <div class="dv-info">
        <h1 class="dv-title" id="dvTitle"></h1>
        <div class="dv-meta" id="dvMeta"></div>
        <div class="dv-tags" id="dvTags"></div>
        <div class="dv-desc" id="dvDesc"></div>
        <div class="dv-desc-toggle" id="dvDescToggle"></div>
        <div class="dv-actions">
          <button class="dv-play" id="dvPlay">立即播放</button>
          <button class="dv-ghost" id="dvCopy">复制链接</button>
        </div>
        <div class="dv-section-title">播放线路</div>
        <div class="dv-sources" id="dvSources"></div>
      </div>
    </div>
  </div>

  <script src="https://cdn.jsdelivr.net/npm/hls.js@1.5.13/dist/hls.min.js"></script>

__DATA_SCRIPTS__

  <script>
    const CATEGORIES = __CATEGORIES__;
    // 全量数据已分片为 output/web/data_*.js（Cloudflare Pages 单文件上限 25 MiB，
    // 内联会导致 index.html 超限、部署失败）。分片脚本同步加载后写入这些全局变量。
    const RESOURCES = window.__RESOURCES__ || {};
    const LIVE = window.__LIVE_DATA__ || [];
    // 内置中转线路：服务端代理拉流（同源返回，规避浏览器 CORS/反盗链），等价于 dmhyy 的代理播放
    const BUILTIN_RESOLVERS = [
      { name: '本站中转', url: location.origin + '/proxy?u={url}', mode: 'direct' }
    ];
    const RESOLVER_LINES = BUILTIN_RESOLVERS.concat(__RESOLVERS__ || []);
    const LIVE_CATS = { cctv: '央视', satellite: '卫视', local: '地方', hmt: '港澳台' };
    const DIM_LABELS = { media_type: '类型', region: '地区', year: '年代' };

    let currentCat = 'home';
    let activeFilters = { media_type: '', region: '', year: '' };
    let liveFilter = '';
    let searchQuery = '';
    let currentSort = 'pop';   // pop=人气 / latest=最新 / score=评分 / chan=频道序 / lat=延迟
    let displayLimit = 200;    // 首屏最多渲染条目数，避免大列表卡顿
    const PAGE_SIZE = 200;
    let searchTimer = null;

    const $ = id => document.getElementById(id);

    // 综合人气分：播放量按评分加权，低评分大幅降权，没评分按 5 分兜底
    function popScore(it) {
      const hits = it.hits || 0;
      const lines = it.lines || 1;
      // 没评分按 5.0 算；低分（<5）会显著降权，避免低分烂片霸榜
      const s = (it.score > 0 && it.score <= 10) ? it.score : 5.0;
      const weight = Math.pow(s / 10, 2);
      return (hits || lines * 1000) * weight;
    }

    // 排序：人气=综合人气分 最新=年份近到远 评分=豆瓣分高到低
    function sortItems(items) {
      const arr = items.slice();
      const nameCmp = (a, b) => (a.name || '').localeCompare(b.name || '', 'zh-CN');
      if (currentSort === 'latest') {
        // 最新 = 年份（上映时间）降序；同年份内按名称，纯粹按时间呈现
        arr.sort((a, b) => (parseInt(b.year) || 0) - (parseInt(a.year) || 0)
          || nameCmp(a, b));
      } else if (currentSort === 'score') {
        arr.sort((a, b) => (b.score || 0) - (a.score || 0)
          || popScore(b) - popScore(a) || (b.lines || 0) - (a.lines || 0) || nameCmp(a, b));
      } else {
        // 人气：按加权播放量，低评分自动下沉
        arr.sort((a, b) => popScore(b) - popScore(a)
          || (b.lines || 0) - (a.lines || 0) || nameCmp(a, b));
      }
      return arr;
    }

    function initSortGroup() {
      const bar = $('sortGroup');
      bar.innerHTML = '';
      const opts = currentCat === 'live'
        ? [['chan', '频道序'], ['lat', '延迟']]
        : [['pop', '人气'], ['latest', '最新'], ['score', '评分']];
      if (currentCat === 'live' && currentSort !== 'chan' && currentSort !== 'lat') currentSort = 'chan';
      if (currentCat !== 'live' && currentSort === 'chan') currentSort = 'pop';
      if (currentCat !== 'live' && currentSort === 'lat') currentSort = 'pop';
      opts.forEach(([key, label]) => {
        const btn = document.createElement('button');
        btn.className = 'sort-btn' + (currentSort === key ? ' active' : '');
        btn.textContent = label;
        btn.onclick = () => { currentSort = key; displayLimit = PAGE_SIZE; renderGridOnly(); };
        bar.appendChild(btn);
      });
    }

    function initTabs() {
      const tabs = $('tabs');
      tabs.innerHTML = '';
      Object.entries(CATEGORIES).forEach(([key, info]) => {
        const btn = document.createElement('button');
        btn.className = 'tab' + (key === currentCat ? ' active' : '');
        btn.textContent = info.label;
        btn.onclick = () => switchCat(key);
        tabs.appendChild(btn);
      });
    }

    function makeLiveTags() {
      const c = $('liveTags');
      c.innerHTML = '';
      const all = document.createElement('span');
      all.className = 'tag' + (!liveFilter ? ' active' : '');
      all.textContent = '全部';
      all.onclick = () => { liveFilter = ''; displayLimit = PAGE_SIZE; render(); };
      c.appendChild(all);
      const counts = {};
      LIVE.forEach(it => { counts[it.c] = (counts[it.c] || 0) + 1; });
      Object.entries(LIVE_CATS).forEach(([key, label]) => {
        const span = document.createElement('span');
        span.className = 'tag' + (liveFilter === key ? ' active' : '');
        span.textContent = label + ' ' + (counts[key] || 0);
        span.onclick = () => { liveFilter = key; displayLimit = PAGE_SIZE; render(); };
        c.appendChild(span);
      });
    }

    function makeTags(containerId, dim, values) {
      const c = $(containerId);
      c.innerHTML = '';
      const all = document.createElement('span');
      all.className = 'tag' + (!activeFilters[dim] ? ' active' : '');
      all.textContent = '全部';
      all.onclick = () => { activeFilters[dim] = ''; displayLimit = PAGE_SIZE; render(); };
      c.appendChild(all);
      values.forEach(v => {
        const span = document.createElement('span');
        span.className = 'tag' + (activeFilters[dim] === v ? ' active' : '');
        span.textContent = v;
        span.onclick = () => { activeFilters[dim] = v; displayLimit = PAGE_SIZE; render(); };
        c.appendChild(span);
      });
    }

    // 地区标签白名单（顺序即显示顺序）
    const REGION_ORDER = ['内地', '香港', '台湾', '美国', '日本', '英国', '韩国', '印度', '加拿大', '法国', '德国', '泰国', '西班牙', '俄罗斯', '澳大利亚', '巴西', '意大利', '菲律宾', '马来西亚', '墨西哥'];

    function normalizeRegion(raw) {
      if (!raw) return '';
      const s = String(raw);
      // 合拍片优先识别华语区
      if (/(中国|大陆|华语)/.test(s)) return '内地';
      if (/香港/.test(s)) return '香港';
      if (/台湾/.test(s)) return '台湾';
      for (const r of REGION_ORDER) {
        if (s.includes(r)) return r;
      }
      return '';
    }

    function getFilterValues(cat, dim) {
      const set = new Set();
      RESOURCES[cat].forEach(it => {
        let v = it[dim];
        if (dim === 'region') {
          v = normalizeRegion(v);
        }
        if (v) set.add(v);
      });
      const arr = Array.from(set);
      if (dim === 'year') {
        return arr.sort((a, b) => {
          if (a === '更早') return 1;
          if (b === '更早') return -1;
          return parseInt(b, 10) - parseInt(a, 10); // 近->远
        });
      }
      if (dim === 'region') {
        return arr.sort((a, b) => REGION_ORDER.indexOf(a) - REGION_ORDER.indexOf(b));
      }
      return arr.sort((a, b) => a.localeCompare(b, 'zh-CN'));
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

    let hlsPlayer = null;
    let currentSources = [];
    let currentSourceIdx = 0;
    let currentBaseIdx = 0;    // 当前选中的原始源索引（解析线路基于此源）
    let currentItemKey = '';   // 进度记忆 key: cat|name|year
    let currentUrl = '';
    let pvProgressTimer = null;
    let loadTimeout = null;
    let loadStartIdx = -1;
    let playerToken = 0;       // 每次开播自增，用于丢弃过期的异步线路检测结果
    let loadTimer = null;      // 加载网速 timer
    let loadStartTime = 0;     // 开始加载时刻（performance.now）
    let loadBytes = 0;         // 已下载字节（hls.js FRAG_LOADED / performance entries 累加）
    let currentIsNative = false;   // 当前是否为原生 <video> 直链（非 hls）
    let nativeTotal = 0;           // 同源探测到的文件总字节（用于估算原生下载网速）
    let _lastNativeBytes = 0;      // 上一拍估算已下载字节
    let _lastNativeT = 0;          // 上一拍时间戳
    let currentEpIdx = 0;          // 当前选中的选集下标
    let hlsStarted = false;        // 本次播放是否已成功起播（用于区分「加载阶段失效」与「播放中瞬时抖动」）
    let hlsFatalStreak = 0;       // 连续致命错误计数：视频有进度即清零，连续多次才放弃切源

    // 是否为 HLS：带 .m3u8/.m3u 或未知短链交给 hls.js；明确的视频文件走原生播放
    function isHls(url) {
      // 中转线路 /proxy?u=... 需看内部原始地址判断真实类型
      if (url && url.indexOf('/proxy?u=') >= 0) {
        const m = url.match(/[?&]u=([^&]+)/);
        if (m) { try { return isHls(decodeURIComponent(m[1])); } catch (e) {} }
        return true;
      }
      const u = (url || '').split('?')[0].toLowerCase();
      if (u.endsWith('.m3u8') || u.endsWith('.m3u')) return true;
      if (/\\.(mp4|webm|ogg|mov|mkv|flv)$/.test(u)) return false;
      return true;
    }

    function fmtTime(s) {
      s = Math.max(0, Math.floor(s || 0));
      const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), ss = s % 60;
      if (h > 0) return h + ':' + String(m).padStart(2, '0') + ':' + String(ss).padStart(2, '0');
      return m + ':' + String(ss).padStart(2, '0');
    }

    function cardProgress(key) {
      try {
        const t = parseFloat(localStorage.getItem('pv_' + key) || '0') || 0;
        const d = parseFloat(localStorage.getItem('pvd_' + key) || '0') || 0;
        return { t, d };
      } catch (e) { return { t: 0, d: 0 }; }
    }

    function readProgress(key) {
      try { return parseFloat(localStorage.getItem('pv_' + key) || '0') || 0; } catch (e) { return 0; }
    }

    function saveProgress() {
      if (!currentItemKey) return;
      try {
        const v = $('pvVideo');
        if (v.duration && isFinite(v.duration)) {
          localStorage.setItem('pvd_' + currentItemKey, String(v.duration));
          if (v.currentTime > 5 && v.currentTime < v.duration - 5) {
            localStorage.setItem('pv_' + currentItemKey, String(v.currentTime));
          }
        }
      } catch (e) {}
    }

    function startProgressWatch() {
      if (pvProgressTimer) clearInterval(pvProgressTimer);
      pvProgressTimer = setInterval(() => {
        const v = $('pvVideo');
        if (v.duration && isFinite(v.duration)) {
          const pct = (v.currentTime / v.duration) * 100;
          $('pvProgressBar').style.width = pct.toFixed(1) + '%';
          if (v.currentTime > 5 && v.currentTime < v.duration - 5) saveProgress();
        }
      }, 4000);
    }

    // 打开沉浸式播放视图（item 含 name/url/sources；live 由调用方包装）
    // 简介按分类分片存放：首次打开某分类的详情页时才加载，
    // 避免首屏多下载 12.48 MiB（详见 _write_desc_shards 说明）。
    const _descLoaded = {};
    function loadDescs(cat, cb) {
      if (cat === 'live' || _descLoaded[cat]) { if (cb) cb(); return; }
      _descLoaded[cat] = true;
      const s = document.createElement('script');
      s.src = '/web/desc_' + encodeURIComponent(cat) + '.js';
      s.onload = function () { if (cb) cb(); };
      s.onerror = function () { if (cb) cb(); };
      document.head.appendChild(s);
    }

    function getDesc(cat, item) {
      const m = (window.__DESCS__ || {})[cat];
      if (!m) return '';
      return m[(item.name || '') + '|' + (item.year || '')] || '';
    }

    let currentDetail = null;

    function openDetail(item, cat) {
      currentDetail = { item: item, cat: cat };
      $('dvTopTitle').textContent = item.name || '';
      $('dvTitle').textContent = item.name || '';
      $('dvMeta').textContent = [item.year, item.region, item.media_type]
        .filter(Boolean).join(' · ');

      const tags = [];
      if (item.score > 0) tags.push('<span class="dv-tag score">' + item.score + ' 分</span>');
      if (item.quality) tags.push('<span class="dv-tag">' + htmlEscape(item.quality) + '</span>');
      // 源站是整剧集单地址（无分集数据），用「全集」角标替代 dmhyy 的选集模块
      tags.push('<span class="dv-tag">'
        + (cat === 'tv' || cat === 'anime' || cat === 'variety' ? '全集' : '正片') + '</span>');
      if (item.lines > 1) tags.push('<span class="dv-tag">' + item.lines + ' 条线路</span>');
      $('dvTags').innerHTML = tags.join('');

      const img = $('dvPoster');
      if (item.cover) { img.style.display = ''; img.src = item.cover; }
      else { img.style.display = 'none'; img.removeAttribute('src'); }
      img.onerror = function () { img.style.display = 'none'; };

      const descBox = $('dvDesc');
      const toggle = $('dvDescToggle');
      descBox.textContent = '简介加载中…';
      descBox.classList.remove('open');
      toggle.textContent = '';
      toggle.onclick = null;
      loadDescs(cat, function () {
        // 异步期间用户可能已切换到别的条目
        if (!currentDetail || currentDetail.item !== item) return;
        const d = getDesc(cat, item);
        descBox.textContent = d || '暂无简介';
        if (d && d.length > 60) {
          toggle.textContent = '展开';
          toggle.onclick = function () {
            const open = descBox.classList.toggle('open');
            this.textContent = open ? '收起' : '展开';
          };
        }
      });

      const box = $('dvSources');
      box.innerHTML = '';
      const list = (item.sources && item.sources.length)
        ? item.sources : [{ src: '默认线路', url: item.url }];
      list.forEach(function (s, i) {
        const b = document.createElement('button');
        b.className = 'dv-src';
        b.innerHTML = '<span class="label">' + htmlEscape(s.src || ('线路' + (i + 1))) + '</span>';
        // 点哪条线路就播哪条，在内嵌播放器加载
        b.onclick = function () { closeDetail(); openPlayer(item, cat, i); };
        box.appendChild(b);
      });

      $('detailView').classList.add('show');
      document.body.style.overflow = 'hidden';
    }

    function closeDetail() {
      $('detailView').classList.remove('show');
      document.body.style.overflow = '';
      currentDetail = null;
    }

    // 开播前并发探测每条线路是否可取到流（走 worker /probe，服务端发起，不受浏览器跨域限制）。
    // 目的：先剔除已失效线路，直接落到第一条可用线路，避免用户对着死链空等超时。
    function probeUrl(u, ms) {
      return new Promise(function (resolve) {
        if (!u) return resolve(null);
        let done = false;
        const finish = v => { if (!done) { done = true; clearTimeout(timer); resolve(v); } };
        const timer = setTimeout(() => finish(null), ms || 4000);
        fetch(location.origin + '/probe?u=' + encodeURIComponent(u), { cache: 'no-store' })
          .then(r => r.json().catch(() => ({})))
          .then(d => finish(!!(d && d.ok)))
          .catch(() => finish(null));
      });
    }

    function showHint(text, isErr) {
      const h = $('pvHint');
      if (!text) { h.classList.remove('show'); return; }
      h.textContent = text;
      h.classList.toggle('err', !!isErr);
      h.classList.add('show');
    }

    // 加载/缓冲遮罩：点源后立即显示，缓冲/换源时给出可见反馈
    function showLoading(text) {
      const el = $('pvLoading');
      const t = $('pvLoadingText');
      if (text) t.innerHTML = text;
      el.classList.add('show');
    }
    function hideLoading() {
      $('pvLoading').classList.remove('show');
    }

    // 加载网速：实时显示下载速度（Mb/s），比单纯秒表更直观
    function startLoadTimer() {
      stopLoadTimer();
      loadStartTime = performance.now();
      loadBytes = 0;
      nativeTotal = 0;
      _lastNativeBytes = 0;
      _lastNativeT = 0;
      currentIsNative = false;
      _loadSeen.clear();
      updateLoadSpeed();
      loadTimer = setInterval(updateLoadSpeed, 300);
    }
    const _loadSeen = new Set();
    // 同源探测原生直链文件总大小（绕过跨域 CORS 屏蔽 transferSize 的限制），
    // 用于「总大小 × 已缓冲比例」估算实时下载网速。
    function probeNativeSize(url) {
      try {
        const u = location.origin + '/proxy?u=' + encodeURIComponent(url);
        fetch(u, { method: 'GET', headers: { 'Range': 'bytes=0-0' }, referrerPolicy: 'no-referrer' })
          .then(r => {
            if (!r.ok && r.status !== 206) return;
            const cr = r.headers.get('content-range') || '';
            const m = /[0-9]+\/([0-9]+)/.exec(cr);
            if (m && parseInt(m[1], 10) > 0) nativeTotal = parseInt(m[1], 10);
            else {
              const cl = parseInt(r.headers.get('content-length') || '0', 10);
              if (cl > 0) nativeTotal = cl;
            }
          })
          .catch(() => {});
      } catch (e) {}
    }
    function updateLoadSpeed() {
      const elapsed = Math.max(0.1, (performance.now() - loadStartTime) / 1000);
      // 1) hls.js / 同源 performance entries：直接取真实字节
      try {
        performance.getEntriesByType('resource').forEach(e => {
          const name = e.name || '';
          if (/\.(m3u8|m3u|ts|mp4|flv|aac|mp3|webm)(\?|$)/i.test(name) && e.transferSize > 0) {
            const key = name + '|' + e.startTime;
            if (!_loadSeen.has(key)) { _loadSeen.add(key); loadBytes += e.transferSize; }
          }
        });
      } catch (e) {}
      if (loadBytes > 0) {
        const bps = (loadBytes * 8) / elapsed;
        showLoading('加载中… ' + formatBitrate(bps));
        return;
      }
      // 2) 原生跨域 mp4：transferSize 被 CORS 屏蔽，用「总大小(同源探测) × 已缓冲比例」估算实时网速
      const v = $('pvVideo');
      if (currentIsNative && nativeTotal > 0 && v && v.duration > 0 && v.buffered && v.buffered.length) {
        const be = v.buffered.end(v.buffered.length - 1);
        const frac = Math.max(0, Math.min(1, be / v.duration));
        const downloaded = frac * nativeTotal;
        const now = performance.now();
        const dt = (now - _lastNativeT) / 1000;
        if (_lastNativeT > 0 && dt > 0.3) {
          const inst = ((downloaded - _lastNativeBytes) * 8) / dt;
          _lastNativeBytes = downloaded; _lastNativeT = now;
          if (inst > 1000 && inst < 2e12) {
            showLoading('加载中… ' + formatBitrate(inst));
            return;
          }
        } else if (_lastNativeT === 0) {
          _lastNativeBytes = downloaded; _lastNativeT = now;
        }
        // 还没算出瞬时速度，先给个缓冲进度感（不带秒数，避免误导）
        showLoading('缓冲中… ' + Math.round(frac * 100) + '%');
        return;
      }
      // 3) 兜底：未知大小
      showLoading('连接中…');
    }
    function formatBitrate(bps) {
      if (bps >= 1e9) return (bps / 1e9).toFixed(2) + ' Gb/s';
      if (bps >= 1e6) return (bps / 1e6).toFixed(1) + ' Mb/s';
      if (bps >= 1e3) return (bps / 1e3).toFixed(1) + ' Kb/s';
      return bps.toFixed(0) + ' b/s';
    }
    function stopLoadTimer() {
      if (loadTimer) { clearInterval(loadTimer); loadTimer = null; }
    }

    // 返回候选线路的下标数组：直连源在前、中转线路在后
    function candidateOrder(cat) {
      const resolverList = (window.RESOLVER_LINES || []).filter(r => r && r.url);
      const order = [];
      for (let i = 0; i < currentSources.length; i++) order.push(i);
      if (cat !== 'live') {
        for (let j = 0; j < resolverList.length; j++) order.push(currentSources.length + j);
      }
      return order;
    }

    function urlOfIdx(idx) {
      const resolverList = (window.RESOLVER_LINES || []).filter(r => r && r.url);
      if (idx < currentSources.length) {
        const s = currentSources[idx];
        return s ? s.url : '';
      }
      const r = resolverList[idx - currentSources.length];
      const base = currentSources[currentBaseIdx] || currentSources[0] || {};
      return r ? buildResolverUrl(r, base.url) : '';
    }

    function openPlayer(item, cat, startIdx) {
      const myToken = ++playerToken;
      currentItemKey = cat + '|' + (item.name || '') + '|' + (item.year || '');
      currentSources = (item.sources && item.sources.length)
        ? item.sources.slice() : [{ src: '默认线路', url: item.url }];
      // 默认首选服务端中转线路：可自定义 Referer/UA 绕过源站反盗链；
      // 若中转失败再回退尝试直连源。直播保持直连，避免 worker 代理实时流。
      const resolverList = (window.RESOLVER_LINES || []).filter(r => r && r.url);
      currentBaseIdx = 0;
      if (typeof startIdx === 'number' && startIdx >= 0) {
        currentSourceIdx = startIdx;             // 详情页点了具体线路 → 就播那一条
        currentBaseIdx = Math.min(startIdx, currentSources.length - 1);
      } else {
        // 点卡片直接播放：默认先选最佳原始源（生成期已按可用度排序），探测后再决定；直播保持直连
        currentSourceIdx = (cat === 'live') ? 0 : 0;
      }
      $('pvTitle').textContent = item.name || '播放';
      $('pvLine').textContent = '正在初始化播放源…';
      const meta = [item.region, item.year, item.quality, item.media_type]
        .filter(Boolean).join(' · ');
      $('pvMeta').textContent = meta || (cat === 'live' ? '直播频道' : '');
      // 播放页展示简介（dmhyy 播放页同样是 标题/年份·地区/简介 三件套）。
      // 简介按分类分片、按需加载，直播无简介。
      const descEl = $('pvDesc');
      descEl.textContent = '';
      const itemKey = currentItemKey;
      loadDescs(cat, function () {
        if (currentItemKey !== itemKey) return;
        descEl.textContent = getDesc(cat, item) || '';
      });
      // 保存当前 item，详情页按钮可复用
      currentDetail = { item: item, cat: cat };
      const video = $('pvVideo');
      video.pause(); video.removeAttribute('src'); video.load();
      if (hlsPlayer) { try { hlsPlayer.destroy(); } catch (e) {} hlsPlayer = null; }
      // 重置续播标记/进度条，避免上一个影片的残留
      $('pvResume').classList.remove('show');
      $('pvResume').onclick = null;
      $('pvProgressBar').style.width = '0%';
      hideLoading();
      $('playerView').classList.add('show');
      currentEpIdx = 0;
      renderSources();
      renderEpisodes();
      // 内嵌播放器：打开时滚动到播放器位置，而不是锁 body 滚动
      setTimeout(() => { $('playerView').scrollIntoView({ behavior: 'smooth', block: 'start' }); }, 30);

      // 点了具体线路（详情页/换源）→ 直接播，不再探测；
      // 否则先并发体检所有线路，挑第一条可用的播。
      startLoadTimer();
      if (typeof startIdx === 'number' && startIdx >= 0) {
        showHint('');
        loadSource(currentSourceIdx, true);
        return;
      }
      const order = candidateOrder(cat);
      if (order.length <= 1 || cat === 'live') {
        showHint('');
        loadSource(currentSourceIdx, true);
        return;
      }
      const picked = currentSourceIdx;
      Promise.all(order.map(i => probeUrl(urlOfIdx(i)))).then(function (res) {
        if (myToken !== playerToken) return;         // 用户已切到别的影片
        let okIdx = -1, unknownIdx = -1;
        for (let k = 0; k < order.length; k++) {
          const idx = order[k];
          // 探测的是中转线路时，底层取的是当前 base 源；base 不可达则中转也无意义
          if (res[k] === true && okIdx < 0) okIdx = idx;
          else if (res[k] === null && unknownIdx < 0) unknownIdx = idx;
        }
        // 把探测结论记到线路上，供红绿灯展示
        for (let k = 0; k < order.length; k++) {
          if (order[k] < currentSources.length) currentSources[order[k]]._probe = res[k];
        }
        const okCount = res.filter(v => v === true).length;
        const unknownCount = res.filter(v => v === null).length;
        let target = okIdx >= 0 ? okIdx : (unknownIdx >= 0 ? unknownIdx : picked);
        currentSourceIdx = target;
        if (target >= 0 && target < currentSources.length) currentBaseIdx = target;
        renderSources();
        if (okCount === 0 && unknownCount === 0) {
          // 全部明确不可达：直接报失效，不再让用户空等超时
          stopLoadTimer();
          showLoading('当前资源暂不可用<br><span style="font-size:12px;color:#aab">所有线路均无法连接</span>');
          return;
        }
        loadSource(target, true);
      });
    }

    function renderSources() {
      const box = $('pvSources');
      box.innerHTML = '';
      const resolverList = (window.RESOLVER_LINES || []).filter(r => r && r.url);
      const total = currentSources.length + resolverList.length;
      $('pvSrcTitle').textContent = total > 1 ? '播放源（' + total + '）' : '播放源';

      // 原始源：圆点按后端健康度/探测结果染色（绿=可用 红=失效 灰=未知）
      currentSources.forEach((s, i) => {
        const isDead = s._probe === false || s._failed;
        // 探测成功 或 未被标记为失败 且 有 url 的源都先显示绿色，点播失败后再变红
        const isOk = s._probe === true || (!isDead && s.url);
        const dotClass = isDead ? 'bad' : (isOk ? 'ok' : '');
        const btn = document.createElement('button');
        btn.className = 'pv-src' + (i === currentSourceIdx ? ' active' : '')
          + (s._failed ? ' failed' : '')
          + (s._probe === false ? ' dead' : '');
        btn.innerHTML = '<span class="dot ' + dotClass + '"></span><span class="label">'
          + htmlEscape(s.src || ('线路' + (i + 1))) + '</span>';
        btn.onclick = () => {
          if (i !== currentSourceIdx) { currentSourceIdx = i; currentBaseIdx = i; currentEpIdx = 0; renderSources(); loadSource(i, false); }
        };
        box.appendChild(btn);
      });

      // 解析线路（基于当前选中的原始源）：绿色表示基础源可用（解析成功率高）
      resolverList.forEach((r, i) => {
        const idx = currentSources.length + i;
        const isActive = idx === currentSourceIdx;
        const base = currentSources[currentBaseIdx] || currentSources[0] || {};
        const baseOk = base.url && !base._failed && base._probe !== false;
        const dotClass = baseOk ? 'ok' : '';
        const btn = document.createElement('button');
        btn.className = 'pv-src resolver' + (isActive ? ' active' : '');
        btn.innerHTML = '<span class="dot ' + dotClass + '"></span><span class="label">'
          + htmlEscape(r.name || ('解析' + (i + 1))) + '</span>';
        btn.onclick = () => {
          if (idx !== currentSourceIdx) { currentSourceIdx = idx; currentEpIdx = 0; renderSources(); loadSource(idx, false); }
        };
        box.appendChild(btn);
      });
      updatePvLine();
      renderEpisodes();
    }

    // 渲染选集（仅当当前线路含多集时显示）。不同线路的选集相互独立。
    function renderEpisodes() {
      const box = $('pvEpisodes');
      const title = $('pvEpTitle');
      const s = currentSources[currentSourceIdx];
      const eps = (s && s.episodes) || [];
      if (!eps || eps.length <= 1) {
        box.innerHTML = '';
        box.classList.remove('show');
        title.style.display = 'none';
        return;
      }
      title.style.display = '';
      box.classList.add('show');
      box.innerHTML = '';
      eps.forEach((ep, i) => {
        const label = ep.label || ('第' + (i + 1) + '集');
        const btn = document.createElement('button');
        btn.className = 'pv-ep' + (i === currentEpIdx ? ' active' : '');
        btn.textContent = label;
        btn.title = label;
        btn.onclick = () => {
          if (i === currentEpIdx) return;
          currentEpIdx = i;
          renderEpisodes();
          playEpisode(ep.url);
        };
        box.appendChild(btn);
      });
    }

    // 播放某一集（同线路内切换集数，不重置线路）
    function playEpisode(epUrl) {
      const video = $('pvVideo');
      video.pause(); video.removeAttribute('src'); video.load();
      if (hlsPlayer) { try { hlsPlayer.destroy(); } catch (e) {} hlsPlayer = null; }
      $('pvResume').classList.remove('show');
      $('pvResume').onclick = null;
      $('pvProgressBar').style.width = '0%';
      startLoadTimer();
      playUrl(epUrl, currentSourceIdx, false);
    }

    function updatePvLine() {
      const resolverList = (window.RESOLVER_LINES || []).filter(r => r && r.url);
      let text = '';
      if (currentSourceIdx >= currentSources.length) {
        const r = resolverList[currentSourceIdx - currentSources.length];
        text = r ? (r.name || '解析线路') : '解析线路';
      } else {
        const s = currentSources[currentSourceIdx];
        const q = (currentDetail && currentDetail.item && currentDetail.item.quality) ? currentDetail.item.quality : '';
        text = (s ? (s.src || ('线路' + (currentSourceIdx + 1))) : '正在加载…') + (q ? ' · ' + q : '');
      }
      $('pvLine').textContent = text;
    }

    function buildResolverUrl(r, originalUrl) {
      const tpl = (r && r.url) || '';
      return tpl.replace(/\x7burl\x7d/g, encodeURIComponent(originalUrl || ''));
    }

    function clearLoadTimeout() {
      if (loadTimeout) { clearTimeout(loadTimeout); loadTimeout = null; }
      loadStartIdx = -1;
    }

    function startLoadTimeout(idx) {
      clearLoadTimeout();
      loadStartIdx = idx;
      loadTimeout = setTimeout(() => {
        if (loadStartIdx === idx) handleSourceFail(idx, false, 'timeout');
      }, 12000);
    }

    function loadSource(idx, resume) {
      const resolverList = (window.RESOLVER_LINES || []).filter(r => r && r.url);
      const isResolver = idx >= currentSources.length;
      let s;
      if (isResolver) {
        const r = resolverList[idx - currentSources.length];
        const base = currentSources[currentBaseIdx] || currentSources[0] || {};
        const resolverUrl = buildResolverUrl(r, base.url);
        s = { src: r.name, url: resolverUrl, _resolver: r };
      } else {
        s = currentSources[idx];
        if (s) currentBaseIdx = idx;
      }
      if (!s) return;
      currentUrl = s.url;
      const video = $('pvVideo');
      $('pvProgressBar').style.width = '0%';
      video.pause(); video.removeAttribute('src'); video.load();
      if (hlsPlayer) { try { hlsPlayer.destroy(); } catch (e) {} hlsPlayer = null; }
      hlsStarted = false;
      hlsFatalStreak = 0;
      startLoadTimeout(idx);

      if (isResolver && s._resolver && s._resolver.mode === 'json') {
        // json 模式：先 fetch 解析接口拿真实 URL（需接口支持 CORS）
        fetchJsonResolver(s._resolver, s.url, idx, resume);
        return;
      }
      playUrl(s.url, idx, resume);
    }

    function fetchJsonResolver(r, resolverUrl, idx, resume) {
      fetch(resolverUrl, { method: 'GET', referrerPolicy: 'no-referrer' })
        .then(resp => resp.json().catch(() => ({})))
        .then(data => {
          const realUrl = data && (data.url || data.data && data.data.url || data.m3u8 || data.playUrl);
          if (realUrl) {
            currentUrl = realUrl;
            playUrl(realUrl, idx, resume);
          } else {
            handleSourceFail(idx, true);
          }
        })
        .catch(err => {
          // fetch 跨域失败时 fallback 为 direct：直接播放解析接口 URL
          if (r.url) {
            const base = currentSources[currentBaseIdx] || currentSources[0] || {};
            currentUrl = buildResolverUrl(r, base.url);
            playUrl(currentUrl, idx, resume);
          } else {
            handleSourceFail(idx, true);
          }
        });
    }

    function playUrl(url, idx, resume) {
      const video = $('pvVideo');
      // 缓冲/可播事件：缓冲时显示遮罩，开始播放即隐藏（断流/拖动 seek 也会触发）
      video.onwaiting = () => { if (!loadTimer) showLoading('视频缓冲中…'); };
      video.onplaying = () => { hlsStarted = true; hlsFatalStreak = 0; stopLoadTimer(); hideLoading(); clearLoadTimeout(); };
      video.oncanplay = () => { hlsStarted = true; stopLoadTimer(); hideLoading(); clearLoadTimeout(); };
      // 视频一旦有进度，说明链路通畅，清零致命错误连击计数（避免把瞬时抖动累计成失败）
      video.ontimeupdate = () => { if (video.currentTime > 1) { hlsStarted = true; hlsFatalStreak = 0; } };
      video.onpause = () => { if (!video.seeking) hideLoading(); };
      // 原生播放时通过 performance entries 估算下载速度
      video.onprogress = () => {
        try {
          performance.getEntriesByType('resource').forEach(e => {
            const name = e.name || '';
            if (name === (video.currentSrc || video.src) && e.transferSize > 0) {
              const key = name + '|' + e.startTime;
              if (!_loadSeen.has(key)) { _loadSeen.add(key); loadBytes += e.transferSize; }
            }
          });
        } catch (e) {}
      };
      startLoadTimeout(idx);
      if (isHls(url)) {
        if (video.canPlayType('application/vnd.apple.mpegurl')) {
          video.src = url;
          video.onerror = () => { video.onerror = null; handleSourceFail(idx); };
          video.onloadeddata = () => { video.onerror = null; stopLoadTimer(); hideLoading(); clearLoadTimeout(); };
          video.play().catch(() => {});
        } else if (window.Hls && Hls.isSupported()) {
          hlsPlayer = new Hls({ maxBufferLength: 30, enableWorker: false });
          hlsPlayer.loadSource(url);
          hlsPlayer.attachMedia(video);
          hlsPlayer.on(Hls.Events.MANIFEST_PARSED, () => { stopLoadTimer(); hideLoading(); clearLoadTimeout(); video.play().catch(() => {}); });
          hlsPlayer.on(Hls.Events.FRAG_LOADED, (ev, data) => {
            if (data && data.stats) {
              const bytes = data.stats.loaded || data.stats.total || 0;
              if (bytes > 0) loadBytes += bytes;
            }
          });
          hlsPlayer.on(Hls.Events.ERROR, (ev, data) => {
            if (!data.fatal) return;
            const alreadyPlaying = hlsStarted || (video.currentTime > 1 && !video.paused);
            const giveup = () => handleSourceFail(idx);
            if (data.type === Hls.ErrorTypes.NETWORK_ERROR) {
              const code = (data.response && data.response.code) || 0;
              // 加载阶段：403/401/530（源码站反盗链/拉黑）或未知(code 0)基本不可恢复，直接切源
              if (!alreadyPlaying && (code === 403 || code === 401 || code === 530 || code === 0)) {
                giveup();
                return;
              }
              // 已起播：单分片瞬时抖动（CDN 节点轮换/签名过期/超时）hls.js 可自愈，
              // 连续多次才放弃切源；每次视频有进度都会把 streak 清零。
              hlsFatalStreak++;
              if (hlsFatalStreak >= 3) { giveup(); return; }
              showLoading('网络抖动，自动重试…');
              try { hlsPlayer.startLoad(); } catch (e) { giveup(); }
            } else if (data.type === Hls.ErrorTypes.MEDIA_ERROR) {
              hlsFatalStreak++;
              if (hlsFatalStreak >= 3) { giveup(); return; }
              try { hlsPlayer.recoverMediaError(); } catch (e) { giveup(); }
            } else {
              // 未知致命错误：先尝试重载，连续多次才切源
              hlsFatalStreak++;
              if (hlsFatalStreak >= 3) { giveup(); return; }
              try { hlsPlayer.startLoad(); } catch (e) { giveup(); }
            }
          });
        } else {
          handleSourceFail(idx);
        }
      } else {
        video.src = url;
        currentIsNative = true;
        probeNativeSize(url);
        video.onerror = () => { video.onerror = null; handleSourceFail(idx); };
        video.onloadeddata = () => { video.onerror = null; stopLoadTimer(); hideLoading(); clearLoadTimeout(); };
        video.play().catch(() => {});
      }
      startProgressWatch();
      if (resume) {
        const t = readProgress(currentItemKey);
        if (t > 5) {
          let resumed = false;
          const doResume = () => {
            if (resumed) return;
            const v = $('pvVideo');
            if (!v || !isFinite(v.duration) || v.duration <= 0) return;
            resumed = true;
            const btn = $('pvResume');
            btn.classList.remove('show');
            if (v.currentTime < t - 2) v.currentTime = t;
            v.play().catch(() => {
              // 移动端浏览器常阻止自动播放， fallback 显示按钮
              btn.textContent = '继续观看 · ' + fmtTime(t);
              btn.onclick = () => { v.currentTime = t; btn.classList.remove('show'); v.play().catch(() => {}); };
              btn.classList.add('show');
            });
          };
          video.addEventListener('loadedmetadata', doResume, { once: true });
          video.addEventListener('canplay', doResume, { once: true });
        }
      }
    }

    // 某线路不可用：标记失败并自动切换；优先顺序为 中转 > 直连，
    // 因为服务端中转可自定义 Referer/UA，最能绕过源站反盗链。
    function handleSourceFail(idx, resolverFail, reason) {
      const resolverList = (window.RESOLVER_LINES || []).filter(r => r && r.url);
      const isResolver = idx >= currentSources.length;
      if (!isResolver && currentSources[idx]) currentSources[idx]._failed = true;
      renderSources();
      clearLoadTimeout();
      stopLoadTimer();

      // 尝试下一个未失败的原始源：优先跳过预检已判定失效的线路，减少无谓等待
      const trySource = () => {
        for (let pass = 0; pass < 2; pass++) {
          for (let i = 0; i < currentSources.length; i++) {
            const s = currentSources[i];
            if (i === idx || s._failed) continue;
            if (pass === 0 && s._probe === false) continue;
            currentSourceIdx = i; currentBaseIdx = i; currentEpIdx = 0; renderSources(); loadSource(i, false);
            return true;
          }
        }
        return false;
      };
      // 尝试下一个未使用过的中转线路
      const tryResolver = () => {
        for (let j = 0; j < resolverList.length; j++) {
          const ridx = currentSources.length + j;
          if (ridx !== idx) {
            currentSourceIdx = ridx; currentBaseIdx = 0; currentEpIdx = 0; renderSources(); loadSource(ridx, false);
            return true;
          }
        }
        return false;
      };

      if (isResolver) {
        // 中转失败：回退尝试直连源；直连也失败再换其他中转
        if (trySource()) { showToast('中转线路不可用，尝试直连'); return; }
        if (tryResolver()) { showToast('中转线路不可用，已切换'); return; }
      } else {
        // 直连失败：先换其他直连；全部直连失败再切中转
        if (trySource()) { showToast('线路不可用，已切换'); return; }
        if (tryResolver()) { showToast('直连源不可用，已切到中转线路'); return; }
      }
      // 全部失败后保留遮罩提示，区分“超时”与“不可用”
      const reasonText = reason === 'timeout'
        ? '连接超时，当前网络/源站响应较慢'
        : '当前资源暂不可用，所有线路均无法连接';
      showLoading(reasonText + '<br><span style="font-size:12px;color:#aab">请手动切换下方其他线路</span>');
    }

    function pvSeek(t) {
      const v = $('pvVideo');
      if (isFinite(t)) v.currentTime = Math.max(0, t);
    }

    function toggleFullscreen() {
      const v = $('pvVideo');
      if (!document.fullscreenElement) { if (v.requestFullscreen) v.requestFullscreen(); }
      else { if (document.exitFullscreen) document.exitFullscreen(); }
    }

    function closePlayer() {
      saveProgress();
      clearLoadTimeout();
      stopLoadTimer();
      if (hlsPlayer) { try { hlsPlayer.destroy(); } catch (e) {} hlsPlayer = null; }
      const video = $('pvVideo');
      video.pause(); video.removeAttribute('src'); video.load();
      $('playerView').classList.remove('show');
      hideLoading();
      if (pvProgressTimer) { clearInterval(pvProgressTimer); pvProgressTimer = null; }
      // 退出全屏（若用户在全屏模式下关闭播放器）
      if (document.fullscreenElement && document.exitFullscreen) document.exitFullscreen();
    }

    // 播放视图内的键盘快捷键（避免在 video 原生控件聚焦时与其冲突）
    document.addEventListener('keydown', e => {
      // 详情页在最上层时，Esc 关闭详情页、Enter 直接播放
      if ($('detailView').classList.contains('show')) {
        if (e.key === 'Escape') { closeDetail(); }
        else if (e.key === 'Enter') {
          const d = currentDetail;
          if (d) { e.preventDefault(); closeDetail(); openPlayer(d.item, d.cat); }
        }
        return;
      }
      if (!$('playerView').classList.contains('show')) return;
      const v = $('pvVideo');
      // 内嵌播放器：Esc 关闭播放器；空格/F/方向键控制播放（video 聚焦时由其原生处理）
      if (document.activeElement && document.activeElement.tagName === 'VIDEO') {
        if (e.key === 'Escape') { e.preventDefault(); closePlayer(); }
        return;
      }
      if (e.key === 'Escape') { e.preventDefault(); closePlayer(); }
      else if (e.key === ' ') { e.preventDefault(); if (v.paused) v.play().catch(() => {}); else v.pause(); }
      else if (e.key === 'ArrowRight') { pvSeek(v.currentTime + 10); }
      else if (e.key === 'ArrowLeft') { pvSeek(v.currentTime - 10); }
      else if (e.key === 'f' || e.key === 'F') { toggleFullscreen(); }
    });

    $('pvClose').onclick = closePlayer;

    $('dvBack').onclick = closeDetail;
    $('dvPlay').onclick = function () {
      const d = currentDetail;
      if (d) { closeDetail(); openPlayer(d.item, d.cat); }
    };
    $('dvCopy').onclick = function () {
      const d = currentDetail;
      if (!d) return;
      const s = d.item.sources || [];
      copyToClipboard((s[0] && s[0].url) || d.item.url || '');
    };

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

    function showToast(msg) {
      const t = $('toast');
      t.textContent = msg || '播放链接已复制';
      t.classList.add('show');
      setTimeout(() => t.classList.remove('show'), 2000);
    }

    function renderGrid(items) {
      const grid = $('grid');
      grid.className = 'grid';
      grid.innerHTML = '';
      const total = items.length;
      const shown = items.slice(0, displayLimit);
      $('resultCount').textContent = total > shown.length ? shown.length + ' / ' + total + ' 部' : total + ' 部';
      $('sectionName').textContent = CATEGORIES[currentCat].label;
      if (!shown.length) {
        grid.innerHTML = `<div class="empty">
          <svg viewBox="0 0 24 24"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm0 18c-4.41 0-8-3.59-8-8s3.59-8 8-8 8 3.59 8 8-3.59 8-8 8zm-1-13h2v6h-2zm0 8h2v2h-2z"/></svg>
          <div>没有找到相关资源</div>
        </div>`;
        return;
      }
      shown.forEach(it => {
        const card = document.createElement('a');
        card.className = 'card';
        card.href = it.url;
        card.target = '_blank';
        card.onclick = e => {
          e.preventDefault();
          // dmhyy 式：点卡片直接弹出播放器（内嵌页面中间）播最佳线路
          openPlayer(it, currentCat);
        };
        const meta = [it.region, it.year, it.quality].filter(Boolean).join(' · ');
        card.innerHTML = `
          <div class="poster">
            <img src="${it.cover || ''}" alt="${htmlEscape(it.name)}" loading="lazy" onerror="this.style.display='none'">
            <div class="play-icon"><svg viewBox="0 0 24 24"><path d="M8 5v14l11-7z"/></svg></div>
            ${it.quality ? `<span class="quality-badge">${htmlEscape(it.quality)}</span>` : ''}
            ${it.score > 0 ? `<span class="score-badge${it.score >= 8 ? ' high' : ''}">${it.score.toFixed(1)}</span>` : ''}
            ${(currentCat === 'tv' || currentCat === 'anime' || currentCat === 'variety')
              ? '<span class="ep-badge">全集</span>' : ''}
          </div>
          <div class="info">
            <div class="name">${htmlEscape(it.name)}</div>
            <div class="meta">${htmlEscape(meta)}</div>
          </div>
        `;
        grid.appendChild(card);
        // 详情页入口：卡片右上角显示「详情」小按钮，点击不进播放
        const infoBtn = document.createElement('button');
        infoBtn.className = 'card-info-btn';
        infoBtn.textContent = '详情';
        infoBtn.title = '查看简介与线路';
        infoBtn.onclick = e => { e.preventDefault(); e.stopPropagation(); openDetail(it, currentCat); };
        const posterEl = card.querySelector('.poster');
        posterEl.appendChild(infoBtn);
        posterEl.style.position = 'relative';
        // 续播标记：本地存过播放进度则显示「续」徽标 + 进度条
        const cp = cardProgress(currentCat + '|' + it.name + '|' + (it.year || ''));
        if (cp.t > 5) {
          const poster = posterEl;
          const badge = document.createElement('div');
          badge.className = 'continue-badge';
          badge.textContent = '续';
          poster.appendChild(badge);
          if (cp.d > 0) {
            const bar = document.createElement('div');
            bar.className = 'continue-bar';
            bar.style.width = Math.min(100, (cp.t / cp.d) * 100) + '%';
            poster.appendChild(bar);
          }
        }
      });
      if (total > shown.length) {
        const more = document.createElement('button');
        more.className = 'load-more';
        more.textContent = '加载更多（还剩 ' + (total - shown.length) + ' 部）';
        more.onclick = () => { displayLimit += PAGE_SIZE; renderGrid(items); };
        grid.appendChild(more);
      }
    }

    function htmlEscape(s) {
      return String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    }

    function latBadgeClass(lat) {
      if (!lat) return '';
      if (lat < 150) return '';
      if (lat < 400) return ' mid';
      return ' slow';
    }

    function renderLiveGrid(items) {
      const grid = $('grid');
      grid.className = 'live-grid';
      grid.innerHTML = '';
      const total = items.length;
      const shown = items.slice(0, displayLimit);
      $('resultCount').textContent = total > shown.length ? shown.length + ' / ' + total + ' 个频道' : total + ' 个频道';
      $('sectionName').textContent = '直播';
      if (!shown.length) {
        grid.innerHTML = `<div class="empty">
          <svg viewBox="0 0 24 24"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm0 18c-4.41 0-8-3.59-8-8s3.59-8 8-8 8 3.59 8 8-3.59 8-8 8zm-1-13h2v6h-2zm0 8h2v2h-2z"/></svg>
          <div>没有找到相关频道</div>
        </div>`;
        return;
      }
      shown.forEach(it => {
        const card = document.createElement('a');
        card.className = 'live-card';
        card.href = it.u;
        card.onclick = e => {
          e.preventDefault();
          openPlayer({ name: it.n, url: it.u, sources: [{ src: '直播线路', url: it.u }], year: '' }, 'live');
        };
        card.innerHTML = `
          ${it.l ? `<img class="live-logo" src="${it.l}" loading="lazy" onerror="this.style.display='none'">`
                 : `<div class="live-logo"></div>`}
          <div class="live-info">
            <div class="live-name">${htmlEscape(it.n)}</div>
            <div class="live-meta"><span class="live-dot"></span>${LIVE_CATS[it.c] || ''}</div>
          </div>
          ${it.lat ? `<span class="lat-badge${latBadgeClass(it.lat)}">${it.lat}ms</span>` : ''}
        `;
        grid.appendChild(card);
      });
      if (total > shown.length) {
        const more = document.createElement('button');
        more.className = 'load-more';
        more.textContent = '加载更多（还剩 ' + (total - shown.length) + ' 个频道）';
        more.onclick = () => { displayLimit += PAGE_SIZE; renderLiveGrid(items); };
        grid.appendChild(more);
      }
    }

    function filterLive() {
      return LIVE.filter(it => {
        if (liveFilter && it.c !== liveFilter) return false;
        if (searchQuery) {
          return it.n && it.n.toLowerCase().includes(searchQuery.toLowerCase());
        }
        return true;
      });
    }

    function sortLive(items) {
      const arr = items.slice();
      if (currentSort === 'lat') {
        arr.sort((a, b) => (a.lat || 1e9) - (b.lat || 1e9));
      }
      // 频道序：LIVE 数据本身已按分类顺序+频道号排好，无需再排
      return arr;
    }

    function renderGridOnly() {
      initSortGroup(); // 刷新排序按钮高亮状态
      if (currentCat === 'live') {
        renderLiveGrid(sortLive(filterLive()));
      } else {
        renderGrid(sortItems(filterItems(RESOURCES[currentCat])));
      }
    }

    function switchCat(cat) {
      currentCat = cat;
      activeFilters = { media_type: '', region: '', year: '' };
      liveFilter = '';
      currentSort = cat === 'live' ? 'chan' : 'pop';
      displayLimit = PAGE_SIZE;
      render();
    }

    function hcardHtml(it, rank) {
      const score = it.score > 0 ? it.score.toFixed(1) : '';
      const sub = [it.year, it.media_type].filter(Boolean).join(' · ');
      let h = '<div class="hcard-poster">';
      if (rank) h += '<span class="hcard-rank">' + rank + '</span>';
      if (score) h += '<span class="hcard-score">' + score + '</span>';
      if (it.lines > 1) h += '<span class="hcard-tag">' + it.lines + ' 线路</span>';
      if (it.cover) {
        // 注意：不要在这里写内联 onerror——本模板是 HTML 里的 <script> 块，
        // 内联引号会被提前截断导致语法错误。onerror 在 renderHome 里用 JS 绑定。
        h += '<img src="' + htmlEscape(it.cover) + '" loading="lazy" referrerpolicy="no-referrer">';
      }
      h += '</div>'
        + '<div class="hcard-title">' + htmlEscape(it.name) + '</div>'
        + '<div class="hcard-sub">' + htmlEscape(sub) + '</div>';
      return h;
    }

    function renderHome() {
      const box = $('homeView');
      box.innerHTML = '';
      const cats = Object.keys(RESOURCES).filter(c => RESOURCES[c] && RESOURCES[c].length);

      const addSection = (title, entries, moreCat, ranked) => {
        if (!entries.length) return;
        const sec = document.createElement('section');
        sec.className = 'home-sec';
        const head = document.createElement('div');
        head.className = 'home-sec-head';
        const h = document.createElement('h2');
        h.className = 'home-sec-title';
        h.textContent = title;
        head.appendChild(h);
        if (moreCat) {
          const more = document.createElement('button');
          more.className = 'home-more';
          more.textContent = '更多 ›';
          more.onclick = () => switchCat(moreCat);
          head.appendChild(more);
        }
        sec.appendChild(head);
        const row = document.createElement('div');
        row.className = 'home-row';
        entries.forEach((e, i) => {
          const card = document.createElement('div');
          card.className = 'hcard';
          card.innerHTML = hcardHtml(e.it, ranked ? (i + 1) : 0);
          const img = card.querySelector('.hcard-poster img');
          if (img) img.onerror = function () { this.style.visibility = 'hidden'; };
          card.onclick = () => openPlayer(e.it, e.cat);
          row.appendChild(card);
        });
        sec.appendChild(row);
        box.appendChild(sec);
      };

      const all = [];
      cats.forEach(c => RESOURCES[c].forEach(it => all.push({ it: it, cat: c })));

      // 热播榜：全站按人气取前 10（对齐 dmhyy 的排行榜模块）
      addSection('热播榜',
        all.slice().sort((a, b) => (b.it.hits || 0) - (a.it.hits || 0)).slice(0, 10), '', true);
      // 最近更新：按采集更新时间倒序（updated 只到日期，同日按人气排）
      addSection('最近更新',
        all.slice().sort((a, b) => (b.it.updated || '').localeCompare(a.it.updated || '')
          || (b.it.hits || 0) - (a.it.hits || 0)).slice(0, 18), '', false);
      // 各分类板块：按评分取前 12
      cats.forEach(c => {
        addSection((CATEGORIES[c] && CATEGORIES[c].label) || c,
          RESOURCES[c].slice().sort((a, b) => (b.score || 0) - (a.score || 0)
            || (b.hits || 0) - (a.hits || 0)).slice(0, 12).map(it => ({ it: it, cat: c })), c, false);
      });
    }

    // 页面生命周期：切后台/离开页面前保存进度；返回前台/bfcache 恢复时继续播放
    document.addEventListener('visibilitychange', () => {
      if (document.hidden) {
        saveProgress();
      } else {
        const v = $('pvVideo');
        if (v && v.paused && !v.ended && v.currentTime > 0 && $('playerView').classList.contains('show')) {
          v.play().catch(() => {});
        }
      }
    });
    window.addEventListener('pagehide', saveProgress);
    window.addEventListener('beforeunload', saveProgress);
    window.addEventListener('pageshow', e => {
      if (!e.persisted) return;
      const v = $('pvVideo');
      if (v && $('playerView').classList.contains('show')) v.play().catch(() => {});
    });

    function render() {
      const isLive = currentCat === 'live';
      const isHome = currentCat === 'home';
      initTabs();
      initSortGroup();
      // 首页：隐藏筛选/排序/结果条，只展示多板块
      $('mediaFilters').style.display = (isLive || isHome) ? 'none' : '';
      $('regionFilterSec').style.display = (isLive || isHome) ? 'none' : '';
      $('mediaFiltersYear').style.display = (isLive || isHome) ? 'none' : '';
      $('liveFilterSec').style.display = isLive ? '' : 'none';
      $('playerView').style.display = isHome ? 'none' : '';
      $('sectionBar').style.display = isHome ? 'none' : '';
      $('grid').style.display = isHome ? 'none' : '';
      $('homeView').style.display = isHome ? 'block' : 'none';
      $('search').placeholder = isLive ? '搜索频道...' : '搜索片名...';
      if (isHome) { renderHome(); return; }
      if (isLive) {
        makeLiveTags();
      } else {
        makeTags('typeTags', 'media_type', getFilterValues(currentCat, 'media_type'));
        makeTags('regionTags', 'region', getFilterValues(currentCat, 'region'));
        makeTags('eraTags', 'year', getFilterValues(currentCat, 'year'));
      }
      renderGridOnly();
    }

    $('search').addEventListener('input', e => {
      searchQuery = e.target.value.trim();
      if (searchTimer) clearTimeout(searchTimer);
      searchTimer = setTimeout(() => {
        displayLimit = PAGE_SIZE;
        renderGridOnly();
      }, 300);
    });

    render();
  </script>
</body>
</html>
"""


def _item_to_json(it: dict, sources: list = None) -> dict:
    """把数据库条目转成页面需要的轻量字段

    sources: 该片所有播放线路（换源用），形如 [{"src": 源名, "url": 播放地址, "episodes": [...]}, ...]；
    单线路影片传入空列表即可（播放器自动隐藏换源面板）。
    """
    try:
        score = round(float(it.get("_best_score") or 0), 1)
    except (TypeError, ValueError):
        score = 0.0
    # 解析多集选集（入库时序列化为 JSON）
    episodes = []
    raw_eps = it.get("episodes") or ""
    if raw_eps:
        try:
            eps = json.loads(raw_eps)
            if isinstance(eps, list) and len(eps) > 1:
                episodes = eps
        except Exception:
            pass
    return {
        "name": it.get("_clean_name") or clean_title(it["name"]),
        "media_type": it.get("media_type") or "",
        "region": _normalize_region(it.get("region")),
        "year": _normalize_year(it.get("year")),
        "quality": it.get("quality") or "",
        "cover": it.get("cover") or "",
        # 网页侧统一走「可播地址」：能直连就直连，否则换成本站同源中转
        "url": _health.play_url(it.get("url") or "") or it.get("url") or "",
        "score": score,
        "hits": int(it.get("_best_hits") or 0),
        "lines": int(it.get("_lines") or 1),
        # 只取日期部分（YYYY-MM-DD）用于「最近更新」排序，比完整时间戳省一半体积
        "updated": (it.get("updated_at") or "")[:10],
        "sources": sources or [],
        "episodes": episodes,
    }


def _normalize_year(raw) -> str:
    """年份标签：直接用具体年份，脏数据（未来年份/非法值）丢弃"""
    try:
        y = int(raw or 0)
    except (ValueError, TypeError):
        return ""
    this_year = datetime.now().year
    if y <= 0 or y > this_year:
        return ""
    if y < 1970:
        return "更早"
    return str(y)


# 地区标签白名单（顺序即显示顺序）
_REGION_ORDER = ["内地", "香港", "台湾", "美国", "日本", "英国", "韩国", "印度", "加拿大", "法国",
                 "德国", "泰国", "西班牙", "俄罗斯", "澳大利亚", "巴西", "意大利", "菲律宾",
                 "马来西亚", "墨西哥"]


def _normalize_region(raw: str) -> str:
    """把原始地区字段映射成页面标签"""
    if not raw:
        return ""
    s = str(raw)
    # 合拍片优先识别华语区
    if re.search(r"(中国|大陆|华语)", s):
        return "内地"
    if "香港" in s:
        return "香港"
    if "台湾" in s:
        return "台湾"
    for r in _REGION_ORDER:
        if r in s:
            return r
    return ""


def generate_index(output_dir: Path = None) -> Path:
    """生成卡片式首页 index.html"""
    out = (output_dir or config.OUTPUT_DIR) / "index.html"
    out.parent.mkdir(parents=True, exist_ok=True)

    resources: Dict[str, List[dict]] = {}
    desc_map: Dict[str, Dict[str, str]] = {}
    _drop_dead = os.environ.get("WEB_KEEP_DEAD", "0") != "1"
    _dead_stat: Dict[str, int] = {}
    for cat in config.M3U_OUTPUT:
        items, _ = prepare_items(cat)
        # 线路体检：剔除已确认全线失效的播放地址（源站 CDN 大面积跑路，
        # 不剔的话 3/4 的卡片点了必然黑屏）。可用线路中「能直连」的排最前。
        if _drop_dead:
            kept = [it for it in items if _health.playable(it.get("url") or "")]
            _dead_stat[cat] = len(items) - len(kept)
            items = kept
        # 聚合每部影片的所有播放线路（换源用）：国内直连源已在 prepare_items 排最前，
        # 截断到 8 条避免 JSON 过大；单线路影片 sources 为空列表。
        src_map: Dict[tuple, list] = {}
        for it in items:
            key = (it["_clean_name"], it.get("year") or "")
            url = it.get("url") or ""
            if not url:
                continue
            lst = src_map.setdefault(key, [])
            if len(lst) < 8:
                play = _health.play_url(url)
                if play is None:
                    continue
                # 解析多集选集
                eps = []
                raw_eps = it.get("episodes") or ""
                if raw_eps:
                    try:
                        ep_list = json.loads(raw_eps)
                        if isinstance(ep_list, list) and len(ep_list) > 1:
                            eps = [{"label": e.get("label", ""), "url": _health.play_url(e.get("url", "")) or e.get("url", "")}
                                   for e in ep_list if e.get("url")]
                    except Exception:
                        pass
                # _rank 仅用于排序（直连优先、需中转的靠后），落盘前移除
                lst.append({"src": it.get("line_name") or it.get("source") or ("线路%d" % (len(lst) + 1)),
                            "url": play, "episodes": eps, "_rank": _health.rank(url)})
        for lst in src_map.values():
            lst.sort(key=lambda s: s.get("_rank", 1))
            for s in lst:
                s.pop("_rank", None)
        # 统计每部影片的线路数（多少个源收录，作为人气的兜底指标）；
        # 并聚合所有线路中最大的人气/评分（保留的线路可能来自无人气数据的源）
        agg: Dict[tuple, dict] = {}
        for it in items:
            key = (it["_clean_name"], it.get("year") or "")
            a = agg.setdefault(key, {"lines": 0, "hits": 0, "score": 0.0})
            a["lines"] += 1
            a["hits"] = max(a["hits"], int(it.get("hits") or 0))
            try:
                a["score"] = max(a["score"], float(it.get("score") or 0))
            except (TypeError, ValueError):
                pass
        for it in items:
            a = agg[(it["_clean_name"], it.get("year") or "")]
            it["_lines"] = a["lines"]
            it["_best_hits"] = a["hits"]
            it["_best_score"] = a["score"]
        # Web 首页同样去重：每部影片只展示一条最优线路，避免搜索时满屏重复；
        # 同时把聚合到的全部线路（换源）一并带出。
        flat = _flat_best_items(items)
        resources[cat] = [_item_to_json(it, src_map.get((it["_clean_name"], it.get("year") or "")))
                         for it in flat]
        # 简介单独收集：主分片不放简介（全量简介 11.98 MiB，会让首屏从 13 MiB 涨到
        # 25 MiB，手机端体验很差），改为按分类生成独立分片，打开详情页时按需加载。
        dmap: Dict[str, str] = {}
        for it in flat:
            jkey = _desc_key(it.get("_clean_name") or clean_title(it["name"]),
                             _normalize_year(it.get("year")))
            d = (it.get("description") or "").strip()
            if d:
                dmap[jkey] = d[:DESC_MAX_CHARS]
        desc_map[cat] = dmap

    # 首页放在第一位（dmhyy 式多板块：热播榜 / 最近更新 / 各分类板块）
    categories: Dict[str, dict] = {"home": {"label": "首页"}}
    for cat, info in config.CATEGORIES.items():
        categories[cat] = {"label": info["label"]}
    # 直播 Tab（频道已按 分类顺序+频道号+延迟 排好，Web 端展示最优线路）
    live_data = _load_live_json()
    if live_data:
        categories["live"] = {"label": "直播"}

    html_text = HTML_TEMPLATE
    html_text = html_text.replace("__CATEGORIES__", json.dumps(categories, ensure_ascii=False))
    html_text = html_text.replace("__RESOLVERS__", json.dumps(getattr(config, "RESOLVER_LINES", []), ensure_ascii=False))
    # 全量数据分片外置（不再内联进 index.html）：见 _write_data_shards 说明。
    html_text = html_text.replace("__DATA_SCRIPTS__", _write_data_shards(resources, live_data, out.parent))
    # 简介按分类单独分片，详情页按需加载（不进首屏，见 _write_desc_shards 说明）。
    _write_desc_shards(desc_map, out.parent)

    out.write_text(html_text, encoding="utf-8")
    print(f"[OK] 已生成 {out}")
    n_host, n_ok = _health.stats()
    kept = sum(len(v) for v in resources.values())
    print(f"[线路体检] 域名 {n_ok}/{n_host} 可用；剔除失效线路 {sum(_dead_stat.values())} 条；"
          f"可播条目 {kept}" + ("（各类：%s）" % ", ".join(
              f"{k} -{v}" for k, v in _dead_stat.items() if v) if _dead_stat else ""))
    return out


# 单个数据分片的大小上限（字节）。Cloudflare Pages 单文件上限 25 MiB，取 6 MiB 留足余量：
# 入库爱奇艺/魔都后全量数据在 40 MiB 量级，分片后可稳定部署且远低于上限。
SHARD_MAX_BYTES = 6 * 1024 * 1024

# 详情页简介的最大字数（超出截断）。平均简介 173 字，400 字足够详情页展示，
# 少数超长简介（最长 3428 字）截断后不影响阅读。
DESC_MAX_CHARS = 400


def _desc_key(name: str, year: str) -> str:
    """简介分片的键，必须与 _item_to_json 输出的 name+year 严格一致。"""
    return "%s|%s" % (name or "", year or "")


def _write_shard(path, cat: str, json_items: List[str]) -> None:
    """把一个分类的一批条目写成 window.__RES__(cat, [...]) 的分片脚本。"""
    with path.open("w", encoding="utf-8") as f:
        f.write("window.__RES__(" + json.dumps(cat) + ",[\n")
        f.write(",\n".join(json_items))
        f.write("\n]);\n")


def _write_data_shards(resources: Dict[str, list], live_data: List[dict], out_dir) -> str:
    """把全量数据分片写到 out_dir/web/data_*.js，返回注入 index.html 的 <script> 标签串。

    背景（关键）：
      Cloudflare Pages 单文件上限 25 MiB。此前把全量资源 JSON 直接内联进 index.html，
      入库爱奇艺（+3.5 万条）后 index.html 涨到 25.6 MiB —— 部署直接失败，
      而 update.yml 的「提交数据库变更」在部署之后，于是那批采集数据全部丢失。
      改为分片外部脚本后，index.html 只保留应用外壳（几十 KB），数据按 6 MiB 切分，
      用同步 <script src> 加载，渲染逻辑无需改动（仍是同步读取全局变量）。
    """
    web_dir = out_dir / "web"
    web_dir.mkdir(parents=True, exist_ok=True)
    # 清理旧分片，避免分类/条目变化后残留文件造成重复数据
    for old in list(web_dir.glob("data_*.js")) + list(web_dir.glob("live.js")):
        try:
            old.unlink()
        except OSError:
            pass

    scripts: List[str] = []
    for cat, items in resources.items():
        buf: List[str] = []
        size = 0
        part = 0
        for it in items:
            s = json.dumps(it, ensure_ascii=False)
            if buf and size + len(s) > SHARD_MAX_BYTES:
                _write_shard(web_dir / f"data_{cat}_{part}.js", cat, buf)
                scripts.append(f'  <script src="/web/data_{cat}_{part}.js"></script>')
                part += 1
                buf, size = [], 0
            buf.append(s)
            size += len(s)
        if buf:
            _write_shard(web_dir / f"data_{cat}_{part}.js", cat, buf)
            scripts.append(f'  <script src="/web/data_{cat}_{part}.js"></script>')

    if live_data:
        live_path = web_dir / "live.js"
        live_path.write_text(
            "window.__LIVESET__(" + json.dumps(live_data, ensure_ascii=False) + ");\n",
            encoding="utf-8")
        scripts.append('  <script src="/web/live.js"></script>')

    # 先定义全局容器与合并函数，再按序加载各分片
    header = (
        "  <script>\n"
        "    window.__RESOURCES__ = {};\n"
        "    window.__LIVE_DATA__ = [];\n"
        "    window.__RES__ = function (c, a) {\n"
        "      var r = window.__RESOURCES__[c] = window.__RESOURCES__[c] || [];\n"
        "      for (var i = 0; i < a.length; i++) r.push(a[i]);\n"
        "    };\n"
        "    window.__LIVESET__ = function (a) { window.__LIVE_DATA__ = a; };\n"
        "    window.__DESCS__ = {};\n"
        "    window.__DESC__ = function (c, m) { window.__DESCS__[c] = m; };\n"
        "  </script>"
    )
    return "\n".join([header] + scripts)


def _write_desc_shards(desc_map: Dict[str, Dict[str, str]], out_dir) -> None:
    """把简介按分类写成独立分片 output/web/desc_{cat}.js，供详情页按需加载。

    背景：全量简介 11.98 MiB，若并入主分片会让首屏从 13 MiB 涨到 25 MiB，
    手机端加载体验很差（且逼近 Cloudflare Pages 单文件 25 MiB 上限）。
    改为按分类独立分片后，首屏大小不变，只有用户首次打开某分类的详情页时才
    加载该分类的简介（movie 7.1 MiB / tv 2.9 / anime 1.5 / variety 0.5），
    且同一会话内只加载一次。
    """
    web_dir = out_dir / "web"
    web_dir.mkdir(parents=True, exist_ok=True)
    for old in web_dir.glob("desc_*.js"):
        try:
            old.unlink()
        except OSError:
            pass
    for cat, dmap in desc_map.items():
        if not dmap:
            continue
        (web_dir / f"desc_{cat}.js").write_text(
            "window.__DESC__(" + json.dumps(cat) + ","
            + json.dumps(dmap, ensure_ascii=False) + ");\n",
            encoding="utf-8")


def _load_live_json() -> List[dict]:
    """live 表 -> Web 端频道列表（每频道最优线路一条）"""
    try:
        from collector.live import list_live
        rows = list_live()
    except Exception as e:
        print(f"[警告] 直播数据读取失败: {e}")
        return []
    channels: Dict[str, dict] = {}
    for r in rows:
        key = f"{r['category']}|{r['name']}"
        if key in channels:
            # 已选中的线路若被体检判定失效，且当前这条可用，则替换（list_live 已按延迟排序）
            prev = channels[key]
            if _health.playable(prev["u"]) or not _health.playable(r["url"]):
                continue
        # 优先使用固定的本地台标（data/live_logos 已进 git，部署时同步到 /covers/live/），
        # 不再依赖易失效的外链 CDN；本地台标缺失时回退外链，最后回退渐变封面。
        ch_id = "l_" + hashlib.md5(key.encode("utf-8")).hexdigest()[:14]
        logo = (r.get("logo") or "").strip()
        if os.path.exists(os.path.join(_LIVE_LOGO_ROOT, ch_id + ".png")):
            cover = f"/covers/live/{ch_id}.png"
        else:
            # 没有本地真台标（多为冷门地方台，源站未收录）：回退到本地生成的渐变封面
            # （带频道名，比失效外链可靠——实测外链约 39% 404 且浏览器常有反盗链）。
            # generate_covers 在 generate 之后执行，故此处不做文件存在性检查。
            cover = f"/covers/live_{ch_id}.jpg"
        channels[key] = {
            "n": r["name"], "c": r["category"],
            "l": cover, "u": r["url"],
            "lat": int(r.get("latency") or 0),
        }
    return list(channels.values())
