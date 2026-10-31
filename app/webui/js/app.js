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

/* ---------------- DeepSeek 网页版连通性提醒（v9.22.2） ----------------
 * 用户要求：「只要用户选择了 DeepSeek 大模型，就要经常性地自检一下有没有连通，
 *            如果没有连通，就弹出一个对话框，提醒用户还没联通，然后引导用户联通。」
 *
 * 后端 `_ds_health_tick()` 在选中网页版模型时周期性推 `ds_health`
 * （{ok, state, kind, detail}），这里负责弹窗 + 三个引导动作。
 * 样式用注入的方式写，避免和别的页面 css 抢文件。
 */
const DS_TIPS = {
  not_started: '网页还没启动。点「打开登录窗口」会自动把浏览器拉起来，登录一次以后就长期免登。',
  not_logged_in: '网页打开了，但还没登录 DeepSeek 账号。点「打开登录窗口」扫码/登录即可。',
  error: '网页报错了。先点「立即重试」；不行就「打开登录窗口」看一眼页面卡在哪。',
  offline: '连不上本机网页。检查一下浏览器是否被关掉了。',
  blocked: '页面被挡在验证码/地区限制上了，需要在网页窗口里手动处理。',
};

function _dsModal() {
  let el = document.getElementById('dsModal');
  if (el) return el;
  const style = document.createElement('style');
  style.textContent = `
  .ds-mask{position:fixed;inset:0;background:rgba(16,18,22,.46);z-index:9000;
    display:flex;align-items:center;justify-content:center;padding:24px;
    backdrop-filter:blur(3px);}
  .ds-mask[hidden]{display:none;}
  .ds-box{width:min(460px,94vw);background:var(--surface,#fff);color:var(--text,#222);
    border-radius:14px;box-shadow:0 18px 60px rgba(0,0,0,.28);overflow:hidden;
    border:1px solid var(--border,#e5e7eb);}
  .ds-head{display:flex;align-items:center;gap:9px;padding:15px 18px 10px;}
  .ds-ico{width:22px;height:22px;border-radius:50%;background:#f59e0b1f;color:#d97706;
    display:flex;align-items:center;justify-content:center;font-weight:700;font-size:13px;flex:0 0 auto;}
  .ds-title{font-size:14.5px;font-weight:650;}
  .ds-body{padding:0 18px 4px;font-size:12.8px;line-height:1.75;color:var(--text-2,#4b5563);}
  .ds-line{margin:6px 0;}
  .ds-kv{font-weight:600;color:var(--text,#222);}
  .ds-foot{display:flex;gap:8px;justify-content:flex-end;padding:14px 18px 16px;flex-wrap:wrap;}
  .ds-btn{border:1px solid var(--border,#d7dbe0);background:var(--surface,#fff);
    color:var(--text,#222);font-size:12.5px;padding:7px 13px;border-radius:8px;cursor:pointer;}
  .ds-btn:hover{border-color:var(--accent,#2563eb);color:var(--accent,#2563eb);}
  .ds-btn.primary{background:var(--accent,#2563eb);border-color:transparent;color:#fff;font-weight:600;}
  .ds-btn.primary:hover{filter:brightness(1.06);color:#fff;}
  .ds-btn[disabled]{opacity:.55;cursor:default;}
  `;
  document.head.appendChild(style);

  el = document.createElement('div');
  el.className = 'ds-mask';
  el.id = 'dsModal';
  el.hidden = true;
  el.innerHTML = `
    <div class="ds-box" role="dialog" aria-modal="true" aria-labelledby="dsTitle">
      <div class="ds-head">
        <span class="ds-ico">!</span>
        <span class="ds-title" id="dsTitle">DeepSeek 网页版没有连上</span>
      </div>
      <div class="ds-body">
        <div class="ds-line">当前状态：<span class="ds-kv" id="dsState">未知</span></div>
        <div class="ds-line" id="dsTip">点下面的按钮就能把它连通。</div>
      </div>
      <div class="ds-foot">
        <button class="ds-btn" id="dsUseAuto">改用「自动」</button>
        <button class="ds-btn" id="dsOpen">打开登录窗口</button>
        <button class="ds-btn primary" id="dsRetry">立即重试</button>
      </div>
    </div>`;
  document.body.appendChild(el);

  el.querySelector('#dsRetry').addEventListener('click', async (ev) => {
    const b = ev.currentTarget;
    b.disabled = true; b.textContent = '正在重试…';
    try { await call('rpc', { method: 'ds_health_check', args: {} }); } catch (e) { /* 忽略 */ }
    setTimeout(() => { b.disabled = false; b.textContent = '立即重试'; }, 2500);
    setDsTip('已经发出重连请求，等几秒看状态变化。');
  });
  el.querySelector('#dsOpen').addEventListener('click', async () => {
    setDsTip('正在打开 DeepSeek 网页窗口…登录一次以后长期免登。');
    try { await call('rpc', { method: 'ds_show_window', args: {} }); } catch (e) { /* 忽略 */ }
  });
  el.querySelector('#dsUseAuto').addEventListener('click', () => {
    call('set_model', { choice: 'auto' });
    el.hidden = true;
  });
  return el;
}

function setDsTip(text) {
  const t = document.getElementById('dsTip');
  if (t) t.textContent = text;
}

let _dsDismissedAt = 0;

function showDsHealth(p) {
  const el = _dsModal();
  if (!p || p.ok) {                 // 连上了 → 自动收掉
    el.hidden = true;
    return;
  }
  // 用户刚手动关掉就别马上又弹（后端 90s 节流之外再加一道）
  if (Date.now() - _dsDismissedAt < 60000) return;
  const state = String(p.state || 'unknown');
  el.querySelector('#dsState').textContent =
    ({ not_started: '网页未启动', not_logged_in: '网页已打开，但没登录',
       error: '网页报错', offline: '连不上本机网页', blocked: '页面被拦截' }[state])
    || (p.detail || state);
  setDsTip(DS_TIPS[state] || (p.detail || '点下面的按钮试着把它连上。'));
  el.hidden = false;
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
  // ★ v9.22.2：DeepSeek 网页版连通性提醒（选中网页版模型时后端周期性推）
  on('ds_health', showDsHealth);
  await start();
}

boot();
