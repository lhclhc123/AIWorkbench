// views/tasks.js —— 定时任务页：任务表格 + 新建/立即执行/启用停用/删除
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

let tasks = [];
let selectedId = null;

function build(root) {
  root.innerHTML = `
    <div class="page-head">
      <h2>定时任务</h2>
      <span class="page-hint" id="tasksHint"></span>
      <span class="spacer"></span>
      <button class="btn btn-primary" id="tasksNew">新建任务</button>
      <button class="btn" id="tasksRun">立即执行</button>
      <button class="btn" id="tasksToggle">启用/停用</button>
      <button class="btn btn-danger" id="tasksDel">删除</button>
      <button class="btn" id="tasksReload">刷新</button>
    </div>
    <div class="card" id="tasksForm" hidden>
      <div class="card-title">新建任务</div>
      <div class="inline-form">
        <input class="input grow" id="taskName" placeholder="任务名">
        <select class="input" id="taskKind"></select>
      </div>
      <div class="inline-form" style="margin-top:8px;">
        <input class="input" id="taskAt" placeholder="at（如 09:00）">
        <input class="input" id="taskWeekdays" placeholder="weekdays（如 1,2,3,4,5）">
        <input class="input" id="taskInterval" placeholder="interval_min（分钟）">
      </div>
      <textarea class="input" id="taskPrompt" placeholder="任务 prompt" style="margin-top:8px;min-height:120px;"></textarea>
      <div class="inline-form" style="margin-top:8px;">
        <button class="btn btn-primary" id="taskSave">保存</button>
        <button class="btn" id="taskCancel">取消</button>
      </div>
    </div>
    <div class="card" style="flex:1;min-height:0;overflow-y:auto;">
      <div class="card-title">任务列表</div>
      <table class="data-table">
        <thead>
          <tr><th>任务名</th><th>重复规则</th><th>下次</th><th>状态</th><th>上次结果</th></tr>
        </thead>
        <tbody id="tasksBody"></tbody>
      </table>
    </div>
  `;
}

function fmt(v) {
  if (v == null) return '';
  if (typeof v === 'string') return v;
  try { return JSON.stringify(v); } catch (e) { return String(v); }
}

// next 可能是时间戳（秒/毫秒）或字符串，统一宽松格式化
function fmtTime(v) {
  if (v == null || v === '') return '';
  if (typeof v === 'string') return v;
  const n = Number(v);
  if (!Number.isFinite(n)) return String(v);
  const d = new Date(n < 1e12 ? n * 1000 : n);
  return d.toLocaleString('zh-CN', { hour12: false });
}

function renderTasks(root) {
  const tb = $('#tasksBody', root);
  tb.innerHTML = '';
  if (!tasks.length) {
    tb.innerHTML = '<tr><td colspan="5"><div class="empty">暂无任务</div></td></tr>';
    return;
  }
  for (const t of tasks) {
    const tr = document.createElement('tr');
    tr.className = 'row-select';
    if (String(t.id) === String(selectedId)) {
      tr.classList.add('is-active');
      tr.style.background = 'var(--accent-weak)';
    }
    tr.addEventListener('click', () => {
      selectedId = t.id;
      renderTasks(root);
    });

    const cName = document.createElement('td');
    cName.textContent = t.name || t.id || '（未命名）';

    const cRule = document.createElement('td');
    cRule.textContent = t.describe || '';

    const cNext = document.createElement('td');
    cNext.textContent = fmtTime(t.next);

    const cStatus = document.createElement('td');
    const pill = document.createElement('span');
    const on = !!t.enabled;
    pill.className = 'pill ' + (on ? 'on' : 'off');
    pill.textContent = on ? '启用' : '停用';
    cStatus.appendChild(pill);

    const cLast = document.createElement('td');
    cLast.textContent = t.last_status != null && t.last_status !== '' ? fmt(t.last_status) : fmt(t.last_result);

    tr.appendChild(cName);
    tr.appendChild(cRule);
    tr.appendChild(cNext);
    tr.appendChild(cStatus);
    tr.appendChild(cLast);
    tb.appendChild(tr);
  }
}

async function loadKinds(root) {
  try {
    const d = await rpc('tasks.enums', {});
    const kinds = Array.isArray(d.kinds) ? d.kinds : [];
    const labels = d.kind_labels || {};
    const sel = $('#taskKind', root);
    sel.innerHTML = '';
    if (!kinds.length) {
      const o = document.createElement('option');
      o.value = '';
      o.textContent = '（无可用类型）';
      sel.appendChild(o);
      return;
    }
    for (const k of kinds) {
      const o = document.createElement('option');
      o.value = k;
      o.textContent = labels[k] || k;
      sel.appendChild(o);
    }
  } catch (e) {
    const sel = $('#taskKind', root);
    sel.innerHTML = '';
    const o = document.createElement('option');
    o.value = '';
    o.textContent = '（加载类型失败）';
    sel.appendChild(o);
  }
}

async function refresh(root) {
  try {
    const d = await rpc('tasks.list', {});
    tasks = Array.isArray(d.tasks) ? d.tasks : [];
    renderTasks(root);
    $('#tasksHint', root).textContent = `共 ${tasks.length} 个任务`;
  } catch (e) {
    $('#tasksHint', root).textContent = '加载失败';
    $('#tasksBody', root).innerHTML = '<tr><td colspan="5"><div class="empty">加载失败</div></td></tr>';
  }
}

function wire(root) {
  $('#tasksNew', root).addEventListener('click', () => {
    $('#tasksForm', root).hidden = false;
  });
  $('#tasksCancel', root).addEventListener('click', () => {
    $('#tasksForm', root).hidden = true;
  });
  $('#taskSave', root).addEventListener('click', async () => {
    const name = $('#taskName', root).value.trim();
    const prompt = $('#taskPrompt', root).value;
    if (!name) { toast('请填写任务名'); return; }
    const intervalRaw = $('#taskInterval', root).value.trim();
    const args = {
      name,
      prompt,
      kind: $('#taskKind', root).value,
      at: $('#taskAt', root).value.trim(),
      weekdays: $('#taskWeekdays', root).value.trim(),
      interval_min: intervalRaw ? parseInt(intervalRaw, 10) : 0,
    };
    try {
      const d = await rpc('tasks.create', args);
      if (d && d.ok === false) { toast('创建失败：' + (d.error || '未知错误')); return; }
      toast('任务已创建');
      $('#tasksForm', root).hidden = true;
      $('#taskName', root).value = '';
      $('#taskPrompt', root).value = '';
      $('#taskAt', root).value = '';
      $('#taskWeekdays', root).value = '';
      $('#taskInterval', root).value = '';
      await refresh(root);
    } catch (e) {
      toast('创建失败：' + e.message);
    }
  });
  $('#tasksRun', root).addEventListener('click', async () => {
    if (selectedId == null) { toast('请先选择任务'); return; }
    try {
      await rpc('tasks.run_now', { id: selectedId });
      toast('已触发立即执行');
    } catch (e) { toast('执行失败：' + e.message); }
  });
  $('#tasksToggle', root).addEventListener('click', async () => {
    const t = tasks.find((x) => String(x.id) === String(selectedId));
    if (!t) { toast('请先选择任务'); return; }
    const target = !t.enabled;
    try {
      const d = await rpc('tasks.update', { id: t.id, enabled: target });
      if (d && d.ok === false) { toast('操作失败：' + (d.error || '未知错误')); return; }
      toast(target ? '已启用' : '已停用');
      await refresh(root);
    } catch (e) { toast('操作失败：' + e.message); }
  });
  $('#tasksDel', root).addEventListener('click', async () => {
    const t = tasks.find((x) => String(x.id) === String(selectedId));
    if (!t) { toast('请先选择任务'); return; }
    if (!confirm(`确定删除任务「${t.name || t.id}」？此操作不可撤销。`)) return;
    try {
      const d = await rpc('tasks.delete', { id: t.id });
      if (d && d.ok === false) { toast('删除失败：' + (d.error || '未知错误')); return; }
      toast('已删除');
      selectedId = null;
      await refresh(root);
    } catch (e) { toast('删除失败：' + e.message); }
  });
  $('#tasksReload', root).addEventListener('click', () => refresh(root));
}

export async function init(root) {
  if (!root.dataset.built) {
    build(root);
    root.dataset.built = '1';
    wire(root);
  }
  await Promise.all([refresh(root), loadKinds(root)]);
}
