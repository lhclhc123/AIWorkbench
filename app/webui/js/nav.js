// nav.js —— v9.21.2 全 Web 导航 + 视图懒加载
// 单一 SPA：侧栏顶部导航区切 section.page；每个页面一个视图模块懒加载。
'use strict';

import { rpc } from './bridge.js';

// 导航项（对齐 Qt 版 PAGES 顺序）。tools/team 不画格子（团队收进对话 TeamBar）。
// qt=true 表示该页不在 SPA 内，点导航时 RPC nav_to 让后端切 Qt stack：
//   assistant → im 独立 Web 页（awb://ui/im.html）
//   browser  → Qt 内嵌浏览器页
const PAGES = [
  { key: 'chat',        label: '对话',       view: null,          primary: true },
  { key: 'assistant',   label: '钉钉助手',   view: null,          qt: true },
  { key: 'skills',      label: '技能',       view: 'views/skills.js' },
  { key: 'tasks',       label: '定时任务',   view: 'views/tasks.js' },
  { key: 'memory',      label: '记忆',       view: 'views/memory.js' },
  { key: 'tools',       label: '工具记录',   view: 'views/tools.js' },
  { key: 'bg',          label: '并行任务',   view: 'views/bg.js' },
  { key: 'team',        label: '团队',       view: 'views/team.js' },
  { key: 'integrations',label: '集成',       view: 'views/integrations.js' },
  { key: 'settings',    label: '设置',       view: 'views/settings.js' },
  { key: 'about',       label: '关于',       view: 'views/about.js' },
  { key: 'browser',     label: '浏览器',     view: null,          qt: true },
];

// 不画格子的页面：tools/team 收进别处；chat 不再占用侧栏格子——
// 用户直接点左侧对话列表里的某一项即可回到对话页（见 views/chat.js）。
// PAGES 里 chat 那条仍保留，供 switchTo('chat') 的页面切换逻辑使用。
const NAV_SKIP = new Set(['tools', 'team', 'chat']);

let current = 'chat';
const viewCache = new Map();   // key -> {loaded, init}

const $ = (s) => document.querySelector(s);

function buildNav() {
  const rail = $('#navRail');
  if (!rail) return;
  rail.innerHTML = '';
  PAGES.forEach((p) => {
    if (NAV_SKIP.has(p.key)) return;
    const b = document.createElement('button');
    b.className = 'nav-chip' + (p.primary ? ' nav-chat' : '') +
                  (p.key === current ? ' is-active' : '');
    b.dataset.key = p.key;
    b.textContent = p.label;
    b.addEventListener('click', () => switchTo(p.key));
    rail.appendChild(b);
  });
}

function setActive(key) {
  document.querySelectorAll('.nav-chip').forEach((b) => {
    b.classList.toggle('is-active', b.dataset.key === key);
  });
}

async function loadView(key) {
  const p = PAGES.find((x) => x.key === key);
  if (!p) return;
  // browser 页是 Qt 版，导航时切回 Qt stack
  if (p.qt) {
    try { await rpc('nav_to', { key }); } catch (e) { console.error('[nav] nav_to 失败', e); }
    return;
  }
  if (!p.view) return;
  if (!viewCache.has(key)) {
    try {
      const mod = await import('./' + p.view);
      viewCache.set(key, { init: mod.init || null });
    } catch (e) {
      console.error('[nav] 加载视图失败', p.view, e);
      const sec = $(`#page-${key}`);
      if (sec) sec.innerHTML = `<div class="empty">视图加载失败：${p.view}</div>`;
      return;
    }
  }
}

export async function switchTo(key) {
  const p = PAGES.find((x) => x.key === key);
  if (!p) return;
  // browser 页走 Qt
  if (p.qt) {
    await loadView(key);
    return;
  }
  // 隐藏所有 section，显示目标
  document.querySelectorAll('.page').forEach((s) => { s.hidden = true; });
  const sec = $(`#page-${key}`);
  if (sec) sec.hidden = false;
  current = key;
  setActive(key);
  await loadView(key);
  // 视图就绪后调用其 init（每次切换都调，让其刷新数据）
  const cached = viewCache.get(key);
  if (cached && typeof cached.init === 'function') {
    try { await cached.init(sec); } catch (e) { console.error('[nav] 视图 init 失败', key, e); }
  }
}

export function initNav() {
  buildNav();
  switchTo('chat');
}
