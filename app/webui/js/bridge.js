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
  if (t) {
    t.onEvent(dispatch);
    // ★ v9.21.1 修复：连接成功后主动向上行发 ready，让后端在收到 ready 后
    //   再 push init（含模型/会话列表）。否则后端在 loadFinished 时盲推 init，
    //   前端还没 onEvent 就丢掉了 → 网页拿不到初始状态（空壳）。
    t.invoke('ready', {});
  }
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

// 通用 RPC：请求-响应式调用后端方法。返回 Promise，resolve 后端结果。
// 后端契约：call('rpc', {method, args, reqId}) → 事件 rpc_result {reqId, ok, data}。
let _rpcSeq = 0;
const _rpcPending = new Map(); // reqId -> {resolve, reject, timer}

function _onRpcResult(payload) {
  const reqId = payload && payload.reqId;
  const p = _rpcPending.get(reqId);
  if (!p) return;
  _rpcPending.delete(reqId);
  clearTimeout(p.timer);
  if (payload && payload.ok) p.resolve(payload.data);
  else p.reject(new Error((payload && payload.data && payload.data.error) || 'RPC 失败'));
}

// 在 start() 里注册一次 rpc_result 监听（幂等，用标志位避免重复注册）
let _rpcWired = false;

export async function rpc(method, args, timeoutMs = 30000) {
  const t = await ensure();
  if (!t) throw new Error('未连接到后端桥');
  if (!_rpcWired) {
    _rpcWired = true;
    on('rpc_result', _onRpcResult);
  }
  const reqId = 'r' + (++_rpcSeq) + '_' + Date.now();
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      _rpcPending.delete(reqId);
      reject(new Error(`RPC 超时: ${method}`));
    }, timeoutMs);
    _rpcPending.set(reqId, { resolve, reject, timer });
    t.invoke('rpc', { method, args: args || {}, reqId });
  });
}

// 页面卸载时把通道标记失效，避免「永远转圈」的未完成请求
window.addEventListener('beforeunload', () => {
  transport = null;
});
