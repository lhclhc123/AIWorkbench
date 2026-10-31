// views/about.js —— 关于页：应用信息 + 检查更新 + 复制诊断信息
'use strict';

import { rpc, on } from '../bridge.js';

const $ = (s, root) => (root || document).querySelector(s);

function toast(msg) {
  const el = $('#toast');
  el.textContent = msg;
  el.classList.add('show');
  clearTimeout(toast._t);
  toast._t = setTimeout(() => el.classList.remove('show'), 1900);
}

// ★ v9.22.1 修复（问题3「关于页点检查更新，自动跳到钉钉集成的 Qt 界面且退不出」）：
//   检查结果一律**就地**显示在按钮右边，绝不跳页。
//   后端 _do_update_check() 在 web 模式下不再 _open_page("integrations")，
//   改为 emit_event("update_checked", {ok, has_update, tag, current, error})；
//   这里监听该事件渲染文案。整个流程共三条兜底路径：
//     1) update_checked 事件（正常）
//     2) app.check_update 的 RPC 回执（后端同步执行完才返回，事件可能更早到）
//     3) CHECK_TIMEOUT_MS 超时（后端异常/桥断了也不会永远「检查中…」）
const CHECK_TIMEOUT_MS = 90000;

const UPDATE_COLORS = {
  checking: 'var(--text-3)',
  ok: 'var(--ok)',
  new: 'var(--warn)',
  err: 'var(--danger)',
};

// ★ v9.22.2（用户原话）：「程序更新的那个东西不用上面还显示正在找最快的方案什么东西，
//   就直接一个进度条，然后说正在下载安装啊之类的就可以了。」
//   所以自动更新这块只做两件事：
//     1) 一行人话（阶段文案由后端 updater._stage_label() 统一产出）
//     2) 一条进度条：有 percent 就按百分比走，没有就是不确定态（横向流动）
//   线路名 / 域名 / 测速 / 镜像 这些实现细节一律不上界面（plainText 再兜底抹一遍）。
const STAGE_TEXT = {
  checking: '正在检查更新…',
  probing: '正在准备下载…',
  downloading: '正在下载…',
  downloaded: '下载完成，正在准备安装…',
  extracting: '正在解压更新包…',
  installing: '正在安装，请稍候…',
  restarting: '正在重启到新版本…',
  done: '已更新到最新版本，正在重启…',
  failed: '更新失败，请稍后重试',
};

// 进行中的阶段：按钮要 disabled + 文案「正在更新…」
// （checking 不算：它由 setChecking 负责，按钮文案是「检查中…」）
const ACTIVE_STAGES = [
  'probing', 'downloading', 'downloaded',
  'extracting', 'installing', 'restarting',
];

// 下载器内部词汇：即使后端误传，也不许出现在界面上。
const INTERNAL_WORDS = ['测速', '最快', '通道', '镜像', '直连', '断点', '回退'];

// 本视图自带的局部样式（scoped 到 about 页内部），不影响其它视图
const STYLE = `
.up-wrap{display:flex;flex-direction:column;gap:6px;margin:0 0 12px;padding:10px 12px;
  border:1px solid var(--border);border-radius:var(--r-md);background:var(--surface);}
.up-wrap[hidden]{display:none;}
.up-line{display:flex;align-items:baseline;gap:8px;font-size:12.5px;color:var(--text-2);min-width:0;}
.up-text{flex:1 1 auto;min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.up-size{flex:0 0 auto;font-size:11.5px;color:var(--text-3);font-variant-numeric:tabular-nums;}
.up-bar{position:relative;height:6px;border-radius:999px;background:var(--surface-2);overflow:hidden;}
.up-bar>i{display:block;height:100%;width:0;border-radius:999px;background:var(--accent);
  transition:width .22s ease;}
.up-bar.is-ok>i{background:var(--ok);}
.up-bar.is-indeterminate>i{width:35%;animation:up-slide 1.15s ease-in-out infinite;}
@keyframes up-slide{0%{transform:translateX(-100%);}100%{transform:translateX(320%);}}
`;

let _updateMsg = null;   // {text, kind} 最近一次结果；切页回来仍能恢复显示
let _checking = false;
let _checkTimer = null;
let _updating = false;    // 下载/安装进行中（按钮 disabled）
let _lastProgress = null; // 最近一次 update_progress 载荷；切页回来恢复进度条

function updateBtn() { return document.getElementById('aboutUpdate'); }
function updateMsgEl() { return document.getElementById('aboutUpdateMsg'); }
function updateNowBtn() { return document.getElementById('aboutUpdateNow'); }

/** 渲染按钮右侧的状态文案（就地显示，不弹窗、不跳页）。 */
function setUpdateMsg(text, kind) {
  _updateMsg = text ? { text: String(text), kind: kind || 'checking' } : null;
  const el = updateMsgEl();
  if (!el) return;
  el.textContent = _updateMsg ? _updateMsg.text : '';
  el.style.color = UPDATE_COLORS[(_updateMsg && _updateMsg.kind) || 'checking'];
}

/** 按钮的三种态：空闲 / 检查中 / 正在更新（更新优先级最高）。 */
function syncUpdateBtn() {
  const btn = updateBtn();
  if (!btn) return;
  btn.disabled = !!(_updating || _checking);
  btn.textContent = _updating ? '正在更新…' : (_checking ? '检查中…' : '检查更新');
}

/** 切换「检查中」态：按钮 disabled + 文案变化（>500ms 的操作必须有进行中反馈）。 */
function setChecking(on) {
  _checking = !!on;
  syncUpdateBtn();
  if (_checkTimer) { clearTimeout(_checkTimer); _checkTimer = null; }
  if (_checking) {
    _checkTimer = setTimeout(() => {
      _checkTimer = null;
      if (!_checking) return;
      setChecking(false);
      setUpdateMsg('检查超时，请再点一次「检查更新」', 'err');
      toast('检查超时，请重试');
    }, CHECK_TIMEOUT_MS);
  }
}

/** 切换「正在更新」态：下载/安装期间按钮 disabled + 文案「正在更新…」。 */
function setUpdating(on) {
  _updating = !!on;
  syncUpdateBtn();
  const nb = updateNowBtn();
  if (nb && on) nb.hidden = true;
}

/** 抹掉一切实现细节与网址，只留一句人话。 */
function plainText(s) {
  let t = String(s == null ? '' : s);
  t = t.replace(/https?:\/\/\S+/gi, ' ');                                  // 网址
  t = t.replace(/[\w-]+\.(?:com|net|org|io|top|cc|cn)\b/gi, ' ');          // 域名
  for (const w of INTERNAL_WORDS) t = t.split(w).join('');                 // 内部术语
  t = t.replace(/\bETag\b/gi, '');
  t = t.replace(/[（(]\s*[)）]/g, '');                                     // 被掏空的括号
  return t.replace(/\s{2,}/g, ' ').trim();
}

/** 字节 → 「12.3 MB」（只给大小，不给地址）。 */
function fmtMB(bytes) {
  const n = Number(bytes);
  if (!isFinite(n) || n <= 0) return '';
  const mb = n / 1048576;
  return (mb >= 100 ? mb.toFixed(0) : mb.toFixed(1)) + ' MB';
}

/** 百分比：优先用后端给的 percent，否则用 done/total 现算；都算不出返回 null。 */
function normPercent(d) {
  const data = d || {};
  if (data.percent != null) {
    const p = Number(data.percent);
    if (isFinite(p)) return Math.max(0, Math.min(100, Math.round(p)));
  }
  const done = Number(data.done);
  const total = Number(data.total);
  if (isFinite(done) && isFinite(total) && total > 0) {
    return Math.max(0, Math.min(100, Math.round(done * 100 / total)));
  }
  return null;
}

function updWrap() { return document.getElementById('aboutUpdWrap'); }
function updTextEl() { return document.getElementById('aboutUpdText'); }
function updSizeEl() { return document.getElementById('aboutUpdSize'); }
function updBarEl() { return document.getElementById('aboutUpdBar'); }
function updFillEl() { return document.getElementById('aboutUpdFill'); }

/** 收起进度条（检查阶段、失败后不再显示）。 */
function hideProgress() {
  const wrap = updWrap();
  if (wrap) wrap.hidden = true;
  _lastProgress = null;
}

/**
 * 渲染一条「人话 + 进度条」。
 * 后端契约（main_window 推的 update_progress）：{stage, text, done, total, percent}
 */
function renderProgress(d) {
  const data = d || {};
  const stage = String(data.stage || '').toLowerCase();
  const wrap = updWrap();
  if (!wrap || !stage) return;

  if (stage === 'failed') {
    _lastProgress = null;
    hideProgress();
    setUpdating(false);
    setChecking(false);
    const raw = plainText(data.text) || STAGE_TEXT.failed;
    const msg = /^更新失败/.test(raw) ? raw : ('更新失败：' + raw);
    setUpdateMsg(msg, 'err');
    toast(msg);
    return;
  }

  _lastProgress = data;
  const percent = (stage === 'done') ? 100 : normPercent(data);
  let text = plainText(data.text) || STAGE_TEXT[stage] || '';
  if (stage === 'done') text = STAGE_TEXT.done;
  if (!text) { hideProgress(); return; }

  wrap.hidden = false;
  setUpdating(ACTIVE_STAGES.indexOf(stage) >= 0);   // done/failed 之外都算进行中
  if (stage === 'failed') {
    const nb = updateNowBtn();
    if (nb) nb.hidden = false;   // 失败后可重试
  }

  const textEl = updTextEl();
  if (textEl) textEl.textContent = text;   // 一行文案

  // 只有下载阶段才有必要显示「已下 / 总大小」，其余阶段不塞数字。
  const sizeEl = updSizeEl();
  if (sizeEl) {
    sizeEl.textContent = (stage === 'downloading' && !!(Number(data.done) && Number(data.total)))
      ? (fmtMB(data.done) + ' / ' + fmtMB(data.total))
      : '';
  }

  const bar = updBarEl();
  const fill = updFillEl();
  if (!bar || !fill) return;
  bar.classList.toggle('is-ok', stage === 'done');
  if (percent == null) {
    // 不确定态：CSS 动画横向流动（不用转圈图标）
    bar.classList.add('is-indeterminate');
    bar.removeAttribute('aria-valuenow');
    fill.style.width = '';
  } else {
    bar.classList.remove('is-indeterminate');
    bar.setAttribute('aria-valuenow', String(percent));
    fill.style.width = percent + '%';      // ← 进度条宽度完全由 percent 决定
  }
}

/** 把后端回执/事件统一解析成一条可读文案。 */
function applyUpdateResult(d) {
  const data = d || {};
  if (data.async === true && data.ok === true && data.has_update === undefined) return; // 纯回执
  setChecking(false);
  if (!data.ok) {
    const reason = plainText(data.error) || '未知原因';
    setUpdateMsg('检查失败：' + reason, 'err');
    toast('检查更新失败：' + reason);
    return;
  }
  const cur = data.current ? (' v' + String(data.current).replace(/^v/i, '')) : '';
  if (data.has_update) {
    const tag = data.tag ? ('v' + String(data.tag).replace(/^v/i, '')) : '新版本';
    setUpdateMsg('发现新版本 ' + tag + '（当前' + (cur || ' 未知') + '）', 'new');
    toast('发现新版本 ' + tag);
  } else {
    setUpdateMsg('已是最新' + cur, 'ok');
    toast('已是最新' + cur);
  }
  const nb = updateNowBtn();
  if (nb) nb.hidden = !data.has_update;   // 有更新才显示「下载并安装」
}

function build(root) {
  root.innerHTML = `
    <style>${STYLE}</style>
    <div class="page-head">
      <h2>关于</h2>
      <span class="spacer"></span>
      <span class="hint" id="aboutUpdateMsg"></span>
      <button class="btn" id="aboutUpdate">检查更新</button>
      <button class="btn btn-primary" id="aboutUpdateNow" hidden>下载并安装</button>
      <button class="btn" id="aboutDiag">复制诊断信息</button>
    </div>
    <div class="up-wrap" id="aboutUpdWrap" hidden>
      <div class="up-line">
        <span class="up-text" id="aboutUpdText"></span>
        <span class="up-size" id="aboutUpdSize"></span>
      </div>
      <div class="up-bar" id="aboutUpdBar" role="progressbar"
           aria-valuemin="0" aria-valuemax="100"><i id="aboutUpdFill"></i></div>
    </div>
    <div class="card">
      <div class="card-title">应用信息</div>
      <div class="readonly-body rich" id="aboutBody">加载中…</div>
    </div>
  `;
}

function fmt(v) {
  if (v == null) return '';
  if (typeof v === 'string') return v;
  try { return JSON.stringify(v); } catch (e) { return String(v); }
}

function render(root, info) {
  const el = $('#aboutBody', root);
  el.innerHTML = '';
  const items = [
    ['名称', info.name],
    ['版本', info.version],
    ['构建日期', info.build_date],
    ['主页', info.homepage],
    ['更新说明', info.releases],
  ];
  for (const [label, val] of items) {
    if (val == null || val === '') continue;
    const row = document.createElement('div');
    row.style.marginBottom = '8px';
    const b = document.createElement('span');
    b.style.fontWeight = '600';
    b.textContent = label + '：';
    row.appendChild(b);
    row.appendChild(document.createTextNode(fmt(val)));
    el.appendChild(row);
  }
  if (!el.childNodes.length) el.textContent = '暂无信息';
}

async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    return;
  } catch (e) { /* fallthrough 到 fallback */ }
  // fallback：临时 textarea 选中复制
  const ta = document.createElement('textarea');
  ta.value = text;
  ta.style.position = 'fixed';
  ta.style.opacity = '0';
  document.body.appendChild(ta);
  ta.select();
  try { document.execCommand('copy'); } catch (e2) { /* 忽略 */ }
  document.body.removeChild(ta);
}

async function refresh(root) {
  try {
    const d = await rpc('app.about_info', {});
    render(root, d || {});
  } catch (e) {
    $('#aboutBody', root).textContent = '加载失败：' + e.message;
  }
}

function wire(root) {
  $('#aboutUpdate', root).addEventListener('click', async () => {
    if (_checking || _updating) return;
    setChecking(true);
    setUpdateMsg('正在检查…', 'checking');
    try {
      const d = await rpc('app.check_update', {});
      // 回执通常只是 {"ok":true,"async":true}；真正结果走 update_checked 事件。
      if (d && d.has_update !== undefined) {
        applyUpdateResult(d);
      } else if (d && d.ok === false) {
        applyUpdateResult(d);
      } else {
        // 事件与 rpc_result 走同一条通道且事件先发，正常已到达；留 2s 宽限兜底。
        setTimeout(() => {
          if (!_checking) return;
          setChecking(false);
          setUpdateMsg('检查已完成，但没收到结果，请重试', 'err');
        }, 2000);
      }
    } catch (e) {
      setChecking(false);
      setUpdateMsg('检查失败：' + e.message, 'err');
      toast('检查更新失败：' + e.message);
    }
  });
  $('#aboutUpdateNow', root).addEventListener('click', async () => {
    if (_updating || _checking) return;
    if (!window.confirm('要下载并安装新版本吗？下载完成后程序会自动重启。')) return;
    const nb = updateNowBtn();
    if (nb) nb.hidden = true;
    setUpdating(true);
    hideProgress();
    try {
      await rpc('update.apply', {});
      // 真正进度走 update_progress 事件；此处无需额外处理
    } catch (e) {
      setUpdating(false);
      const msg = '更新启动失败：' + e.message;
      setUpdateMsg(msg, 'err');
      toast(msg);
      if (nb) nb.hidden = false;
    }
  });
  $('#aboutDiag', root).addEventListener('click', async () => {
    try {
      const d = await rpc('app.diag_info', {});
      await copyText(d.text || '');
      toast('诊断信息已复制');
    } catch (e) { toast('获取诊断信息失败：' + e.message); }
  });
}

// 模块加载时注册一次（bridge.on 是幂等的 Set，模块只会被 import 一次）。
// 后端 _do_update_check() 在 web 模式下推这个事件，这里就地渲染，不跳页。
on('update_checked', (d) => {
  applyUpdateResult(d);
});

// ★ v9.22.2：自动更新的过程只用「一行人话 + 一条进度条」表达。
//   载荷（后端契约）：{stage, text, done, total, percent}
on('update_progress', (d) => {
  renderProgress(d);
});

// web 模式下「自动更新」不再弹 Qt 进度框，改为推 update_error —— 就地提示原因
on('update_error', (d) => {
  const reason = plainText(d && d.error) || '未知原因';
  hideProgress();
  setUpdating(false);
  setChecking(false);
  setUpdateMsg('更新失败：' + reason, 'err');
});

export async function init(root) {
  if (!root.dataset.built) {
    build(root);
    root.dataset.built = '1';
    wire(root);
  }
  // 切页回来时把上次结果原样恢复（DOM 被重建但内存里的 _updateMsg 还在）
  if (_updateMsg) setUpdateMsg(_updateMsg.text, _updateMsg.kind);
  if (_lastProgress) renderProgress(_lastProgress);
  syncUpdateBtn();
  await refresh(root);
}
