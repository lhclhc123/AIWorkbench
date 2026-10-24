// app.js —— 启动、主题切换、页面壳逻辑
'use strict';

import { getState, patch, subscribe } from './store.js';
import { start, call } from './bridge.js';
import { initChat } from './views/chat.js';

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
  await start();
}

boot();
