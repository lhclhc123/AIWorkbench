// views/team.js —— 团队面板：列出团队、选择本对话团队、发起 / 取消团队任务，状态自动刷新
// 仅依赖 bridge.rpc，不碰传输层。init(root) 在每次切回时由 nav.js 调用，用 dataset.built
// 保证 DOM 只 build 一次；自动刷新用 setInterval（init 每次调用时重启，避免重复计时器）。
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

let teams = [];
let selectedTeamId = null;
let timer = null;

// 本视图自带局部样式（scoped 到 team 页内部），对齐 settings 页的 .f-row / .f-label 用法
const STYLE = `
.team-list{display:flex;flex-direction:column;gap:6px;}
.team-item{display:flex;flex-direction:column;gap:2px;padding:8px 10px;border:1px solid var(--border);border-radius:9px;cursor:pointer;transition:background-color .15s,border-color .15s;}
.team-item:hover{background:var(--row-hover);}
.team-item.is-active{background:var(--accent-weak);border-color:var(--accent);}
.team-name{font-size:13px;font-weight:600;color:var(--text);}
.team-meta{font-size:11px;color:var(--text-3);}
.f-row{display:flex;align-items:center;gap:10px;padding:6px 0;flex-wrap:wrap;}
.f-label{flex:0 0 64px;font-size:12.5px;color:var(--text-2);}
.f-row .input{flex:1;min-width:160px;max-width:420px;}
`;

function build(root) {
  root.innerHTML = `
    <style>${STYLE}</style>
    <div class="page-head">
      <h2>团队</h2>
      <span class="page-hint" id="teamHint"></span>
      <span class="spacer"></span>
      <button class="btn" id="teamRefresh">刷新</button>
    </div>

    <div class="card">
      <div class="card-title">我的团队</div>
      <div id="teamList" class="team-list"><div class="empty">暂无团队</div></div>
    </div>

    <div class="card">
      <div class="card-title">选择本对话团队</div>
      <div class="f-row">
        <span class="f-label">团队</span>
        <select class="input" id="teamSelect"></select>
        <button class="btn btn-primary" id="teamChoose">选择</button>
      </div>
      <div class="hint" id="teamChosen"></div>
    </div>

    <div class="card">
      <div class="card-title">发起团队任务</div>
      <div class="inline-form">
        <input class="input grow" id="teamGoal" placeholder="输入团队目标…">
        <button class="btn btn-primary" id="teamStart">开始团队任务</button>
      </div>
      <div class="hint" id="teamStatus"></div>
    </div>
  `;
}

function renderTeams(root) {
  const list = $('#teamList', root);
  list.innerHTML = '';
  $('#teamHint', root).textContent = teams.length ? `共 ${teams.length} 个团队` : '';
  if (!teams.length) {
    list.innerHTML = '<div class="empty">暂无团队</div>';
    return;
  }
  for (const tm of teams) {
    const item = document.createElement('div');
    item.className = 'team-item' + (String(tm.id) === String(selectedTeamId) ? ' is-active' : '');
    const name = document.createElement('span');
    name.className = 'team-name';
    name.textContent = tm.name || tm.id || '（未命名）';
    const meta = document.createElement('span');
    meta.className = 'team-meta';
    meta.textContent = tm.description || tm.detail || '';
    item.append(name, meta);
    item.addEventListener('click', () => {
      selectedTeamId = tm.id;
      renderTeams(root);
      const sel = $('#teamSelect', root);
      if (sel) sel.value = String(tm.id);
    });
    list.appendChild(item);
  }
}

function fillTeamSelect(root) {
  const sel = $('#teamSelect', root);
  const cur = sel.value;
  sel.innerHTML = '';
  if (!teams.length) {
    const o = document.createElement('option');
    o.value = '';
    o.textContent = '（无可用团队）';
    sel.appendChild(o);
    return;
  }
  for (const tm of teams) {
    const o = document.createElement('option');
    o.value = String(tm.id);
    o.textContent = tm.name || tm.id;
    sel.appendChild(o);
  }
  sel.value = cur || (teams[0] ? String(teams[0].id) : '');
  if (!selectedTeamId && teams[0]) selectedTeamId = teams[0].id;
}

async function refresh(root) {
  try {
    const d = await rpc('team.list', {});
    teams = Array.isArray(d && d.teams) ? d.teams : [];
  } catch (e) {
    $('#teamHint', root).textContent = '加载失败';
  }
  fillTeamSelect(root);
  renderTeams(root);
}

function wire(root) {
  $('#teamRefresh', root).addEventListener('click', () => refresh(root));

  $('#teamChoose', root).addEventListener('click', async () => {
    const sel = $('#teamSelect', root);
    const teamId = sel && sel.value;
    if (!teamId) { toast('请先选择团队'); return; }
    selectedTeamId = teamId;
    try {
      await rpc('team.select', { team_id: teamId });
      const tm = teams.find((x) => String(x.id) === String(teamId));
      $('#teamChosen', root).textContent = '已选择团队：' + (tm && tm.name ? tm.name : teamId);
      toast('已选择团队');
    } catch (e) {
      toast('选择失败：' + e.message);
    }
  });

  $('#teamStart', root).addEventListener('click', async () => {
    const goalEl = $('#teamGoal', root);
    const goal = goalEl && goalEl.value.trim();
    const teamId = (selectedTeamId) || ($('#teamSelect', root) && $('#teamSelect', root).value);
    if (!goal) { toast('请填写团队目标'); return; }
    if (!teamId) { toast('请先选择团队'); return; }
    try {
      const d = await rpc('team.start', { goal, team_id: teamId });
      const runId = (d && (d.id || d.run_id || d.task_id)) || '';
      const statusEl = $('#teamStatus', root);
      statusEl.textContent = '已发起团队任务' + (runId ? '（运行 ID：' + runId + '）' : '');
      if (runId) {
        const cancel = document.createElement('button');
        cancel.className = 'btn btn-danger';
        cancel.textContent = '取消';
        cancel.dataset.act = 'cancel';
        cancel.dataset.id = String(runId);
        statusEl.appendChild(document.createTextNode(' '));
        statusEl.appendChild(cancel);
      }
      toast('已发起团队任务');
    } catch (e) {
      toast('发起失败：' + e.message);
    }
  });

  // 取消：从状态文本里取最近一次运行 ID；若无则提示
  $('#teamStatus', root).addEventListener('click', async (e) => {
    if (e.target && e.target.dataset && e.target.dataset.act === 'cancel') {
      const id = e.target.dataset.id;
      try {
        await rpc('team.cancel', { id });
        $('#teamStatus', root).textContent = '已取消团队任务 ' + id;
        toast('已取消');
      } catch (err) {
        toast('取消失败：' + err.message);
      }
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
  // 运行状态自动刷新：init 每次切回都调用，先清旧的再起新的，保证至多一个计时器
  if (timer) clearInterval(timer);
  timer = setInterval(() => refresh(root), 2000);
}
