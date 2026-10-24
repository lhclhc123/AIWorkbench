// components/tool-capsule.js —— <tool-capsule> 工具胶囊（默认收起，点开显示结果）
'use strict';

const SVG_GEAR = `
  <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round">
    <circle cx="8" cy="8" r="2.2"/><path d="M8 2.2v1.3M8 12.5v1.3M2.2 8h1.3M12.5 8h1.3M4 4l.9.9M11.1 11.1l.9.9M12 4l-.9.9M4.9 11.1 4 12"/>
  </svg>`;
const SVG_OK = `
  <svg width="11" height="11" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m3.4 8.4 3 3 6.2-6.2"/></svg>`;
const SVG_SPIN = `
  <svg class="spin" width="11" height="11" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M8 1.5v2M8 12.5v2M1.5 8h2M12.5 8h2M3.4 3.4l1.4 1.4M11.2 11.2l1.4 1.4M12.6 3.4l-1.4 1.4M4.8 11.2l-1.4 1.4"/></svg>`;
const SVG_CHEV = `
  <svg class="chev" width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="m4 6.5 4 4 4-4"/></svg>`;

class ToolCapsule extends HTMLElement {
  constructor() {
    super();
    this._name = '工具';
    this._arg = '';
    this._status = 'running'; // running | done
    this._result = '';
    this._duration = '';
    this._open = false;
    this._built = false;
  }

  connectedCallback() {
    this._ensure();
  }

  _ensure() {
    if (this._built) return;
    this._built = true;
    this.classList.add('tool');

    const pill = document.createElement('button');
    pill.className = 'tool-pill';
    pill.setAttribute('aria-expanded', 'false');
    pill.innerHTML =
      `<span class="tool-ico">${SVG_GEAR}</span>` +
      `<span class="tool-name"></span>` +
      `<span class="tool-arg"></span>` +
      `<span class="tool-status"></span>` +
      `<span class="tool-dur"></span>` +
      SVG_CHEV;

    const body = document.createElement('div');
    body.className = 'tool-body';
    const inner = document.createElement('div');
    const pre = document.createElement('pre');
    inner.appendChild(pre);
    body.appendChild(inner);

    this.append(pill, body);
    this._pill = pill;
    this._nameEl = pill.querySelector('.tool-name');
    this._argEl = pill.querySelector('.tool-arg');
    this._statusEl = pill.querySelector('.tool-status');
    this._durEl = pill.querySelector('.tool-dur');
    this._pre = pre;

    pill.addEventListener('click', () => { this.open = !this.open; });

    this._nameEl.textContent = this._name;
    this._argEl.textContent = this._arg;
    if (this._duration) this._durEl.textContent = this._duration;
    this._applyStatus();
    this._pre.textContent = this._result;
  }

  _applyStatus() {
    if (!this._built) return;
    if (this._status === 'running') {
      this._statusEl.innerHTML = SVG_SPIN;
      this._statusEl.title = '执行中';
    } else {
      this._statusEl.innerHTML = SVG_OK;
      this._statusEl.title = '完成';
    }
  }

  get open() { return this._open; }
  set open(v) {
    this._open = !!v;
    if (this._built) {
      this.classList.toggle('open', this._open);
      this._pill.setAttribute('aria-expanded', String(this._open));
    }
  }

  get name() { return this._name; }
  set name(v) { this._name = v; if (this._built) this._nameEl.textContent = v; }

  get arg() { return this._arg; }
  set arg(v) { this._arg = v; if (this._built) this._argEl.textContent = v; }

  get duration() { return this._duration; }
  set duration(v) { this._duration = v; if (this._built) this._durEl.textContent = v; }

  // setRunning(bool)：运行中显示转圈，完成显示对勾
  setRunning(running) {
    this._status = running ? 'running' : 'done';
    this._applyStatus();
  }

  get result() { return this._result; }
  set result(v) {
    this._result = v == null ? '' : String(v);
    if (this._built) this._pre.textContent = this._result;
  }
}

if (!customElements.get('tool-capsule')) {
  customElements.define('tool-capsule', ToolCapsule);
}
