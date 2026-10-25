// app.js —— 启动、主题切换、页面壳逻辑
'use strict';

import { getState, patch, subscribe } from './store.js';
import { start, call, on } from './bridge.js';
import { initChat } from './views/chat.js';
import { initNav } from './nav.js';

const root = document.documentElement;
const $ = (s) => document.querySelector(s);

/* ---------------- 主题：首屏前由 index.html 内联脚本定过，这里收进 store ---------------- */
function applyTheme(theme) {
  const next = theme === 'dark' ? 'dark' : 'light';
  root.dataset.theme = next;
}

// 启动时把内联脚本定的主题读进 store
patch({ theme: root.dataset.theme === 'dark' ? 'dark' : 'light' });

// 任何一处 patch({theme}) 都会同步到 <html data-theme>
subscribe((state, changed) => {
  if (changed && 'theme' in changed) applyTheme(state.theme);
});

function toggleTheme() {
  const next = getState().theme === 'dark' ? 'light' : 'dark';
  patch({ theme: next });
  call('set_theme', { theme: next });
}

/* ---------------- 嵌入模式：嵌进主窗口时隐藏自带侧栏/标题条 ----------------
 * 主窗口有 Qt 全局侧栏（导航 + 对话列表），网页若再显示自己那套 titlebar/sidebar
 * 就会「左侧两个任务栏」。后端在 init 里推 embedded=true，这里据此给 body 打标，
 * 由 CSS 隐藏 titlebar + sidebar，只保留对话主区。
 */
function applyEmbedded(embedded) {
  document.body.classList.toggle('is-embedded', !!embedded);
}

/* ---------------- 顶栏 / 侧栏壳逻辑 ---------------- */
function wireShell() {
  const themeBtn = $('#themeBtn');
  const listBtn = $('#listBtn');
  const newConvBtn = $('#newConvBtn');
  const sidebar = $('#sidebar');

  themeBtn.addEventListener('click', toggleTheme);

  listBtn.addEventListener('click', () => {
    sidebar.classList.toggle('is-hidden');
  });

  newConvBtn.addEventListener('click', () => {
    call('new_conv', {});
  });
}

/* ---------------- 启动 ---------------- */
async function boot() {
  wireShell();
  initChat();
  initNav();   // ★ v9.21.2：全 Web 导航（侧栏格子切 section.page）
  // 嵌入标志在 init 事件里下发，这里先订阅（在 start 建立通道前注册 handler）
  on('init', (p) => {
    if (p && typeof p.embedded === 'boolean') applyEmbedded(p.embedded);
  });
  await start();
}

boot();
