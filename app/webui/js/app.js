// app.js —— 启动、主题切换、页面壳逻辑
'use strict';

import { getState, patch, subscribe } from './store.js';
import { start, call, on, rpc } from './bridge.js';
import { initChat } from './views/chat.js';
import { initNav, switchTo } from './nav.js';

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

/* ---------------- 全局自动更新：弹窗 + 进度条（v9.22.6） ----------------
 * 用户原话：
 *   「一进去之后要检测有没有新版本，现在能检测到，但是不会自动弹出下载安装的呀。
 *     还要有命令的那个，就是进度条啊，我现在只有点更新才行，而且也是没有弹出进度条。」
 *
 * 病根：以前「发现新版本」只写了状态栏一行字（Web 模式下），进度条只画在「关于」页里，
 *       用户停在对话页时什么都看不到。现在这两个都放到 **外壳层**（app.js），
 *       不论当前在哪一页都看得见：
 *         update_available → 弹窗（版本/大小/三个按钮）
 *         update_progress  → 右下角悬浮进度卡（任何页面可见）
 *         update_error     → 弹窗显示原因 + 发布页兜底
 */
const UPD_STAGE = {
  picking: '正在挑选最快的下载通道…',
  connecting: '正在连接下载通道…',
  downloading: '正在下载更新包',
  extracting: '正在解压更新包…',
  installing: '正在安装，别关电脑…',
  restarting: '正在重启到新版本…',
  failed: '更新没有成功',
};

let _updTag = '';
let _updBusy = false;          // 已经点了「下载并安装」，防重复点击
let _updHideTimer = 0;

function _fmtMB(n) {
  const v = Number(n) || 0;
  if (v <= 0) return '';
  if (v >= 1048576) return (v / 1048576).toFixed(1) + ' MB';
  return Math.max(1, Math.round(v / 1024)) + ' KB';
}

function _ensureUpdStyle() {
  if (document.getElementById('updStyle')) return;
  const style = document.createElement('style');
  style.id = 'updStyle';
  style.textContent = `
  /* ---- 悬浮进度卡（任何页面可见） ----
     位置定在右上角：对话页右下角是输入框，压在上面既挡字又误触；
     右上角在 12 个页面里都空着，不会遮住主操作。 */
  .upd-card{position:fixed;right:18px;top:58px;z-index:9500;
    width:min(370px,calc(100vw - 36px));background:var(--surface,#fff);color:var(--text,#222);
    border:1px solid var(--border,#e5e7eb);border-radius:12px;
    box-shadow:0 12px 36px rgba(0,0,0,.18);padding:13px 15px 12px;
    font-size:12.6px;line-height:1.6;transition:opacity .18s,transform .18s;}
  .upd-card[hidden]{display:none;}
  .upd-card.is-out{opacity:0;transform:translateY(-8px);}
  .upd-crow{display:flex;align-items:baseline;gap:8px;}
  .upd-ctitle{font-size:13px;font-weight:650;flex:1 1 auto;overflow:hidden;
    text-overflow:ellipsis;white-space:nowrap;}
  .upd-cpct{font-variant-numeric:tabular-nums;color:var(--accent,#2563eb);font-weight:700;
    flex:0 0 auto;}
  .upd-ctext{margin-top:2px;font-size:12px;color:var(--text-2,#4b5563);
    overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}
  .upd-track{margin-top:8px;height:6px;border-radius:99px;overflow:hidden;
    background:var(--border,#e5e7eb);}
  .upd-fill{height:100%;width:0%;border-radius:99px;
    background:linear-gradient(90deg,var(--accent,#2563eb),var(--accent-hover,#3b82f6));
    transition:width .25s ease;}
  .upd-fill.indet{width:35%!important;animation:updSlide 1.15s ease-in-out infinite;}
  @keyframes updSlide{0%{margin-left:-35%}100%{margin-left:100%}}
  .upd-card.is-fail{border-color:var(--danger,#ef4444);}
  .upd-card.is-fail .upd-fill{background:var(--danger,#ef4444);width:100%!important;animation:none;}

  /* ---- 弹窗 ---- */
  .upd-mask{position:fixed;inset:0;background:rgba(16,18,22,.46);z-index:9200;
    display:flex;align-items:center;justify-content:center;padding:24px;
    backdrop-filter:blur(3px);}
  .upd-mask[hidden]{display:none;}
  .upd-box{width:min(480px,94vw);background:var(--surface,#fff);color:var(--text,#222);
    border-radius:14px;box-shadow:0 18px 60px rgba(0,0,0,.28);overflow:hidden;
    border:1px solid var(--border,#e5e7eb);}
  .upd-head{display:flex;align-items:center;gap:10px;padding:16px 18px 8px;}
  .upd-ico{width:26px;height:26px;border-radius:8px;background:var(--accent-weak,#2563eb1a);
    color:var(--accent,#2563eb);
    display:flex;align-items:center;justify-content:center;font-size:15px;flex:0 0 auto;}
  .upd-title{font-size:15px;font-weight:680;}
  .upd-body{padding:2px 18px 4px;font-size:12.9px;line-height:1.8;
    color:var(--text-2,#4b5563);}
  .upd-body .kv{color:var(--text,#222);font-weight:600;}
  .upd-note{margin-top:8px;padding:8px 10px;border-radius:8px;
    background:var(--surface-2,#f6f7f9);font-size:12.2px;line-height:1.7;}
  .upd-foot{display:flex;gap:8px;justify-content:flex-end;padding:14px 18px 16px;flex-wrap:wrap;}
  .upd-btn{border:1px solid var(--border,#d7dbe0);background:var(--surface,#fff);
    color:var(--text,#222);font-size:12.5px;padding:8px 14px;border-radius:8px;cursor:pointer;}
  .upd-btn:hover{border-color:var(--accent,#2563eb);color:var(--accent,#2563eb);}
  .upd-btn.primary{background:var(--accent,#2563eb);border-color:transparent;color:#fff;font-weight:600;}
  .upd-btn.primary:hover{filter:brightness(1.06);color:#fff;}
  .upd-btn[disabled]{opacity:.55;cursor:default;}
  `;
  document.head.appendChild(style);
}

function _updCard() {
  _ensureUpdStyle();
  let el = document.getElementById('updCard');
  if (el) return el;
  el = document.createElement('div');
  el.className = 'upd-card';
  el.id = 'updCard';
  el.hidden = true;
  el.innerHTML = `
    <div class="upd-crow">
      <span class="upd-ctitle" id="updCTitle">正在更新</span>
      <span class="upd-cpct" id="updCPct"></span>
    </div>
    <div class="upd-ctext" id="updCText"></div>
    <div class="upd-track"><div class="upd-fill" id="updCFill"></div></div>`;
  document.body.appendChild(el);
  return el;
}

function updShowProgress(stage, text, done, total, percent) {
  const el = _updCard();
  clearTimeout(_updHideTimer);
  el.hidden = false;
  el.classList.remove('is-out');
  const fail = stage === 'failed';
  el.classList.toggle('is-fail', fail);
  const fill = el.querySelector('#updCFill');
  const pctEl = el.querySelector('#updCPct');
  el.querySelector('#updCTitle').textContent =
    fail ? '更新失败' : ('正在更新' + (_updTag ? ' ' + _updTag : ''));
  let pctText = '';
  const hasPct = Number.isFinite(percent) && percent !== null && percent !== undefined;
  if (fail) {
    fill.classList.remove('indet');
    fill.style.width = '100%';
    pctText = '';
  } else if (hasPct && percent > 0) {
    fill.classList.remove('indet');
    fill.style.width = Math.max(1, Math.min(100, percent)) + '%';
    pctText = percent + '%';
  } else {
    // 还没拿到总大小（选通道/连接中）→ 用不定长动画，别让用户以为卡死了
    fill.classList.add('indet');
    pctText = '';
  }
  pctEl.textContent = pctText;
  const bits = [];
  const line = String(text || '').trim() || UPD_STAGE[stage] || '正在处理…';
  bits.push(line);
  if (!fail && total > 0 && done > 0) bits.push(_fmtMB(done) + ' / ' + _fmtMB(total));
  el.querySelector('#updCText').textContent = bits.join(' · ');
  if (fail) {
    _updHideTimer = setTimeout(() => { el.classList.add('is-out'); }, 8000);
  }
}

function updHideProgress() {
  const el = document.getElementById('updCard');
  if (!el) return;
  clearTimeout(_updHideTimer);
  el.classList.add('is-out');
  _updHideTimer = setTimeout(() => { el.hidden = true; el.classList.remove('is-out'); }, 260);
}

function _updModal() {
  _ensureUpdStyle();
  let el = document.getElementById('updModal');
  if (el) return el;
  el = document.createElement('div');
  el.className = 'upd-mask';
  el.id = 'updModal';
  el.hidden = true;
  el.innerHTML = `
    <div class="upd-box" role="dialog" aria-modal="true" aria-labelledby="updTitle">
      <div class="upd-head">
        <span class="upd-ico">↑</span>
        <span class="upd-title" id="updTitle">发现新版本</span>
      </div>
      <div class="upd-body">
        <div id="updInfo"></div>
        <div class="upd-note" id="updNote"></div>
      </div>
      <div class="upd-foot">
        <button class="upd-btn" id="updPage">打开发布页</button>
        <button class="upd-btn" id="updLater">稍后</button>
        <button class="upd-btn primary" id="updGo">立即下载并安装</button>
      </div>
    </div>`;
  document.body.appendChild(el);

  el.querySelector('#updLater').addEventListener('click', () => { el.hidden = true; });
  el.querySelector('#updPage').addEventListener('click', () => {
    const url = el.dataset.url;
    if (url) call('rpc', { method: 'app.open_url', args: { url } });
  });
  el.querySelector('#updGo').addEventListener('click', async (ev) => {
    if (_updBusy) return;
    _updBusy = true;
    const b = ev.currentTarget;
    b.disabled = true;
    b.textContent = '正在开始…';
    el.hidden = true;
    updShowProgress('picking', '马上开始，几秒钟内就会有下载进度…', 0, 0, null);
    try {
      await rpc('update.apply', {}, 60000);
    } catch (e) {
      updShowProgress('failed', String((e && e.message) || e), 0, 0, null);
    }
    setTimeout(() => { b.disabled = false; b.textContent = '立即下载并安装'; _updBusy = false; }, 1500);
  });
  return el;
}

function showUpdateAvailable(d) {
  if (!d || !d.tag) return;
  _updTag = String(d.tag || '');
  const el = _updModal();
  el.dataset.url = String(d.html_url || '');
  el.querySelector('#updTitle').textContent = '发现新版本 ' + _updTag;
  const sizeTxt = _fmtMB(d.size);
  const patchTxt = _fmtMB(d.patch_size);
  const info = [];
  info.push(`当前版本 <span class="kv">${String(d.current || '')}</span>，`
    + `最新版本 <span class="kv">${_updTag}</span>。`);
  if (patchTxt) {
    // 有增量包就明说"只要几 MB"，这正是用户要的「特别快」
    info.push(`本次只需下载增量包，约 <span class="kv">${patchTxt}</span>`
      + (sizeTxt ? `（完整包 ${sizeTxt}）` : '') + `，通常几秒就好。`);
  } else if (sizeTxt) {
    info.push(`更新包约 <span class="kv">${sizeTxt}</span>，自动下载安装。`);
  }
  el.querySelector('#updInfo').innerHTML = info.join('<br>');
  el.querySelector('#updNote').textContent =
    '点「立即下载并安装」会自动挑选最快的下载通道（同时测速多个代理，取最快的那个），'
    + '下载完自动替换文件并重启本程序。中途不需要你动手。';
  el.querySelector('#updPage').hidden = !d.html_url;
  el.hidden = false;
}

function showUpdateError(d) {
  const msg = String((d && d.error) || (d && d.text) || '更新没有成功，原因不明。');
  const el = _updModal();
  el.querySelector('#updTitle').textContent = '更新没有成功';
  el.querySelector('#updInfo').textContent = '';
  el.querySelector('#updInfo').appendChild(
    Object.assign(document.createElement('div'), { textContent: msg }));
  el.querySelector('#updNote').textContent =
    '可以点「打开发布页」手动下载 zip，解压覆盖即可（原工作区数据不受影响）。';
  el.querySelector('#updGo').hidden = true;
  el.querySelector('#updLater').textContent = '知道了';
  el.dataset.url = String((d && d.url) || '');
  el.querySelector('#updPage').hidden = !el.dataset.url;
  el.hidden = false;
  updShowProgress('failed', msg, 0, 0, null);
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

  // ★ v9.22.4：非对话页没有导航格子，标题栏给一个「← 对话」兜底，
  //   否则停在 设置/技能/关于 时（尤其会话列表为空）回不到对话。
  const backBtn = $('#backToChat');
  if (backBtn) {
    backBtn.addEventListener('click', () => switchTo('chat'));
    document.addEventListener('awb:nav', (e) => {
      const key = e.detail && e.detail.key;
      backBtn.hidden = (key === 'chat');
    });
  }
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

  // ★ v9.22.6：全局自动更新（外壳层，任何页面都看得见）
  on('update_available', showUpdateAvailable);
  on('update_progress', (d) => {
    if (!d) return;
    updShowProgress(String(d.stage || ''), d.text, Number(d.done) || 0,
                    Number(d.total) || 0,
                    (d.percent === null || d.percent === undefined) ? null : Number(d.percent));
  });
  on('update_error', showUpdateError);
  await start();
  // ★ v9.22.4：标题栏版本号从后端取，别再写死（之前长期停在 v9.22.1）。
  try {
    const info = await rpc('app.about_info', {});
    const v = info && info.version;
    const el = $('#verLabel');
    if (el && v) el.textContent = 'v' + v;
  } catch (e) { /* 未连接时保留 index.html 的兜底版本 */ }
}

boot();

// ★ v9.22.6：导出三个更新 UI 函数，只为「用真浏览器验证弹窗/进度条」
//   （CDP 里 import 本模块直接调，不污染 window，也不影响正常启动）。
//   ES 模块缓存保证重复 import 不会二次执行 boot()。
export { showUpdateAvailable, showUpdateError, updShowProgress };
