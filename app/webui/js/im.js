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
  cli: {},         // {busy, ok, message, done_at} — 后端 CLI 异步任务状态（v9.22.1）
  checkTries: 0,   // 「检查中…」连续重试次数（v9.22.1）
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

/* ---------------- 状态轮询（v9.22.1 修复「永远检查中」） ----------------
 * 背景：后端查 dws 登录态是异步的（最坏 40s）。旧代码只有 boot 后一次性
 * setTimeout(refreshState, 3000)：3 秒时缓存还是占位串，之后再也没有刷新，
 * 页面就永远停在「检查中…」。现在改成：
 *   - 状态含「检查」：每 1.5s 重试，最多 20 次（约 30s），超限显示「状态未知」
 *   - 拿到真实状态：每 10s 刷新一次
 *   - 页面隐藏时暂停，回到前台立即刷一次
 *   - 点状态文字 = 立即重新检测
 */
let statusTimer = null;
const STATUS_RETRY_MS = 1500;
const STATUS_IDLE_MS = 10000;
const STATUS_MAX_TRIES = 20;

function statusIsChecking() {
  return /检查/.test(String(state.dingStatus || ''));
}

function scheduleStatusPoll() {
  clearTimeout(statusTimer);
  statusTimer = null;
  if (document.visibilityState === 'hidden') return;   // 后台不白跑
  if (statusIsChecking()) {
    state.checkTries = (state.checkTries || 0) + 1;
    if (state.checkTries > STATUS_MAX_TRIES) {
      state.dingStatus = '状态未知，点「重新检测」';
      renderStatus();
      return;                                          // 超限停止，别无限转圈
    }
    statusTimer = setTimeout(refreshState, STATUS_RETRY_MS);
  } else {
    state.checkTries = 0;
    statusTimer = setTimeout(refreshState, STATUS_IDLE_MS);
  }
}

function refreshStatusNow() {
  clearTimeout(statusTimer);
  statusTimer = null;
  state.checkTries = 0;
  refreshState();
}

if (typeof document !== 'undefined') {
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') refreshStatusNow();
    else clearTimeout(statusTimer);
  });
}

/* ---------------- CLI 异步任务观察（v9.22.1） ----------------
 * 后端 im_cli_setup / im_cli_login 已改成立即返回 async=True，真正干活在
 * 后台线程。这里在 busy 期间每 1.5s 拉一次状态，完成时 toast 结果并回调
 * onDone（用来恢复按钮）—— 一趟装 npm 包可能要好几分钟，所以上限放到
 * 200 次（≈5min），不能像旧版那样 60s 就撒手不管。
 */
let cliWatchTimer = null;

function startCliWatch(onDone) {
  clearTimeout(cliWatchTimer);
  let settled = false;
  const finish = () => {
    if (settled) return;
    settled = true;
    if (typeof onDone === 'function') {
      try { onDone(); } catch (e) { /* 恢复按钮失败不影响主流程 */ }
    }
  };
  const tick = async (n) => {
    await refreshState();
    const cli = state.cli || {};
    if (cli.busy && n < 200) {                 // 最多观察 ~5min
      cliWatchTimer = setTimeout(() => tick(n + 1), 1500);
      return;
    }
    if (!cli.busy && cli.message) toast(cli.message);
    finish();
  };
  cliWatchTimer = setTimeout(() => tick(0), 800);
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
  const ding = $('#dingStatus');
  ding.textContent = state.dingStatus || '状态未知，点「重新检测」';
  ding.title = '点击重新检测钉钉登录状态';
  ding.style.cursor = 'pointer';

  // ★ v9.22.1：CLI 状态单独展示。旧代码把它赋值成 state.dingStatus（复读），
  //   所以 CLI 那格永远跟着显示「检查中…」。
  const cli = state.cli || {};
  let cliText = cli.message || '';
  if (cli.busy && !cliText) cliText = '正在执行…';
  if (!cliText) cliText = '尚未检测';
  $('#cliStatus').textContent = cliText;
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
    state.cli = d.cli || {};
    renderMessages();
    renderSettings();
    renderTags();
    renderStatus();
  } catch (e) {
    console.error('[im] refreshState 失败', e);
  } finally {
    // 每次拉完都重新排下一次（检查中 1.5s / 已就绪 10s / 隐藏时暂停）
    scheduleStatusPoll();
  }
}

/* ---------------- 按钮「进行中」态 ---------------- */
async function withBusy(btn, label, fn) {
  if (!btn) return await fn();
  const oldHtml = btn.innerHTML;
  btn.disabled = true;
  btn.textContent = label;
  try {
    return await fn();
  } finally {
    btn.disabled = false;
    btn.innerHTML = oldHtml;
  }
}

/* ---------------- 事件接线 ---------------- */
function wire() {
  // ★ v9.21.2：返回对话（切回 chat SPA，后端 _open_page('chat') 切 stack index 0）
  const backBtn = $('#backBtn');
  if (backBtn) backBtn.addEventListener('click', () => rpc('nav_to', { key: 'chat' }));

  // 状态文字可点 → 立即重新检测（v9.22.1）
  const dingEl = $('#dingStatus');
  if (dingEl) dingEl.addEventListener('click', () => refreshStatusNow());

  // tab
  wireTabs();

  // 消息页操作
  $('#delBtn').addEventListener('click', async () => {
    if (state.selected.size === 0) return;
    await withBusy($('#delBtn'), '删除中…', async () => {
      await rpc('im_delete_msgs', { msgids: [...state.selected] });
      state.selected.clear();
      await refreshState();
    });
    toast('已删除');
  });
  $('#allBtn').addEventListener('click', () => {
    const btn = $('#allBtn');
    const oldHtml = btn.innerHTML;
    btn.disabled = true;
    btn.textContent = '已提交…';
    call('rpc', { method: 'im_process_all', args: {}, reqId: 'all' + Date.now() });
    toast('已提交全部消息给 AI');
    setTimeout(async () => {
      await refreshState();
      btn.disabled = false;
      btn.innerHTML = oldHtml;
    }, 1500);
  });

  // 启动/停止
  $('#toggleBtn').addEventListener('click', async () => {
    await withBusy($('#toggleBtn'), '处理中…', async () => {
      await rpc('im_toggle', { start: !state.running });
      await refreshState();
    });
  });

  // 白名单
  $('#wlAdd').addEventListener('click', async () => {
    const name = $('#wlInput').value.trim();
    if (!name) return;
    const kind = $('#wlKind').value === '单聊' ? 'p2p' : 'group';
    await withBusy($('#wlAdd'), '添加中…', async () => {
      await rpc('im_whitelist_add', { cid: name, name: name, kind });
      $('#wlInput').value = '';
      await refreshState();
    });
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
    await withBusy($('#saveBtn'), '保存中…', async () => {
      await rpc('im_set_settings', patch);
      await refreshState();
    });
    toast('设置已保存');
  });

  // 标签
  $('#scanBtn').addEventListener('click', () => {
    const btn = $('#scanBtn');
    const oldHtml = btn.innerHTML;
    btn.disabled = true;
    btn.textContent = '扫描中…';
    call('rpc', { method: 'im_scan_tags', args: {}, reqId: 'scan' + Date.now() });
    toast('已开始扫描打标签');
    setTimeout(async () => {
      await refreshState();
      btn.disabled = false;
      btn.innerHTML = oldHtml;
    }, 2000);
  });
  $('#tagSave').addEventListener('click', async () => {
    if (!state.selectedTagId) {
      toast('先在上方选一个会话');
      return;
    }
    const tag = $('#tagInput').value.trim();
    await withBusy($('#tagSave'), '保存中…', async () => {
      await rpc('im_set_tag', { thread_id: state.selectedTagId, tag });
      await refreshState();
    });
    toast('标签已保存');
  });

  // CLI 一键配置（后端 v9.22.1 改异步：立即返回 async=True，不再干等 120s）
  $('#cliSetupBtn').addEventListener('click', async () => {
    const btn = $('#cliSetupBtn');
    const restore = () => { btn.disabled = false; btn.textContent = '一键配置钉钉 CLI'; };
    state.cli = { busy: true, message: '正在配置…（可离开本页，完成后会提示）' };
    renderStatus();
    btn.disabled = true;
    btn.textContent = '配置中…';
    try {
      const d = await rpc('im_cli_setup', {});
      if (d && d.message) toast(d.message);
      if (d && d.ok === false && !d.async) {
        toast(d.error || '配置失败');
        restore();
        return;
      }
    } catch (e) {
      toast('CLI 配置失败：' + e.message);
      restore();
      return;
    }
    // 后端已受理：按钮保持「配置中…」，等真正跑完再由 startCliWatch 恢复
    startCliWatch(restore);
    await refreshState();
  });
  $('#cliLoginBtn').addEventListener('click', async () => {
    const btn = $('#cliLoginBtn');
    const restore = () => { btn.disabled = false; btn.textContent = '登录'; };
    state.cli = { busy: true, message: '请在弹出的窗口/浏览器完成授权…' };
    renderStatus();
    btn.disabled = true;
    btn.textContent = '等待授权…';
    try {
      const d = await rpc('im_cli_login', {});
      if (d && d.message) toast(d.message);
      if (d && d.ok === false && !d.async) {
        toast(d.error || '登录失败');
        restore();
        return;
      }
    } catch (e) {
      toast('登录失败：' + e.message);
      restore();
      return;
    }
    startCliWatch(restore);
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
