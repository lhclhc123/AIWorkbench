// views/chat.js —— 对话流渲染：消息、流式、工具胶囊、思维链、输入区
// 只依赖 store / bridge / components，不碰传输层。
'use strict';

import { getState, patch, subscribe } from '../store.js';
import { on, call } from '../bridge.js';
import { switchTo } from '../nav.js';
import '../components/msg.js';
import '../components/tool-capsule.js';
import '../components/think-card.js';

/* ---------------- DOM 引用 ---------------- */
let threadWrap, thread, welcome, input, composer, sendBtn,
    stateDot, stateText, convList, modelBtn, modelLabel, modelMenu,
    attachmentsEl, fileBtn, micBtn;

/* ---------------- 流式运行态 ---------------- */
let streamMsg = null;   // 正在流式输出的 <msg-bubble>
let streamThink = null; // 正在流式的 <think-card>
let currentTool = null; // 当前工具胶囊
// 本轮流式开始前，thread 里已有的消息节点数（不含 welcome）。
// 收尾时靠它切出「本轮新增」的区间，从而只重建新增消息，不碰历史。
let streamBaseCount = 0;


/* ---------------- 工具函数 ---------------- */
const $ = (s) => document.querySelector(s);

function toast(msg) {
  const el = $('#toast');
  el.textContent = msg;
  el.classList.add('show');
  clearTimeout(toast._t);
  toast._t = setTimeout(() => el.classList.remove('show'), 1900);
}

function nowLabel() {
  const d = new Date();
  const p = (n) => (n < 10 ? '0' + n : '' + n);
  return p(d.getHours()) + ':' + p(d.getMinutes());
}

function scrollBottom(force) {
  const near = threadWrap.scrollHeight - threadWrap.scrollTop - threadWrap.clientHeight < 140;
  if (force || near) threadWrap.scrollTop = threadWrap.scrollHeight;
}

function hideWelcome() {
  if (welcome) welcome.hidden = true;
}

function setComposerBusy(on) {
  composer.classList.toggle('is-busy', on);
  sendBtn.title = on ? '停止生成' : '发送';
  stateDot.classList.toggle('is-busy', on);
  stateText.textContent = on ? '正在生成回复…' : getState().phase || '就绪';
  syncSend();
}

function syncSend() {
  const st = getState();
  sendBtn.disabled = !st.streaming && input.value.trim() === '';
}

function autosize() {
  input.style.height = 'auto';
  input.style.height = Math.min(input.scrollHeight, 132) + 'px';
}

function selectedModelLabel() {
  const st = getState();
  if (!st.models.length) return st.selectedModel || '模型';
  const cur = st.models.find((m) => m.choice === st.selectedModel);
  return cur ? cur.label : (st.selectedModel || st.models[0].label);
}

/* ---------------- 极小 Markdown 渲染器（标题/列表/加粗/代码块/表格） ---------------- */
function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

function inlineMd(s) {
  let out = escapeHtml(s);
  out = out.replace(/`([^`]+)`/g, (m, c) => `<code>${c}</code>`);
  out = out.replace(/\*\*([^*]+)\*\*/g, '<b>$1</b>');
  return out;
}

function splitRow(row) {
  return row.trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map((s) => s.trim());
}

function renderMarkdown(text) {
  if (!text) return '';
  const lines = String(text).replace(/\r\n/g, '\n').split('\n');
  const html = [];
  let list = null;
  const flushList = () => {
    if (list) { html.push(`<${list.type}>` + list.items.map((it) => `<li>${it}</li>`).join('') + `</${list.type}>`); list = null; }
  };
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    const t = line.trim();
    if (!t) { flushList(); i++; continue; }

    const fence = t.match(/^```(\S*)/);
    if (fence) {
      flushList();
      const lang = fence[1];
      const code = [];
      i++;
      while (i < lines.length && !/^```\s*$/.test(lines[i].trim())) { code.push(lines[i]); i++; }
      i++;
      const langLabel = lang ? `<span class="code-lang">${escapeHtml(lang)}</span>` : '';
      html.push(`<pre>${langLabel}<code>${escapeHtml(code.join('\n'))}</code></pre>`);
      continue;
    }

    const h = t.match(/^(#{1,3})\s+(.*)$/);
    if (h) { flushList(); const lv = h[1].length; html.push(`<h${lv}>${inlineMd(h[2])}</h${lv}>`); i++; continue; }

    if (/^\|.*\|$/.test(t) && i + 1 < lines.length && /^\|[\s\-:|]+\|$/.test(lines[i + 1].trim())) {
      flushList();
      const head = splitRow(t);
      const rows = [];
      i += 2;
      while (i < lines.length && /^\|.*\|$/.test(lines[i].trim())) { rows.push(splitRow(lines[i].trim())); i++; }
      html.push('<table><thead><tr>' + head.map((c) => `<th>${inlineMd(c)}</th>`).join('') + '</tr></thead><tbody>' +
        rows.map((r) => '<tr>' + r.map((c) => `<td>${inlineMd(c)}</td>`).join('') + '</tr>').join('') + '</tbody></table>');
      continue;
    }

    if (/^[-*]\s+/.test(t)) {
      if (!list || list.type !== 'ul') { flushList(); list = { type: 'ul', items: [] }; }
      list.items.push(inlineMd(t.replace(/^[-*]\s+/, '')));
      i++; continue;
    }
    if (/^\d+[.)]\s+/.test(t)) {
      if (!list || list.type !== 'ol') { flushList(); list = { type: 'ol', items: [] }; }
      list.items.push(inlineMd(t.replace(/^\d+[.)]\s+/, '')));
      i++; continue;
    }

    flushList();
    const para = [t];
    i++;
    while (i < lines.length && lines[i].trim() && !/^(#{1,3}\s|```|[-*]\s|\d+[.)]\s|\|.*\||>\s)/.test(lines[i])) {
      para.push(lines[i].trim()); i++;
    }
    html.push('<p>' + para.map(inlineMd).join('<br>') + '</p>');
  }
  flushList();
  return html.join('');
}

/* ---------------- 渲染：消息列表 ---------------- */
function buildMessage(m) {
  const role = m.role || 'assistant';
  const meta = m.meta || {};
  if (role === 'user') {
    const el = document.createElement('msg-bubble');
    el.role = 'user';
    el.text = m.text || '';
    el.time = meta.time || '';
    return el;
  }
  if (role === 'think') {
    const el = document.createElement('think-card');
    el.open = false;
    el.text = m.text || '';
    return el;
  }
  if (role === 'tool') {
    const el = document.createElement('tool-capsule');
    el.name = meta.name || '工具';
    el.result = m.text || '';
    el.setRunning(false);
    return el;
  }
  const el = document.createElement('msg-bubble');
  el.role = 'assistant';
  el.time = meta.time || '';
  el.meta = meta.label || '';
  el.html = renderMarkdown(m.text || '');
  el.showActions();
  return el;
}

function renderMessages(messages) {
  const msgs = messages || [];
  const frag = document.createDocumentFragment();
  for (const m of msgs) frag.appendChild(buildMessage(m));
  // 保留欢迎态节点（它在 thread 内部），只重建消息本身
  if (welcome && welcome.parentNode === thread) thread.removeChild(welcome);
  thread.innerHTML = '';
  if (welcome) {
    welcome.hidden = msgs.length > 0;
    thread.appendChild(welcome);
  }
  thread.appendChild(frag);
  scrollBottom(true);
}

/* ---------------- 渲染：会话列表 / 模型 ---------------- */
function renderConvList() {
  const st = getState();
  convList.innerHTML = '';
  if (!st.convs.length) {
    const empty = document.createElement('div');
    empty.className = 'conv-group';
    empty.textContent = '暂无对话';
    convList.appendChild(empty);
    return;
  }
  for (const c of st.convs) {
    const btn = document.createElement('button');
    btn.className = 'conv' + (c.id === st.currentConvId ? ' is-active' : '');
    btn.innerHTML =
      `<span class="conv-title"></span><span class="conv-preview"></span>`;
    btn.querySelector('.conv-title').textContent = c.title || '未命名对话';
    btn.querySelector('.conv-preview').textContent = c.preview || '';
    btn.addEventListener('click', () => {
      // 一步直达：从任意页面（设置/技能…）点左侧对话项，都先切回对话页。
      // 不 await —— 切页是同步展示，避免阻塞下面的对话切换。
      switchTo('chat');
      convList.querySelectorAll('.conv').forEach((x) => x.classList.remove('is-active'));
      btn.classList.add('is-active');
      patch({ currentConvId: c.id });
      call('select_conv', { convId: c.id });
    });
    convList.appendChild(btn);
  }
}

function renderModelMenu() {
  const st = getState();
  modelLabel.textContent = selectedModelLabel();
  modelMenu.innerHTML = '';
  if (!st.models.length) { modelMenu.hidden = true; return; }
  for (const m of st.models) {
    const opt = document.createElement('button');
    opt.className = 'model-opt' + (m.choice === st.selectedModel ? ' is-active' : '');
    opt.innerHTML =
      `<span>${escapeHtml(m.label)}</span>` +
      (m.choice === st.selectedModel ? '<span class="tick"><svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="m3.4 8.4 3 3 6.2-6.2"/></svg></span>' : '');
    opt.addEventListener('click', () => {
      patch({ selectedModel: m.choice });
      modelMenu.hidden = true;
      renderModelMenu();
      call('set_model', { choice: m.choice });
    });
    modelMenu.appendChild(opt);
  }
}

/* ---------------- 渲染：附件条 ---------------- */
function renderAttachments() {
  const files = getState().attachments;
  attachmentsEl.hidden = !files.length;
  attachmentsEl.innerHTML = '';
  for (const f of files) {
    const chip = document.createElement('span');
    chip.className = 'att-chip';
    chip.innerHTML =
      `<span class="file-ico"><svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round"><path d="M4 2.4h4.6L12 5.8v7.8H4z"/><path d="M8.6 2.4v3.4H12"/></svg></span>` +
      `<span class="att-name"></span>` +
      `<button class="rm" title="移除"><svg width="10" height="10" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"><path d="M3.6 3.6l8.8 8.8M12.4 3.6l-8.8 8.8"/></svg></button>`;
    chip.querySelector('.att-name').textContent = f.name;
    chip.querySelector('.rm').addEventListener('click', () => {
      const list = getState().attachments.filter((x) => x !== f);
      patch({ attachments: list });
      renderAttachments();
    });
    attachmentsEl.appendChild(chip);
  }
}

/* ---------------- 流式生命周期 ---------------- */

// thread 里当前的消息节点数（welcome 不算消息）
function countMessageNodes() {
  return Array.from(thread.children).filter((el) => el.id !== 'welcome').length;
}

function beginStream() {
  // ★ 必须在 streamMsg 入列之前记：这里记的是「本轮开始前」的节点数，
  //   finalizeStream 靠它切出「本轮新增」的区间。
  streamBaseCount = countMessageNodes();
  patch({ streaming: true, busy: true, phase: '' });
  setComposerBusy(true);

  streamMsg = document.createElement('msg-bubble');
  streamMsg.role = 'assistant';
  streamMsg.time = nowLabel();
  streamMsg.meta = selectedModelLabel() + ' · 生成中…';
  // 注入渲染器：流式中「已写完的段落」即时变成最终 markdown（粗体/列表/代码块），
  // 收尾时的排版跳动就只剩最后一段。组件本身不依赖这个函数，不注入即退化为纯文本。
  streamMsg.setMarkdownFn(renderMarkdown);
  streamMsg.setTyping(true);
  thread.appendChild(streamMsg);
  streamThink = null;
  currentTool = null;
  hideWelcome();
  scrollBottom(true);
}

function ensureThink() {
  if (streamThink) return streamThink;
  streamThink = document.createElement('think-card');
  streamThink.open = true;
  if (streamMsg) streamMsg.insertBeforeBody(streamThink);
  return streamThink;
}

/**
 * 收尾。传入 messages 时做「增量重建」：
 *   只删掉本轮新增的临时节点、只重建本轮新增的消息，历史消息一律不碰。
 * 传空参（如 on('error')）时只复位状态，不动 DOM。
 * @param {Array<object>=} messages 后端 finished 事件带回来的完整消息列表
 */
function finalizeStream(messages) {
  const liveMsg = streamMsg;   // 先留住引用：下面会被置空
  patch({ streaming: false, busy: false });
  setComposerBusy(false);
  streamMsg = null;
  streamThink = null;
  currentTool = null;

  if (!Array.isArray(messages) || messages.length <= streamBaseCount) return;

  // 安全阀：如果流式气泡已经不在 thread 里（例如生成期间切过对话，
  // 已被 on('messages') 全量重建），就别按旧的 streamBaseCount 去删节点，
  // 否则会误删新会话的历史消息。
  if (!liveMsg || liveMsg.parentNode !== thread) {
    if (welcome) welcome.hidden = messages.length > 0;
    return;
  }

  const keep = streamBaseCount;
  const kids = Array.from(thread.children).filter((el) => el.id !== 'welcome');
  // 只删「本轮流式期间产生」的节点（第 keep 个之后的）
  for (let i = keep; i < kids.length; i++) kids[i].remove();

  // 只重建本轮新增的消息；历史消息的 DOM 与其 markdown 渲染结果原样保留
  const frag = document.createDocumentFragment();
  for (const m of messages.slice(keep)) frag.appendChild(buildMessage(m));
  thread.appendChild(frag);

  if (welcome) welcome.hidden = messages.length > 0;
  scrollBottom(true);
}

function send() {
  const st = getState();
  const text = input.value.trim();
  if (!text || st.streaming) return;

  // 乐观追加用户气泡
  const user = document.createElement('msg-bubble');
  user.role = 'user';
  user.text = text;
  user.time = nowLabel();
  thread.appendChild(user);

  input.value = '';
  autosize();
  beginStream();
  scrollBottom(true);
  call('send', { text });
}

/* ---------------- 事件接线 ---------------- */
function wireEvents() {
  input.addEventListener('input', () => { autosize(); syncSend(); });
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
      e.preventDefault();
      if (!getState().streaming) send();
    }
  });

  sendBtn.addEventListener('click', () => {
    if (getState().streaming) {

      call('stop');
      return;
    }
    send();
  });

  fileBtn.addEventListener('click', () => call('pick_file'));
  micBtn.addEventListener('click', () => toast('语音输入暂未接入'));

  modelBtn.addEventListener('click', (e) => {
    e.stopPropagation();
    modelMenu.hidden = !modelMenu.hidden;
  });
  document.addEventListener('click', (e) => {
    if (!e.target.closest('.model-wrap')) modelMenu.hidden = true;
  });

  // 底部操作（复制 / 重答）
  threadWrap.addEventListener('click', (e) => {
    const act = e.target.closest('.act');
    if (!act) return;
    if (act.dataset.act === 'regenerate') { call('regenerate'); return; }
    if (act.dataset.act === 'copy') {
      const msg = act.closest('msg-bubble');
      const text = msg ? (msg._body ? msg._body.innerText : '') : '';
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(() => toast('已复制')).catch(() => toast('复制失败'));
      } else {
        toast('复制不可用');
      }
    }
  });
}

/* ---------------- 下行事件 ---------------- */
function bindBridge() {
  on('init', (p) => {
    if (p && p.theme) patch({ theme: p.theme });
    patch({
      convs: (p && p.convs) || [],
      currentConvId: (p && p.currentConvId) || null,
      models: (p && p.models) || [],
      selectedModel: (p && p.selectedModel) || '',
    });
    renderConvList();
    renderModelMenu();
  });

  on('messages', (p) => {
    if (!p) return;
    const msgs = p.messages || [];
    patch({ messages: msgs, currentConvId: p.convId || getState().currentConvId });
    renderMessages(msgs);
  });

  on('token', (p) => {
    if (!p || !streamMsg) return;
    streamMsg.appendText(p.delta || '');
    scrollBottom(false);
  });

  on('reason', (p) => {
    if (!p) return;
    const think = ensureThink();
    think.appendText(p.delta || '');
    scrollBottom(false);
  });

  on('tool_start', (p) => {
    if (!p) return;
    const cap = document.createElement('tool-capsule');
    cap.name = (p.text || '执行工具').split(/[（(]/)[0];
    cap.arg = p.text || '';
    cap.setRunning(true);
    if (streamMsg) streamMsg.insertBeforeBody(cap);
    currentTool = cap;
    scrollBottom(false);
  });

  on('tool_result', (p) => {
    if (!p) return;
    if (currentTool) {
      currentTool.setRunning(false);
      currentTool.result = p.text || '';
      currentTool = null;
    }
    scrollBottom(false);
  });

  on('phase', (p) => {
    if (!p) return;
    const text = p.detail || p.kind || '';
    patch({ phase: text });
    stateText.textContent = text || (getState().streaming ? '正在生成回复…' : '就绪');
  });

  on('finished', (p) => {
    // 增量收尾：不再 renderMessages 全量重建（那会清空整个消息流并重解析
    // 所有历史消息的 markdown，对话一长就卡）。
    const msgs = (p && p.messages) || null;
    if (msgs) patch({ messages: msgs });
    finalizeStream(msgs);
  });

  on('error', (p) => {
    const msg = (p && p.message) || '发生错误';
    toast(msg);
    stateText.textContent = msg;
    finalizeStream();
  });

  on('attachments', (p) => {
    patch({ attachments: (p && p.files) || [] });
    renderAttachments();
  });

  on('theme', (p) => {
    if (p && p.theme) patch({ theme: p.theme });
  });
}

/* ---------------- 启动 ---------------- */
export function initChat() {
  threadWrap = $('#threadWrap');
  thread = $('#thread');
  welcome = $('#welcome');
  input = $('#input');
  composer = $('#composer');
  sendBtn = $('#sendBtn');
  stateDot = $('#stateDot');
  stateText = $('#stateText');
  convList = $('#convList');
  modelBtn = $('#modelBtn');
  modelLabel = $('#modelLabel');
  modelMenu = $('#modelMenu');
  attachmentsEl = $('#attachments');
  fileBtn = $('#fileBtn');
  micBtn = $('#micBtn');

  bindBridge();
  wireEvents();
  autosize();
  syncSend();
  renderConvList();
  renderModelMenu();
  renderAttachments();
}
