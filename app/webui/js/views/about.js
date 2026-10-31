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

let _updateMsg = null;   // {text, kind} 最近一次结果；切页回来仍能恢复显示
let _checking = false;
let _checkTimer = null;

function updateBtn() { return document.getElementById('aboutUpdate'); }
function updateMsgEl() { return document.getElementById('aboutUpdateMsg'); }

/** 渲染按钮右侧的状态文案（就地显示，不弹窗、不跳页）。 */
function setUpdateMsg(text, kind) {
  _updateMsg = text ? { text: String(text), kind: kind || 'checking' } : null;
  const el = updateMsgEl();
  if (!el) return;
  el.textContent = _updateMsg ? _updateMsg.text : '';
  el.style.color = UPDATE_COLORS[(_updateMsg && _updateMsg.kind) || 'checking'];
}

/** 切换「检查中」态：按钮 disabled + 文案变化（>500ms 的操作必须有进行中反馈）。 */
function setChecking(on) {
  _checking = !!on;
  const btn = updateBtn();
  if (btn) {
    btn.disabled = _checking;
    btn.textContent = _checking ? '检查中…' : '检查更新';
  }
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

/** 把后端回执/事件统一解析成一条可读文案。 */
function applyUpdateResult(d) {
  const data = d || {};
  if (data.async === true && data.ok === true && data.has_update === undefined) return; // 纯回执
  setChecking(false);
  if (!data.ok) {
    const reason = data.error || '未知原因';
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
}

function build(root) {
  root.innerHTML = `
    <div class="page-head">
      <h2>关于</h2>
      <span class="spacer"></span>
      <span class="hint" id="aboutUpdateMsg"></span>
      <button class="btn" id="aboutUpdate">检查更新</button>
      <button class="btn" id="aboutDiag">复制诊断信息</button>
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
    if (_checking) return;
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

// web 模式下「自动更新」不再弹 Qt 进度框，改为推 update_error —— 就地提示原因
on('update_error', (d) => {
  const reason = (d && d.error) || '未知原因';
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
  setChecking(_checking);
  await refresh(root);
}
