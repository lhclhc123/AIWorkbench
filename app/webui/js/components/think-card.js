// components/think-card.js —— <think-card> 可折叠「思考过程 / 过程分组」块
// 光 DOM 自定义元素，内部 class 对齐 mockup，样式统一收在 chat.css。
//
// 两种模式：
//   mode="think"   （默认）一条思维链：头行 + 可展开的正文（markdown 渲染）
//   mode="process" 一组过程消息（v9.22.1）：头行（人话标题 + 条数）+ 折叠态小结论
//                  + 展开后的步骤列表（think 段落 / 工具胶囊行，均不再单独折叠）
'use strict';

const SVG_THINK = `
  <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round">
    <path d="M6.2 2.6a2.4 2.4 0 0 0-2.4 2.4c0 .5.1.9.4 1.3a2.4 2.4 0 0 0 .3 4.3 2.4 2.4 0 0 0 4.3 1.6V2.6H6.2z"/>
    <path d="M9.8 2.6a2.4 2.4 0 0 1 2.4 2.4c0 .5-.1.9-.4 1.3a2.4 2.4 0 0 1-.3 4.3 2.4 2.4 0 0 1-4.3 1.6"/>
  </svg>`;
const SVG_CHEV = `
  <svg class="chev" width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="m4 6.5 4 4 4-4"/></svg>`;

// 极小行内 markdown（折叠态那行小结论用）：粗体 / 斜体 / 行内代码。
function escHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]));
}

function inlineMd(s) {
  let out = escHtml(s);
  out = out.replace(/`([^`]+)`/g, (m, c) => `<code>${c}</code>`);
  out = out.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
  out = out.replace(/(^|[^*])\*([^*\n]+)\*/g, '$1<em>$2</em>');
  return out;
}

class ThinkCard extends HTMLElement {
  constructor() {
    super();
    this._title = '深度思考';
    this._meta = '';
    this._open = false;
    this._text = '';
    this._textNode = null;
    this._built = false;
    this._mode = 'think';     // 'think' | 'process'
    this._mdFn = null;        // 调用方注入的 markdown 渲染函数（可缺省，缺省退化为纯文本）
    this._steps = [];         // process 模式：步骤列表
    this._summary = '';       // process 模式：折叠态小结论
  }

  connectedCallback() {
    this._ensure();
  }

  _ensure() {
    if (this._built) return;
    this._built = true;
    this.classList.add('think-card');

    const isProcess = this._mode === 'process' || this.getAttribute('mode') === 'process';
    this._mode = isProcess ? 'process' : 'think';
    if (isProcess) this.classList.add('mode-process');

    const head = document.createElement('button');
    head.className = 'think-head';
    head.type = 'button';
    head.setAttribute('aria-expanded', 'false');
    head.innerHTML =
      `<span class="think-ico">${SVG_THINK}</span>` +
      `<span class="think-title"></span>` +
      `<span class="think-meta"></span>` +
      `<span class="spacer"></span>` +
      SVG_CHEV;
    head.addEventListener('click', () => { this.open = !this.open; });

    this._head = head;
    this._titleEl = head.querySelector('.think-title');
    this._metaEl = head.querySelector('.think-meta');

    if (isProcess) {
      // 折叠态小结论行（单独一行、可点开；展开时由 CSS 隐藏）
      const summary = document.createElement('button');
      summary.className = 'proc-summary';
      summary.type = 'button';
      summary.hidden = true;
      summary.addEventListener('click', () => { this.open = !this.open; });

      // 展开后的步骤列表
      const body = document.createElement('div');
      body.className = 'think-body';
      const inner = document.createElement('div');
      const steps = document.createElement('div');
      steps.className = 'proc-steps';
      inner.appendChild(steps);
      body.appendChild(inner);

      this.append(head, summary, body);
      this._summaryEl = summary;
      this._stepsEl = steps;
    } else {
      const body = document.createElement('div');
      body.className = 'think-body';
      const inner = document.createElement('div');
      const stream = document.createElement('div');
      stream.className = 'think-stream';
      inner.appendChild(stream);
      body.appendChild(inner);
      this.append(head, body);
      this._stream = stream;
    }

    this._titleEl.textContent = this._title;
    this._metaEl.textContent = this._meta;
    if (this._open) this.classList.add('open');
    if (isProcess) {
      this._renderSteps();
      this._renderSummary();
    } else if (this._text) {
      this._renderText();
    }
  }

  // process 模式：设置步骤 + 小结论。步骤形如
  //   {kind:'think', text} 或 {kind:'tool', name, arg, result}
  setProcess(steps, summary) {
    this._steps = Array.isArray(steps) ? steps : [];
    this._summary = summary == null ? '' : String(summary);
    this._ensure();
    this._renderSteps();
    this._renderSummary();
    return this;
  }

  _renderSteps() {
    if (!this._stepsEl) return;
    this._stepsEl.innerHTML = '';
    for (const st of this._steps) {
      const row = document.createElement('div');
      if (st && st.kind === 'tool') {
        row.className = 'proc-step proc-step-tool';
        const pill = document.createElement('div');
        pill.className = 'proc-tool';
        // 结果不再单独折叠，但仍能看：挂在 title 上做悬停提示
        if (st.result) pill.title = String(st.result);
        const nm = document.createElement('span');
        nm.className = 'pt-name';
        nm.textContent = st.name || '工具';
        const ar = document.createElement('span');
        ar.className = 'pt-arg';
        ar.textContent = st.arg || '';
        pill.append(nm, ar);
        row.appendChild(pill);
      } else {
        row.className = 'proc-step proc-step-think';
        const div = document.createElement('div');
        div.className = 'proc-think';
        const txt = (st && st.text) || '';
        if (this._mdFn) {
          try { div.innerHTML = this._mdFn(txt); }
          catch (e) { div.textContent = txt; }
        } else {
          div.textContent = txt;
        }
        row.appendChild(div);
      }
      this._stepsEl.appendChild(row);
    }
  }

  _renderSummary() {
    if (!this._summaryEl) return;
    const s = String(this._summary || '').replace(/\s+/g, ' ').trim();
    if (!s) {
      this._summaryEl.hidden = true;
      this._summaryEl.innerHTML = '';
      return;
    }
    this._summaryEl.hidden = false;
    this._summaryEl.innerHTML = inlineMd(s);
  }

  // 注入 markdown 渲染函数（由调用方传入；不注入则退化为纯文本）
  setMarkdownFn(fn) {
    this._mdFn = typeof fn === 'function' ? fn : null;
    if (this._built && this._mode !== 'process' && this._text) this._renderText();
    return this;
  }

  // think 模式：把整段文本渲染成 markdown（粗体/斜体/行内代码/换行都生效）
  _renderText() {
    if (!this._stream) return;
    const txt = this._text || '';
    if (this._mdFn) {
      try { this._stream.innerHTML = this._mdFn(txt); }
      catch (e) { this._stream.textContent = txt; }
    } else {
      this._stream.textContent = txt;
    }
    this._textNode = null;
  }

  get mode() { return this._mode; }
  set mode(v) {
    this._mode = v === 'process' ? 'process' : 'think';
    if (this._built) this.classList.toggle('mode-process', this._mode === 'process');
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

  // 流式追加：只改文本节点，避免整块重建（仅 think 模式；process 组只在收尾时建）
  appendText(delta) {
    this._ensure();
    if (!this._stream) return;
    if (!this._textNode) {
      this._textNode = document.createTextNode('');
      this._stream.textContent = '';
      this._stream.appendChild(this._textNode);
    }
    this._textNode.nodeValue += delta;
    this._text = this._textNode.nodeValue;
  }

  get text() { return this._text; }
  set text(v) {
    this._text = v == null ? '' : String(v);
    if (this._built) this._renderText();
  }
}

if (!customElements.get('think-card')) {
  customElements.define('think-card', ThinkCard);
}
