// components/msg.js —— <msg-bubble> 单条消息
//   role=user      → 蓝色气泡，右对齐
//   role=assistant → 无气泡左对齐：角色头 + 正文（可插入思维链 / 工具胶囊）+ 底部操作
// 流式期间用 appendText() 只改一个文本节点，避免整块重建。
// 已写完的段落会在「段落结束」时立刻渲染成最终 markdown（由调用方注入渲染函数），
// 只有「最后一段（还在增长中）」保持纯文本 —— 收尾时的排版跳动只发生在最后一段。
'use strict';

// 少于该字符数的段落不做实时 markdown 渲染：极短段落（如单个分隔线、空行残片）
// 渲染收益低，密集触发反而浪费；这类段落收尾时随整条消息一起渲染即可。
const LIVE_MD_MIN_CHARS = 20;

const SVG_SPARK = `
  <svg width="10" height="10" viewBox="0 0 16 16" fill="currentColor"><path d="M8 1.8l1.4 3.9 3.9 1.4-3.9 1.4L8 12.4 6.6 8.5 2.7 7.1l3.9-1.4L8 1.8z"/></svg>`;

class MsgBubble extends HTMLElement {
  constructor() {
    super();
    this._role = 'assistant';
    this._text = '';
    this._time = '';
    this._meta = '';
    this._textNode = null;
    this._liveP = null;
    this._caret = null;
    this._mdFn = null;        // 调用方注入的 markdown 渲染函数（可缺省）
    this._stableParas = 0;    // 已渲染成 HTML 的段落数
    this._built = false;
  }

  connectedCallback() {
    this._ensure();
  }

  _ensure() {
    if (this._built) return;
    this._built = true;

    if (this._role === 'user') {
      this.classList.add('msg', 'msg-user');
      const bubble = document.createElement('div');
      bubble.className = 'bubble';
      bubble.textContent = this._text;
      const time = document.createElement('div');
      time.className = 'msg-time';
      time.textContent = this._time;
      this.append(bubble, time);
      this._bubble = bubble;
      this._timeEl = time;
      return;
    }

    this.classList.add('msg', 'msg-ai');
    const head = document.createElement('div');
    head.className = 'msg-role';
    head.innerHTML =
      `<span class="avatar">${SVG_SPARK}</span>` +
      `<span class="role-name">AI 工作台</span>` +
      `<span class="role-time"></span>` +
      `<span class="typing-hint" hidden>正在生成…</span>`;

    const body = document.createElement('div');
    body.className = 'md';

    const actions = document.createElement('div');
    actions.className = 'msg-actions';
    actions.hidden = true;
    actions.innerHTML =
      `<button class="act" data-act="read">朗读</button>` +
      `<button class="act" data-act="copy">复制</button>` +
      `<button class="act" data-act="regenerate">重答</button>` +
      `<span class="spacer"></span>` +
      `<span class="msg-meta"></span>`;

    this.append(head, body, actions);
    this._head = head;
    this._body = body;
    this._actions = actions;
    this._timeEl = head.querySelector('.role-time');
    this._hint = head.querySelector('.typing-hint');
    this._metaEl = actions.querySelector('.msg-meta');

    this._timeEl.textContent = this._time;
    this._metaEl.textContent = this._meta;
    if (this._text) this._body.textContent = this._text;
    // ★ v9.21.1 修复：assistant 历史消息用 html setter 存了 markdown 渲染结果，
    //   但 connect 前 _built=false，setter 直接 return 没写进 body；这里必须补写，
    //   否则历史 assistant 消息正文永远是空的（空壳 bug 的一环）。
    if (this._html) this._body.innerHTML = this._html;
  }

  get role() { return this._role; }
  set role(v) { this._role = v; }

  get text() { return this._text; }
  set text(v) {
    this._text = v == null ? '' : String(v);
    if (!this._built) return;
    this._textNode = null;
    this._liveP = null;
    this._stableParas = 0;
    if (this._body) this._body.classList.remove('live');   // user 气泡没有 _body
    if (this._role === 'user') this._bubble.textContent = this._text;
    else this._body.textContent = this._text;
  }

  get time() { return this._time; }
  set time(v) { this._time = v == null ? '' : String(v); if (this._built) this._timeEl.textContent = this._time; }

  get meta() { return this._meta; }
  set meta(v) { this._meta = v == null ? '' : String(v); if (this._built) this._metaEl.textContent = this._meta; }

  // 设置正文 HTML（用于历史消息的 Markdown 渲染，仅助手）
  get html() { return this._html; }
  set html(v) {
    this._html = v == null ? '' : String(v);
    if (!this._built) return;
    this._textNode = null;
    this._liveP = null;
    this._stableParas = 0;
    if (this._role === 'assistant') {
      this._body.classList.remove('live');
      this._body.innerHTML = this._html;
    }
  }

  /* ---------------- 流式追加 ---------------- */
  /**
   * 注入 markdown 渲染函数（由调用方传入，组件本身不依赖具体渲染实现）。
   * 不注入时，流式段落一律退化为纯文本（老行为），不会报错。
   * @param {(text: string) => string} fn 纯文本 → HTML 的渲染函数
   * @returns {this}
   */
  setMarkdownFn(fn) {
    this._mdFn = typeof fn === 'function' ? fn : null;
    return this;
  }

  /**
   * 新建一个流式段落，并把文本节点（以及光标）挂进去。
   * 光标必须留在「最后一个段落内部」，否则 <p> 是块级元素，光标会被挤到下一行。
   * @private
   */
  _openLiveP() {
    const p = document.createElement('p');
    p.className = 'live-p';
    this._body.appendChild(p);
    if (this._caret) p.appendChild(this._caret);
    this._textNode = document.createTextNode('');
    if (this._caret) p.insertBefore(this._textNode, this._caret);
    else p.appendChild(this._textNode);
    this._liveP = p;
  }

  /**
   * 关闭当前流式段落：若注入了渲染函数且段落够长，就地换成最终 markdown HTML。
   *
   * 注意：不能用 `p.innerHTML = html` —— renderMarkdown 的输出本身就带 <p>/<ul>/<pre>
   * 等块级标签，塞进 <p> 里会产生非法嵌套（浏览器会把外层 <p> 拆开）。
   * 正确做法是把渲染结果**替换掉整个 <p class="live-p"> 节点**，这样流式期间的结构
   * 与收尾后 renderMarkdown 的结构完全一致，段落级零跳变。
   * @private
   */
  _closeLiveP() {
    const p = this._liveP;
    this._liveP = null;
    if (!p) return;

    const txt = this._textNode ? this._textNode.nodeValue : p.textContent;
    // 先把光标摘出来：下面要替换/丢弃这个 <p>，不能连光标一起扔掉
    if (this._caret && this._caret.parentNode === p) p.removeChild(this._caret);

    const fn = this._mdFn;
    if (typeof fn !== 'function' || txt.length < LIVE_MD_MIN_CHARS) return;  // 保持纯文本
    if (!p.parentNode) return;

    try {
      const html = fn(txt);
      if (!html) return;                                   // 渲染不出东西就保持纯文本
      const tmp = document.createElement('div');
      tmp.innerHTML = html;
      const parent = p.parentNode;
      const keep = p;                                      // 插入位置锚点
      while (tmp.firstChild) parent.insertBefore(tmp.firstChild, keep);
      parent.removeChild(keep);
      this._stableParas += 1;
    } catch (e) {
      // 渲染失败兜底：p 未被改动，段落保持纯文本原样，流式继续，
      // 绝不因为一次渲染异常把已经生成的内容吞掉。
      console.warn('[msg-bubble] 流式段落渲染失败，退回纯文本', e);
    }
  }

  /**
   * 流式追加（仅助手）：只改文本节点，按空行分段。
   * 分段成 <p class="live-p"> 是为了让「流式中」和「收尾后 renderMarkdown 出的
   * <p>」结构一致 —— 否则收尾瞬间行距/段距整体突变，看起来就是「字体错位」。
   * 一段写完（遇到空行）就立刻渲染成最终 HTML，只有最后一段保持纯文本。
   * @param {string} delta 本次增量文本
   */
  appendText(delta) {
    this._ensure();
    if (this._role !== 'assistant') return;
    const d = delta == null ? '' : String(delta);
    if (!d) return;

    this._body.classList.add('live');
    if (!this._liveP) this._openLiveP();

    let rest = d;
    // 跨 chunk 边界：上一块以 \n 结尾、本块又以 \n 开头 → 边界处其实是一个空行，
    // 按 markdown 语义应当另起段落（否则空行会留在段内，收尾替换时段距会跳一下）。
    // 顺手把上一段末尾残留的换行去掉，免得 pre-wrap 在段尾多渲染一个空行。
    while (rest.charAt(0) === '\n' && /\n$/.test(this._textNode.nodeValue)) {
      this._textNode.nodeValue = this._textNode.nodeValue.replace(/\n+$/, '');
      rest = rest.replace(/^\n+/, '');
      this._closeLiveP();   // 本段结束 → 尽量渲染成最终 HTML
      this._openLiveP();
    }

    const parts = rest.split(/\n{2,}/);
    this._textNode.nodeValue += parts[0];
    for (let i = 1; i < parts.length; i++) {
      this._closeLiveP();
      this._openLiveP();
      this._textNode.nodeValue = parts[i];
    }
  }

  // 显示 / 隐藏「正在生成…」与光标
  setTyping(on) {
    this._ensure();
    if (this._role !== 'assistant') return;
    if (this._hint) this._hint.hidden = !on;
    if (on) {
      if (!this._caret) {
        this._caret = document.createElement('span');
        this._caret.className = 'caret';
        this._body.appendChild(this._caret);
      }
    } else if (this._caret) {
      this._caret.remove();
      this._caret = null;
    }
  }

  // 结束：露出底部操作
  showActions() {
    this._ensure();
    if (this._actions) this._actions.hidden = false;
  }

  // 在正文之前插入思维链 / 工具胶囊（保持顺序：头 → 中间块 → 正文）
  insertBeforeBody(el) {
    this._ensure();
    if (this._role === 'assistant') this._body.before(el);
    else this.append(el);
  }
}

if (!customElements.get('msg-bubble')) {
  customElements.define('msg-bubble', MsgBubble);
}
