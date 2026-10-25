// views/memory.js —— 记忆页：长期记忆编辑 + 自动写入记录 + 工作总结
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
      <h2>长期记忆</h2>
      <span class="page-hint" id="memHint"></span>
      <span class="spacer"></span>
      <button class="btn" id="memReload">重新载入</button>
      <button class="btn btn-primary" id="memSave">保存记忆</button>
    </div>
    <div class="card" style="flex:1;min-height:0;display:flex;flex-direction:column;">
      <div class="card-title">长期记忆（Markdown）
        <span class="spacer"></span>
        <button class="btn btn-danger btn-ghost" id="memClear">清空全部</button>
      </div>
      <textarea class="input" id="memEdit" style="flex:1;min-height:200px;font-family:var(--mono);font-size:12.5px;"></textarea>
    </div>
    <div class="card">
      <div class="card-title">自动写入记录 <span class="hint" id="autoHint"></span></div>
      <div class="readonly-body" id="autoView" style="max-height:220px;">加载中…</div>
    </div>
    <div class="card">
      <div class="card-title">工作总结
        <span class="spacer"></span>
        <button class="btn" id="worklogOpen">打开文件夹</button>
      </div>
      <div class="readonly-body" id="worklogView" style="max-height:220px;">加载中…</div>
    </div>
  `;
}

async function refresh(root) {
  try {
    const d = await rpc('memory.get_text', {});
    $('#memEdit', root).value = d.text || '';
    const stats = d.stats || {};
    const n = Object.values(stats).reduce((a, v) => a + (Array.isArray(v) ? v.length : 0), 0);
    const cats = Object.keys(stats).join(' / ') || '无';
    $('#memHint', root).textContent = `共 ${n} 条 | 分类：${cats}`;
  } catch (e) {
    $('#memHint', root).textContent = '加载失败';
  }
  try {
    const d = await rpc('memory.auto_logs', {});
    $('#autoView', root).textContent = d.logs || '（无自动写入记录）';
  } catch (e) { $('#autoView', root).textContent = '加载失败'; }
  try {
    const d = await rpc('memory.worklog', {});
    const today = d.today || {};
    const dayText = typeof today === 'object' ? (today.summary || JSON.stringify(today)) : today;
    $('#worklogView', root).textContent = (d.text || '') + (dayText ? '\n\n' + dayText : '');
  } catch (e) { $('#worklogView', root).textContent = '加载失败'; }
}

function wire(root) {
  $('#memSave', root).addEventListener('click', async () => {
    await rpc('memory.save_text', { text: $('#memEdit', root).value });
    toast('记忆已保存');
    await refresh(root);
  });
  $('#memReload', root).addEventListener('click', () => refresh(root));
  $('#memClear', root).addEventListener('click', async () => {
    if (!confirm('确定清空全部长期记忆？此操作不可撤销。')) return;
    await rpc('memory.clear', {});
    toast('已清空');
    await refresh(root);
  });
  $('#worklogOpen', root).addEventListener('click', async () => {
    await rpc('memory.open_worklog_dir', {});
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
