// im.js —— 钉钉助手页逻辑：消息双栏 + 三 tab + 设置 + 标签 + CLI 一键配置
// 依赖 bridge.js 的 rpc() 调后端方法；数据全来自后端 IMStore。
'use strict';

import { rpc, call, on, start } from './bridge.js';

const $ = (s) => document.querySelector(s);

let state = {
  rows: [],        // [{msgid,cid,name,kind,text,time,status,reply,sender}]
  settings: {},    // IMStore settings
  tags: [],        // [{id,name,kind,tag,tag_auto,msg_count}]
  whitelist: [],
  running: false,
  dingStatus: '',
  cli: {},         // {busy, ok, message, done_at} — 后端 CLI 异步任务状态（v9.22.1）
  scan: {},        // {busy,i,n,message} — 扫描打标签进度（v9.22.5）
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
 *   - 状态含「检查」：每 0.8s 重试，最多 30 次（约 24s），超限显示「状态未知」
 *   - 拿到真实状态：每 10s 刷新一次
 *   - 页面隐藏时降频（不彻底停，否则切回来之前永远不更新）
 *   - 点状态文字 = 立即重新检测
 *
 * ★ v9.22.5：旧代码在 checkTries 超限后直接 `return`，轮询链就此断掉 ——
 *   后端其实早就查好了，页面却一直挂着「状态未知」，除非用户手动点一下。
 *   现在超限后只降频到 15s 重试，保证最终一定能自愈。
 */
let statusTimer = null;
const STATUS_RETRY_MS = 800;
const STATUS_IDLE_MS = 10000;
const STATUS_MAX_TRIES = 30;

function statusIsChecking() {
  return /检查/.test(String(state.dingStatus || ''));
}

function scheduleStatusPoll() {
  clearTimeout(statusTimer);
  statusTimer = null;
  const hidden = document.visibilityState === 'hidden';
  if (statusIsChecking()) {
    state.checkTries = (state.checkTries || 0) + 1;
    if (state.checkTries > STATUS_MAX_TRIES) {
      state.dingStatus = '状态未知，点「重新检测」';
      renderStatus();
      // 不再「永久放弃」：慢速兜底重试，后端一旦查好就会自动恢复正常
      statusTimer = setTimeout(refreshState, 15000);
      return;
    }
    statusTimer = setTimeout(refreshState, hidden ? 3000 : STATUS_RETRY_MS);
    return;
  }
  state.checkTries = 0;
  statusTimer = setTimeout(refreshState, hidden ? 30000 : STATUS_IDLE_MS);
}

function refreshStatusNow() {
  clearTimeout(statusTimer);
  statusTimer = null;
  state.checkTries = 0;
  // ★ v9.22.4：force_ding 让后端绕过 60s 状态缓存 —— 否则刚登录成功 / 刚点
  //   「重新检测」，前端拿到的还是缓存里的旧串，看着像没反应。
  refreshState(true);
}

if (typeof document !== 'undefined') {
  document.addEventListener('visibilitychange', () => {
    // 回到前台：立刻刷一次并把节奏恢复正常
    if (document.visibilityState === 'visible') refreshStatusNow();
    // 切到后台：不彻底停（旧代码 clearTimeout 后就再也没有人排下一次了），
    // 改成重新排一个 30s 的慢速轮询，回来时数据是热的。
    else scheduleStatusPoll();
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
    if (cli.busy && n >= 200) {
      // 观察窗口用尽但任务还在跑（装 npm 包可能很慢）——明确告知，别让按钮
      // 无声无息地永久卡在「配置中…」。后台任务仍在继续，回来还能看到进度。
      toast('仍在后台执行，可稍后回到本页查看进度');
      finish();
      return;
    }
    if (!cli.busy && cli.message) toast(cli.message);
    // 收尾时强制绕缓存刷一次登录态：否则页面还挂着 60s 前的「未登录」
    if (!cli.busy) await refreshState(true);
    finish();
  };
  cliWatchTimer = setTimeout(() => tick(0), 800);
}

/* ---------------- 配置进度窗口（v9.22.2） ----------------
 * 用户原话：「配置的过程我也看不见，你不行再多弹出来一个窗口嘛，它在安装
 *           什么干什么东西进度条都显示出来啊」
 * 后端 _im_cli_state_data 现在推 steps / percent / log 三个字段，这里把它们
 * 画成一个模态小窗：进度条 + 四步状态 + 可展开的实时日志 + 用时计时器。
 * 关掉窗口不打断后台任务（busy 仍在跑），下次新的一次运行才会再自动弹。
 */
const CLI_STEP_ICON = { pending: '○', running: '◐', done: '✓', fail: '✕' };
let cliShownRun = 0;        // 已经为哪一次运行弹过窗（后端 started_at）
let cliElapsedTimer = null;

function fmtSec(s) {
  const m = Math.floor(s / 60);
  const r = s % 60;
  return m > 0 ? `${m} 分 ${r} 秒` : `${r} 秒`;
}

function tickCliElapsed() {
  const el = $('#cliElapsed');
  if (!el) return;
  const cli = state.cli || {};
  const st = Number(cli.started_at || 0);
  if (!st) { el.textContent = ''; return; }
  const end = cli.busy ? Date.now() / 1000 : (Number(cli.done_at || 0) || Date.now() / 1000);
  el.textContent = (cli.busy ? '已用 ' : '用时 ') + fmtSec(Math.max(0, Math.round(end - st)));
}

function openCliModal() {
  const m = $('#cliModal');
  if (!m || !m.hidden) return;
  m.hidden = false;
  clearInterval(cliElapsedTimer);
  tickCliElapsed();
  cliElapsedTimer = setInterval(tickCliElapsed, 1000);
}

function closeCliModal() {
  const m = $('#cliModal');
  if (m) m.hidden = true;
  clearInterval(cliElapsedTimer);
  cliElapsedTimer = null;
}

/* 把后端状态画到窗口上（refreshState / im_refresh 都会调） */
function renderCli(cli) {
  const modal = $('#cliModal');
  if (!modal) return;
  cli = cli || {};
  const busy = !!cli.busy;
  const runId = Number(cli.started_at || 0);
  const steps = cli.steps || [];
  // 从没跑过就别动（页面平时 10s 轮询一次，不能让它自己冒出来）。
  // 但 busy 时允许 steps 还没铺满 —— 否则刚点配置的那一两秒窗口是空白标题。
  if (!runId || (steps.length === 0 && !busy)) return;

  // 新的一次运行（started_at 变了）且正在跑 → 自动弹窗；用户关掉后不再骚扰
  if (busy && runId !== cliShownRun) {
    cliShownRun = runId;
    openCliModal();
  }

  const pct = Math.max(0, Math.min(100, Number(cli.percent || 0)));
  const bar = $('#cliBar');
  if (bar) {
    bar.classList.toggle('is-indet', busy && pct <= 0);
    bar.style.width = (busy && pct <= 0 ? 100 : Math.max(2, pct)) + '%';
    bar.classList.toggle('is-done', !busy && !!cli.ok);
    bar.classList.toggle('is-fail', !busy && cli.ok === false);
  }

  const dot = $('#cliDot');
  if (dot) dot.className = 'cli-dot ' + (busy ? 'on' : (cli.ok ? 'ok' : 'bad'));
  // 登录只有一步（key=auth），措辞要和「装 CLI」区分开 ——
  // 用户原话：「登录完成以后也没任何反应，没提示登录成功」。以前这里只会写
  // 「配置完成」，用户根本不知道自己登没登上。
  const isLogin = steps.length === 1 && steps[0] && steps[0].key === 'auth';
  const title = $('#cliTitle');
  if (title) {
    if (busy) title.textContent = isLogin ? '等待钉钉授权' : '正在配置钉钉 CLI';
    else if (cli.ok) title.textContent = isLogin ? '登录成功' : '配置完成';
    else title.textContent = isLogin ? '登录未完成' : '配置未完成';
  }
  const hint = $('#cliHint');
  if (hint) hint.textContent = cli.message || (busy ? '正在处理…' : '');

  // 四步状态
  const ul = $('#cliSteps');
  if (ul) {
    ul.innerHTML = '';
    steps.forEach((s) => {
      const st = s.status || 'pending';
      const li = document.createElement('li');
      li.className = 'cli-step is-' + st;
      const ic = document.createElement('span');
      ic.className = 'cli-step-ic';
      ic.textContent = CLI_STEP_ICON[st] || '○';
      const tx = document.createElement('span');
      tx.className = 'cli-step-tx';
      const lb = document.createElement('b');
      lb.textContent = s.label || s.key || '';
      tx.appendChild(lb);
      if (s.detail) {
        const dt = document.createElement('i');
        dt.textContent = String(s.detail).slice(0, 300);
        tx.appendChild(dt);
      }
      li.appendChild(ic);
      li.appendChild(tx);
      ul.appendChild(li);
    });
  }

  // 实时日志（用户看得到「到底在装什么」）
  const log = Array.isArray(cli.log) ? cli.log : [];
  const wrap = $('#cliLogWrap');
  const pre = $('#cliLog');
  if (wrap && pre) {
    if (log.length === 0) {
      wrap.hidden = true;
    } else {
      wrap.hidden = false;
      const atBottom = pre.scrollTop + pre.clientHeight >= pre.scrollHeight - 28;
      pre.textContent = log.join('\n');
      if (atBottom) pre.scrollTop = pre.scrollHeight;
    }
  }

  // 设备码 / 授权链接：登录时 dws 会打印，之前全丢了，用户不知道码是啥（v9.22.4）
  const code = String(cli.device_code || '').trim();
  const url = String(cli.auth_url || '').trim();
  const authBox = $('#cliAuthBox');
  if (authBox) authBox.hidden = !(code || url);
  const codeRow = $('#cliCodeRow');
  if (codeRow) codeRow.hidden = !code;
  const urlRow = $('#cliUrlRow');
  if (urlRow) urlRow.hidden = !url;
  const codeEl = $('#cliCode');
  if (codeEl && code) codeEl.textContent = code;
  const urlEl = $('#cliUrl');
  if (urlEl && url) {
    urlEl.textContent = url;
    urlEl.href = url;
  }
  const tip = $('#cliAuthTip');
  if (tip) {
    tip.textContent = code
      ? '把上面的设备码填进授权页（或直接点授权页确认），完成后本窗口会自动变成「登录成功」。'
      : '在浏览器里完成授权后，本窗口会自动变成「登录成功」。';
  }

  // 底部按钮：还没登录 → 给「去登录钉钉」；跑完 → 给「完成」
  const needLogin = !busy && steps.some((s) => s.key === 'auth' && s.status !== 'done');
  const go = $('#cliLoginGo');
  if (go) go.hidden = !needLogin;
  const done = $('#cliDone');
  if (done) done.hidden = busy;
  // 运行中可取消；失败可重试；缺 Node 给下载入口（v9.22.4）
  const cancel = $('#cliCancel');
  if (cancel) cancel.hidden = !busy;
  const retry = $('#cliRetry');
  if (retry) retry.hidden = busy || cli.ok !== false;
  const nodeBtn = $('#cliNode');
  if (nodeBtn) nodeBtn.hidden = !cli.need_node;

  tickCliElapsed();
}

/* 复制文本：QWebEngineView 里 navigator.clipboard 可能被拒，退回 execCommand */
async function copyText(text) {
  const s = String(text || '');
  if (!s) return false;
  try {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      await navigator.clipboard.writeText(s);
      return true;
    }
  } catch (e) { /* 继续走兜底 */ }
  try {
    const ta = document.createElement('textarea');
    ta.value = s;
    ta.style.position = 'fixed';
    ta.style.left = '-9999px';
    document.body.appendChild(ta);
    ta.select();
    const ok = document.execCommand('copy');
    ta.remove();
    return ok;
  } catch (e) {
    return false;
  }
}

const NODE_DOWNLOAD = 'https://nodejs.org/zh-cn/download';

function wireCliModal() {
  const m = $('#cliModal');
  if (!m) return;
  $('#cliClose').addEventListener('click', closeCliModal);
  $('#cliDone').addEventListener('click', closeCliModal);
  // 点遮罩空白处也能关（别让用户觉得窗口关不掉）
  // ★ v9.22.5：再加一层**事件委托**兜底 —— 万一某个按钮的单独绑定没生效，
  //   或者用户点中的是按钮内的 svg/文字节点，也一定能关掉。
  m.addEventListener('click', (e) => {
    const t = e.target;
    const hit = (t && t.closest) ? t.closest('#cliClose,#cliDone') : null;
    if (hit || t === m) closeCliModal();
  });
  // Esc 关闭（键盘用户）
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && !m.hidden) closeCliModal();
  });
  $('#cliLoginGo').addEventListener('click', () => {
    closeCliModal();
    const b = $('#cliLoginBtn');
    if (b && !b.disabled) b.click();
  });

  // ★ v9.22.4：取消 —— 以前点了配置只能干等（npm 卡住最长 7 分钟）
  const cancel = $('#cliCancel');
  if (cancel) {
    cancel.addEventListener('click', async () => {
      cancel.disabled = true;
      cancel.textContent = '取消中…';
      try {
        const d = await rpc('im_cli_cancel', {});
        toast((d && d.message) || '已取消');
      } catch (e) {
        toast('取消失败：' + e.message);
      } finally {
        cancel.disabled = false;
        cancel.textContent = '取消';
        await refreshState();
      }
    });
  }

  // 失败重试：授权步骤失败 → 重新登录；其它 → 重跑一键配置
  const retry = $('#cliRetry');
  if (retry) {
    retry.addEventListener('click', () => {
      const failed = (state.cli && state.cli.failed_step) || '';
      const btn = failed === 'auth' ? $('#cliLoginBtn') : $('#cliSetupBtn');
      closeCliModal();
      if (btn && !btn.disabled) btn.click();
    });
  }

  // 缺 Node.js → 打开官方下载页（网页里 target=_blank 会被吞，走后端开系统浏览器）
  const nodeBtn = $('#cliNode');
  if (nodeBtn) {
    nodeBtn.addEventListener('click', async () => {
      try {
        const d = await rpc('app.open_url', { url: NODE_DOWNLOAD });
        if (!d || !d.ok) toast('打开失败，请手动访问 ' + NODE_DOWNLOAD);
      } catch (e) {
        toast('打开失败，请手动访问 ' + NODE_DOWNLOAD);
      }
    });
  }

  const copyCode = $('#cliCopyCode');
  if (copyCode) {
    copyCode.addEventListener('click', async () => {
      const code = ($('#cliCode') && $('#cliCode').textContent) || '';
      const ok = await copyText(code);
      toast(ok ? '设备码已复制' : '复制失败，请手动选中复制');
    });
  }
  const copyUrl = $('#cliCopyUrl');
  if (copyUrl) {
    copyUrl.addEventListener('click', async () => {
      const url = ($('#cliUrl') && $('#cliUrl').textContent) || '';
      const ok = await copyText(url);
      toast(ok ? '链接已复制' : '复制失败，请手动选中复制');
    });
  }
  // 点链接直接开系统浏览器
  const urlEl = $('#cliUrl');
  if (urlEl) {
    urlEl.addEventListener('click', async (e) => {
      e.preventDefault();
      const url = urlEl.textContent || '';
      if (!url) return;
      try {
        const d = await rpc('app.open_url', { url });
        if (!d || !d.ok) toast('打开失败，请复制链接到浏览器');
      } catch (err) {
        toast('打开失败，请复制链接到浏览器');
      }
    });
  }
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
    // ★ v9.22.5：显示会话昵称（群名 / 对方昵称），绝不把 openConversationId
    //   当名字显示出来 —— 用户原话：「我不要的是会话的 ID，而是应该是这个的
    //   昵称，比如说哪个群啊或者谁呀之类的」。
    const nm = String(t.name || '').trim();
    const isRawId = !nm || nm === t.id || /^(cid|openConversationId)/i.test(nm);
    btn.querySelector('.tag-conv').textContent = isRawId ? '（未拿到昵称）' : nm;
    const v = btn.querySelector('.tag-value');
    const tagsArr = Array.isArray(t.tags) ? t.tags : [];
    // 没标签就明确写「未知」（后端判定证据不足时也会落一条空记录），
    // 用户选中后可以在下面手动补。
    v.textContent = tagsArr.length > 0 ? tagsArr.join('、') : '未知';
    if (tagsArr.length === 0) v.classList.add('is-empty');
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
  if (!cliText) cliText = '未自检，点「一键配置钉钉 CLI」';
  $('#cliStatus').textContent = cliText;
}

/* ---------------- 扫描打标签进度（v9.22.5） ----------------
 * 后端 _im_tag_scan 是后台 QThread，逐个会话调 AI，可能要几分钟。以前进度只
 * 回填 Qt 版钉钉页，Web 页的 #scanState 没人写 —— 用户点完按钮页面毫无变化，
 * 就以为「打标签不好使」。现在进度直接来自 im_get_state 的 scan 字段。
 */
let scanBtnHtml = null;

function renderScan() {
  const s = state.scan || {};
  const el = $('#scanState');
  if (el) {
    // ★ v9.22.5：有标签数据但本次会话还没扫过时，以前一直写「尚未扫描」，
    //   跟下面 94 行标签自相矛盾。改成把已有结果统计出来。
    let msg = s.message || (s.busy ? '正在扫描…' : '');
    if (!s.busy && (!msg || msg === '尚未扫描')) {
      const tags = state.tags || [];       // 后端给的是列表，不是字典
      if (tags.length) {
        const tagged = tags.filter((t) => (t.tags || []).length > 0).length;
        msg = `共 ${tags.length} 个会话，已打标签 ${tagged} 个` +
              (tagged ? '（上次扫描结果，可重新扫描）' : '');
      } else {
        msg = '尚未扫描';
      }
    }
    el.textContent = msg;
  }
  const btn = $('#scanBtn');
  if (!btn) return;
  if (scanBtnHtml === null) scanBtnHtml = btn.innerHTML;
  if (s.busy) {
    btn.disabled = true;
    btn.textContent = s.n ? `扫描中 ${s.i}/${s.n}…` : '扫描中…';
  } else {
    btn.disabled = false;
    if (btn.innerHTML !== scanBtnHtml) btn.innerHTML = scanBtnHtml;
  }
}

/* ---------------- 数据刷新 ---------------- */
let linkErrToasted = false;

/* 只在内容真的变了才重绘。以前每次轮询（0.8s/10s）都全量重建消息双栏 +
   设置表单 + 标签列表，配置窗口一弹出来叠加 im_refresh 推送就卡成幻灯片。 */
let sigRows = '';
let sigSettings = '';

/* 用户正在设置表单里打字时，别用轮询结果把他输入的内容冲掉 */
function settingsFocused() {
  const a = document.activeElement;
  if (!a || a === document.body) return false;
  const p = document.querySelector('.panel[data-panel="settings"]');
  return !!(p && p.contains(a));
}

async function refreshState(forceDing) {
  try {
    const d = await rpc('im_get_state', forceDing ? { force_ding: true } : {});
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
    state.scan = d.scan || {};
    linkErrToasted = false;

    const rowsSig = JSON.stringify([state.rows, state.tags, state.whitelist]);
    if (rowsSig !== sigRows) {
      sigRows = rowsSig;
      renderMessages();
      renderTags();
    }
    const setSig = JSON.stringify(state.settings);
    if (setSig !== sigSettings && !settingsFocused()) {
      sigSettings = setSig;
      renderSettings();
    }
    renderStatus();
    renderScan();
    renderCli(state.cli);      // ★ v9.22.2：配置进度窗口跟着状态一起刷
  } catch (e) {
    console.error('[im] refreshState 失败', e);
    // ★ v9.22.5：RPC 超时 / 桥断开时，界面以前会永远停在「正在配置…」的
    //   前端假状态（后端根本没收到），看着像卡死。这里把真实原因写进界面，
    //   并允许用户点状态文字重试。
    state.dingStatus = '连接后端失败，点这里重试';
    if (state.cli && state.cli.busy) {
      state.cli = { ok: false, message: '与后端通信失败：' + (e && e.message) };
    }
    try {
      renderStatus();
      renderCli(state.cli);
    } catch (_) { /* 渲染失败不影响轮询 */ }
    if (!linkErrToasted) {
      linkErrToasted = true;
      toast('与后端通信失败：' + ((e && e.message) || '未知错误'));
    }
  } finally {
    // 每次拉完都重新排下一次（检查中 0.8s / 已就绪 10s / 隐藏时降频）
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

  // 状态文字可点 → 立即重新检测（v9.22.1）；键盘回车/空格同样生效（v9.22.4）
  const dingEl = $('#dingStatus');
  if (dingEl) {
    dingEl.addEventListener('click', () => refreshStatusNow());
    dingEl.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        refreshStatusNow();
      }
    });
  }

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
  $('#allBtn').addEventListener('click', async () => {
    const btn = $('#allBtn');
    const oldHtml = btn.innerHTML;
    btn.disabled = true;
    btn.textContent = '已提交…';
    // ★ v9.22.4：改用 await rpc —— call() 不等结果，后端抛错前端完全不知情，
    //   按钮却在 1.5s 后无条件恢复，看着像「点了没反应」。
    try {
      await rpc('im_process_all', {});
      toast('已提交全部消息给 AI');
    } catch (e) {
      toast('提交失败：' + e.message);
    }
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
  $('#scanBtn').addEventListener('click', async () => {
    try {
      await rpc('im_scan_tags', {});
      toast('已开始扫描，进度显示在上方');
    } catch (e) {
      toast('扫描失败：' + e.message);
    }
    // 按钮的忙碌态与「扫描中 i/n」全部由 renderScan 跟随后端 scan 状态驱动，
    // 不再在这里手动改文案再定时恢复（以前 2 秒后无条件复位，看着像没开始就结束）。
    await refreshState();
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
        state.cli = { ok: false, message: d.error || '配置失败' };
        renderStatus(); renderCli(state.cli);
        restore();
        return;
      }
    } catch (e) {
      toast('CLI 配置失败：' + e.message);
      // 不能让界面停在假的「正在配置…」上（这正是用户看到「一直卡着」的来源）
      state.cli = { ok: false, message: '配置失败：' + e.message };
      renderStatus(); renderCli(state.cli);
      restore();
      return;
    }
    // 后端已受理：立刻把进度窗口弹出来（用户点完就能看见「在装什么」），
    // 后续 steps/percent/log 由 renderCli 一点点填进去。
    openCliModal();
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
        state.cli = { ok: false, message: d.error || '登录失败' };
        renderStatus(); renderCli(state.cli);
        restore();
        return;
      }
    } catch (e) {
      toast('登录失败：' + e.message);
      state.cli = { ok: false, message: '登录失败：' + e.message };
      renderStatus(); renderCli(state.cli);
      restore();
      return;
    }
    openCliModal();
    startCliWatch(restore);
    await refreshState();
  });
}

/* ---------------- 启动 ---------------- */
async function boot() {
  wire();
  wireCliModal();
  // 订阅后端主动推送的 IM 刷新事件（消息到达/状态变化时后端会推 im_refresh）。
  // 先注册再 start()，保证连上瞬间推来的事件也不会漏。
  // ★ v9.22.5：合并 150ms 内的多次推送 —— 装 CLI 时日志是成串来的，以前每个
  //   im_refresh 都触发一次全量拉取 + 重绘，界面直接卡住。
  let imRefreshTimer = null;
  on('im_refresh', () => {
    clearTimeout(imRefreshTimer);
    imRefreshTimer = setTimeout(() => {
      imRefreshTimer = null;
      refreshState();
    }, 150);
  });
  // 主题跟随主程序（以前 im 页收不到 init，只能用 prefers-color-scheme 猜）
  on('init', (p) => {
    if (p && p.theme) document.documentElement.dataset.theme = (p.theme === 'dark' ? 'dark' : 'light');
  });
  on('theme', (p) => {
    if (p && p.theme) document.documentElement.dataset.theme = (p.theme === 'dark' ? 'dark' : 'light');
  });
  // ★★★ v9.22.5 致命修复 —— 这一句是整个钉钉页能不能用的分水岭 ★★★
  //   im.html 是独立页面，有自己独立的 QWebChannel + 独立的 bridge 模块实例。
  //   bridge.start() 才会把后端的 webui_event 信号接到 dispatch() 上，
  //   rpc_result 也才能被派发到 rpc() 的 Promise。
  //   v9.21.2「全 Web 化」重写时漏了这一行，于是：上行 rpc 能到后端干活，
  //   下行结果永远收不到 → 每个 rpc 都在 30 秒后抛「RPC 超时」。
  //   用户看到的就是：状态一直检测→状态未知、一键配置卡住最后超时、
  //   浏览器里登录成功了页面毫无反应。
  //   实测：补上后 im_get_state 从 30000ms 超时变成 5ms 返回。
  try {
    await start();
  } catch (e) {
    console.error('[im] 桥接初始化失败', e);
  }
  await refreshState();
}

boot();
