// views/bg.js —— 并行任务池监控：实时列出后台并行任务，支持取消 / 全部取消 / 清除已完成
// 仅依赖 bridge.rpc / bridge.on，不碰传输层。视图每次切回都走 init(root)（nav.js 行为），
// 用 dataset.built 保证 DOM 只 build 一次，自动刷新用 setInterval（init 每次调用时重启，
// 避免重复计时器；无 destroy 钩子，故不主动清理，至多一个存活计时器）。
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
let timer = null;

function build(root) {
  root.innerHTML = `
    <div class="page-head">
      <h2>并行任务池</h2>
      <span class="page-hint" id="bgHint"></span>
      <span class="spacer"></span>
      <button class="btn" id="bgCancelAll">全部取消</button>
      <button class="btn" id="bgClear">清除已完成</button>
    </div>
    <div class="card" style="flex:1;min-height:0;overflow-y:auto;">
      <div class="card-title">运行中的任务</div>
      <table class="data-table">
        <thead>
          <tr><th>ID</th><th>名称</th><th>状态</th><th>进度</th><th>操作</th></tr>
        </thead>
        <tbody id="bgBody"></tbody>
      </table>
      <div class="empty" id="bgEmpty" hidden>暂无并行任务</div>
    </div>
  `;
}

// 进度可能后端给 0~1 浮点、0~100 整数或不给，做容错展示
function fmtProgress(t) {
  const p = t && t.progress;
  if (p == null || p === '') return '—';
  const n = Number(p);
  if (!Number.isFinite(n)) return String(p);
  const pct = n > 1 ? n : n * 100;            // 0~1 视为比例，否则视为已百分比
  return Math.round(pct) + '%';
}

function fmtStatus(t) {
  return (t && (t.status || t.state || t.phase)) || '—';
}

function renderTasks(root) {
  const tb = $('#bgBody', root);
  tb.innerHTML = '';
  $('#bgEmpty', root).hidden = tasks.length > 0;
  $('#bgHint', root).textContent = tasks.length ? `共 ${tasks.length} 个并行任务` : '';
  for (const t of tasks) {
    const tr = document.createElement('tr');

    const cId = document.createElement('td');
    cId.textContent = t.id != null ? String(t.id) : '—';

    const cName = document.createElement('td');
    cName.textContent = t.name || t.title || '（未命名）';

    const cStatus = document.createElement('td');
    const pill = document.createElement('span');
    const st = String(fmtStatus(t)).toLowerCase();
    const done = /(done|完成|完成|success|ok|finished|完成)/i.test(st);
    const fail = /(fail|error|失败|错误|cancel|取消)/i.test(st);
    pill.className = 'pill ' + (fail ? 'off' : done ? 'on' : 'on');
    pill.textContent = fmtStatus(t);
    cStatus.appendChild(pill);

    const cProg = document.createElement('td');
    cProg.textContent = fmtProgress(t);

    const cOp = document.createElement('td');
    const cancel = document.createElement('button');
    cancel.className = 'btn';
    cancel.textContent = '取消';
    cancel.addEventListener('click', async () => {
      try {
        await rpc('bg.cancel', { id: t.id });
        toast('已取消任务 ' + (t.id != null ? t.id : ''));
      } catch (e) {
        toast('取消失败：' + e.message);
      }
    });
    cOp.appendChild(cancel);

    tr.append(cId, cName, cStatus, cProg, cOp);
    tb.appendChild(tr);
  }
}

async function refresh(root) {
  try {
    const d = await rpc('bg.list', {});
    tasks = Array.isArray(d && d.tasks) ? d.tasks : [];
  } catch (e) {
    // 后端 RPC 尚未就绪时不刷红，保持上一次列表，避免占位态闪烁
    $('#bgHint', root).textContent = '加载失败';
  }
  renderTasks(root);
}

function wire(root) {
  $('#bgCancelAll', root).addEventListener('click', async () => {
    try {
      await rpc('bg.cancel_all', {});
      toast('已请求全部取消');
    } catch (e) {
      toast('全部取消失败：' + e.message);
    }
  });
  $('#bgClear', root).addEventListener('click', async () => {
    try {
      await rpc('bg.clear', {});
      toast('已清除已完成');
    } catch (e) {
      toast('清除失败：' + e.message);
    }
  });
}

export async function init(root) {
  if (!root.dataset.built) {
    build(root);
    root.dataset.built = '1';
    wire(root);
  }
  await refresh(root);
  // 自动刷新：init 每次切回都调用，先清旧的再起新的，保证至多一个计时器
  if (timer) clearInterval(timer);
  timer = setInterval(() => refresh(root), 1500);
}
