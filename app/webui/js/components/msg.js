// components/msg.js —— <msg-bubble> 单条消息
//   role=user      → 蓝色气泡，右对齐
//   role=assistant → 无气泡左对齐：角色头 + 正文（可插入思维链 / 工具胶囊）+ 底部操作
// 流式期间用 appendText() 只改一个文本节点，避免整块重建。
'use strict';

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
    this._caret = null;
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
  }

  get role() { return this._role; }
  set role(v) { this._role = v; }

  get text() { return this._text; }
  set text(v) {
    this._text = v == null ? '' : String(v);
    if (!this._built) return;
    this._textNode = null;
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
    if (this._role === 'assistant') this._body.innerHTML = this._html;
  }

  // 流式追加（仅助手）：只更新文本节点
  appendText(delta) {
    this._ensure();
    if (this._role !== 'assistant') return;
    if (!this._textNode) {
      this._body.classList.add('live');
      this._textNode = document.createTextNode('');
      if (this._caret) this._caret.before(this._textNode);
      else this._body.appendChild(this._textNode);
    }
    this._textNode.nodeValue += delta;
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
