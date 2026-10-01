# -*- coding: utf-8 -*-
"""第二层：DeepSeek 网页驱动 + 状态机。

职责边界：只讲"DeepSeek 网页"这件事——探测登录态、把提示词填进输入框、点发送、
等生成结束、抓答复原文、新建对话、防重复发送。所有 JSON 解析与协议约束都在上层。

⚠️ 本文件是**最易碎**的一层：DeepSeek 一改版，选择器就会失效。
   全部 DOM 选择器都写成"多候选 + 兜底"，并标注 `TODO(真机核对)`，
   真机验证工具见 `tests/probe_cdp_state.py`。
"""
import json
import time

from . import errors
from .cdp_client import CDPClient, CDPError


class WebDriverError(Exception):
    """网页驱动的失败，携带统一的 WebErrorKind。"""

    def __init__(self, kind, message=""):
        self.kind = kind if isinstance(kind, errors.WebErrorKind) else errors.WebErrorKind.OTHER
        super().__init__(message or errors.humanize(self.kind))


# ---------------------------------------------------------------------------
# 注入到页面的 JS：安装 window.__AWB__（一组 DOM 操作函数）。
#
# 为什么用 contenteditable 兼容写法：DeepSeek 网页的输入框不是 <textarea>，
# 而是富文本编辑容器；直接赋 .value 无效、提交前校验也读不到内容。
# 这里同时兼容两种形态（HTMLTextAreaElement/InputElement 与 contenteditable）。
# TODO(真机核对)：下面所有选择器候选顺序需在已登录真机上用 probe_cdp_state.py 校准。
# ---------------------------------------------------------------------------
# 注入脚本版本：页面会常驻旧的 __AWB__，改过选择器后必须靠版本号强制重注入，
# 否则新代码不生效（真机踩过：只判断 'typeof __AWB__ === object' 会拿到旧脚本）。
AGENT_VERSION = 8

# ★ 版本号只写在这一处（AGENT_VERSION）。脚本里的 `__v` 由 agent_script() 注入，
#   绝不在 JS 里再写一份字面量 —— 否则 `_ensure_agent()` 的版本校验会永远失败
#   （页面常驻旧脚本，校验失效就会一直拿到改选择器之前的旧代码；v9.14.2 踩过一次）。
JS_AGENT_TEMPLATE = r"""
(function () {
  const AWB = { __v: __AWB_AGENT_VERSION__ };

  const visible = (el) => {
    if (!el) { return false; }
    const r = el.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) { return false; }
    const st = window.getComputedStyle(el);
    return st.visibility !== 'hidden' && st.display !== 'none' && st.opacity !== '0';
  };
  const queryAll = (sel) => { try { return Array.from(document.querySelectorAll(sel)); } catch (e) { return []; } };

  // 停止图标判定：DeepSeek 的"发送"是上箭头 <path>；"停止"是方形（<rect>）。
  // 真机核对(2026-10-01)：idle 时 div.ds-button--circle 的 svg 只有 <path>（箭头），无 <rect>。
  const isStopIcon = (el) => {
    try {
      const svg = el.querySelector ? el.querySelector('svg') : null;
      if (!svg) { return false; }
      return !!svg.querySelector('rect') && !svg.querySelector('path');
    } catch (e) { return false; }
  };
  // 禁用态判定（真机核对 2026-10-01）：
  //   输入框为空时，发送键 class 里确实带 `ds-button--disabled`，同时 opacity=0.4，
  //   而 aria-disabled 为 null、pointer-events 仍是 auto。
  //   只认 class 太脆（改版换个说法就失效），所以四条一起看：
  //     class / aria-disabled / DOM disabled / pointer-events / opacity。
  const hasDisabled = (el) => {
    try {
      if (!el) { return true; }
      const cls = (el.className || '').toString();
      if (cls.indexOf('disabled') >= 0) { return true; }
      if (el.getAttribute && el.getAttribute('aria-disabled') === 'true') { return true; }
      if (el.disabled === true) { return true; }
      const st = window.getComputedStyle(el);
      if (st && st.pointerEvents === 'none') { return true; }
      const op = parseFloat(st ? st.opacity : '1');
      if (!isNaN(op) && op < 0.6) { return true; }
      return false;
    } catch (e) { return false; }
  };

  // ↓↓↓ 输入框选择器候选（contenteditable 优先）
  // 真机核对(2026-10-01)：当前版本输入框是
  //   <textarea placeholder="给 DeepSeek 发送消息 ">（class 是 CSS Modules 哈希，会变，不可依赖）。
  //   所以优先用 placeholder 锚定，"contenteditable 富文本"作为兼容保留。 ↓↓↓
  AWB.findComposer = () => {
    const sels = [
      'textarea[placeholder*="发送消息"]',
      'textarea[placeholder*="DeepSeek"]',
      'textarea#chat-input',
      'div#chat-input[contenteditable="true"]',
      'div[contenteditable="true"][data-testid*="input"]',
      'div[contenteditable="true"]',
      'textarea[data-testid*="input"]',
      'textarea[placeholder]',
      'textarea',
    ];
    for (const s of sels) {
      const list = queryAll(s).filter(visible);
      if (list.length) { return list[list.length - 1]; }
    }
    return null;
  };

  AWB.composerText = () => {
    const el = AWB.findComposer();
    if (!el) { return ''; }
    const tag = (el.tagName || '').toUpperCase();
    if (tag === 'TEXTAREA' || tag === 'INPUT') { return el.value || ''; }
    return el.innerText || el.textContent || '';
  };

  AWB.setComposer = (text) => {
    const el = AWB.findComposer();
    if (!el) { return false; }
    try { el.focus(); } catch (e) {}
    const tag = (el.tagName || '').toUpperCase();
    if (tag === 'TEXTAREA' || tag === 'INPUT') {
      const proto = (tag === 'TEXTAREA')
        ? window.HTMLTextAreaElement.prototype
        : window.HTMLInputElement.prototype;
      const desc = Object.getOwnPropertyDescriptor(proto, 'value');
      if (desc && desc.set) { desc.set.call(el, String(text)); } else { el.value = String(text); }
      el.dispatchEvent(new Event('input', { bubbles: true }));
      el.dispatchEvent(new Event('change', { bubbles: true }));
    } else {
      // contenteditable 富文本
      el.innerHTML = '';
      const lines = String(text).split('\n');
      for (let i = 0; i < lines.length; i++) {
        if (i > 0) { el.appendChild(document.createElement('br')); }
        el.appendChild(document.createTextNode(lines[i]));
      }
      try {
        const sel = window.getSelection();
        const range = document.createRange();
        range.selectNodeContents(el);
        range.collapse(false);
        sel.removeAllRanges();
        sel.addRange(range);
      } catch (e) {}
      // React 必须收到 input 事件才会把值同步进内部状态
      try {
        el.dispatchEvent(new InputEvent('input', {
          bubbles: true, cancelable: true, inputType: 'insertText', data: String(text),
        }));
      } catch (e) {
        el.dispatchEvent(new Event('input', { bubbles: true }));
      }
      el.dispatchEvent(new Event('change', { bubbles: true }));
    }
    return true;
  };

  // ↓↓↓ 发送按钮选择器候选
  // 真机核对(2026-10-01)：发送键 = div.ds-button.ds-button--primary.ds-button--filled.ds-button--circle
  //   （图标是上箭头 <path>；输入为空时带 .ds-button--disabled）。
  //   ds-button--* 是设计系统类名（稳定），_xxxxxx 是 CSS Modules 哈希（会变，不可依赖）。
  // 兜底：找不到按钮就在输入框上按 Enter（DeepSeek 约定 Enter=发送）。 ↓↓↓
  AWB.findSend = () => {
    const circles = queryAll('div.ds-button--circle, div[class*="ds-button--circle"]')
      .filter(visible);
    for (const c of circles) {
      if (!hasDisabled(c) && !isStopIcon(c)) { return c; }
    }
    const sels = [
      'div[role="button"][aria-label*="发送"]',
      'button[aria-label*="发送"]',
      'div[role="button"][aria-label*="Send"]',
      'button[aria-label*="Send"]',
      '[data-testid="send-button"]',
      '[class*="send-btn"]',
      '[class*="sendButton"]',
      '[class*="send_btn"]',
    ];
    for (const s of sels) {
      const list = queryAll(s).filter(visible).filter((el) => !hasDisabled(el));
      if (list.length) { return list[list.length - 1]; }
    }
    // ★ 绝不兜底返回"禁用态"按钮。
    //   v9.14.1 及以前这里是 `return circles[circles.length - 1];  // 即使 disabled 也点一下`，
    //   结果是：`.click()` 打在禁用按钮上 = 空点击，但 clickSend() 照样报成功
    //   -> 日志写「已提交提示词」-> 网页其实没发 -> 12 秒后报超时。
    //   （用户现象：字留在网页输入框里，一直没发出去。）返回 null 让调用方去等就绪 / 换真键盘。
    return null;
  };

  // 发送键当前是否可用（React 是异步渲染：setComposer 之后按钮的 disabled
  // 不会立刻消失，必须等它变回可用再点，否则就是上面说的空点击）。
  AWB.sendState = () => {
    if (!AWB.findComposer()) { return 'no-composer'; }
    const btn = AWB.findSend();
    if (btn) { return 'ready'; }
    const circles = queryAll('div.ds-button--circle, div[class*="ds-button--circle"]')
      .filter(visible);
    return circles.length ? 'disabled' : 'missing';
  };

  AWB.clickSend = () => {
    if (!AWB.findComposer()) { return 'no-composer'; }
    const btn = AWB.findSend();
    if (!btn) { return 'disabled'; }
    if (hasDisabled(btn)) { return 'disabled'; }
    try {
      btn.click();
      return 'button';
    } catch (e) {
      return 'click-error';
    }
  };

  // 给输入框抢焦点（Python 侧用 CDP 发"真键盘 Enter"之前必须先聚焦）
  AWB.focusComposer = () => {
    const el = AWB.findComposer();
    if (!el) { return false; }
    try { el.focus(); } catch (e) {}
    try { el.click(); } catch (e) {}
    try { return document.activeElement === el; } catch (e) { return false; }
  };

  // ↓↓↓ 停止按钮选择器候选（用于判断"正在生成"）
  // 策略：① 明确的停止按钮（aria-label/data-testid/class 含 stop）；
  //       ② 生成中发送位变成"停止"——圆形按钮里换成方形图标（<rect>）。
  // TODO(真机核对)：isStopIcon 的"有 <rect>"推断需在"生成中"观测确认；
  //                 若停止图标仍是 <path>，本判定会失真。 ↓↓↓
  AWB.findStop = () => {
    const sels = [
      'div[aria-label*="停止"]',
      'button[aria-label*="停止"]',
      'div[aria-label*="Stop"]',
      'button[aria-label*="Stop"]',
      '[data-testid="stop-button"]',
      '[data-testid*="stop"]',
      '[class*="stop-button"]',
      '[class*="stopButton"]',
      '[class*="stop_btn"]',
    ];
    for (const s of sels) {
      const list = queryAll(s).filter(visible);
      if (list.length) { return list[list.length - 1]; }
    }
    const circles = queryAll('div.ds-button--circle, div[class*="ds-button--circle"]')
      .filter(visible);
    for (const c of circles) {
      if (isStopIcon(c)) { return c; }
    }
    return null;
  };

  AWB.isGenerating = () => !!AWB.findStop();

  AWB.stopGeneration = () => {
    const b = AWB.findStop();
    if (b) { try { b.click(); return true; } catch (e) {} }
    return false;
  };

  // ↓↓↓ 答复容器选择器候选（取"最后一条助手消息正文"）
  // 真机核对(2026-10-01)：有真实回答时，助手正文容器 class 是
  //   div.ds-markdown.ds-assistant-message-main-content（末条 = 最新答复）；
  //   段落级子元素是 *.ds-markdown-paragraph（必须排除，否则只会取到其中一段）。
  //   ⚠️ 页面底部的「智能搜索」是 ds-toggle-button（**不是**答复）；
  //   旧的结构兜底会把它当答复返回（"极短提示"那轮实测拿到 '智能搜索'），
  //   故这里：① 优先精确类名；② 结构兜底显式排除控件与 UI 文案。 ↓↓↓
  // DeepSeek 页面底部的免责声明。真机实测：整页 innerText 里确实有这句
  //   （tests/probe_answer_purity.py -> pageHasDisclaimer=True）。
  //   一旦它被卷进"答复正文"，模型会把这句声明当成回答，所以统一剥掉。
  const DISCLAIMER = '内容由 AI 生成，请仔细甄别';
  const stripDisclaimer = (t) => {
    let s = String(t || '');
    if (s.trim() === DISCLAIMER) { return ''; }        // 整段就是声明 -> 当成空
    const i = s.lastIndexOf(DISCLAIMER);
    if (i >= 0 && (s.length - (i + DISCLAIMER.length)) <= 12) {
      s = s.slice(0, i);                               // 结尾挂着声明 -> 去掉
    }
    return s.trim();
  };
  // 单行 UI 文案（侧栏/开关/按钮）。注意必须**逐行**判断：
  //   真机实测，兜底路径取到的是 `'深度思考\n智能搜索'`（多行拼接），
  //   老的 `^(…)$` 整段匹配对多行不生效，于是把开关文案当成了答复正文。
  const UI_NOISE_LINE = /^(智能搜索|深度思考|联网搜索|开启新对话|新对话|收起|展开|复制|重新生成|停止生成|发送消息|按 ?Enter 发送|Shift ?\+ ?Enter)$/;
  // 整段**每一行**都是 UI 文案（或免责声明）才算噪声。
  const isUiNoise = (t) => {
    const lines = String(t || '').split('\n').map((x) => x.trim()).filter((x) => x);
    if (!lines.length) { return true; }
    return lines.every((ln) => UI_NOISE_LINE.test(ln) || ln === DISCLAIMER);
  };
  const UI_NOISE = { test: isUiNoise };     // 兼容旧引用

  AWB.answerText = () => {
    const sels = [
      '.ds-assistant-message-main-content',
      'div.ds-markdown',
      '[class*="ds-assistant-message-main-content"]',
      '[class*="message-content"]',
    ];
    const good = (el) => {
      const t = stripDisclaimer(el.innerText || '');
      return t.length > 0 && !isUiNoise(t);
    };
    for (const s of sels) {
      const list = queryAll(s).filter(visible)
        .filter((el) => String(el.className || '').indexOf('-paragraph') < 0)
        .filter(good);
      if (list.length) {
        return stripDisclaimer(list[list.length - 1].innerText || '');
      }
    }
    // 结构兜底：找主对话滚动容器（排除左侧会话栏），取它最后一块"像答复"的文本块；
    // 严格排除 button/[role=button]/toggle 等控件以及已知 UI 文案
    try {
      const scrollers = queryAll('div[class*="scroll-area"], div[class*="scrollArea"]')
        .filter((el) => visible(el) && el.scrollHeight > el.clientHeight + 40)
        .filter((el) => { const r = el.getBoundingClientRect(); return r.left > 180; })
        .sort((a, b) => (b.scrollHeight - a.scrollHeight));
      const main = scrollers[0];
      if (main) {
        const blocks = [...main.querySelectorAll('div,p,article,li')]
          .filter((el) => !el.closest('button, a, [role="button"], [class*="toggle"]'))
          .filter((el) => {
            const t = stripDisclaimer(el.innerText || '');
            return t.length > 0 && !isUiNoise(t);
          });
        if (blocks.length) {
          const last = blocks[blocks.length - 1];
          return stripDisclaimer(last.innerText || '');
        }
      }
    } catch (e) {}
    return '';
  };

  // 助手答复"块数量"：用于判断"是否真的新增了一条答复"。
  // 比文本比较更可靠 —— 可覆盖"同一问题两次得到同样文字"（文本相等但确实新增了答复）。
  AWB.answerCount = () => {
    const good = (el) => {
      if (String(el.className || '').indexOf('-paragraph') >= 0) { return false; }
      const t = stripDisclaimer(el.innerText || '');
      return t.length > 0 && !isUiNoise(t);
    };
    let list = queryAll('.ds-assistant-message-main-content').filter(visible).filter(good);
    if (!list.length) {
      list = queryAll('div.ds-markdown').filter(visible).filter(good);
    }
    return list.length;
  };

  // 输入框**是否存在**（注意：不能拿"composerText() 为空串"当判据 ——
  //  输入框不存在时它同样返回空串，会被误当成"发送后输入框被清空"= 已发送）。
  AWB.hasComposer = () => !!AWB.findComposer();
  // 当前地址（新建对话会让它变成新的 /a/chat/s/<id>，可作为"确实发出去了"的旁证之一）
  AWB.pageHref = () => String(location.href || '');
  // 页面可见性。真机实测(2026-10-01)：窗口被最小化/藏到屏幕外时这里是 'hidden'，
  // 此时输入框照样能写进字，但**点发送键和真键盘 Enter 都不生效**
  // —— 正是用户报的"字在输入框里、没发送、最后报超时"。
  AWB.visibility = () => String(document.visibilityState || 'unknown');

  // ---- 网页自身错误态识别（用于"快速失败"，见 04-qa §6.2）----
  // 真机实测(2026-10-01， QA 04-qa §4.1)：「服务器暂时不可用」「已停止」曾出现在 DeepSeek 自身横幅里。
  // TODO(真机核对)：ERR_STRONG 里除「服务器暂时不可用」外的候选均为**推断**，待真机复核（改版时补）。
  const ERR_STRONG = [
    '服务器暂时不可用',   // ← 实测
    '服务器繁忙',         // 推断
    '服务器开小差',       // 推断
    '服务器不可用',       // 推断
    '服务暂时不可用',     // 推断
    '服务已停止',         // 推断
    '服务异常',           // 推断
    '服务器错误',         // 推断
    '系统繁忙',           // 推断
    '请求失败',           // 推断
    '网络异常',           // 推断
  ];
  // TODO(真机核对)：这些太通用，只敢在"明确的错误/提示类容器"里用（全局扫会误判）。
  const ERR_WEAK = ['已停止', '出错了', '暂时无法', '稍后重试', '请稍后再试'];

  const _hit = (t, pats) => { for (const p of pats) { if (t.indexOf(p) >= 0) { return p; } } return ''; };

  AWB.serverError = () => {
    // ① 明确的错误/提示类容器
    const boxes = queryAll('[class*="error"],[class*="toast"],[class*="banner"],' +
      '[class*="alert"],[class*="notice"],[class*="tip"],[class*="modal"],[role="alert"]')
      .filter(visible);
    for (const el of boxes) {
      const t = (el.innerText || '').trim();
      if (!t) { continue; }
      if (_hit(t, ERR_STRONG.concat(ERR_WEAK))) { return t.slice(0, 140); }
    }
    // ② 短文本兜底（横幅类通常很短；用 textContent 廉价预筛，避开长答复/用户消息/输入框）
    for (const el of queryAll('div,span,p')) {
      const c = (el.textContent || '').trim();
      if (!c || c.length > 40) { continue; }
      if (!_hit(c, ERR_STRONG)) { continue; }
      if (el.closest('[class*="markdown"],textarea') || el.isContentEditable) { continue; }
      if (!visible(el)) { continue; }
      return c.slice(0, 140);
    }
    return '';
  };

  // 页面正文总长度：作为"是否有任何活动（含思考流式）"的粗信号，用于零增量兜底。
  AWB.pageTextLen = () => ((document.body && document.body.innerText) || '').length;

  AWB.loginHint = () => {
    const t = ((document.body && document.body.innerText) || '');
    const hit = /登录|log ?in|sign ?in|验证码|扫码|手机号|邮箱/i.test(t);
    return hit && !AWB.findComposer();
  };

  // ↓↓↓ 新对话入口选择器候选
  // 真机核对(2026-10-01)：页面里有文本「开启新对话」。优先按 aria/label，再按文本兜底。
  // TODO(真机核对)：文本兜底未真机点击验证。 ↓↓↓
  AWB.newChat = () => {
    const sels = [
      'div[role="button"][aria-label*="新对话"]',
      'button[aria-label*="新对话"]',
      'div[role="button"][aria-label*="新建"]',
      '[data-testid="new-chat"]',
      '[class*="new-chat"]',
    ];
    for (const s of sels) {
      const list = queryAll(s).filter(visible);
      if (list.length) { try { list[0].click(); return true; } catch (e) {} }
    }
    // 文本兜底：找可点击祖先里含"开启新对话 / 新对话"的元素
    const clickable = queryAll('div[role="button"], button, a');
    for (const el of clickable) {
      if (!visible(el)) { continue; }
      if (/开启新对话|新对话|新建对话/.test((el.innerText || '').trim())) {
        try { el.click(); return true; } catch (e) {}
      }
    }
    return false;
  };

  // 页面级防重发标记（对齐 00-context 2.3-4：已提交未拿结果被刷新 -> 只报错不重发）
  AWB.setPending = (fp) => {
    try {
      sessionStorage.setItem('awb_pending', String(fp));
      sessionStorage.setItem('awb_pending_at', String(Date.now()));
    } catch (e) {}
    return true;
  };
  AWB.getPending = () => { try { return sessionStorage.getItem('awb_pending') || ''; } catch (e) { return ''; } };
  AWB.pendingAge = () => {
    try { return Date.now() - Number(sessionStorage.getItem('awb_pending_at') || 0); }
    catch (e) { return 1e12; }
  };
  AWB.clearPending = () => {
    try {
      sessionStorage.removeItem('awb_pending');
      sessionStorage.removeItem('awb_pending_at');
    } catch (e) {}
    return true;
  };

  AWB.pageState = () => ({
    url: location.href,
    title: document.title,
    ready: document.readyState,
    composer: !!AWB.findComposer(),
    generating: AWB.isGenerating(),
    loginHint: AWB.loginHint(),
    loggedOut: /sign_in|\/login|\/signin/i.test(location.href),
    bodyLen: (document.body ? (document.body.innerText || '').length : 0),
  });

  window.__AWB__ = AWB;
  return true;
})()
"""


def agent_script():
    """把 AGENT_VERSION 注入页面脚本（单一来源；改版本号只改上面那一行）。"""
    return JS_AGENT_TEMPLATE.replace("__AWB_AGENT_VERSION__", str(int(AGENT_VERSION)))


# 兼容别名：老代码/测试里直接引用 JS_AGENT 的地方继续可用。
JS_AGENT = agent_script()

JS_STATE = "JSON.stringify(window.__AWB__ ? window.__AWB__.pageState() : {missing:true})"

# 结束判定：答复文本看起来是一段"完整的 JSON 对象"（对齐 2.3-3 的规则 b）
def _looks_complete(text):
    """文本是否"看起来已经完整"（当前只认完整 JSON 对象，可扩展）。"""
    t = (text or "").strip()
    if not t:
        return False
    if t.startswith("{") and t.endswith("}"):
        try:
            json.loads(t)
            return True
        except Exception:
            return False
    return False


class WebDriver:
    """DeepSeek 网页的状态机与操作入口。"""

    # 相邻两次答复文本"无变化"多少秒后，视为生成已停（对齐 2.3-3 的规则 a）
    QUIET_SECONDS = 0.8

    # 网页自身错误态的"确认时长"：错误文案需连续出现这么久才判失败，
    # 避免瞬时 toast 误判（真机横幅会停留数秒，3s 相对 300s 已算"立即"）。
    ERROR_CONFIRM_SECONDS = 3.0

    # 零增量兜底：连续这么久"页面没有任何活动"（答复文本 / 答复块数 / 页面正文长度都不变）
    # 就提前失败，不再空等到 answer_timeout（见 04-qa §6.2）。
    NO_OUTPUT_SECONDS = 60.0

    # 点了发送之后，留给"发送键由禁用变可用"的时间上限（秒）。
    # React 状态更新是异步的，实测 setComposer 之后按钮会先保持禁用一小会儿。
    SEND_READY_SECONDS = 3.0

    # 触发发送后，验证"网页真的接受了"的时间上限（秒）。
    # 超时未确认 -> 判定这次触发没生效 -> 才允许用真键盘 Enter 补一次（最多补一次）。
    SEND_CONFIRM_SECONDS = 5.0

    # 页面级防重发标记的"过期时间"（毫秒）。超过则视为陈旧标记，允许重新发送，
    # 避免页面被刷新/异常中断后永远卡在"重复请求"。
    STALE_PENDING_MS = 900_000        # 15 分钟

    def __init__(self, cdp=None, log=None):
        """
        Args:
            cdp: 一个已连接的 CDPClient，或 None（未启动）。
            log: 可选日志回调 log(level, msg)。
        """
        self.cdp = cdp
        self._log = log or (lambda *a, **k: None)
        self._agent_ready = False

    # ---------- 内部工具 ----------
    def _logf(self, level, msg):
        try:
            self._log(level, msg)
        except Exception:
            pass

    def _ensure_agent(self):
        """确保页面里已注入**正确版本**的 window.__AWB__；返回是否可用。

        页面会常驻旧的注入脚本，所以必须校验版本号（`__AWB__.__v`）：
        只判断 `typeof __AWB__ === 'object'` 会一直拿到改选择器之前的旧代码。
        """
        if self.cdp is None:
            return False
        check = "!!(window.__AWB__ && window.__AWB__.__v === %d)" % int(AGENT_VERSION)
        if self._agent_ready:
            try:
                if self.cdp.evaluate(check, await_promise=False):
                    return True
                self._agent_ready = False
            except Exception:
                self._agent_ready = False
        try:
            self.cdp.evaluate(agent_script(), await_promise=False)
        except CDPError as exc:
            self._logf("warn", f"注入脚本失败：{exc}")
            return False
        except Exception as exc:
            self._logf("warn", f"注入脚本异常：{exc}")
            return False
        try:
            ok = bool(self.cdp.evaluate(check, await_promise=False))
        except Exception:
            ok = False
        self._agent_ready = ok
        return ok

    def _js(self, expression):
        """执行一段 JS 并取回值（表达式里通常用到 window.__AWB__）。"""
        if not self._ensure_agent():
            raise WebDriverError(errors.WebErrorKind.NOT_STARTED, "网页脚本环境不可用")
        try:
            return self.cdp.evaluate(expression, await_promise=False)
        except CDPError as exc:
            raise WebDriverError(errors.WebErrorKind.PAGE_CHANGED, str(exc))

    def _read_state(self):
        raw = self._js(JS_STATE)
        if not raw:
            return {}
        if isinstance(raw, dict):
            return raw
        try:
            return json.loads(raw)
        except Exception:
            return {}

    # ---------- 状态探测 ----------
    def probe_state(self):
        """返回 (WebErrorKind, 中文说明)。"""
        if self.cdp is None:
            return errors.WebErrorKind.NOT_STARTED, errors.WEB_ERROR_TEXT[errors.WebErrorKind.NOT_STARTED]
        try:
            alive = self.cdp.is_alive()
        except Exception:
            alive = True
        if not alive:
            return errors.WebErrorKind.NOT_STARTED, "调试连接已断开，请点「重试」重新连接。"
        if not self._ensure_agent():
            return (errors.WebErrorKind.NOT_STARTED,
                    "无法连接 DeepSeek 网页（调试端口不可用或网页未打开）。")
        try:
            st = self._read_state()
        except WebDriverError as exc:
            return exc.kind, str(exc)
        if not st or st.get("missing"):
            return (errors.WebErrorKind.PAGE_CHANGED,
                    "无法识别网页结构（页面可能已改版）。")
        if st.get("loggedOut") or (st.get("loginHint") and not st.get("composer")):
            return (errors.WebErrorKind.NOT_LOGGED_IN,
                    errors.WEB_ERROR_TEXT[errors.WebErrorKind.NOT_LOGGED_IN])
        if st.get("composer"):
            if st.get("generating"):
                return errors.WebErrorKind.GENERATING, "网页正在生成回复"
            # 窗口不可见时"看起来就绪"，实际发不出去 —— 单独报出来（v9.14.2）
            try:
                if str(self._js("window.__AWB__.visibility()")) != "visible":
                    return (errors.WebErrorKind.WINDOW_HIDDEN,
                            errors.WEB_ERROR_TEXT[errors.WebErrorKind.WINDOW_HIDDEN])
            except WebDriverError:
                pass
            return errors.WebErrorKind.READY, "已连接，可以对话"
        return (errors.WebErrorKind.PAGE_CHANGED,
                "找不到网页输入框（网页结构可能已变）。")

    # ---------- 导航 / 新对话 / 停止 ----------
    def open_page(self, url="https://chat.deepseek.com/", wait=20.0):
        """导航到 DeepSeek 网页并等待就绪。"""
        if self.cdp is None:
            raise WebDriverError(errors.WebErrorKind.NOT_STARTED)
        self._agent_ready = False
        try:
            self.cdp.navigate(url, timeout=20)
        except CDPError as exc:
            raise WebDriverError(errors.WebErrorKind.NOT_STARTED, str(exc))
        deadline = time.time() + float(wait)
        while time.time() < deadline:
            try:
                if self._ensure_agent():
                    st = self._read_state()
                    if st.get("composer") or st.get("loginHint") or st.get("loggedOut"):
                        return
            except Exception:
                pass
            time.sleep(0.6)
        # 超时也不算致命：可能是登录页，交给 probe_state 判定

    def new_conversation(self):
        """点「新对话」；找不到入口则退化为重新导航到首页。"""
        if self.cdp is None:
            raise WebDriverError(errors.WebErrorKind.NOT_STARTED)
        try:
            done = self._js("window.__AWB__.newChat()")
        except WebDriverError:
            done = False
        if done:
            self._logf("info", "已点击「新对话」")
            time.sleep(0.8)
            return
        # 兜底：直接回到首页（登录态持久化，不会掉登录）
        self._logf("warn", "找不到「新对话」入口，改用重新导航到首页")
        try:
            self.open_page("https://chat.deepseek.com/", wait=15.0)
        except WebDriverError as exc:
            raise WebDriverError(errors.WebErrorKind.PAGE_CHANGED,
                                  f"无法新建网页对话：{exc}")

    def stop_generation(self):
        """点「停止生成」（失败静默）。"""
        if self.cdp is None:
            return
        try:
            self._js("window.__AWB__.stopGeneration()")
        except Exception:
            pass

    # ---------- 发送链路的"真确认"工具（v9.14.2）----------
    def ensure_interactive(self, wait=3.0):
        """确保网页**可见/可交互**（不可见时发送 100% 失效）。

        真机实测(2026-10-01)：窗口被最小化或藏到屏幕外时
        `document.visibilityState === 'hidden'`，此时：
          - 输入框照样能写进字（composerText 正常）；
          - 输入框照样"有焦点"（activeElement = TEXTAREA）；
          - 但 **点发送键、发真键盘 Enter 都不生效** —— 字一直留在框里。
        => 这正是用户报的「字放到输入框里、并没有发送、然后报超时」。

        这里先尝试自救（Page.bringToFront + 恢复窗口到屏幕内），
        仍不可见就返回 False，由调用方**提前报错**（而不是白等 60 秒）。

        Returns: True 表示当前可见（可交互）。
        """
        if self.cdp is None:
            return False
        # ① 让标签页到前台
        try:
            self.cdp.call("Page.bringToFront", {}, timeout=6)
        except Exception:
            pass
        # ② 把窗口从"最小化/离屏"恢复成普通窗口并摆到屏幕内
        try:
            res = self.cdp.call("Browser.getWindowForTarget", {}, timeout=6)
            wid = (res.get("result") or {}).get("windowId")
            if wid:
                self.cdp.call("Browser.setWindowBounds", {
                    "windowId": wid, "bounds": {"windowState": "normal"}}, timeout=6)
                self.cdp.call("Browser.setWindowBounds", {
                    "windowId": wid,
                    "bounds": {"left": 60, "top": 50, "width": 1180, "height": 840}},
                    timeout=6)
        except Exception:
            pass
        # ③ 轮询可见性
        deadline = time.time() + float(wait)
        while time.time() < deadline:
            try:
                if str(self._js("window.__AWB__.visibility()")) == "visible":
                    return True
            except WebDriverError:
                pass
            time.sleep(0.25)
        try:
            self._logf("warn", "网页窗口不可见（visibilityState="
                               f"{self._js('window.__AWB__.visibility()')}），"
                               "发送会被浏览器丢弃")
        except Exception:
            pass
        return False

    def _confirm_sent(self, seconds, *, baseline_count=0, baseline_text="", baseline_href=""):
        """网页是否**真的**接受了这次提问（秒级轮询）。

        任一成立即确认：
          ① 出现"停止生成"按钮（正在生成）；
          ② 答复块数量 > 提交前基线；
          ③ 答复文本 ≠ 提交前基线；
          ④ 输入框**存在且**被清空（提交后网页会清空输入框）；
          ⑤ 地址变成了另一个 /a/chat/s/<id>（新建对话 = 这一问确实发出去了）。

        ★ ④ 必须同时要求"输入框存在"：
          输入框**不存在**时 composerText() 也返回空串，若只看空串就会把
          "页面正在导航/结构变化"误判成"已发送"——那就又变成假成功了。

        全不成立 = 这次触发**没生效**。此时才允许补发，绝不会造成重复提问。
        """
        base_cnt = int(baseline_count or 0)
        base_txt = str(baseline_text or "")
        base_href = str(baseline_href or "")
        t0 = time.time()
        while time.time() - t0 < float(seconds):
            try:
                st = self._read_state()
                if st.get("generating"):
                    return True
                try:
                    cnt = int(self._js("window.__AWB__.answerCount()") or 0)
                except WebDriverError:
                    cnt = base_cnt
                if cnt > base_cnt:
                    return True
                try:
                    txt = str(self._js("window.__AWB__.answerText()") or "")
                except WebDriverError:
                    txt = base_txt
                if txt != base_txt:
                    return True
                try:
                    has_composer = bool(self._js("window.__AWB__.hasComposer()"))
                except WebDriverError:
                    has_composer = False
                if has_composer:
                    left = self._js("window.__AWB__.composerText()") or ""
                    if not str(left).strip():
                        return True
                if base_href:
                    try:
                        href = str(self._js("window.__AWB__.pageHref()") or "")
                    except WebDriverError:
                        href = base_href
                    if href and href != base_href and "/a/chat/s/" in href:
                        return True
            except WebDriverError:
                pass
            time.sleep(0.3)
        return False

    def _press_enter_real(self):
        """用 CDP 的 Input 域发**真实** Enter 键（isTrusted=True）。

        为什么不能只在 JS 里 `dispatchEvent(new KeyboardEvent('keydown'))`：
        合成事件的 `isTrusted === false`，DeepSeek 的 React onKeyDown 完全可以忽略它
        （旧版就吃了这个亏，日志里那唯一一次 `发送方式=enter` 很可能就是空炮）。
        CDP 的 Input.dispatchKeyEvent 走浏览器真实输入管线，等价于人手按键。

        安全性：本方法只在"确认上一次触发完全没生效"之后才被调用（见 `submit`）。
        """
        if self.cdp is None:
            return False
        try:
            focused = self._js("window.__AWB__.focusComposer()")
            self._logf("info", f"真键盘 Enter 前置：输入框已聚焦={bool(focused)}")
        except WebDriverError:
            pass
        common = {"windowsVirtualKeyCode": 13, "nativeVirtualKeyCode": 13,
                  "key": "Enter", "code": "Enter"}
        try:
            # keyDown 带 text="\r"：DeepSeek 收到后即按"Enter=发送"处理
            self.cdp.call("Input.dispatchKeyEvent",
                          dict(common, type="keyDown", text="\r",
                               unmodifiedText="\r", autoRepeat=False,
                               isKeypad=False, isSystemKey=False), timeout=8)
            self.cdp.call("Input.dispatchKeyEvent",
                          dict(common, type="keyUp", autoRepeat=False,
                               isKeypad=False, isSystemKey=False), timeout=8)
            return True
        except CDPError as exc:
            self._logf("warn", f"真键盘 Enter 发送失败：{exc}")
            return False

    # ---------- 主流程：提交一轮 ----------
    def submit(self, prompt_text, *, on_progress=None,
               ack_timeout=12.0, answer_timeout=300.0, dedupe_key=None):
        """把一段提示词发给网页，等生成结束，返回答复原文。

        Args:
            prompt_text: 要发送的提示词（单段文本）。
            on_progress: 可选回调 on_progress(delta, reasoning)。
            ack_timeout: 提交后等待"网页确认收到"的上限（秒）。超时判 TIMEOUT 且**不重发**。
            answer_timeout: 单轮生成上限（秒）。
            dedupe_key: 幂等指纹；用于页面级防重发。

        Returns:
            答复原文（str）。

        Raises:
            WebDriverError: 携带 WebErrorKind。
        """
        if self.cdp is None:
            raise WebDriverError(errors.WebErrorKind.NOT_STARTED)
        if not self._ensure_agent():
            raise WebDriverError(errors.WebErrorKind.PAGE_CHANGED, "无法注入网页脚本")

        # ⓪ 可见性闸门：窗口被最小化/藏到屏幕外时，发出去的字会被浏览器直接丢掉。
        #    提前判死并给出可操作的中文提示，而不是填完字白等 60 秒。
        if not self.ensure_interactive():
            raise WebDriverError(errors.WebErrorKind.WINDOW_HIDDEN)

        # ① 防重发：同一指纹若已在页面标记里（且标记未过期）-> 直接报错，绝不重发（2.3-4）
        if dedupe_key:
            try:
                pend = self._js("window.__AWB__.getPending()")
                age = self._js("window.__AWB__.pendingAge()")
            except WebDriverError:
                pend, age = "", 0
            try:
                age = float(age or 0)
            except Exception:
                age = 0.0
            if pend and str(pend) == str(dedupe_key) and age < self.STALE_PENDING_MS:
                raise WebDriverError(errors.WebErrorKind.DUPLICATE)
            self._js("window.__AWB__.setPending(%s)" % json.dumps(str(dedupe_key)))

        # ①.5 记录"提交前的答复基线"：提交后必须出现**与基线不同**的新内容才算答复。
        #      真机实测踩坑：提交后网页还没开始生成时，页面上已有的 UI 文案
        #      （如「智能搜索」）会被误当答复提前返回 —— 用基线把这种情况挡掉。
        try:
            baseline_text = str(self._js("window.__AWB__.answerText()") or "")
        except Exception:
            baseline_text = ""
        try:
            baseline_count = int(self._js("window.__AWB__.answerCount()") or 0)
        except Exception:
            baseline_count = 0
        try:
            baseline_href = str(self._js("window.__AWB__.pageHref()") or "")
        except Exception:
            baseline_href = ""

        # ② 填输入框（contenteditable / textarea 双兼容）
        ok = self._js("window.__AWB__.setComposer(%s)" % json.dumps(str(prompt_text)))
        if not ok:
            raise WebDriverError(errors.WebErrorKind.PAGE_CHANGED, "找不到网页输入框")
        now = self._js("window.__AWB__.composerText()") or ""
        if not str(now).strip():
            # 提示词没进去 = 判死（2.3-1）
            raise WebDriverError(errors.WebErrorKind.PAGE_CHANGED,
                                  "提示词未能写入网页输入框（网页结构可能已变）")

        # ③ 等发送键"真正可用"再点。
        #    React 是异步渲染：刚 setComposer 完，发送键的 disabled 还没消失
        #    （实测：空输入框时按钮 opacity=0.4 + class 带 ds-button--disabled）。
        #    这时候点它 = 空点击。旧版（v9.14.1）没等这一步，于是出现
        #    「字在输入框里、网页没发出去、12 秒后报超时」。
        base_cnt = int(baseline_count or 0)
        send_state = "missing"
        deadline = time.time() + float(self.SEND_READY_SECONDS)
        while time.time() < deadline:
            try:
                send_state = str(self._js("window.__AWB__.sendState()") or "missing")
            except WebDriverError:
                send_state = "missing"
            if send_state == "ready":
                break
            time.sleep(0.2)
        if send_state == "no-composer":
            raise WebDriverError(errors.WebErrorKind.PAGE_CHANGED, "找不到网页输入框")

        # ③.1 只在按钮确实可用时才点（clickSend 已改成"禁用就不点"）
        mode = str(self._js("window.__AWB__.clickSend()") or "none")
        self._logf("info", f"点击发送键：mode={mode}，发送键状态={send_state}，"
                           f"{len(str(prompt_text))} 字")

        # ③.2 发送确认：真发出去了一定会出现
        #      「输入框被清空 / 出现停止键 / 多出答复块 / 答复文本变了」之一。
        #      全都没出现 -> 上一次触发**确定没生效**（不存在"其实发出去了"的可能），
        #      这时才用 CDP 真键盘事件补一次 Enter —— 所以不会造成重复提问。
        confirmed = self._confirm_sent(self.SEND_CONFIRM_SECONDS,
                                       baseline_count=base_cnt, baseline_text=baseline_text,
                                       baseline_href=baseline_href)
        if not confirmed:
            self._logf("warn", "点击发送未生效（网页没反应），改用真键盘 Enter 补发一次")
            if self._press_enter_real():
                confirmed = self._confirm_sent(self.SEND_CONFIRM_SECONDS,
                                               baseline_count=base_cnt,
                                               baseline_text=baseline_text,
                                               baseline_href=baseline_href)
        if not confirmed:
            # ★ 绝不假成功：如实上报"字还在输入框里"，并给用户可执行的出路。
            raise WebDriverError(
                errors.WebErrorKind.TIMEOUT,
                "没有发出去：文字已经填进 DeepSeek 网页的输入框，但网页没把它发送。"
                "可切到网页窗口手动按回车或点发送键，或点「重试」。（不会重复发送）")
        self._logf("info", "网页已确认收到本次提问")

        # ④ ack 兜底：确认过之后这里通常立刻通过，只作为最后一道保险。
        t0 = time.time()
        acked = False
        while time.time() - t0 < float(ack_timeout):
            st = self._read_state()
            if st.get("generating"):
                acked = True
                break
            left = self._js("window.__AWB__.composerText()") or ""
            if not str(left).strip():
                acked = True
                break
            time.sleep(0.4)
        if not acked:
            raise WebDriverError(
                errors.WebErrorKind.TIMEOUT,
                f"{int(ack_timeout)} 秒内未等到网页确认收到（已停止，不会重复发送）")

        # ⑤ 等生成结束
        return self._wait_answer(on_progress=on_progress, answer_timeout=float(answer_timeout),
                                 dedupe_key=dedupe_key, baseline=baseline_text,
                                 baseline_count=baseline_count)

    def _wait_answer(self, *, on_progress=None, answer_timeout=300.0, dedupe_key=None,
                     baseline="", baseline_count=0):
        """轮询答复文本 / 停止按钮 / 网页错误态，判定生成结束或**快速失败**。

        `baseline` / `baseline_count`：提交前页面上的答复文本与答复块数量。
        只有出现**新答复**时才认作结果：
          ① 文本 ≠ 基线；② 答复块数量 > 基线数量（覆盖"同问题两次同答案"）。

        快速失败（只报错、**绝不重发**；中文可读、无英文堆栈，见 04-qa §6.2）：
          A. 网页自身错误态文案（"服务器暂时不可用"等）在**页面空闲**时持续
             ERROR_CONFIRM_SECONDS 秒 -> SERVER_ERROR；
          B. 连续 NO_OUTPUT_SECONDS 秒页面毫无活动（答复文本 / 答复块数 / 正文长度都不变）
             -> TIMEOUT（提示已等待秒数）。
        两者都不再空等到 answer_timeout（原来是 300s）。

        TODO(真机核对)：结束判定的两条规则（停止按钮消失 + 文本 ≥0.8s 未变 /
        文本是完整 JSON）需在真机上、用长回复与"深度思考"场景标定，确认不会提前结束。
        """
        t0 = time.time()
        base = str(baseline or "")
        base_cnt = int(baseline_count or 0)
        last_text = base          # 初值设为基线：避免把"提交前已有的文本"当成新增量
        last_cnt = base_cnt
        last_text_change = time.time()   # 最后一次"答复文本变化"（用于 quiet 判结束）
        last_activity = time.time()      # 最后一次"页面任何活动"（用于零增量兜底）
        emitted = []              # 已下发的增量；用于收尾对账，确保绝不丢正文
        err_since = 0.0           # 网页错误文案在"空闲态"连续出现的起点（0 = 当前没看到）
        try:
            last_page_len = int(self._js("window.__AWB__.pageTextLen()") or 0)
        except Exception:
            last_page_len = 0

        while time.time() - t0 < answer_timeout:
            st = self._read_state()
            text = self._js("window.__AWB__.answerText()") or ""
            try:
                cnt = int(self._js("window.__AWB__.answerCount()") or 0)
            except Exception:
                cnt = 0
            try:
                page_len = int(self._js("window.__AWB__.pageTextLen()") or 0)
            except Exception:
                page_len = last_page_len

            # ---- 活动检测：答复文本 / 答复块数 / 页面正文长度 任一变化 = 有增量 ----
            if text != last_text:
                if isinstance(text, str) and isinstance(last_text, str) and text.startswith(last_text):
                    delta = text[len(last_text):]
                else:
                    delta = text if isinstance(text, str) else ""
                last_text = text if isinstance(text, str) else ""
                if on_progress and delta:
                    on_progress(delta, "")
                    emitted.append(delta)
                last_text_change = time.time()
                last_activity = time.time()
            if cnt != last_cnt:
                last_cnt = cnt
                last_activity = time.time()
            if page_len != last_page_len:
                last_page_len = page_len
                last_activity = time.time()

            # ---- 结束判定：出现新答复 且 停止按钮消失 且 文本 ≥0.8s 未变（或像完整 JSON）----
            stopped = not st.get("generating")
            quiet = (time.time() - last_text_change) >= self.QUIET_SECONDS
            cur = last_text.strip()
            fresh = (cur != base.strip()) or (cnt > base_cnt)
            if cur and fresh and stopped and (quiet or _looks_complete(last_text)):
                result = last_text.strip()
                # 收尾对账：若中途一个增量都没发出（例如答案文字与基线**相同**，感知不到变化），
                # 这里补发差额，保证客户端拿到的正文 == result —— 绝不出现"空回复"。
                if on_progress:
                    sent = "".join(emitted)
                    if result != sent:
                        tail = result[len(sent):] if (sent and result.startswith(sent)) else result
                        if tail:
                            on_progress(tail, "")
                            emitted.append(tail)
                if dedupe_key:
                    try:
                        self._js("window.__AWB__.clearPending()")
                    except Exception:
                        pass
                return result

            # ---- 快速失败 A：网页自身错误态（仅在"页面空闲"时才计时，避免误杀仍在输出的正规答复）----
            idle = (time.time() - last_activity) >= self.QUIET_SECONDS
            try:
                err = str(self._js("window.__AWB__.serverError()") or "")
            except Exception:
                err = ""
            if err and idle:
                if not err_since:
                    err_since = time.time()
                if (time.time() - err_since) >= self.ERROR_CONFIRM_SECONDS:
                    raise WebDriverError(
                        errors.WebErrorKind.SERVER_ERROR,
                        "网页显示：" + err.replace("\n", " "))
            else:
                err_since = 0.0

            # ---- 快速失败 B：零增量兜底（长时间毫无活动 -> 提前报错，不再空等）----
            if (time.time() - last_activity) >= self.NO_OUTPUT_SECONDS:
                raise WebDriverError(
                    errors.WebErrorKind.TIMEOUT,
                    f"已等待约 {int(self.NO_OUTPUT_SECONDS)} 秒，网页没有任何输出"
                    f"（已停止，不会重复发送）")

            time.sleep(0.35)

        # 生成超时：停止生成，报 TIMEOUT（不重发）
        self.stop_generation()
        raise WebDriverError(
            errors.WebErrorKind.TIMEOUT,
            f"网页 {int(answer_timeout)} 秒内仍未结束生成（已停止，不会重复发送）")

    def close(self):
        """释放 CDP 连接。"""
        if self.cdp is not None:
            self.cdp.close()
        self.cdp = None
        self._agent_ready = False

    def diagnose(self):
        """真机核对用：报告各选择器 / 错误态 / 活动信号的命中情况（不改变页面状态）。

        返回 dict：composer / composerEditable / send / stop / answerLen / answerCount /
        serverError / pageTextLen / pending / state。
        """
        js = r"""JSON.stringify((() => {
          const A = window.__AWB__;
          if (!A) { return {missing: true}; }
          const c = A.findComposer();
          return {
            composer: c ? c.tagName : null,
            composerEditable: c ? (c.getAttribute && c.getAttribute('contenteditable')) : null,
            send: !!A.findSend(),
            stop: !!A.isGenerating(),
            answerLen: (A.answerText() || '').length,
            answerCount: A.answerCount(),
            serverError: A.serverError(),
            pageTextLen: A.pageTextLen(),
            pending: A.getPending(),
            state: A.pageState(),
          };
        })())"""
        try:
            raw = self._js(js)
        except WebDriverError as exc:
            return {"error": str(exc), "kind": exc.kind.value}
        if isinstance(raw, dict):
            return raw
        try:
            return json.loads(raw or "{}")
        except Exception:
            return {}
