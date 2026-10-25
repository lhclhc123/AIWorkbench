// im.js —— 钉钉助手页逻辑：消息双栏 + 三 tab + 设置 + 标签 + CLI 一键配置
// 依赖 bridge.js 的 rpc() 调后端方法；数据全来自后端 IMStore。
'use strict';

import { rpc, call, on } from './bridge.js';

const $ = (s) => document.querySelector(s);

let state = {
  rows: [],        // [{msgid,cid,name,kind,text,time,status,reply,sender}]
  settings: {},    // IMStore settings
  tags: [],        // [{id,name,kind,tag,tag_auto,msg_count}]
  whitelist: [],
  running: false,
  dingStatus: '',
  selected: new Set(),   // 选中的 msgid
  selectedTagId: null,
};

function toast(msg) {
  const el = $('#toast');
  el.textContent = msg;
  el.classList.add('show');
  clearTimeout(toast._t);
  toast._t = setTimeout(() => el.classList.remove('show'), 1900);
}

/* ---------------- tab 切换 ---------------- */
function wireTabs() {
  document.querySelectorAll('.tab').forEach((t) => {
    t.addEventListener('click', () => {
      document.querySelectorAll('.tab').forEach((x) => x.classList.toggle('is-active', x === t));
      document.querySelectorAll('.panel').forEach((p) => p.classList.toggle('is-active', p.dataset.panel === t.dataset.tab));
    });
  });
}

/* ---------------- 消息双栏渲染 ---------------- */
function statusCls(status) {
  if (status === '已回复') return 'st-done';
  if (status === '需人工') return 'st-manual';
  if (status === '处理中') return 'st-work';
  if (status === '失败' || status === '发送失败') return 'st-fail';
  return 'st-wait';
}

function renderMessages() {
  const left = $('#colLeft');
  const right = $('#colRight');
  left.innerHTML = '';
  right.innerHTML = '';
  const done = state.rows.filter((r) => r.status === '已回复');
  const pending = state.rows.filter((r) => r.status !== '已回复');

  const build = (r) => {
    const row = document.createElement('label');
    row.className = 'im-row';
    row.innerHTML =
      `<input class="cb" type="checkbox" data-msgid="${r.msgid}">` +
      `<span class="cbx"><svg width="10" height="10" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="m3.2 8.4 3.1 3.1 6.5-6.5"/></svg></span>` +
      `<div class="im-card">` +
        `<div class="im-top"><span class="who"></span><span class="kind"></span><span class="im-time"></span></div>` +
        `<p class="im-text"></p>` +
        `<div class="im-sub"><span class="conv-name"></span><span class="st"></span></div>` +
      `</div>`;
    row.querySelector('.who').textContent = r.name || '钉钉';
    row.querySelector('.kind').textContent = r.kind === 'p2p' ? '单聊' : '群聊';
    row.querySelector('.im-time').textContent = r.time || '';
    row.querySelector('.im-text').textContent = r.text || '';
    row.querySelector('.conv-name').textContent = r.sender || '';
    const st = row.querySelector('.st');
    st.textContent = r.status;
    st.className = 'st ' + statusCls(r.status);

    // 已回复的显示 AI 回复
    if (r.status === '已回复' && r.reply) {
      const reply = document.createElement('div');
      reply.className = 'ai-reply';
      reply.innerHTML = `<span class="ai-badge"><svg width="9" height="9" viewBox="0 0 16 16" fill="currentColor"><path d="M8 1.8l1.4 3.9 3.9 1.4-3.9 1.4L8 12.4 6.6 8.5 2.7 7.1l3.9-1.4L8 1.8z"/></svg>AI 回复</span><p></p>`;
      reply.querySelector('p').textContent = r.reply;
      row.querySelector('.im-card').appendChild(reply);
    }

    const cb = row.querySelector('.cb');
    cb.addEventListener('change', () => {
      if (cb.checked) state.selected.add(r.msgid);
      else state.selected.delete(r.msgid);
      updateFoot();
    });
    return row;
  };

  if (pending.length === 0) {
    const e = document.createElement('div');
    e.className = 'empty';
    e.textContent = '暂无待处理消息';
    left.appendChild(e);
  } else pending.forEach((r) => left.appendChild(build(r)));

  if (done.length === 0) {
    const e = document.createElement('div');
    e.className = 'empty';
    e.textContent = '暂无已处理消息';
    right.appendChild(e);
  } else done.forEach((r) => right.appendChild(build(r)));

  $('#leftCount').textContent = pending.length;
  $('#rightCount').textContent = done.length;
  $('#tabCount').textContent = state.rows.length;
  updateFoot();
}

function updateFoot() {
  const n = state.selected.size;
  $('#delBtn').disabled = n === 0;
  $('#delLabel').textContent = n > 0 ? `删除选中 (${n})` : '删除选中';
  $('#selInfo').textContent = n > 0 ? `已选 ${n} 条` : '未选择';
}

/* ---------------- 设置表单 ---------------- */
function renderSettings() {
  const s = state.settings || {};
  $('#scopeGroup').checked = !!s.scope_group;
  $('#scopeP2p').checked = !!s.scope_p2p;
  $('#scopeAtMe').checked = !!s.scope_at_me;
  $('#delay').value = s.delay_seconds != null ? s.delay_seconds : 60;
  $('#uncertainSw').checked = s.uncertain_reply != null ? !!s.uncertain_reply : true;
  $('#uncertainText').value = s.uncertain_text || '';
  $('#identity').value = s.identity || '';
  $('#interval').value = s.interval != null ? s.interval : 25;
  $('#autoReply').checked = s.auto_reply != null ? !!s.auto_reply : true;
  renderWhitelist();
}

function renderWhitelist() {
  const list = $('#wlList');
  list.innerHTML = '';
  const wl = state.whitelist || [];
  if (wl.length === 0) {
    const e = document.createElement('div');
    e.className = 'empty';
    e.textContent = '暂无白名单';
    list.appendChild(e);
    return;
  }
  wl.forEach((w) => {
    const row = document.createElement('div');
    row.className = 'wl-row';
    row.innerHTML = `<span></span><span class="wl-kind"></span><span class="spacer" style="flex:1;"></span><button class="wl-del" title="移除"><svg width="11" height="11" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"><path d="M3.6 3.6l8.8 8.8M12.4 3.6l-8.8 8.8"/></svg></button>`;
    row.querySelector('span').textContent = w.name || w.id;
    row.querySelector('.wl-kind').textContent = w.kind === 'p2p' ? '单聊' : '群聊';
    row.querySelector('.wl-del').addEventListener('click', async () => {
      await rpc('im_whitelist_remove', { cid: w.id });
      await refreshState();
    });
    list.appendChild(row);
  });
}

/* ---------------- 标签渲染 ---------------- */
function renderTags() {
  const list = $('#tagsList');
  list.innerHTML = '';
  const tags = state.tags || [];
  if (tags.length === 0) {
    const e = document.createElement('div');
    e.className = 'empty';
    e.textContent = '暂无会话（先登录钉钉拉取会话，再扫描打标签）';
    list.appendChild(e);
    return;
  }
  tags.forEach((t) => {
    const btn = document.createElement('button');
    btn.className = 'tag-row' + (t.id === state.selectedTagId ? ' is-active' : '');
    btn.innerHTML =
      `<span class="tag-kind"></span><span class="tag-conv"></span><span class="tag-value"></span>`;
    btn.querySelector('.tag-kind').textContent = t.kind === 'p2p' ? '【单聊】' : '【群聊】';
    btn.querySelector('.tag-conv').textContent = t.name || t.id;
    const v = btn.querySelector('.tag-value');
    const tagsArr = Array.isArray(t.tags) ? t.tags : [];
    v.textContent = tagsArr.length > 0 ? tagsArr.join('、') : (t.tag || '未标注');
    if (tagsArr.length === 0 && !t.tag) v.classList.add('is-empty');
    btn.addEventListener('click', () => {
      state.selectedTagId = t.id;
      $('#tagInput').value = tagsArr.length > 0 ? tagsArr.join('、') : (t.tag || '');
      renderTags();
    });
    list.appendChild(btn);
  });
}

/* ---------------- 运行状态 / 钉钉状态 ---------------- */
function renderStatus() {
  const pill = $('#livePill');
  const txt = $('#liveText');
  if (state.running) {
    pill.classList.add('on');
    txt.textContent = '运行中';
    $('#toggleBtn').textContent = '停止助理';
  } else {
    pill.classList.remove('on');
    txt.textContent = '未运行';
    $('#toggleBtn').textContent = '启动助理';
  }
  $('#dingStatus').textContent = state.dingStatus || '';
  $('#cliStatus').textContent = state.dingStatus || '';
}

/* ---------------- 数据刷新 ---------------- */
async function refreshState() {
  try {
    const d = await rpc('im_get_state', {});
    if (!d || !d.ready) {
      toast('钉钉助手未初始化');
      return;
    }
    state.rows = d.rows || [];
    state.settings = d.settings || {};
    state.tags = d.tags || [];
    state.whitelist = d.whitelist || [];
    state.running = !!d.running;
    state.dingStatus = d.ding_status || '';
    renderMessages();
    renderSettings();
    renderTags();
    renderStatus();
  } catch (e) {
    console.error('[im] refreshState 失败', e);
  }
}

/* ---------------- 事件接线 ---------------- */
function wire() {
  // ★ v9.21.2：返回对话（切回 chat SPA，后端 _open_page('chat') 切 stack index 0）
  const backBtn = $('#backBtn');
  if (backBtn) backBtn.addEventListener('click', () => rpc('nav_to', { key: 'chat' }));

  // tab
  wireTabs();

  // 消息页操作
  $('#delBtn').addEventListener('click', async () => {
    if (state.selected.size === 0) return;
    await rpc('im_delete_msgs', { msgids: [...state.selected] });
    state.selected.clear();
    await refreshState();
    toast('已删除');
  });
  $('#allBtn').addEventListener('click', () => {
    call('rpc', { method: 'im_process_all', args: {}, reqId: 'all' + Date.now() });
    toast('已提交全部消息给 AI');
  });

  // 启动/停止
  $('#toggleBtn').addEventListener('click', async () => {
    await rpc('im_toggle', { start: !state.running });
    await refreshState();
  });

  // 白名单
  $('#wlAdd').addEventListener('click', async () => {
    const name = $('#wlInput').value.trim();
    if (!name) return;
    const kind = $('#wlKind').value === '单聊' ? 'p2p' : 'group';
    await rpc('im_whitelist_add', { cid: name, name: name, kind });
    $('#wlInput').value = '';
    await refreshState();
  });

  // 保存设置
  $('#saveBtn').addEventListener('click', async () => {
    const patch = {
      scope_group: $('#scopeGroup').checked,
      scope_p2p: $('#scopeP2p').checked,
      scope_at_me: $('#scopeAtMe').checked,
      delay_seconds: parseInt($('#delay').value || '0', 10),
      uncertain_reply: $('#uncertainSw').checked,
      uncertain_text: $('#uncertainText').value,
      identity: $('#identity').value,
      interval: parseInt($('#interval').value || '25', 10),
      auto_reply: $('#autoReply').checked,
    };
    await rpc('im_set_settings', patch);
    toast('设置已保存');
    await refreshState();
  });

  // 标签
  $('#scanBtn').addEventListener('click', () => {
    call('rpc', { method: 'im_scan_tags', args: {}, reqId: 'scan' + Date.now() });
    toast('已开始扫描打标签');
    setTimeout(refreshState, 3000);
  });
  $('#tagSave').addEventListener('click', async () => {
    if (!state.selectedTagId) {
      toast('先在上方选一个会话');
      return;
    }
    const tag = $('#tagInput').value.trim();
    await rpc('im_set_tag', { thread_id: state.selectedTagId, tag });
    await refreshState();
    toast('标签已保存');
  });

  // CLI 一键配置
  $('#cliSetupBtn').addEventListener('click', async () => {
    toast('开始检测/安装钉钉 CLI…');
    try {
      const d = await rpc('im_cli_setup', {}, 120000);
      if (d && d.ok) toast(d.message || '钉钉 CLI 配置完成');
      else toast((d && d.error) || '配置失败');
    } catch (e) {
      toast('CLI 配置失败：' + e.message);
    }
    await refreshState();
  });
  $('#cliLoginBtn').addEventListener('click', async () => {
    toast('开始登录…');
    try {
      const d = await rpc('im_cli_login', {}, 120000);
      if (d && d.ok) toast(d.message || '登录完成');
      else toast((d && d.error) || '登录失败');
    } catch (e) {
      toast('登录失败：' + e.message);
    }
    await refreshState();
  });
}

/* ---------------- 启动 ---------------- */
async function boot() {
  wire();
  // 订阅后端主动推送的 IM 刷新事件（消息到达/状态变化时后端会推 im_refresh）
  on('im_refresh', () => refreshState());
  await refreshState();
}

boot();
