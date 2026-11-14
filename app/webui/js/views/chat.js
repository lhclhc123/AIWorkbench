// views/chat.js —— 对话流渲染：消息、流式、工具胶囊、思维链、输入区
// 只依赖 store / bridge / components，不碰传输层。
'use strict';

import { getState, patch, subscribe } from '../store.js';
import { on, call, rpc } from '../bridge.js';
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

/* ---------------- 语音 / 朗读 运行态 ---------------- */
// 当前正在朗读的 SpeechSynthesisUtterance；非空表示朗读进行中（再点按钮即停止）。
let currentUtterance = null;
// 正在朗读的那条消息上的「朗读」按钮，便于停止时还原文案 / 高亮。
let currentReadBtn = null;
// 语音状态占位符：记录 input 默认占位符，语音状态变化时临时覆盖，状态结束后还原。
let voicePlaceholderDefault = '';
/* ★ v9.22.8 语音整段替换语义
 * 用户反馈（以及后端契约）：voice_partial / voice_transcript 推的都是
 * **当前整段识别结果**，不是增量。老代码 `input.value += p.text` 会重复叠加。
 * 所以录音开始时把输入框原文记进 voiceBaseValue，之后一律
 *   input.value = voiceBaseValue + 最新整段文本
 * final 到达后清空基准值（下一次录音重新取当时的输入框内容）。
 */
let voiceBaseValue = '';     // 录音开始时的输入框原文（null 表示未开始录音）
let voiceSession = false;    // 是否处于一次录音会话中

/* ---------------- 思考计时（v9.22.8） ----------------
 * 「已思考 12 秒」里的秒数只能在前端测：后端 reason 事件不带时长。
 * 流式期间从第一次 reason 开始计时，思考结束时把秒数记进 liveThinkSecs，
 * 收尾重建时按顺序回填给对应那条 think 消息（历史消息没有计时就用「已思考」）。
 */
let thinkStartAt = 0;
let liveThinkSecs = [];


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

// 语音状态占位符：有状态文本就临时覆盖输入框占位符，空或停止类词则还原默认文案。
function setVoicePlaceholder(msg) {
  const m = String(msg || '');
  const stopped = !m || /(停止|stop|关闭|off|idle|就绪|完成|停止录音|已停止)/i.test(m);
  input.placeholder = stopped ? voicePlaceholderDefault : m;
}

/* ---------------- 语音：录音会话 / 整段替换（v9.22.8） ---------------- */

/**
 * 对话页此刻是否是「用户正看着的那一页」。
 * 语音是**全局唯一**的一条录音链路（后端只有一个 VoiceInput），所以识别结果
 * 只会推到对话页的 bridge。如果用户其实是在钉钉页点「身份背景」旁的麦克风，
 * 这一条就必须拒收，否则文字会跑进对话页输入框、把用户草稿写脏。
 * @returns {boolean}
 */
function chatPageActive() {
  try {
    if (document.visibilityState === 'hidden') return false;   // 整个 webview 被切走
    const sec = document.querySelector('#page-chat');
    if (sec && sec.hidden) return false;                       // 页内切到了别的 tab
    return true;
  } catch (e) {
    return true;    // 判断不了就按「是本页」处理，绝不因此让语音失效
  }
}

/**
 * 判断一条 voice_status 文案是否表示「正在录音」。
 * 后端推的是中文自然语言（如「正在录音…」「识别中…」「已停止」），
 * 这里只在**明确是开始类**词时返回 true，拿不准一律当 idle —— 宁可少标红，
 * 也不要让麦克风永远红着。
 * @param {string} msg 状态文案
 * @returns {boolean}
 */
function voiceIsRecording(msg) {
  const s = String(msg || '');
  if (!s) return false;
  // ★ v9.22.8 修：后端的**开始**文案里本身就带「结束」两个字 ——
  //   「正在录音…中间停顿约 10 秒才会自动结束」。老正则把「结束」列进停止词，
  //   于是录音一开始就被判定成"已停止"，麦克风的红色录音态当场被撤掉
  //   （用户看到的就是「麦克风根本没在动，像是坏了」）。
  //   这里只认**真正的停止语**；「识别中」也算会话进行中 —— 基准值绝不能
  //   在 final 到达前丢掉，否则最终整段会和已经写进输入框的 partial 叠成两遍。
  if (/(已停止|停止录音|录音结束|已结束|语音输入已关闭|就绪|空闲|失败|出错|超时|^\s*(stop|idle)\s*$)/i.test(s)) {
    return false;
  }
  return /(录音|聆听|说话|recording|识别|正在听)/i.test(s);
}

/** 明确的「已经停下来了」文案（只有这种才允许收尾录音会话）。 */
function voiceIsStopped(msg) {
  const s = String(msg || '');
  if (!s) return false;
  return /(已停止|停止录音|录音结束|语音输入已关闭|就绪|空闲|^\s*(stop|idle)\s*$)/i.test(s);
}

// 开始一次录音会话：记下输入框原文 + 把麦克风标红。
function beginVoiceSession() {
  if (voiceSession) return;
  voiceSession = true;
  voiceBaseValue = input.value;
  if (micBtn) {
    micBtn.classList.add('is-rec');
    micBtn.setAttribute('aria-pressed', 'true');
    micBtn.title = '正在录音：再点一下结束';
  }
}

// 结束录音会话：清基准值 + 取消标红（已经写进输入框的文本保留）。
function endVoiceSession() {
  voiceSession = false;
  voiceBaseValue = '';
  if (micBtn) {
    micBtn.classList.remove('is-rec');
    micBtn.setAttribute('aria-pressed', 'false');
    micBtn.title = '语音输入：点一下开始说话，再点一下结束';
  }
}

/**
 * 把一次「整段识别结果」写进输入框。
 * ★ 整段替换，不是追加 —— 后端推的 partial/final 都是当前这一整段的结果。
 * @param {string} text 整段识别文本
 */
function applyVoiceText(text) {
  if (!voiceSession) beginVoiceSession();       // 事件先到（status 后到）时的兜底
  const base = voiceBaseValue || '';
  input.value = base + String(text == null ? '' : text);
  autosize();
  syncSend();
  try {
    const n = input.value.length;
    input.setSelectionRange(n, n);              // 光标放到末尾
  } catch (e) { /* textarea 未挂焦点时个别环境会抛，忽略 */ }
}

/* ---------------- 思考计时（v9.22.8） ---------------- */
function thinkNow() {
  try {
    if (window.performance && typeof window.performance.now === 'function') {
      return window.performance.now();
    }
  } catch (e) { /* 忽略 */ }
  return Date.now();
}

// 开始给「当前这一轮思考」计时（重复调用不会重置）。
function startThinkTimer() {
  if (!thinkStartAt) thinkStartAt = thinkNow();
}

/**
 * 结束当前思考计时，把秒数记进 liveThinkSecs。
 * @returns {number} 本轮思考秒数（至少 1）；没有在计时返回 0
 */
function stopThinkTimer() {
  if (!thinkStartAt) return 0;
  const sec = Math.max(1, Math.round((thinkNow() - thinkStartAt) / 1000));
  thinkStartAt = 0;
  liveThinkSecs.push(sec);
  return sec;
}

/**
 * 当前流式思考卡收尾：写标题（「已思考 N 秒」）并收起。
 * 工具开始 / 正文开始 / 本轮结束都会调它。
 */
function settleStreamThink() {
  const card = streamThink;
  const sec = stopThinkTimer();
  if (!card) return;
  if (sec > 0) card.title = '已思考 ' + sec + ' 秒';
  card.open = false;
  streamThink = null;
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

/* ---------------- 过程消息语义分组（v9.22.2） ----------------
 * 用户：「过程消息是挨个一条条像现在这样生成完后最后有个动画一样的再合并成过程
 *       消息，只留结论，而且也不一定都要合并成一个过程消息，可以多分几组比如
 *       "查找text.cpp"、"了解程序运行逻辑"等简洁合理，中间也可以有小结论」
 * 用户（v9.22.2）：「深度思考为什么把好几轮的深度思考都放到一块儿了？不要都混到
 *       一块儿，一个是一个，每个都是单独的一小块儿」
 *
 * 分组规则（只在**收尾/重建**时执行，流式期间仍逐条显示）：
 *   · 相邻且**同类工具**归一组，切换类别开新组；
 *   · ★ think 绝不进组、也绝不被组吞：它自己打断当前过程组，独立渲染成一张
 *     <think-card>（一个是一个，互不污染）；
 *   · 单组最多 PROC_MAX_STEPS 条，超了断新组；
 *   · 组标题取第一件工具的**实际参数对象**，说人话（查找 text.cpp / 修改 main.py）。
 * 折叠态只显示组标题 + 小结论；展开才看全部步骤。
 */
const PROC_MAX_STEPS = 8;

// 工具类别 → 「人话」前缀 + 对象取参规则
// 注意：这里**没有** think —— 思考不是过程步骤，是独立的卡片（见 planNodes）。
const PROC_CATEGORIES = {
  find:  { verb: '查找', tool: ['find_lines', 'search_files'], keys: ['pattern', 'query', 'text', 'q'] },
  read:  { verb: '了解', tool: ['read_file', 'read_document', 'list_dir'], keys: ['path', 'file', 'dir', 'directory', 'name'], base: true },
  write: { verb: '修改', tool: ['write_file', 'edit_lines', 'apply_patch', 'create_document'], keys: ['path', 'file', 'name'], base: true },
  run:   { verb: '运行', tool: ['run_command', 'run_python', 'compile_run', 'build_exe'], keys: ['command', 'cmd', 'code'], clip: 12 },
  web:   { verb: '联网查', tool: ['web_search', 'web_visit', 'web_scrape', 'fetch_url'], keys: ['query', 'q', 'url'], clip: 16 },
  ding:  { verb: '钉钉', tool: null, prefix: 'dingtalk', keys: ['action', 'op', 'to', 'title'], clip: 16 },
};
const PROC_TOOL_CAT = {};
for (const [cat, def] of Object.entries(PROC_CATEGORIES)) {
  for (const t of (def.tool || [])) PROC_TOOL_CAT[t] = cat;
}

// 过程角色（think / tool）：它们都不单独成消息，而是收进紧随其后的助手消息内部。
// ★ v9.22.8 起 planNodes 用 role 直接判断，这个谓词留给外部/后续复用。
function isProcRole(m) { return !!m && (m.role === 'think' || m.role === 'tool'); }


// JSON 字符串能解析就解析成对象，解析不了原样返回（参数摘要要按 key=value 压一行）
function normaliseArgs(a) {
  if (typeof a === 'string') {
    const s = a.trim();
    if (s.charAt(0) === '{' || s.charAt(0) === '[') {
      try { return JSON.parse(s); } catch (e) { return s; }
    }
  }
  return a;
}

/**
 * 从工具结果 / 回喂文本里抠工具名（最后一层兜底）。
 * 认得的真实格式：
 *   · agent.tool_result_feedback()  → 「（系统已执行工具 find_lines，以下是…」
 *   · 跳过类消息                     → 「[跳过] find_lines 这个调用已连续失败 2 次…」
 *   · 流式 tool_start 的描述         → 「find_lines（text.cpp）」
 * @param {string} text
 * @returns {string} 工具名；认不出返回 ''
 */
function nameFromText(text) {
  const s = String(text == null ? '' : text);
  let hit = s.match(/（系统已执行工具\s*([A-Za-z][A-Za-z0-9_]*)/);
  if (hit) return hit[1];
  hit = s.match(/\[(?:成功|失败|跳过)\]\s*([A-Za-z][A-Za-z0-9_]*)\s/);
  if (hit) return hit[1];
  hit = s.match(/^\s*([A-Za-z][A-Za-z0-9_]*)\s*[（(]/);
  if (hit) return hit[1];
  return '';
}

function toolNameOf(m) {
  if (!m) return '';
  const meta = m.meta || {};
  // meta.name 优先；其次是后端 out_messages 里顶层的 name（chat_worker 补的）
  let n = meta.name || meta.tool || meta.toolName || m.name || '';
  // 兜底：流式 tool_start 的文本是「工具名（参数）」；历史消息可能把它塞进 raw
  if (!n && meta.raw) n = String(meta.raw).split(/[（(]/)[0].trim();
  // 最后一层：从结果文本里抠（老历史消息没有 name 字段，别退化成「工具」两个字）
  if (!n) n = nameFromText(m.text);
  return String(n || '');
}

function toolArgsOf(m) {
  if (!m) return '';
  const meta = m.meta || {};
  let a = meta.args != null ? meta.args : (meta.arguments != null ? meta.arguments : meta.arg);
  if (a == null) a = m.args;
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

/**
 * 把一次调用的参数压成**一行**（工具胶囊 / 过程步骤的副标签）。
 * @param {*} raw 参数：对象 / JSON 字符串 / 普通字符串
 * @param {number=} max 最大长度（默认 60）
 * @returns {string} 一行参数摘要，放不下就截断加省略号
 */
function compressArgs(raw, max) {
  const limit = (max == null) ? 60 : Number(max);
  const a = normaliseArgs(raw);
  let s = '';
  if (a && typeof a === 'object') {
    const parts = [];
    for (const k of Object.keys(a)) {
      const v = a[k];
      if (v == null || v === '') continue;
      let sv = (typeof v === 'string') ? v : JSON.stringify(v);
      sv = String(sv == null ? '' : sv).replace(/\s+/g, ' ').trim();
      if (sv.length > 40) sv = sv.slice(0, 39) + '…';
      parts.push(k + '=' + sv);
    }
    s = parts.join(' ');
  } else if (a != null) {
    s = String(a);
  }
  s = s.replace(/\s+/g, ' ').trim();
  if (limit > 1 && s.length > limit) s = s.slice(0, limit - 1) + '…';
  return s;
}

/**
 * 把流式 tool_start 的描述拆成「工具名 + 参数」。
 * chat_worker 的格式是 `f"{name}  {json}"`（两个空格），历史格式是「name（args）」。
 * @param {string} desc
 * @returns {{name: string, arg: string}}
 */
function splitToolDesc(desc) {
  const s = String(desc == null ? '' : desc).trim();
  if (!s) return { name: '', arg: '' };
  let hit = s.match(/^([A-Za-z][A-Za-z0-9_]*)\s*[（(]([\s\S]*)[）)]\s*$/);
  if (hit) return { name: hit[1], arg: hit[2] };
  hit = s.match(/^([A-Za-z][A-Za-z0-9_]*)\s+([\s\S]*)$/);
  if (hit) return { name: hit[1], arg: hit[2] };
  return { name: s.split(/[（(]/)[0].trim(), arg: '' };
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
  // think 不是过程步骤：它不参与分组，planNodes 里直接单独成块
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
  const def = PROC_CATEGORIES[cat] || {};
  if (!def.verb) {                                          // 认不出的工具：用工具名，别用 id 唬人
    const nm = toolNameOf(firstItem);
    return nm || '工具';
  }
  const obj = groupObject(cat, firstItem);
  return def.verb + (obj ? ' ' + obj : '');
}

// 工具结果的第一行：折叠态「小结论」的素材（只在本组内部取，绝不跨轮）
//
// ★ v9.22.10：这里以前直接取 `lines[0]`，于是 `run_command` 的结果
//   「命令：`dir /b`」变成折叠态那一行 —— 用户看到的原文是
//   「调用的命令怎么就跑到对话中的小结论中了？」。
//   命令回显 / 退出码 / 输出标题都是**过程元数据**，不是结论，必须跳过，
//   往后找第一句真正有信息量的话；一句都没有就留空（只显示组标题）。
const META_HEAD_RE = /^(命令|退出码|超时上限|输出|执行命令|已执行命令|解释器|脚本路径|工作目录)[\s:：]/;
const CMD_LIKE_RE = /^(?:\$|>|PS>|cmd(?:\.exe)?\b|powershell\b|python(?:\.exe)?\b|pip\b|npm\b|npx\b|git\b|node\b|dir\b|cd\b|copy\b|move\b|del\b|taskkill\b)/i;

function resultFirstLine(m) {
  const t = (m && m.text) || '';
  if (!t) return '';
  const head = String(t).slice(0, 400);
  // run_command 的结果：首行固定是「命令：`xxx`」，中间是「退出码：0」。
  // ★ 成功的命令**没有「结论」可言** —— 后面第一行输出往往只是文件列表的第一项
  //   （dir /b → A.txt），显示出来同样是噪音。失败时才有价值（那里是报错那行）。
  if (/(^|\n)\s*(?:\[(?:成功|失败|跳过)\])?命令[：:]\s*`/.test(head)
      && /退出码[：:]\s*0\b/.test(head)) {
    return '';
  }
  const lines = String(t).replace(/\r\n/g, '\n').split('\n')
    .map((s) => s.trim()).filter(Boolean);
  if (!lines.length) return '';
  for (const raw of lines.slice(0, 8)) {
    let s = raw.replace(/^\[(?:成功|失败|跳过)\]\s*/, '').replace(/\s+/g, ' ').trim();
    if (!s) continue;
    if (s.startsWith('```')) continue;          // 代码块围栏
    if (META_HEAD_RE.test(s)) continue;         // 命令 / 退出码 / 输出标题
    if (CMD_LIKE_RE.test(s)) continue;          // 裸命令行
    return s.length > 60 ? s.slice(0, 59) + '…' : s;
  }
  return '';
}

/**
 * 组的小结论（折叠态那一行）：只从**本组自己的步骤**里取 ——
 * 先看有没有显式 label，没有就取最后一条工具结果的第一句。
 * ★ 绝不再从 think 取（think 已经独立成卡片，跨轮混在一起正是用户骂的那个 bug）。
 */
function groupSummary(items) {
  for (let i = items.length - 1; i >= 0; i--) {
    const meta = (items[i] && items[i].meta) || {};
    if (meta.label) return String(meta.label);
  }
  for (let i = items.length - 1; i >= 0; i--) {
    const line = resultFirstLine(items[i]);
    if (line) return line;
  }
  return '';
}

/**
 * 把消息列表编排成「渲染节点序列」：
 *   {type:'msg', m}             单条消息（用户 / 助手）
 *   {type:'msg', m, procs:[]}   助手消息 + 收在这条消息**内部**的过程块
 *   {type:'group', cat, title, items:[]}  一组过程消息（只在 procs 里出现，不单独成节点）
 * 纯函数、无副作用 —— renderMessages 与 finalizeStream 都走它，两条路径结构必然一致。
 *
 * ★ v9.22.8 形态（用户要求，照 WorkBuddy）：
 *   用户原话：「「深度思考」现在独立成了一个对话块，WorkBuddy 里不是这样 —— 思考
 *              应该收在同一条助手消息内部，以一行很轻的灰字（如「已思考 12 秒」）
 *              形式出现，点开才展开正文」。
 *   所以：自上一次普通消息以来累积的 think / tool 过程块**整体附着到下一条助手消息**
 *   上，在消息内部按原始顺序排开（think → 工具组 → think → 工具组 …），正文排在最后。
 *   如果这串过程后面没有助手正文（用户中途停止生成 / 老数据只留了过程），就造一条
 *   **空正文的助手消息**来承载 —— 视觉上仍然是一条 AI 消息里的一行「已思考」，
 *   而不是消息流里独立的一块。
 *
 * 分组规则不变：相邻且同类工具归一组，切换类别开新组，单组最多 PROC_MAX_STEPS 条。
 * think 依旧**不进组、也不被组吞**（v9.22.2 用户明确要求「一个是一个」），
 * 只是位置从「顶层节点」变成「消息内部的插槽块」。
 *
 * @param {Array<object>} messages 消息列表
 * @param {{thinkSeconds?: number[]}=} opts thinkSeconds: 流式期间量到的每轮思考秒数
 * @returns {Array<object>} 渲染节点序列
 */
function planNodes(messages, opts) {
  const secs = (opts && Array.isArray(opts.thinkSeconds)) ? opts.thinkSeconds : null;
  let secCursor = 0;      // secs 的消费下标（一个 think 消费一个）
  const out = [];
  let procs = [];         // 待附着的内部过程块（think / group），按出现顺序
  let group = null;       // 正在累积的工具组

  // 思考秒数：消息自带就用消息的（后端将来补时长即可无缝生效），否则回填流式量到的
  const resolveSecs = (m) => {
    const meta = (m && m.meta) || {};
    const raw = [meta.seconds, meta.duration, meta.elapsed, meta.thinkSec,
                 (m && m.seconds)].find(
      (v) => typeof v === 'number' && isFinite(v) && v > 0);
    let sec = raw ? Math.round(Number(raw)) : 0;
    if (!sec && secs) {
      const guess = Number(secs[secCursor]);
      if (isFinite(guess) && guess > 0) sec = Math.round(guess);
    }
    secCursor += 1;
    return sec;
  };

  // 把累积的过程块挂到一条消息上；没有可挂的消息就造一条空正文助手消息承载
  const flush = (m) => {
    out.push({
      type: 'msg',
      m: m || { role: 'assistant', text: '', meta: {}, virtual: true },
      procs: procs,
    });
    procs = [];
    group = null;
  };

  for (const m of (messages || [])) {
    if (!m) continue;
    if (m.role === 'think') {
      group = null;                                   // think 打断工具组，但不独立成节点
      procs.push({ type: 'think', m, seconds: resolveSecs(m) });
      continue;
    }
    if (m.role === 'tool') {
      const cat = categoryOf(m);
      if (group && group.cat === cat && group.items.length < PROC_MAX_STEPS) {
        group.items.push(m);
      } else {
        group = { type: 'group', cat, title: groupTitle(cat, m), items: [m] };
        procs.push(group);
      }
      continue;
    }
    if (m.role === 'assistant' && procs.length) {
      flush(m);                                       // 整段过程收进这条助手消息
      continue;
    }
    if (procs.length) flush();                        // 过程后跟的是 user：造空助手消息承载
    group = null;
    out.push({ type: 'msg', m });
  }
  if (procs.length) flush();
  return out;
}

// 把一条**工具**消息转成 think-card(process) 能吃的步骤描述。
// ★ 组里已经没有 think 了（think 独立成卡），所以这里只需要 tool 一种 kind；
//   即使真有怪东西掉进来，think-card 也会走它的 else 分支当纯文本渲染，不会报错。
function toProcStep(m) {
  const cat = categoryOf(m);
  // 认得出的类别先用「人话对象」（text.cpp），认不出才退回完整参数摘要
  const obj = (cat === 'other') ? '' : groupObject(cat, m);
  return {
    kind: 'tool',
    name: toolNameOf(m) || '工具',
    arg: obj || compressArgs(toolArgsOf(m), 60),
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
  if (!node) return buildMessage(null);
  if (node.type === 'group') return buildGroup(node);
  return buildMessage(node.m, node.procs);
}

/**
 * 内联思考条的标题文案：「已思考 12 秒」/ 秒数未知时只写「已思考」。
 * @param {number} seconds 秒数
 * @returns {string}
 */
function thinkTitle(seconds) {
  const s = Number(seconds);
  return (isFinite(s) && s > 0) ? ('已思考 ' + Math.round(s) + ' 秒') : '已思考';
}

/**
 * 造一张**内联**思考条（v9.22.8）：挂在助手消息内部的 .think-slot 里，
 * 折叠态只有一行浅灰小字，点开在这条消息内部展开正文（样式见 .mode-inline）。
 * @param {object} m think 消息
 * @param {number} seconds 该轮思考秒数（0/未知 → 标题省略秒数）
 * @returns {HTMLElement} <think-card> 元素
 */
function buildThinkCard(m, seconds) {
  const el = document.createElement('think-card');
  el.inline = true;
  el.open = false;
  el.title = thinkTitle(seconds);
  el.meta = '';
  el.setMarkdownFn(renderMarkdown);
  el.text = (m && m.text) || '';
  return el;
}

/* ---------------- 渲染：消息列表 ---------------- */
/**
 * 造一条消息节点。
 * ★ v9.22.8：助手消息会带上 procs（思考 / 工具过程组），按顺序挂进它内部的
 *   .think-slot —— 思考不再是消息流里独立的一块。
 * @param {object} m 消息
 * @param {Array<object>=} procs 内部过程块（think / group）
 * @returns {HTMLElement} <msg-bubble> / <think-card> / <tool-capsule>
 */
function buildMessage(m, procs) {
  const msg = m || {};
  const role = msg.role || 'assistant';
  const meta = msg.meta || {};
  if (role === 'user') {
    const el = document.createElement('msg-bubble');
    el.role = 'user';
    el.text = msg.text || '';
    el.time = meta.time || '';
    return el;
  }
  if (role === 'think') {
    // 兜底：没有可附着的助手消息时（外部直接 buildNode 一条 think），
    // 仍然渲染成内联思考条，保证它长得和消息内部那一行完全一样。
    return buildThinkCard(msg, 0);
  }
  if (role === 'tool') {
    // ★ 工具胶囊必须显示**真实工具名 + 参数摘要**，绝不能只剩「工具」两个字
    const el = document.createElement('tool-capsule');
    el.name = toolNameOf(msg) || '工具';
    el.arg = compressArgs(toolArgsOf(msg), 60);
    el.result = msg.text || '';
    el.setRunning(false);
    return el;
  }
  const el = document.createElement('msg-bubble');
  el.role = 'assistant';
  el.time = meta.time || '';
  el.meta = meta.label || '';
  el.html = renderMarkdown(msg.text || '');
  // ★ 过程块挂进同一条消息内部的插槽，顺序 = 消息顺序（think → 工具组 → think …）
  for (const p of (procs || [])) {
    if (!p) continue;
    if (p.type === 'think') el.appendProc(buildThinkCard(p.m, p.seconds));
    else if (p.type === 'group') el.appendProc(buildGroup(p));
  }
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
    // ★ v9.22.4：外层不能再用 <button>：里面还要放导出/删除两个 <button>，
    //   嵌套 button 是非法 HTML，浏览器可能提前闭合外层，导致 querySelector
    //   拿不到 .conv-del、整个会话列表渲染中断。改用 div[role=button]。
    const btn = document.createElement('div');
    btn.className = 'conv' + (c.id === st.currentConvId ? ' is-active' : '');
    btn.setAttribute('role', 'button');
    btn.tabIndex = 0;
    btn.innerHTML =
      `<span class="conv-main"><span class="conv-title"></span><span class="conv-preview"></span></span>` +
      `<button class="conv-export" type="button" title="导出对话" aria-label="导出对话">` +
      `<svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">` +
      `<path d="M8 2.2v7M5 6.4 8 9.4l3-3M3.2 12.6h9.6"/></svg>` +
      `</button>` +
      `<button class="conv-del" type="button" title="删除对话" aria-label="删除对话">` +
      `<svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">` +
      `<path d="M2.5 4h11M6 4V2.8c0-.4.3-.8.8-.8h2.4c.4 0 .8.4.8.8V4M12.5 4l-.6 9.2c0 .5-.4 1-1 1H5.1c-.6 0-1-.5-1-1L3.5 4M6.5 7v4M9.5 7v4"/></svg>` +
      `</button>`;
    btn.querySelector('.conv-title').textContent = c.title || '未命名对话';
    btn.querySelector('.conv-preview').textContent = c.preview || '';
    const del = btn.querySelector('.conv-del');
    del.addEventListener('click', (e) => {
      e.stopPropagation();
      const title = c.title || '未命名对话';
      if (window.confirm(`确定删除对话「${title}」吗？此操作不可恢复。`)) {
        call('delete_conv', { convId: c.id });
      }
    });
    const exp = btn.querySelector('.conv-export');
    exp.addEventListener('click', (e) => {
      e.stopPropagation();
      call('export_conv', { convId: c.id });
      toast('已导出，正在打开文件夹…');
    });
    btn.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        btn.click();
      }
    });
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
  // ★ v9.22.8：新一轮 → 思考计时/秒数清零（收尾重建时按顺序回填「已思考 N 秒」）
  thinkStartAt = 0;
  liveThinkSecs = [];
  hideWelcome();
  scrollBottom(true);
}

/**
 * 取（必要时新建）当前这一轮的流式思考条。
 * ★ v9.22.8：思考条直接挂在流式助手消息内部（insertBeforeBody），
 *   所以生成过程中看到的形态 = 收尾后的形态（不再是独立的一块）。
 * @returns {HTMLElement} <think-card>
 */
function ensureThink() {
  if (streamThink) return streamThink;
  streamThink = document.createElement('think-card');
  streamThink.inline = true;
  streamThink.title = '正在思考…';
  streamThink.meta = '';
  streamThink.open = true;                 // 思考中默认展开，让用户看到在动
  streamThink.setMarkdownFn(renderMarkdown);
  if (streamMsg) streamMsg.insertBeforeBody(streamThink);
  startThinkTimer();
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
  // ★ v9.22.8：如果整轮结束时思考条还开着（没有正文 / 没有工具），先结算它的秒数，
  //   否则「已思考 N 秒」会缺最后一轮的时长。
  settleStreamThink();
  stopThinkTimer();            // 兜底：没有思考卡但计时还开着（异常路径）时也要归零
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
    // ★ v9.22.8：顺便把流式期间量到的每轮思考秒数按顺序回填（「已思考 12 秒」）
    const frag = document.createDocumentFragment();
    for (const node of planNodes(fresh, { thinkSeconds: liveThinkSecs })) {
      frag.appendChild(buildNode(node));
    }
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
  // 语音输入：切换录音。状态/转写由后端 push 事件驱动（见 bindBridge 的 on(...)）。
  // ★ v9.22.8：rpc 返回的 status 是 'recording' | 'recognizing' | 'idle'，
  //   用它立刻切换麦克风的红色呼吸态（事件可能稍后才到，手感不能等）。
  micBtn.addEventListener('click', async () => {
    try {
      const d = await rpc('voice.toggle');
      if (d && d.ok === false) {
        toast('语音不可用：' + (d.error || '未知原因'));
        return;
      }
      const st = String((d && d.status) || '');
      if (st === 'recording') beginVoiceSession();
      else if (st === 'idle' || st === 'recognizing') {
        // 只结束「录音中」的视觉态；识别中仍然算一次会话（结果还没回来）
        if (st === 'idle') endVoiceSession();
        micBtn.classList.remove('is-rec');
      }
    } catch (e) {
      toast('语音切换失败：' + e.message);
    }
  });

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
    // 朗读：浏览器内置 Web Speech API 朗读助手消息；再点一次停止。
    if (act.dataset.act === 'read') {
      const msg = act.closest('msg-bubble');
      const btn = act;
      if (currentUtterance) {                       // 正在朗读 → 停止（含切到别的消息朗读时）
        const prevBtn = currentReadBtn;
        window.speechSynthesis.cancel();
        currentUtterance = null;
        if (prevBtn) {
          prevBtn.textContent = '朗读';
          prevBtn.classList.remove('is-active');
        }
        currentReadBtn = null;
        if (btn === prevBtn) return;                 // 点的就是正在朗读的那条 → 仅停止
        // 点是另一条：下面继续为它朗读
      }
      const text = msg ? (msg._body ? msg._body.innerText : '') : '';
      if (!text) { toast('没有可朗读的内容'); return; }
      const u = new SpeechSynthesisUtterance(text);
      const reset = () => {
        currentUtterance = null;
        currentReadBtn = null;
        btn.textContent = '朗读';
        btn.classList.remove('is-active');
      };
      u.onend = reset;
      u.onerror = reset;
      currentUtterance = u;
      currentReadBtn = btn;
      btn.textContent = '停止';
      btn.classList.add('is-active');
      window.speechSynthesis.speak(u);
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

  // ★ v9.22.2：会话列表推送。老版本后端只刷 Qt 侧的列表，网页这边**从没收到过
  //   任何「新对话已建好」的通知** —— 这就是用户说的「加号点了没反应」。
  //   后端 _new_conversation / _load_conversations 现在都会 send_convs，
  //   clearThread=true 表示「刚切到一个空会话」，要把消息区清空回欢迎态。
  on('convs', (p) => {
    if (!p) return;
    const next = { convs: p.convs || [] };
    if (p.currentConvId !== undefined) next.currentConvId = p.currentConvId || null;
    if (p.clearThread) {
      next.messages = [];
      next.streaming = false;
      streamMsg = null;
      streamThink = null;
      currentTool = null;
      streamBaseCount = 0;
      streamBaseMsgCount = 0;
    }
    patch(next);
    renderConvList();
    if (p.clearThread) renderMessages([]);
  });

  on('token', (p) => {
    if (!p || !streamMsg) return;
    // ★ v9.22.8：正文一开始出字，说明这一轮思考结束了 —— 结算秒数并把思考条收起
    //   （「已思考 12 秒 ⌄」）。之后再来的 reason 会由 ensureThink() 开新的一条。
    settleStreamThink();
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
    // 描述形如「find_lines  {"pattern": "text.cpp"}」或「find_lines（text.cpp）」，
    // 拆成真实工具名 + 参数摘要 —— 用户要看的是「用的什么工具、执行的啥」。
    const info = splitToolDesc(p.text || '');
    const cap = document.createElement('tool-capsule');
    cap.name = info.name || '工具';
    cap.arg = compressArgs(info.arg, 60);
    cap.setRunning(true);
    if (streamMsg) streamMsg.insertBeforeBody(cap);
    currentTool = cap;
    // ★ v9.22.2：工具是「本轮思考结束」的分界线 —— 收掉当前的流式思考卡，
    //   下一次 send_reason 会开一张新卡。否则好几轮的深度思考在流式期间
    //   也会被写进同一个块里。
    // ★ v9.22.8：收掉时顺便结算秒数（「已思考 N 秒」），并保持内联在同一消息内。
    settleStreamThink();
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

  // ★ v9.22.4：上游重置（停止 / 重答 / 供应商重连）时清掉已渲染的半段答复，
  //   否则同一轮里会出现两段答案叠在一起。后端在 main_window.py 里 emit stream_reset，
  //   但前端此前从未监听。
  on('stream_reset', () => {
    if (streamMsg) {
      try {
        streamMsg._text = '';
        if (streamMsg._body) streamMsg._body.textContent = '';
      } catch (e) { /* ignore */ }
    }
    if (streamThink) {
      try { streamThink.remove(); } catch (e) { /* ignore */ }
      streamThink = null;
    }
    thinkStartAt = 0;
    currentTool = null;
  });

  on('attachments', (p) => {
    patch({ attachments: (p && p.files) || [] });
    renderAttachments();
  });

  on('theme', (p) => {
    if (p && p.theme) patch({ theme: p.theme });
  });

  /* ---------------- 语音（v9.22.8 整段替换语义） ----------------
   * 后端契约：
   *   voice_status   {msg}                录音状态文案（"正在录音…" / "已停止"）
   *   voice_partial  {text, final:false}  录音过程中不断推的**当前整段**识别结果
   *   voice_transcript {text, final:true} 录音结束后的最终**整段**结果
   * ★ 三条都是「整段」，所以一律 input.value = voiceBaseValue + text，
   *   绝不能再 += （老代码就是 +=，partial 一来就重复叠加）。
   */
  on('voice_status', (p) => {
    if (!p) return;
    const msg = p.msg || p.status || '';
    setVoicePlaceholder(msg);
    if (voiceIsRecording(msg)) beginVoiceSession();
    else if (voiceIsStopped(msg)) endVoiceSession();
    // 其它文案（「没听清，再说一次？」之类）**不动**会话：
    //   final 还没到，voiceBaseValue 必须留着，否则整段结果会和 partial 叠两遍。
  });

  on('voice_partial', (p) => {
    if (!p || p.text == null) return;
    if (!chatPageActive()) return;          // 用户在钉钉页说话，别写到聊天输入框
    applyVoiceText(p.text);
  });

  on('voice_transcript', (p) => {
    if (!p || p.text == null) return;
    if (!chatPageActive()) return;          // 同上：不是本页发起的录音就不消费
    applyVoiceText(p.text);          // 整段替换（不是追加）
    endVoiceSession();               // 最终结果到了：清基准值、麦克风取消标红
    try { input.focus(); } catch (e) { /* ignore */ }
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

  voicePlaceholderDefault = input.placeholder || '输入消息，回车发送';

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
