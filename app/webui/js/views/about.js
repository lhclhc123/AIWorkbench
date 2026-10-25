// views/about.js —— 关于页：应用信息 + 检查更新 + 复制诊断信息
'use strict';

import { rpc } from '../bridge.js';

const $ = (s, root) => (root || document).querySelector(s);

function toast(msg) {
  const el = $('#toast');
  el.textContent = msg;
  el.classList.add('show');
  clearTimeout(toast._t);
  toast._t = setTimeout(() => el.classList.remove('show'), 1900);
}

function build(root) {
  root.innerHTML = `
    <div class="page-head">
      <h2>关于</h2>
      <span class="spacer"></span>
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
    try {
      await rpc('app.check_update', {});
      toast('已触发检查更新');
    } catch (e) { toast('检查更新失败：' + e.message); }
  });
  $('#aboutDiag', root).addEventListener('click', async () => {
    try {
      const d = await rpc('app.diag_info', {});
      await copyText(d.text || '');
      toast('诊断信息已复制');
    } catch (e) { toast('获取诊断信息失败：' + e.message); }
  });
}

export async function init(root) {
  if (!root.dataset.built) {
    build(root);
    root.dataset.built = '1';
    wire(root);
  }
  await refresh(root);
}
