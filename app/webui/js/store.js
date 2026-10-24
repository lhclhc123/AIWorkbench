// store.js —— 轻量响应式仓库：state + subscribe + patch
// 视图通过 subscribe 监听变化，通过 patch 更新；不引任何框架。
'use strict';

const state = {
  theme: null,           // 'light' | 'dark'（null = 尚未确定，跟随系统）
  convs: [],             // [{id,title,preview,time}]
  currentConvId: null,
  models: [],            // [{label, choice}]
  selectedModel: '',
  messages: [],          // [{id, role:'user'|'assistant'|'tool'|'think', text, meta?}]
  streaming: false,      // 本轮是否正在生成
  busy: false,           // 输入区忙碌态（发送键变停止）
  phase: '',             // 状态行文字
  attachments: [],       // [{name, path}]
  error: null,
};

const listeners = new Set();

export function getState() {
  return state;
}

export function subscribe(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

export function patch(partial) {
  Object.assign(state, partial);
  for (const fn of [...listeners]) {
    try { fn(state, partial); } catch (e) { console.error('[store] listener error', e); }
  }
}
