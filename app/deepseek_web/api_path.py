# -*- coding: utf-8 -*-
"""DeepSeek 网页通道的「接口直发」模式（v9.19.0）。

为什么要这个
------------
老链路（`web_driver.submit`）是**模拟人手**：往 contenteditable 里塞字 → 点发送键 →
真键盘回车兜底 → 轮询页面文本判断生成结束。它有三类天生毛病：

1. **脆**：DeepSeek 一改版（class 名、按钮位置、React 渲染时序）就废；
2. **慢**：填字 + 等按钮可用 + 等"确认收到"，一轮固定多花 1~3 秒；
3. **脏**：点不准就会重复发送，或者把半截话留在输入框里 —— 用户实测
   "一条对话调了十几条新对话"、"一堆历史消息"。

新链路走的是**浏览器自己发请求**：在页面上下文里 `fetch('/api/v0/chat/completion')`，
带真实 Cookie、真实同源、真实 UA —— 和用户按 F12 在控制台里发一模一样。
不碰输入框、不点按钮，因此上述三类毛病一次消失。

三道关键设计
------------
1. **PoW（工作量证明）**：DeepSeek 要求 `x-ds-pow-response` 头，内容是
   `base64(json)`，其中 `answer` 要满足 `sha3_256(salt_expireAt_nonce) < 2^256/difficulty`。
   `difficulty` 是数字（实测约 144000），所以判据是**数值比较**，不是"前导零个数"。
   本项目自己带了一份 32 位 Keccak（见 `JS_HELPERS`），不依赖页面内部的 wasm 模块。
2. **流式**：SSE 在页面里读完，累积到 `window.__AWBAPI__.buf`；Python 侧每 250ms
   取一次快照算增量。这样 `on_progress` 的体验和原来完全一致。
3. **绝不硬上**：只要有一环不对（没登录、返回码非 0、PoW 被拒、SSE 解不出），
   立刻抛 `ApiUnavailable`，`web_driver.submit()` 捕获后**永久降级**回老的点击链路。
   任何情况下都不会让用户"点了发送什么也没发生"。
"""
import json
import time

# ---------------------------------------------------------------------------
# 页面内脚本（在 chat.deepseek.com 的上下文里执行）
# ---------------------------------------------------------------------------
JS_HELPERS = r"""
(function () {
  if (window.__AWBAPI__ && window.__AWBAPI__.v === 1) return 'ok';

  // ---------------- SHA3-256（32 位实现，Keccak-f[1600]） ----------------
  var RC = [
    0x00000001,0x00000000, 0x00008082,0x00000000, 0x0000808a,0x80000000,
    0x80008000,0x80000000, 0x0000808b,0x00000000, 0x80000001,0x00000000,
    0x80008081,0x80000000, 0x00008009,0x80000000, 0x0000008a,0x00000000,
    0x00000088,0x00000000, 0x80008009,0x00000000, 0x8000000a,0x00000000,
    0x8000808b,0x00000000, 0x0000008b,0x80000000, 0x00008089,0x80000000,
    0x00008003,0x80000000, 0x00008002,0x80000000, 0x00000080,0x80000000,
    0x0000800a,0x00000000, 0x8000000a,0x80000000, 0x80008081,0x80000000,
    0x00008080,0x80000000, 0x80000001,0x00000000, 0x80008008,0x80000000
  ];
  var RHO = [
     0, 1,62,28,27,
    36,44, 6,55,20,
     3,10,43,25,39,
    41,45,15,21, 8,
    18, 2,61,56,14
  ];

  function keccakF(s) {
    var C = new Uint32Array(10);
    var B = new Uint32Array(50);
    for (var round = 0; round < 24; round++) {
      // theta
      for (var x = 0; x < 5; x++) {
        C[2*x]   = s[2*x] ^ s[2*(x+5)] ^ s[2*(x+10)] ^ s[2*(x+15)] ^ s[2*(x+20)];
        C[2*x+1] = s[2*x+1] ^ s[2*(x+5)+1] ^ s[2*(x+10)+1] ^ s[2*(x+15)+1] ^ s[2*(x+20)+1];
      }
      for (var x2 = 0; x2 < 5; x2++) {
        var i1 = (x2 + 1) % 5, im = (x2 + 4) % 5;
        // rotl(C[i1], 1)
        var rlo = (C[2*i1] << 1) | (C[2*i1+1] >>> 31);
        var rhi = (C[2*i1+1] << 1) | (C[2*i1] >>> 31);
        var dlo = C[2*im] ^ rlo;
        var dhi = C[2*im+1] ^ rhi;
        for (var y = 0; y < 5; y++) {
          s[2*(x2+5*y)]   ^= dlo;
          s[2*(x2+5*y)+1] ^= dhi;
        }
      }
      // rho + pi
      for (var x3 = 0; x3 < 5; x3++) {
        for (var y3 = 0; y3 < 5; y3++) {
          var idx = x3 + 5*y3;
          var n = RHO[idx];
          var lo = s[2*idx], hi = s[2*idx+1];
          var olo, ohi;
          if (n === 0) { olo = lo; ohi = hi; }
          else if (n < 32) { olo = (lo << n) | (hi >>> (32-n)); ohi = (hi << n) | (lo >>> (32-n)); }
          else if (n === 32) { olo = hi; ohi = lo; }
          else { var m = n - 32; olo = (hi << m) | (lo >>> (32-m)); ohi = (lo << m) | (hi >>> (32-m)); }
          var pi = y3 + 5 * ((2*x3 + 3*y3) % 5);
          B[2*pi] = olo; B[2*pi+1] = ohi;
        }
      }
      // chi
      for (var y4 = 0; y4 < 5; y4++) {
        for (var x4 = 0; x4 < 5; x4++) {
          var a = 2*(x4 + 5*y4);
          var b = 2*(((x4+1)%5) + 5*y4);
          var c = 2*(((x4+2)%5) + 5*y4);
          s[a]   = B[a]   ^ ((~B[b])   & B[c]);
          s[a+1] = B[a+1] ^ ((~B[b+1]) & B[c+1]);
        }
      }
      // iota
      s[0] ^= RC[2*round];
      s[1] ^= RC[2*round+1];
    }
  }

  function utf8(str) {
    if (typeof TextEncoder !== 'undefined') return new TextEncoder().encode(str);
    var out = [], i, c;
    for (i = 0; i < str.length; i++) {
      c = str.charCodeAt(i);
      if (c < 0x80) out.push(c);
      else if (c < 0x800) out.push(0xc0 | (c >> 6), 0x80 | (c & 63));
      else out.push(0xe0 | (c >> 12), 0x80 | ((c >> 6) & 63), 0x80 | (c & 63));
    }
    return new Uint8Array(out);
  }

  function sha3_256_bytes(bytes) {
    var rate = 136;
    var s = new Uint32Array(50);
    var i, j;
    // ★ 只补到"刚好容纳填充"的整块数：floor(len/rate)+1 块。
    //   以前写成 +2 块会在尾部多吸收一个全零块 —— 吸收零不改状态，但每轮
    //   keccakF 会把状态彻底打乱，于是哈希全错（探针对拍当场抓到了）。
    var blocks = Math.floor(bytes.length / rate) + 1;
    var padded = new Uint8Array(blocks * rate);
    padded.set(bytes);
    padded[bytes.length] ^= 0x06;
    padded[blocks * rate - 1] ^= 0x80;
    for (var off = 0; off < padded.length; off += rate) {
      for (i = 0; i < rate / 8; i++) {
        j = off + i * 8;
        s[2*i]   ^= (padded[j] | (padded[j+1] << 8) | (padded[j+2] << 16) | (padded[j+3] << 24));
        s[2*i+1] ^= (padded[j+4] | (padded[j+5] << 8) | (padded[j+6] << 16) | (padded[j+7] << 24));
      }
      keccakF(s);
    }
    var out = new Uint8Array(32);
    for (i = 0; i < 32; i++) {
      var lane = i >> 3;
      var b = s[2*lane + (i & 4 ? 1 : 0)] >>> ((i & 3) * 8);
      out[i] = b & 0xff;
    }
    return out;
  }

  function toHex(bytes) {
    var h = '';
    for (var i = 0; i < bytes.length; i++) {
      h += (bytes[i] < 16 ? '0' : '') + bytes[i].toString(16);
    }
    return h;
  }

  function sha3Hex(str) { return toHex(sha3_256_bytes(utf8(str))); }

  // ---------------- PoW ----------------
  // 目标：sha3_256(salt + "_" + expire_at + "_" + nonce) < 2^256 / difficulty
  // ★ 性能：一轮可能要算十几万次哈希，所以这里**不复用 sha3_256_bytes** ——
  //   它每次都重新分配数组 + 转 hex 字符串（32 字节 → 64 字符），开销比
  //   keccak 本身还大。改成：预分配一块缓冲区，每轮只改 nonce 那几个字节，
  //   算完直接按字节比较阈值。实测 3 万次/秒 → 15 万次/秒（难度 144000 从
  //   4 秒降到 1 秒内）。
  function thresholdBytes(difficulty) {
    var d = BigInt(difficulty || 1);
    if (d <= 0n) d = 1n;
    var t = ((1n << 256n) / d) - 1n;
    var h = t.toString(16);
    while (h.length < 64) h = '0' + h;
    var out = new Uint8Array(32);
    for (var i = 0; i < 32; i++) out[i] = parseInt(h.substr(i * 2, 2), 16);
    return out;
  }

  function keccakDigest(blocks, nBlocks) {
    var s = new Uint32Array(50);
    for (var b = 0; b < nBlocks; b++) {
      var off = b * 136;
      for (var i = 0; i < 17; i++) {
        var j = off + i * 8;
        s[2*i]   ^= (blocks[j] | (blocks[j+1] << 8) | (blocks[j+2] << 16) | (blocks[j+3] << 24));
        s[2*i+1] ^= (blocks[j+4] | (blocks[j+5] << 8) | (blocks[j+6] << 16) | (blocks[j+7] << 24));
      }
      keccakF(s);
    }
    var out = new Uint8Array(32);
    for (var k = 0; k < 32; k++) {
      var lane = k >> 3;
      var w = s[2*lane + ((k & 4) ? 1 : 0)] >>> ((k & 3) * 8);
      out[k] = w & 0xff;
    }
    return out;
  }

  function ltBytes(a, b) {
    for (var i = 0; i < 32; i++) {
      if (a[i] !== b[i]) return a[i] < b[i];
    }
    return false;
  }

  function solvePow(challenge) {
    var salt = String(challenge.salt || '');
    var expireAt = String(challenge.expire_at || '');
    var th = thresholdBytes(challenge.difficulty);
    var prefix = utf8(salt + '_' + expireAt + '_');
    var blocks = Math.floor((prefix.length + 16) / 136) + 1;
    var size = blocks * 136;
    var buf = new Uint8Array(size);
    var t0 = Date.now();
    for (var nonce = 0; nonce < 200000000; nonce++) {
      var ns = String(nonce);
      buf.fill(0);
      buf.set(prefix, 0);
      for (var k = 0; k < ns.length; k++) buf[prefix.length + k] = ns.charCodeAt(k) & 0xff;
      var len = prefix.length + ns.length;
      buf[len] ^= 0x06;
      buf[size - 1] ^= 0x80;
      if (ltBytes(keccakDigest(buf, blocks), th)) {
        return {answer: nonce, ms: Date.now() - t0};
      }
    }
    throw new Error('pow-not-solved');
  }

  function b64(str) { return btoa(unescape(encodeURIComponent(str))); }

  function token() {
    var raw = localStorage.getItem('userToken');
    if (!raw) return '';
    try { return (JSON.parse(raw) || {}).value || ''; } catch (e) { return ''; }
  }

  function headers(extra) {
    var h = {'content-type': 'application/json'};
    var t = token();
    if (t) h['authorization'] = 'Bearer ' + t;
    if (extra) for (var k in extra) h[k] = extra[k];
    return h;
  }

  function biz(res) {
    // DeepSeek 统一包一层 {code,msg,data:{biz_data}}
    if (!res || typeof res !== 'object') return null;
    if (res.code !== undefined && res.code !== 0) return null;
    var d = res.data || {};
    return d.biz_data !== undefined ? d.biz_data : d;
  }

  async function jpost(path, body, extraHeaders) {
    var r = await fetch(path, {
      method: 'POST', headers: headers(extraHeaders),
      body: JSON.stringify(body || {})
    });
    var txt = await r.text();
    var j = null;
    try { j = JSON.parse(txt); } catch (e) { j = null; }
    return {status: r.status, json: j, text: txt};
  }

  async function powHeader(targetPath) {
    var res = await jpost('/api/v0/chat/create_pow_challenge',
                          {target_path: targetPath});
    var data = biz(res.json);
    var ch = (data && (data.challenge || data)) || null;
    if (!ch || !ch.salt || !ch.challenge) {
      throw new Error('pow-challenge-failed:' + (res.text || '').slice(0, 120));
    }
    var solved = solvePow(ch);
    var payload = {
      algorithm: ch.algorithm || 'DeepSeekHashV1',
      challenge: ch.challenge,
      salt: ch.salt,
      answer: solved.answer,
      signature: ch.signature || '',
      target_path: targetPath
    };
    return {header: b64(JSON.stringify(payload)), ms: solved.ms};
  }

  async function createSession() {
    var res = await jpost('/api/v0/chat_session/create', {character_id: null});
    var data = biz(res.json);
    if (!data || !data.id) {
      throw new Error('session-create-failed:' + (res.text || '').slice(0, 120));
    }
    return data.id;
  }

  function pushContent(o, kind, val) {
    if (!val) return;
    if (kind === 'thinking') o.reasoning += val;
    else o.text += val;
  }

  function applyChunk(o, j) {
    // ① 新版：JSON Patch 风   {"p":"response/content","o":"APPEND","v":"..."}
    if (j && j.p !== undefined) {
      var p = String(j.p || '');
      var v = j.v;
      if (typeof v !== 'string') return;
      if (p.indexOf('thinking') >= 0) pushContent(o, 'thinking', v);
      else if (p === 'response/content' || p.indexOf('content') >= 0) pushContent(o, 'text', v);
      return;
    }
    // ② 老版：{"v":{"response":{"content":"...","thinking_content":"..."}}}
    var inner = (j && j.v !== undefined) ? j.v : j;
    if (!inner || typeof inner !== 'object') return;
    var r = inner.response || inner;
    if (typeof r === 'string') { pushContent(o, 'text', r); return; }
    if (r.thinking_content) pushContent(o, 'thinking', r.thinking_content);
    if (r.content) pushContent(o, 'text', r.content);
    if (r.accumulated_token_usage) o.tokens = r.accumulated_token_usage;
  }

  async function chat(prompt, opts) {
    opts = opts || {};
    var state = window.__AWBAPI__;
    state.buf = {text: '', reasoning: '', done: false, error: '', sessionId: opts.sessionId || '',
                 messageId: null, powMs: 0, chunks: 0, startedAt: Date.now()};
    var buf = state.buf;
    try {
      if (!token()) throw new Error('not-logged-in');
      var sid = opts.sessionId || '';
      if (opts.newSession || !sid) {
        sid = await createSession();
        buf.sessionId = sid;
      }
      var target = '/api/v0/chat/completion';
      var pw = await powHeader(target);
      buf.powMs = pw.ms;
      var body = {
        chat_session_id: sid,
        parent_message_id: opts.parentMessageId === undefined ? null : opts.parentMessageId,
        prompt: prompt,
        ref_file_ids: opts.refFileIds || [],
        thinking_enabled: !!opts.thinking,
        search_enabled: !!opts.search
      };
      var resp = await fetch(target, {
        method: 'POST',
        headers: headers({'x-ds-pow-response': pw.header}),
        body: JSON.stringify(body)
      });
      if (!resp.ok || !resp.body) {
        var t = '';
        try { t = await resp.text(); } catch (e) {}
        throw new Error('completion-http-' + resp.status + ':' + String(t).slice(0, 160));
      }
      var reader = resp.body.getReader();
      var dec = new TextDecoder('utf-8');
      var pending = '';
      while (true) {
        var step = await reader.read();
        if (step.done) break;
        pending += dec.decode(step.value, {stream: true});
        var lines = pending.split('\n');
        pending = lines.pop();
        for (var i = 0; i < lines.length; i++) {
          var line = lines[i].replace(/\r$/, '');
          if (!line) continue;
          if (line.indexOf('data:') !== 0) continue;
          var payload = line.slice(5).trim();
          if (!payload || payload === '[DONE]') continue;
          var j = null;
          try { j = JSON.parse(payload); } catch (e) { continue; }
          if (j && j.code !== undefined && j.code !== 0) {
            throw new Error('completion-code-' + j.code + ':' + String(j.msg || '').slice(0, 120));
          }
          buf.chunks++;
          applyChunk(buf, j);
        }
      }
      buf.done = true;
    } catch (e) {
      buf.error = String((e && e.message) || e);
      buf.done = true;
    }
    return JSON.stringify({ok: !buf.error, error: buf.error, sessionId: buf.sessionId});
  }

  window.__AWBAPI__ = {
    v: 1,
    buf: {text: '', reasoning: '', done: true, error: '', sessionId: '', messageId: null,
          powMs: 0, chunks: 0, startedAt: 0},
    // 供自检用
    sha3: sha3Hex,
    solve: solvePow,
    threshold: function (d) { return toHex(thresholdBytes(d)); },
    tokenLen: function () { return token().length; },
    snapshot: function () { return JSON.stringify(window.__AWBAPI__.buf); },
    reset: function () { window.__AWBAPI__.buf = {text: '', reasoning: '', done: true,
                          error: '', sessionId: '', messageId: null, powMs: 0,
                          chunks: 0, startedAt: 0}; return 'ok'; },
    chat: chat,
    probe: async function () {
      var out = {};
      try {
        out.tokenLen = token().length;
        var r = await fetch('/api/v0/users/current', {headers: headers()});
        out.whoami = r.status + ':' + (await r.text()).slice(0, 160);
      } catch (e) { out.whoamiErr = String(e); }
      try {
        var p = await jpost('/api/v0/chat/create_pow_challenge',
                            {target_path: '/api/v0/chat/completion'});
        var d = biz(p.json);
        var ch = d && (d.challenge || d);
        out.pow = ch ? {algorithm: ch.algorithm, difficulty: ch.difficulty,
                        hasSalt: !!ch.salt, hasSignature: !!ch.signature,
                        expireAt: ch.expire_at} : p.text.slice(0, 200);
      } catch (e) { out.powErr = String(e); }
      return JSON.stringify(out);
    }
  };
  return 'ready';
})()
"""


class ApiUnavailable(RuntimeError):
    """直发通道不可用（未登录 / 接口变了 / PoW 被拒）—— 调用方应降级到点击链路。"""


# 这些错误说明"这条路根本走不通"，不该再试；别的错误（网络抖动）可以再试一次。
_HARD_FAILS = (
    "not-logged-in",
    "pow-challenge-failed",
    "session-create-failed",
    "completion-code-",
    "completion-http-401",
    "completion-http-403",
    "pow-not-solved",
)


def is_hard_fail(err):
    e = str(err or "")
    return any(k in e for k in _HARD_FAILS)


class ApiPath:
    """在页面上下文里直接发 HTTP 请求（不走 DOM 点击）。

    只依赖 `cdp.evaluate`：所有动作都是 `Runtime.evaluate` 里跑 JS，
    所以它和"浏览器地址栏旁边按 F12 在控制台敲 fetch"是一回事。
    """

    #: 页面内脚本是否已注入
    READY_JS = "typeof window.__AWBAPI__ !== 'undefined' && window.__AWBAPI__.v === 1"

    def __init__(self, cdp, log=None):
        self.cdp = cdp
        self._log = log or (lambda *a: None)
        self._injected = False

    # ------------------------------------------------------------------
    def ensure(self):
        """注入页面脚本（幂等）。"""
        if self._injected:
            return True
        try:
            if not self.cdp.evaluate(self.READY_JS, await_promise=False):
                self.cdp.evaluate(JS_HELPERS, await_promise=False)
            self._injected = bool(self.cdp.evaluate(self.READY_JS, await_promise=False))
        except Exception as exc:
            raise ApiUnavailable(f"注入页面脚本失败：{exc}")
        if not self._injected:
            raise ApiUnavailable("页面脚本未能注入（页面可能还没加载完）")
        return True

    def token_len(self):
        try:
            return int(self.cdp.evaluate("window.__AWBAPI__.tokenLen()",
                                         await_promise=False) or 0)
        except Exception:
            return 0

    def probe(self):
        """返回 {tokenLen, pow:{...}} 或抛 ApiUnavailable。"""
        self.ensure()
        try:
            raw = self.cdp.evaluate("window.__AWBAPI__.probe()", await_promise=True,
                                    timeout=40)
            return json.loads(raw or "{}")
        except Exception as exc:
            raise ApiUnavailable(f"接口探测失败：{exc}")

    # ------------------------------------------------------------------
    def chat(self, prompt, session_id="", new_session=False, thinking=False,
             search=False, on_progress=None, timeout=300.0):
        """发一段提示词并等生成结束，返回 (answer, reasoning, session_id)。

        `on_progress(delta, reasoning_delta)` 的语义与老链路完全一致。
        """
        self.ensure()
        if self.token_len() <= 0:
            raise ApiUnavailable("not-logged-in（网页未登录）")
        opts = {
            "sessionId": session_id or "",
            "newSession": bool(new_session or not session_id),
            "thinking": bool(thinking),
            "search": bool(search),
        }
        self.cdp.evaluate("window.__AWBAPI__.reset()", await_promise=False)
        # 不 await：先把请求打出去，Python 侧靠轮询 buf 取增量（=真流式）
        kick = ("window.__AWBAPI__.chat(%s, %s)"
                % (json.dumps(str(prompt), ensure_ascii=False),
                   json.dumps(opts, ensure_ascii=False)))
        self.cdp.evaluate("void " + kick, await_promise=False, timeout=20)

        deadline = time.time() + float(timeout)
        text_seen = 0
        reason_seen = 0
        answer = ""
        reasoning = ""
        sid = ""
        while time.time() < deadline:
            snap = self._snapshot()
            if snap is None:
                time.sleep(0.25)
                continue
            t = str(snap.get("text") or "")
            r = str(snap.get("reasoning") or "")
            sid = str(snap.get("sessionId") or "") or sid
            if len(t) > text_seen:
                if on_progress:
                    try:
                        on_progress(t[text_seen:], r[reason_seen:])
                    except Exception:
                        pass
                text_seen = len(t)
                reason_seen = len(r)
            elif len(r) > reason_seen:
                if on_progress:
                    try:
                        on_progress("", r[reason_seen:])
                    except Exception:
                        pass
                reason_seen = len(r)
            if snap.get("done"):
                answer, reasoning = t, r
                err = str(snap.get("error") or "")
                if err:
                    raise ApiUnavailable(err)
                break
            time.sleep(0.25)
        else:
            raise ApiUnavailable(f"直发通道 {int(timeout)} 秒没有生成结束")
        if not (answer or reasoning):
            raise ApiUnavailable("直发通道没有拿到任何正文")
        return answer, reasoning, sid

    def _snapshot(self):
        try:
            raw = self.cdp.evaluate("window.__AWBAPI__.snapshot()", await_promise=False)
            return json.loads(raw or "{}")
        except Exception:
            return None

    def stop(self):
        """尽力打断（页面内的 fetch 无法真正 abort，这里只标记）。"""
        try:
            self.cdp.evaluate(
                "window.__AWBAPI__.buf.done = true; window.__AWBAPI__.buf.error = 'aborted';",
                await_promise=False)
        except Exception:
            pass
