// transport-qwebchannel.js —— 对接 window.qt.webChannelTransport（真实通道）
// 这是「传输无关」设计里的唯一一个真实传输实现。
//
// 后端（app/webui_bridge.py）契约约定如下（前端已按此实现，请后端对齐）：
//   - 在 QWebChannel 注册一个对象，默认名 "awb"（找不到则自动取第一个带 call 方法的对象）。
//   - 该对象暴露：
//       · slot  call(name: str, args_json: str)   —— 接收前端上行命令
//       · signal webui_event(type: str, payload_json: str) —— 向前端推送下行事件
//
// 前端 bridge.call(name, args)  →  remote.call(name, JSON.stringify(args))
// 后端 emit("webui_event", type, json) → 前端 bridge.on(type, fn)
//
// 若页面以纯浏览器方式打开（无 window.qt / window.QWebChannel），
// connect() 返回 null，进入离线演示模式，不阻塞静态预览。
'use strict';

const OBJECT_NAME = 'awb';
// 等待 transport 就绪的最大时长（毫秒）。后端 attach 在 loadFinished 后，
// 正常几十 ms；软件渲染下 loadFinished 更慢，留足余量。
const WAIT_TIMEOUT = 5000;

export function connect() {
  return new Promise((resolve) => {
    // ★ v9.21.1 修复：后端在 loadFinished 之后才 setWebChannel，
    //   但前端 module 脚本往往更早执行 —— 此刻 window.qt / webChannelTransport
    //   可能还不存在。老代码一上来就 resolve(null) 进离线模式，且 bridge.start()
    //   的 started 标志一旦置位就再也不重连 → 网页永远收不到下行事件（空壳）。
    //   现在改成「轮询等待 transport 就绪」，最多等 WAIT_TIMEOUT 毫秒，期间
    //   每 50ms 探测一次；等到就连接，等不到才放弃进离线模式。
    const startTs = Date.now();

    function attempt() {
      if (window.qt && window.qt.webChannelTransport && window.QWebChannel) {
        try {
          new window.QWebChannel(window.qt.webChannelTransport, (channel) => {
            const remote = pickRemote(channel);
            if (!remote) {
              // 对象还没注册（attach 未完成）：等 50ms 再试，直到超时
              if (Date.now() - startTs >= WAIT_TIMEOUT) {
                console.error('[transport] QWebChannel 未注册任何桥对象');
                resolve(null);
                return;
              }
              setTimeout(attempt, 50);
              return;
            }
            resolve(createTransport(remote));
          });
          return;
        } catch (e) {
          console.error('[transport] QWebChannel 初始化失败', e);
          resolve(null);
          return;
        }
      }
      // transport 还没就绪：等 50ms 再试
      if (Date.now() - startTs >= WAIT_TIMEOUT) {
        console.warn('[transport] 未检测到 QWebChannel，进入离线演示模式');
        resolve(null);
        return;
      }
      setTimeout(attempt, 50);
    }

    attempt();
  });
}

function pickRemote(channel) {
  const objs = channel.objects || {};
  const named = objs[OBJECT_NAME];
  if (named && typeof named.call === 'function') return named;
  for (const key of Object.keys(objs)) {
    const o = objs[key];
    if (o && typeof o.call === 'function') return o;
  }
  const first = Object.values(objs)[0];
  return first || null;
}

function createTransport(remote) {
  return {
    // 上行：命令（fire-and-forget）
    invoke(name, args) {
      try {
        remote.call(name, JSON.stringify(args == null ? {} : args));
      } catch (e) {
        console.error('[transport] invoke 失败', name, e);
      }
    },
    // 下行：订阅 webui_event(type, payloadJson) 信号
    // ⚠ 后端信号名叫 webui_event（不能叫 event，那是 QObject 保留名会崩）
    onEvent(handler) {
      if (remote.webui_event && typeof remote.webui_event.connect === 'function') {
        remote.webui_event.connect((type, payloadJson) => {
          let payload = {};
          try { payload = JSON.parse(payloadJson || '{}'); } catch (e) { /* ignore */ }
          handler(String(type), payload);
        });
      } else {
        console.warn('[transport] 桥对象缺少 webui_event 信号');
      }
    },
  };
}
