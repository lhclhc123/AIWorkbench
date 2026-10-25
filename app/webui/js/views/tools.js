// views/tools.js —— 工具调用记录页：统计摘要 + 可折叠调用列表
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
      <h2>工具调用记录</h2>
      <span class="page-hint" id="toolsHint"></span>
      <span class="spacer"></span>
      <button class="btn" id="toolsReload">刷新</button>
      <button class="btn btn-danger" id="toolsClear">清空记录</button>
    </div>
    <div class="card">
      <div class="card-title">统计</div>
      <div class="hint" id="toolsStats">加载中…</div>
    </div>
    <div class="card" style="flex:1;min-height:0;display:flex;flex-direction:column;">
      <div class="card-title">调用记录</div>
      <div class="readonly-body rich" id="toolsList" style="flex:1;">加载中…</div>
    </div>
  `;
}

function fmt(v) {
  if (v == null) return '';
  if (typeof v === 'string') return v;
  try { return JSON.stringify(v, null, 2); } catch (e) { return String(v); }
}

function fmtTime(t) {
  if (t == null || t === '') return '';
  if (typeof t === 'string') return t;
  const n = Number(t);
  if (!Number.isFinite(n)) return String(t);
  const d = new Date(n < 1e12 ? n * 1000 : n);
  return d.toLocaleString('zh-CN', { hour12: false });
}

// stats 字段未知，宽松遍历展示：嵌套对象展开为 k=v
function renderStats(root, stats) {
  const el = $('#toolsStats', root);
  if (!stats || typeof stats !== 'object' || !Object.keys(stats).length) {
    el.textContent = '暂无统计';
    return;
  }
  const parts = [];
  for (const [k, v] of Object.entries(stats)) {
    if (v && typeof v === 'object') {
      parts.push(`${k}: { ${Object.entries(v).map(([a, b]) => `${a}=${b}`).join(', ')} }`);
    } else {
      parts.push(`${k}: ${v}`);
    }
  }
  el.textContent = parts.join('   ·   ');
}

function renderItems(root, items) {
  const el = $('#toolsList', root);
  el.innerHTML = '';
  if (!items || !items.length) {
    el.textContent = '暂无调用记录';
    return;
  }
  const frag = document.createDocumentFragment();
  for (const it of items) {
    const row = document.createElement('div');
    row.style.padding = '6px 0';
    row.style.borderBottom = '1px solid var(--border)';

    const head = document.createElement('div');
    head.className = 'inline-form';

    const time = document.createElement('span');
    time.className = 'hint';
    time.textContent = '[' + fmtTime(it.t) + ']';

    const name = document.createElement('span');
    name.style.fontWeight = '600';
    name.textContent = it.name || '（未知工具）';

    const ms = document.createElement('span');
    ms.className = 'hint';
    ms.textContent = it.ms != null ? it.ms + 'ms' : '';

    const ok = document.createElement('span');
    ok.className = 'pill ' + (it.ok ? 'on' : 'off');
    ok.textContent = it.ok ? '成功' : '失败';

    head.appendChild(time);
    head.appendChild(name);
    head.appendChild(ms);
    head.appendChild(ok);
    row.appendChild(head);

    const details = document.createElement('details');
    const summary = document.createElement('summary');
    summary.textContent = 'args / result';
    const pre = document.createElement('pre');
    pre.textContent = 'args: ' + fmt(it.args) + '\nresult: ' + fmt(it.result);
    details.appendChild(summary);
    details.appendChild(pre);
    row.appendChild(details);

    frag.appendChild(row);
  }
  el.appendChild(frag);
}

async function refresh(root) {
  try {
    const d = await rpc('trace.stats', {});
    renderStats(root, d.stats);
  } catch (e) {
    renderStats(root, null);
  }
  try {
    const d = await rpc('trace.list', { limit: 200 });
    renderItems(root, d.items);
    $('#toolsHint', root).textContent = `共 ${(d.items || []).length} 条记录`;
  } catch (e) {
    $('#toolsHint', root).textContent = '加载失败';
    $('#toolsList', root).textContent = '加载失败：' + e.message;
  }
}

function wire(root) {
  $('#toolsReload', root).addEventListener('click', () => refresh(root));
  $('#toolsClear', root).addEventListener('click', async () => {
    if (!confirm('确定清空全部工具调用记录？此操作不可撤销。')) return;
    try {
      const d = await rpc('trace.clear', {});
      if (d && d.ok === false) { toast('清空失败：' + (d.error || '未知错误')); return; }
      toast('已清空');
      await refresh(root);
    } catch (e) { toast('清空失败：' + e.message); }
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
