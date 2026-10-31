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
// 收尾时靠它切出「本轮新增」的**DOM 区间**（只删临时节点，不碰历史）。
let streamBaseCount = 0;
// 本轮流式开始前，state.messages 里的**消息条数**。
// ★ v9.22.1：历史消息会被语义分组（多条消息合成一个组容器），所以 DOM 节点数
//   不再等于消息条数；收尾重建必须按**消息下标**切，否则会从历史中间开始重建。
let streamBaseMsgCount = 0;
// 收束动画时长覆盖（探针用 0 让它同步完成；正常运行留空走默认/减弱动画）。
let collapseMsOverride = null;


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
  out = out.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
  // 斜体：单星号（放在粗体之后，避免把已转成 <strong> 的内容再匹配到）
  out = out.replace(/(^|[^*])\*([^*\n]+)\*/g, '$1<em>$2</em>');
  return out;
}

function splitRow(row) {
  return row.trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map((s) => s.trim());
}

export function renderMarkdown(text) {
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

/* ---------------- 过程消息语义分组（v9.22.1） ----------------
 * 用户：「过程消息是挨个一条条像现在这样生成完后最后有个动画一样的再合并成过程
 *       消息，只留结论，而且也不一定都要合并成一个过程消息，可以多分几组比如
 *       "查找text.cpp"、"了解程序运行逻辑"等简洁合理，中间也可以有小结论」
 *
 * 分组规则（只在**收尾/重建**时执行，流式期间仍逐条显示）：
 *   · 相邻且同类（同一工具类别）归一组，切换类别开新组；
 *   · think 是「中性」步骤：并入当前组，不单独断组（3 工具 + 1 think 仍是 1 组）；
 *   · 单组最多 PROC_MAX_STEPS 条，超了断新组；
 *   · 组标题取第一件工具的**实际参数对象**，说人话（查找 text.cpp / 修改 main.py）。
 * 折叠态只显示组标题 + 小结论；展开才看全部步骤。
 */
const PROC_MAX_STEPS = 8;

// 工具类别 → 「人话」前缀 + 对象取参规则
const PROC_CATEGORIES = {
  find:  { verb: '查找', tool: ['find_lines', 'search_files'], keys: ['pattern', 'query', 'text', 'q'] },
  read:  { verb: '了解', tool: ['read_file', 'read_document', 'list_dir'], keys: ['path', 'file', 'dir', 'directory', 'name'], base: true },
  write: { verb: '修改', tool: ['write_file', 'edit_lines', 'apply_patch', 'create_document'], keys: ['path', 'file', 'name'], base: true },
  run:   { verb: '运行', tool: ['run_command', 'run_python', 'compile_run', 'build_exe'], keys: ['command', 'cmd', 'code'], clip: 12 },
  web:   { verb: '联网查', tool: ['web_search', 'web_visit', 'web_scrape', 'fetch_url'], keys: ['query', 'q', 'url'], clip: 16 },
  ding:  { verb: '钉钉', tool: null, prefix: 'dingtalk', keys: ['action', 'op', 'to', 'title'], clip: 16 },
  think: { verb: '思考', tool: null },
};
const PROC_TOOL_CAT = {};
for (const [cat, def] of Object.entries(PROC_CATEGORIES)) {
  for (const t of (def.tool || [])) PROC_TOOL_CAT[t] = cat;
}

function isProcRole(m) { return !!m && (m.role === 'think' || m.role === 'tool'); }

function toolNameOf(m) {
  const meta = (m && m.meta) || {};
  let n = meta.name || meta.tool || meta.toolName || '';
  // 兜底：流式 tool_start 的文本是「工具名（参数）」；历史消息可能把它塞进 raw
  if (!n && meta.raw) n = String(meta.raw).split(/[（(]/)[0].trim();
  return String(n || '');
}

function toolArgsOf(m) {
  const meta = (m && m.meta) || {};
  let a = meta.args != null ? meta.args : (meta.arguments != null ? meta.arguments : meta.arg);
  if (a == null && meta.raw) {
    const mm = String(meta.raw).match(/[（(]([\s\S]*)[）)]\s*$/);
    if (mm) a = mm[1];
  }
  if (a == null) return '';
  if (typeof a === 'string') {
    const s = a.trim();
    if (s.charAt(0) === '{' || s.charAt(0) === '[') {
      try { return JSON.parse(s); } catch (e) { return s; }
    }
    return s;
  }
  return (typeof a === 'object') ? a : String(a);
}

function pickArg(args, keys) {
  if (args == null) return '';
  if (typeof args === 'string') return args;
  for (const k of (keys || [])) {
    const v = args[k];
    if (v != null && v !== '') return v;
  }
  return '';
}

function basenameOf(p) {
  const s = String(p || '').replace(/[\\/]+$/, '');
  const parts = s.split(/[\\/]/);
  return parts[parts.length - 1] || s;
}

function categoryOf(m) {
  if (!m) return 'other';
  if (m.role === 'think') return 'think';
  if (m.role !== 'tool') return 'other';
  const name = toolNameOf(m);
  if (PROC_TOOL_CAT[name]) return PROC_TOOL_CAT[name];
  if (/^dingtalk/i.test(name)) return 'ding';
  return 'other';
}

function groupObject(cat, m) {
  const def = PROC_CATEGORIES[cat] || {};
  let obj = pickArg(toolArgsOf(m), def.keys);
  if (def.base && obj) obj = basenameOf(obj);
  if (typeof obj === 'string') {
    obj = obj.replace(/\s+/g, ' ').trim();
    if (def.clip) obj = obj.slice(0, def.clip);
  }
  if (!obj && cat === 'other') obj = toolNameOf(m) || '';
  return obj ? String(obj) : '';
}

function groupTitle(cat, firstItem) {
  if (cat === 'think') return PROC_CATEGORIES.think.verb;   // 「思考」
  const def = PROC_CATEGORIES[cat] || {};
  if (!def.verb) {                                          // 认不出的工具：用工具名，别用 id 唬人
    const nm = toolNameOf(firstItem);
    return nm || '工具';
  }
  const obj = groupObject(cat, firstItem);
  return def.verb + (obj ? ' ' + obj : '');
}

function thinkLastLine(m) {
  const t = (m && m.text) || '';
  if (!t) return '';
  const lines = String(t).replace(/\r\n/g, '\n').split('\n')
    .map((s) => s.trim()).filter(Boolean);
  return lines.length ? lines[lines.length - 1] : '';
}

// 组的小结论：组内最后一条带 label 的工具、或最后一句 think 文本（取最靠后的那个）
function groupSummary(items) {
  for (let i = items.length - 1; i >= 0; i--) {
    const m = items[i];
    const meta = (m && m.meta) || {};
    if (meta.label) return String(meta.label);
    if (m && m.role === 'think') {
      const line = thinkLastLine(m);
      if (line) return line;
    }
  }
  return '';
}

/**
 * 把消息列表编排成「渲染节点序列」：
 *   {type:'msg', m}                       单条用户/助手消息
 *   {type:'group', cat, title, items:[]}  一组过程消息
 * 纯函数、无副作用 —— renderMessages 与 finalizeStream 都走它，两条路径结构必然一致。
 */
function planNodes(messages) {
  const out = [];
  let cur = null;
  for (const m of (messages || [])) {
    if (!isProcRole(m)) { cur = null; out.push({ type: 'msg', m }); continue; }
    const cat = categoryOf(m);
    if (m.role === 'think') {
      // think 中性：有当前组就并入（作为过程/小结论），没有就开「思考」组
      if (cur && cur.items.length < PROC_MAX_STEPS) {
        cur.items.push(m);
      } else {
        cur = { type: 'group', cat: 'think', title: groupTitle('think', m), items: [m] };
        out.push(cur);
      }
      continue;
    }
    // 工具：同类并入；当前组若还是「纯思考组」，则被这件工具收编（改成本工具类别）
    if (cur && cur.cat === 'think' && cur.items.every((x) => x.role === 'think')
        && cur.items.length < PROC_MAX_STEPS) {
      cur.cat = cat;
      cur.title = groupTitle(cat, m);
      cur.items.push(m);
    } else if (cur && cur.cat === cat && cur.items.length < PROC_MAX_STEPS) {
      cur.items.push(m);
    } else {
      cur = { type: 'group', cat, title: groupTitle(cat, m), items: [m] };
      out.push(cur);
    }
  }
  return out;
}

// 把一条组内消息转成 think-card 能吃的步骤描述
function toProcStep(m) {
  if (m && m.role === 'think') return { kind: 'think', text: m.text || '' };
  const meta = (m && m.meta) || {};
  return {
    kind: 'tool',
    name: toolNameOf(m) || meta.name || '工具',
    arg: groupObject(categoryOf(m), m),
    result: (m && m.text) || '',
  };
}

function buildGroup(g) {
  const el = document.createElement('think-card');
  el.setAttribute('mode', 'process');
  el.mode = 'process';
  el.setMarkdownFn(renderMarkdown);
  el.title = g.title;
  el.meta = '· ' + g.items.length + ' 步';
  el.open = false;
  el.setProcess(g.items.map(toProcStep), groupSummary(g.items));
  return el;
}

function buildNode(node) {
  if (node && node.type === 'group') return buildGroup(node);
  return buildMessage(node ? node.m : node);
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
    // 兜底：正常路径里 think 都进组（见 planNodes）；单独调用也保证 markdown 生效
    const el = document.createElement('think-card');
    el.open = false;
    el.setMarkdownFn(renderMarkdown);
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
  for (const node of planNodes(msgs)) frag.appendChild(buildNode(node));
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

// 测试钩子：把消息列表渲染到一个**已入文档**的隐藏暂存区，返回各过程组的外层 HTML。
// 必须在文档里（connectedCallback 才会触发自定义元素内部构建），否则 outerHTML 是空壳。
function groupsHtml(messages) {
  const stage = document.createElement('div');
  stage.style.cssText = 'position:absolute;left:-99999px;top:0;width:1px;height:1px;overflow:hidden;';
  document.body.appendChild(stage);
  const frag = document.createDocumentFragment();
  for (const node of planNodes(messages || [])) frag.appendChild(buildNode(node));
  stage.appendChild(frag);
  const out = Array.from(stage.querySelectorAll('think-card.mode-process'))
    .map((el) => el.outerHTML);
  stage.remove();
  return out;
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
  //   finalizeStream 靠它切出本轮新增的**DOM** 区间。
  streamBaseCount = countMessageNodes();
  // ★ v9.22.1：同时记「本轮开始前的消息条数」。历史消息被语义分组后，
  //   DOM 节点数 ≠ 消息条数，收尾重建必须按消息下标切（见 finalizeStream）。
  streamBaseMsgCount = ((getState().messages) || []).length;
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
  streamThink.setMarkdownFn(renderMarkdown);
  if (streamMsg) streamMsg.insertBeforeBody(streamThink);
  return streamThink;
}

// 收束动画时长：默认 240ms；探针可用 __AWB_CHAT_TEST__.setCollapseMs(0) 覆盖；
// 用户开了「减少动态效果」则直接跳过动画。
function collapseMs() {
  if (collapseMsOverride != null) return collapseMsOverride;
  try {
    if (window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches) return 0;
  } catch (e) { /* matchMedia 不支持就当没设置 */ }
  return 240;
}

/**
 * 收尾。传入 messages 时做「增量重建」：
 *   1) 先给本轮的过程消息播一个收束动画（淡出 + 高度收拢，约 240ms）；
 *   2) 动画结束后删掉临时节点，按**语义分组**重建本轮新增的消息；
 *   3) 历史消息的 DOM 一律不碰。
 * 传空参（如 on('error')）时只复位状态，不动 DOM。
 * @param {Array<object>=} messages 后端 finished 事件带回来的完整消息列表
 * @returns {Promise<void>} 收尾（含动画）完成后 resolve —— 探针可 await 它拿稳定结构
 */
function finalizeStream(messages) {
  const liveMsg = streamMsg;   // 先留住引用：下面会被置空
  patch({ streaming: false, busy: false });
  setComposerBusy(false);
  streamMsg = null;
  streamThink = null;
  currentTool = null;

  if (!Array.isArray(messages)) return Promise.resolve();

  // 安全阀：如果流式气泡已经不在 thread 里（例如生成期间切过对话，
  // 已被 on('messages') 全量重建），就别按旧的 streamBaseCount 去删节点，
  // 否则会误删新会话的历史消息。
  if (!liveMsg || liveMsg.parentNode !== thread) {
    if (welcome) welcome.hidden = messages.length > 0;
    return Promise.resolve();
  }

  const keepDom = streamBaseCount;
  // 本轮新增的**消息**区间：跳过已乐观插入 DOM 的那条 user 消息。
  let start = streamBaseMsgCount;
  if (messages[start] && messages[start].role === 'user') start += 1;
  const fresh = messages.slice(start);

  const rebuild = () => {
    const kids = Array.from(thread.children).filter((el) => el.id !== 'welcome');
    // 只删「本轮流式期间产生」的节点（第 keepDom 个之后的，本质就是 streamMsg）
    for (let i = keepDom; i < kids.length; i++) {
      if (kids[i].parentNode) kids[i].remove();
    }
    // 只重建本轮新增的消息；历史消息的 DOM 与其 markdown 渲染结果原样保留
    const frag = document.createDocumentFragment();
    for (const node of planNodes(fresh)) frag.appendChild(buildNode(node));
    thread.appendChild(frag);
    if (welcome) welcome.hidden = messages.length > 0;
    scrollBottom(true);
  };

  const animated = liveMsg.querySelectorAll('think-card, tool-capsule');
  const ms = collapseMs();
  if (ms > 0 && animated.length) {
    animated.forEach((n) => n.classList.add('proc-collapse'));
    void thread.offsetHeight;   // 强制回流，让下面的终态过渡真的跑起来
    animated.forEach((n) => n.classList.add('proc-collapse-run'));
    return new Promise((resolve) => {
      setTimeout(() => { rebuild(); resolve(); }, ms);
    });
  }
  rebuild();
  return Promise.resolve();
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

/* ---------------- 测试钩子（只给探针用，产品逻辑不依赖它） ----------------
 * tests/probe_process_group_v9221.py 通过它做两件事：
 *   1) 用同一套 planNodes/buildNode 把消息渲染到隐藏暂存区，取过程组的 outerHTML；
 *   2) 把收束动画时长设为 0，让 send_finished 收尾在事件回调里同步完成。
 * 只暴露纯渲染函数，不暴露流式状态机，避免探针绕过真实链路去造假。
 */
if (typeof window !== 'undefined') {
  window.__AWB_CHAT_TEST__ = {
    renderMessages,
    groupsHtml,
    planNodes,
    buildNode,
    renderMarkdown,
    setCollapseMs: (ms) => { collapseMsOverride = (ms == null ? null : Number(ms)); },
  };
}
