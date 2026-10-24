// bridge.js —— 传输无关的命令 / 事件桥
// 对外只暴露两个 API：
//   call(name, args)   —— 上行命令
//   on(type, fn)       —— 接收下行事件
// 视图层永远不碰 window.qt.webChannelTransport；换传输（将来 HTTP/SSE）只改这一层以下。
'use strict';

import { connect } from './transport-qwebchannel.js';

let transport = null;
let connecting = null;
let started = false;
const handlers = new Map(); // type -> Set<fn>

function dispatch(type, payload) {
  const set = handlers.get(type);
  if (!set) return;
  for (const fn of [...set]) {
    try { fn(payload); } catch (e) { console.error('[bridge] handler error', type, e); }
  }
}

async function ensure() {
  if (transport) return transport;
  if (!connecting) connecting = connect();
  transport = await connecting;
  return transport;
}

// 启动：建立通道并开始接收下行事件（幂等，多次调用安全）
export async function start() {
  if (started) return;
  started = true;
  const t = await ensure();
  if (t) t.onEvent(dispatch);
  return t;
}

export function on(type, fn) {
  if (!handlers.has(type)) handlers.set(type, new Set());
  handlers.get(type).add(fn);
  return () => handlers.get(type).delete(fn);
}

export async function call(name, args) {
  const t = await ensure();
  if (t) t.invoke(name, args);
  return t;
}

// 页面卸载时把通道标记失效，避免「永远转圈」的未完成请求
window.addEventListener('beforeunload', () => {
  transport = null;
});
