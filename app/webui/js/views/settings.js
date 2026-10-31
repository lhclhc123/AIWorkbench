// views/settings.js —— 设置页：5 个分组（通用/模型/语音/密钥/提示词），tab 切换 + 统一保存
'use strict';

import { rpc, on } from '../bridge.js';

const $ = (s, root) => (root || document).querySelector(s);

function toast(msg) {
  const el = $('#toast');
  el.textContent = msg;
  el.classList.add('show');
  clearTimeout(toast._t);
  toast._t = setTimeout(() => el.classList.remove('show'), 1900);
}

// 本视图自带的局部样式（scoped 到 settings 页内部），不影响其它视图
const STYLE = `
.vtabs{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:12px;flex:0 0 auto;}
.vtab{height:30px;padding:0 14px;border:1px solid var(--border);border-radius:8px;background:var(--surface);color:var(--text-2);font-size:12.5px;cursor:pointer;display:inline-flex;align-items:center;gap:4px;transition:background-color .15s,color .15s,border-color .15s;}
.vtab:hover{background:var(--row-hover);color:var(--text);}
.vtab.is-active{background:var(--accent);border-color:var(--accent);color:#fff;font-weight:600;}
.f-row{display:flex;align-items:center;gap:10px;padding:6px 0;flex-wrap:wrap;}
.f-label{flex:0 0 130px;font-size:12.5px;color:var(--text-2);}
.f-row .input{flex:1;min-width:200px;max-width:420px;}
.btn-row{display:flex;gap:8px;flex-wrap:wrap;align-items:center;}
.switch-row input[type=checkbox]{accent-color:var(--accent);width:16px;height:16px;flex:0 0 auto;}
`;

// 模块级状态：视图只 build 一次，之后每次 init 仅刷新数据，用模块变量保持 tab 选中态
let settings = {};
let enums = {};
let formLoaded = false;
let activeTab = 'general';

function setVal(root, sel, v) { const el = $(sel, root); if (el) el.value = (v == null ? '' : v); }
function setChk(root, sel, v) { const el = $(sel, root); if (el) el.checked = !!v; }
function setText(root, sel, v) { const el = $(sel, root); if (el) el.textContent = (v == null ? '' : v); }

function build(root) {
  root.innerHTML = `
    <style>${STYLE}</style>
    <div class="page-head">
      <h2>设置</h2>
      <span class="page-hint" id="setHint"></span>
      <span class="spacer"></span>
      <button class="btn btn-primary" id="setSave">保存设置</button>
    </div>

    <div class="vtabs" role="tablist">
      <button class="vtab is-active" data-tab="general">通用</button>
      <button class="vtab" data-tab="model">模型</button>
      <button class="vtab" data-tab="voice">语音</button>
      <button class="vtab" data-tab="keys">密钥</button>
      <button class="vtab" data-tab="prompt">提示词</button>
    </div>

    <div class="vpanel" data-panel="general">
      <div class="card">
        <div class="card-title">通用</div>
        <div class="f-row">
          <span class="f-label">主题</span>
          <select class="input" id="set_theme">
            <option value="light">浅色</option>
            <option value="dark">深色</option>
          </select>
        </div>
        <div class="f-row">
          <span class="f-label">工作区</span>
          <span class="hint" id="set_workspace">当前工作区未知</span>
          <button class="btn" id="set_workspace_switch">切换工作区</button>
        </div>
        <div class="f-row">
          <span class="f-label">字号缩放</span>
          <input class="input" id="set_font_scale" type="number" min="85" max="130" step="1">
        </div>
        <label class="switch-row"><input type="checkbox" id="set_tray_enabled"><span class="switch-label">启用系统托盘</span></label>
        <label class="switch-row"><input type="checkbox" id="set_tray_on_close"><span class="switch-label">关闭窗口时最小化到托盘</span></label>
        <div class="f-row">
          <span class="f-label">显示热键</span>
          <input class="input" id="set_hotkey_show" placeholder="例如 Ctrl+Alt+A">
        </div>
        <div class="f-row">
          <span class="f-label">语音热键</span>
          <input class="input" id="set_hotkey_voice" placeholder="例如 Ctrl+Alt+V">
        </div>
        <label class="switch-row"><input type="checkbox" id="set_agent_default"><span class="switch-label">Agent 为默认模式</span></label>
        <label class="switch-row"><input type="checkbox" id="set_search_default"><span class="switch-label">联网搜索默认开启</span></label>
        <div class="f-row">
          <span class="f-label">策略</span>
          <select class="input" id="set_policy">
            <option value="strict">严格</option>
            <option value="fallback">降级</option>
          </select>
        </div>
      </div>
    </div>

    <div class="vpanel" data-panel="model" hidden>
      <div class="card">
        <div class="card-title">模型</div>
        <div class="f-row">
          <span class="f-label">当前模型</span>
          <span class="hint" id="set_selected_model">加载中…</span>
        </div>
        <div class="f-row">
          <span class="f-label">DeepSeek 网页</span>
          <span class="pill off" id="setDswStatus">获取中…</span>
          <div class="btn-row">
            <button class="btn" id="dswLaunch">启动</button>
            <button class="btn" id="dswSelftest">自检</button>
            <button class="btn" id="dswRetry">重试</button>
            <button class="btn" id="dswShow">显示窗口</button>
            <button class="btn" id="dswHide">收起窗口</button>
          </div>
        </div>
        <label class="switch-row"><input type="checkbox" id="set_dsw_auto"><span class="switch-label">自动使用 DeepSeek 网页</span></label>
        <label class="switch-row"><input type="checkbox" id="set_dsw_silent"><span class="switch-label">静默模式</span></label>
        <div class="f-row">
          <span class="f-label">GitHub Token</span>
          <input class="input" id="set_github_token" type="password" autocomplete="new-password">
          <button class="btn" id="ghTest">检测连接</button>
        </div>
        <div class="f-row">
          <span class="f-label"></span>
          <button class="btn" id="probeProviders">开始体检</button>
          <span class="hint">对已配置的模型提供方做连通性体检（后台执行）</span>
        </div>
      </div>
    </div>

    <div class="vpanel" data-panel="voice" hidden>
      <div class="card">
        <div class="card-title">语音</div>
        <div class="f-row">
          <span class="f-label">TTS 引擎</span>
          <select class="input" id="set_tts_engine"></select>
        </div>
        <div class="f-row">
          <span class="f-label">Edge 音色</span>
          <select class="input" id="set_tts_edge_voice"></select>
        </div>
        <div class="f-row">
          <span class="f-label">语速</span>
          <input class="input" id="set_tts_rate" type="number" min="-10" max="10" step="1">
        </div>
        <label class="switch-row"><input type="checkbox" id="set_tts_on"><span class="switch-label">启用语音朗读</span></label>
        <div class="f-row">
          <span class="f-label"></span>
          <button class="btn" id="ttsTest">试听</button>
        </div>
        <div class="f-row">
          <span class="f-label">语音识别后端</span>
          <select class="input" id="set_asr_backend"></select>
        </div>
        <div class="f-row">
          <span class="f-label">静音阈值</span>
          <input class="input" id="set_silence_threshold" type="number" min="0.5" max="30" step="0.5">
        </div>
        <div class="f-row">
          <span class="f-label">最长录音（秒）</span>
          <input class="input" id="set_max_record_seconds" type="number" min="30" max="1800" step="1">
        </div>
        <label class="switch-row"><input type="checkbox" id="set_auto_send"><span class="switch-label">语音识别后自动发送</span></label>
      </div>
    </div>

    <div class="vpanel" data-panel="keys" hidden>
      <div class="card">
        <div class="card-title">模型 API 密钥</div>
        <div id="setApiKeys"><div class="hint">加载中…</div></div>
        <div class="hint" style="margin-top:8px;">已保存的密钥显示为脱敏占位（****），留空不覆盖；只有重新输入才会覆盖。</div>
      </div>
    </div>

    <div class="vpanel" data-panel="prompt" hidden>
      <div class="card">
        <div class="card-title">系统提示词
          <span class="spacer"></span>
          <button class="btn" id="promptReset">恢复默认</button>
        </div>
        <textarea class="input" id="set_system_prompt" style="width:100%;min-height:160px;" placeholder="留空则使用内置默认提示词"></textarea>
        <div class="hint" style="margin-top:6px;">留空或点「恢复默认」后清空，将回落到内置默认提示词。</div>
      </div>
    </div>
  `;
}

// 把数组 / 对象填充进下拉框，保持当前选中值
function fillSelect(sel, items) {
  if (!sel) return;
  const cur = sel.value;
  sel.innerHTML = '';
  if (Array.isArray(items)) {
    for (const it of items) {
      const o = document.createElement('option');
      o.value = String(it);
      o.textContent = String(it);
      sel.appendChild(o);
    }
  } else if (items && typeof items === 'object') {
    for (const k of Object.keys(items)) {
      const v = items[k];
      const o = document.createElement('option');
      o.value = k;
      o.textContent = (typeof v === 'string' && v) ? v : ((v && (v.label || v.name)) || k);
      sel.appendChild(o);
    }
  }
  sel.value = cur;
}

// 设置下拉值，若目标值不在选项里则补一个（对未知枚举值容错）
function setSelectValue(sel, v) {
  if (!sel) return;
  sel.value = v == null ? '' : v;
  if (v && sel.value === '' && !Array.from(sel.options).some((o) => o.value === v)) {
    const o = document.createElement('option');
    o.value = String(v);
    o.textContent = String(v);
    sel.appendChild(o);
    sel.value = String(v);
  }
}

function renderApiKeys(root) {
  const box = $('#setApiKeys', root);
  box.innerHTML = '';
  const provs = (enums && enums.providers) || {};
  const keys = Object.keys(provs);
  if (!keys.length) {
    box.innerHTML = '<div class="hint">未获取到模型提供方（settings.enums.providers 为空）</div>';
    return;
  }
  for (const k of keys) {
    const meta = provs[k];
    let labelText = k;
    if (typeof meta === 'string' && meta) labelText = meta;
    else if (meta && meta.name) labelText = meta.name;
    else if (meta && meta.label) labelText = meta.label;

    const saved = (settings.api_keys && settings.api_keys[k]) || '';

    const row = document.createElement('div');
    row.className = 'f-row';

    const label = document.createElement('span');
    label.className = 'f-label';
    label.textContent = labelText;
    label.title = k;

    const inp = document.createElement('input');
    inp.className = 'input api-key-input';
    inp.type = 'password';
    inp.autocomplete = 'new-password';
    inp.dataset.key = k;
    if (saved && String(saved).indexOf('****') !== -1) {
      inp.value = '';
      inp.placeholder = '已保存(' + saved + ')';
    } else {
      inp.value = saved || '';
      inp.placeholder = '未设置';
    }

    row.appendChild(label);
    row.appendChild(inp);
    box.appendChild(row);
  }
}

function populateForm(root) {
  setVal(root, '#set_theme', settings.theme || 'light');
  setVal(root, '#set_font_scale', settings.font_scale != null ? settings.font_scale : 100);
  setChk(root, '#set_tray_enabled', settings.tray_enabled);
  setChk(root, '#set_tray_on_close', settings.tray_on_close);
  setVal(root, '#set_hotkey_show', settings.hotkey_show);
  setVal(root, '#set_hotkey_voice', settings.hotkey_voice);
  setChk(root, '#set_agent_default', settings.agent_default);
  setChk(root, '#set_search_default', settings.search_default);
  setVal(root, '#set_policy', settings.policy || 'fallback');

  setText(root, '#set_selected_model', settings.selected_model || '未选择');
  setChk(root, '#set_dsw_auto', settings.deepseek_web_auto);
  setChk(root, '#set_dsw_silent', settings.deepseek_web_silent);

  // GitHub Token：脱敏值不回填，用 placeholder 提示已保存
  const gh = settings.github_token || '';
  const ghEl = $('#set_github_token', root);
  if (ghEl) {
    if (String(gh).indexOf('****') !== -1) {
      ghEl.value = '';
      ghEl.placeholder = '已保存(' + gh + ')';
    } else {
      ghEl.value = gh;
      ghEl.placeholder = 'GitHub Token';
    }
  }

  fillSelect($('#set_tts_engine', root), (enums && enums.tts_engines) || []);
  setSelectValue($('#set_tts_engine', root), settings.tts_engine);
  fillSelect($('#set_tts_edge_voice', root), (enums && enums.edge_voices) || {});
  setSelectValue($('#set_tts_edge_voice', root), settings.tts_edge_voice);
  setVal(root, '#set_tts_rate', settings.tts_rate != null ? settings.tts_rate : 0);
  setChk(root, '#set_tts_on', settings.tts_on);
  fillSelect($('#set_asr_backend', root), (enums && enums.asr_backends) || {});
  setSelectValue($('#set_asr_backend', root), settings.asr_backend);
  setVal(root, '#set_silence_threshold', settings.silence_threshold != null ? settings.silence_threshold : 10);
  setVal(root, '#set_max_record_seconds', settings.max_record_seconds != null ? settings.max_record_seconds : 60);
  setChk(root, '#set_auto_send', settings.auto_send);

  renderApiKeys(root);

  setVal(root, '#set_system_prompt', settings.system_prompt || '');
}

function num(v, def) {
  const n = parseFloat(v);
  return Number.isFinite(n) ? n : def;
}

function collectPatch(root) {
  const patch = {
    theme: $('#set_theme', root).value,
    font_scale: Math.round(num($('#set_font_scale', root).value, 100)),
    tray_enabled: $('#set_tray_enabled', root).checked,
    tray_on_close: $('#set_tray_on_close', root).checked,
    hotkey_show: $('#set_hotkey_show', root).value.trim(),
    hotkey_voice: $('#set_hotkey_voice', root).value.trim(),
    agent_default: $('#set_agent_default', root).checked,
    search_default: $('#set_search_default', root).checked,
    policy: $('#set_policy', root).value,
    deepseek_web_auto: $('#set_dsw_auto', root).checked,
    deepseek_web_silent: $('#set_dsw_silent', root).checked,
    tts_engine: $('#set_tts_engine', root).value,
    tts_edge_voice: $('#set_tts_edge_voice', root).value,
    tts_rate: Math.round(num($('#set_tts_rate', root).value, 0)),
    tts_on: $('#set_tts_on', root).checked,
    asr_backend: $('#set_asr_backend', root).value,
    silence_threshold: num($('#set_silence_threshold', root).value, 10),
    max_record_seconds: Math.round(num($('#set_max_record_seconds', root).value, 60)),
    auto_send: $('#set_auto_send', root).checked,
    system_prompt: $('#set_system_prompt', root).value,
  };

  // GitHub Token：脱敏值不回写，只回写用户新输入的非空、不含 **** 的值
  const gh = $('#set_github_token', root).value.trim();
  if (gh && gh.indexOf('****') === -1) patch.github_token = gh;

  // api_keys：只回写用户新输入的非空、不含 **** 的 key
  const keys = {};
  root.querySelectorAll('.api-key-input').forEach((inp) => {
    const v = inp.value.trim();
    if (v && v.indexOf('****') === -1) keys[inp.dataset.key] = v;
  });
  if (Object.keys(keys).length) patch.api_keys = keys;

  return patch;
}

// dsw.status 结构未知，做容错解析
function fmtDsw(s) {
  if (s == null) return '未知';
  if (typeof s === 'string') return s;
  if (typeof s !== 'object') return String(s);
  const state = s.state != null ? s.state : (s.status != null ? s.status : '');
  const detail = s.detail != null ? s.detail : (s.message != null ? s.message : (s.msg != null ? s.msg : ''));
  let out = state ? String(state) : '';
  if (detail != null && String(detail) && String(detail) !== out) out += (out ? ' · ' : '') + String(detail);
  return out || JSON.stringify(s);
}

function isDswOn(s) {
  if (s == null) return false;
  const t = String(typeof s === 'string' ? s : (s.state != null ? s.state : (s.status != null ? s.status : JSON.stringify(s))));
  return /(运行|在线|ready|running|ok|on|connected|已登录)/i.test(t) && !/(失败|错误|error|off|停止|未登录|未连接)/i.test(t);
}

async function refreshDsw(root) {
  const el = $('#setDswStatus', root);
  if (!el) return;
  try {
    const d = await rpc('dsw.status', {});
    const s = d && d.status;
    el.textContent = fmtDsw(s);
    el.className = 'pill ' + (isDswOn(s) ? 'on' : 'off');
  } catch (e) {
    el.textContent = '状态获取失败';
    el.className = 'pill off';
  }
}

async function refresh(root, force) {
  try {
    const d = await rpc('settings.get', {});
    settings = (d && d.settings) || {};
    $('#setHint', root).textContent = '';
  } catch (e) {
    $('#setHint', root).textContent = '加载失败';
  }
  try {
    const e = await rpc('settings.enums', {});
    enums = e || {};
  } catch (e) {
    enums = {};
  }
  if (!formLoaded || force) {
    populateForm(root);
    formLoaded = true;
  }
  await refreshDsw(root);
}

function switchTab(root, name) {
  activeTab = name;
  root.querySelectorAll('.vtab').forEach((b) => b.classList.toggle('is-active', b.dataset.tab === name));
  root.querySelectorAll('.vpanel').forEach((p) => { p.hidden = (p.dataset.panel !== name); });
}

function wire(root) {
  root.querySelectorAll('.vtab').forEach((b) => {
    b.addEventListener('click', () => switchTab(root, b.dataset.tab));
  });

  $('#setSave', root).addEventListener('click', async () => {
    const patch = collectPatch(root);
    try {
      const d = await rpc('settings.save', { patch });
      if (d && d.ok === false) { toast('保存失败：' + (d.error || '未知错误')); return; }
      toast('设置已保存');
      await refresh(root, true);
    } catch (e) {
      toast('保存失败：' + e.message);
    }
  });

  // DeepSeek 网页操作（均异步后台执行）
  const dswBtn = (id, method) => {
    $(id, root).addEventListener('click', async () => {
      try { await rpc(method, {}); toast('已提交，后台执行中'); }
      catch (e) { toast('操作失败：' + e.message); }
    });
  };
  dswBtn('#dswLaunch', 'dsw.launch');
  dswBtn('#dswSelftest', 'dsw.selftest');
  dswBtn('#dswRetry', 'dsw.retry');
  dswBtn('#dswShow', 'dsw.show');
  dswBtn('#dswHide', 'dsw.hide');

  $('#ghTest', root).addEventListener('click', async () => {
    const token = $('#set_github_token', root).value.trim();
    if (!token) { toast('请先填写 Token'); return; }
    try { await rpc('settings.gh_test', { token }); toast('检测已提交，后台执行中'); }
    catch (e) { toast('检测失败：' + e.message); }
  });

  $('#probeProviders', root).addEventListener('click', async () => {
    try { await rpc('settings.probe_providers', {}); toast('已开始体检，后台执行中'); }
    catch (e) { toast('体检失败：' + e.message); }
  });

  $('#ttsTest', root).addEventListener('click', async () => {
    const engine = $('#set_tts_engine', root).value;
    try { await rpc('settings.tts_test', { engine }); toast('已试听'); }
    catch (e) { toast('试听失败：' + e.message); }
  });

  $('#promptReset', root).addEventListener('click', () => {
    $('#set_system_prompt', root).value = '';
    toast('已清空，将回落到内置默认提示词');
  });

  // 切换工作区：调用后端打开原生对话框，路径由 workspace_changed 事件回推刷新
  $('#set_workspace_switch', root).addEventListener('click', async () => {
    try {
      await rpc('workspace.switch', {});
      toast('已打开切换工作区对话框');
    } catch (e) {
      toast('切换工作区失败：' + e.message);
    }
  });

  // 后端推送工作区路径变化（RPC 打开对话框选择后回推），更新设置页展示
  on('workspace_changed', (p) => {
    const el = $('#set_workspace', root);
    if (!el) return;
    const path = (p && (p.path || p.workspace)) || '';
    el.textContent = path ? ('当前工作区：' + path) : '当前工作区未知';
  });
}

export async function init(root) {
  if (!root.dataset.built) {
    build(root);
    root.dataset.built = '1';
    wire(root);
    switchTab(root, activeTab);
  }
  await refresh(root);
}
