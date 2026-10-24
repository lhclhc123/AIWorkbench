// components/think-card.js —— <think-card> 可折叠「思考过程」块
// 光 DOM 自定义元素，内部 class 对齐 mockup，样式统一收在 chat.css。
'use strict';

const SVG_THINK = `
  <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round">
    <path d="M6.2 2.6a2.4 2.4 0 0 0-2.4 2.4c0 .5.1.9.4 1.3a2.4 2.4 0 0 0 .3 4.3 2.4 2.4 0 0 0 4.3 1.6V2.6H6.2z"/>
    <path d="M9.8 2.6a2.4 2.4 0 0 1 2.4 2.4c0 .5-.1.9-.4 1.3a2.4 2.4 0 0 1-.3 4.3 2.4 2.4 0 0 1-4.3 1.6"/>
  </svg>`;
const SVG_CHEV = `
  <svg class="chev" width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="m4 6.5 4 4 4-4"/></svg>`;

class ThinkCard extends HTMLElement {
  constructor() {
    super();
    this._title = '深度思考';
    this._meta = '';
    this._open = false;
    this._textNode = null;
    this._built = false;
  }

  connectedCallback() {
    this._ensure();
  }

  _ensure() {
    if (this._built) return;
    this._built = true;
    this.classList.add('think-card');
    const head = document.createElement('button');
    head.className = 'think-head';
    head.setAttribute('aria-expanded', 'false');
    head.innerHTML =
      `<span class="think-ico">${SVG_THINK}</span>` +
      `<span class="think-title"></span>` +
      `<span class="think-meta"></span>` +
      `<span class="spacer"></span>` +
      SVG_CHEV;
    const body = document.createElement('div');
    body.className = 'think-body';
    const inner = document.createElement('div');
    const stream = document.createElement('div');
    stream.className = 'think-stream';
    inner.appendChild(stream);
    body.appendChild(inner);
    this.append(head, body);

    this._head = head;
    this._titleEl = head.querySelector('.think-title');
    this._metaEl = head.querySelector('.think-meta');
    this._stream = stream;

    head.addEventListener('click', () => { this.open = !this.open; });

    this._titleEl.textContent = this._title;
    this._metaEl.textContent = this._meta;
    if (this._open) this.classList.add('open');
    if (this._text) stream.textContent = this._text;
  }

  get open() { return this._open; }
  set open(v) {
    this._open = !!v;
    if (this._built) {
      this.classList.toggle('open', this._open);
      this._head.setAttribute('aria-expanded', String(this._open));
    }
  }

  get title() { return this._title; }
  set title(v) { this._title = v; if (this._built) this._titleEl.textContent = v; }

  get meta() { return this._meta; }
  set meta(v) { this._meta = v; if (this._built) this._metaEl.textContent = v; }

  // 流式追加：只改文本节点，避免整块重建
  appendText(delta) {
    this._ensure();
    if (!this._textNode) {
      this._textNode = document.createTextNode('');
      this._stream.textContent = '';
      this._stream.appendChild(this._textNode);
    }
    this._textNode.nodeValue += delta;
  }

  get text() { return this._text; }
  set text(v) {
    this._text = v == null ? '' : String(v);
    if (this._built) { this._stream.textContent = this._text; this._textNode = null; }
  }
}

if (!customElements.get('think-card')) {
  customElements.define('think-card', ThinkCard);
}
