// views/integrations.js —— 集成页：3 个分组（钉钉 + dws 工作台 / 自动更新 / MCP），tab 切换
'use strict';

import { rpc } from '../bridge.js';

const $ = (s, root) => (root || document).querySelector(s);

function toast(msg) {
  const el = $('#toast');
  el.textContent = msg;
  el.classList.add('show');
  clearTimeout(toast._t);
  toast._t = setTimeout(() => el.classList.remove('show'), 1900);
}

// 本视图自带的局部样式（scoped 到 integrations 页内部），不影响其它视图
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

// 模块级状态：视图只 build 一次，之后每次 init 仅刷新数据
let dingConfig = {};
let updConfig = {};
let streamOn = false;
let formLoaded = false;
let activeTab = 'dingtalk';

function setVal(root, sel, v) { const el = $(sel, root); if (el) el.value = (v == null ? '' : v); }
function setChk(root, sel, v) { const el = $(sel, root); if (el) el.checked = !!v; }
function setText(root, sel, v) { const el = $(sel, root); if (el) el.textContent = (v == null ? '' : v); }

function build(root) {
  root.innerHTML = `
    <style>${STYLE}</style>
    <div class="page-head">
      <h2>集成</h2>
      <span class="page-hint" id="intHint"></span>
      <span class="spacer"></span>
    </div>

    <div class="vtabs" role="tablist">
      <button class="vtab is-active" data-tab="dingtalk">钉钉</button>
      <button class="vtab" data-tab="update">自动更新</button>
      <button class="vtab" data-tab="mcp">MCP</button>
    </div>

    <div class="vpanel" data-panel="dingtalk">
      <div class="card">
        <div class="card-title">钉钉机器人</div>
        <label class="switch-row"><input type="checkbox" id="ding_enabled"><span class="switch-label">启用钉钉集成</span></label>
        <div class="f-row">
          <span class="f-label">模式</span>
          <select class="input" id="ding_mode">
            <option value="webhook">webhook</option>
            <option value="app">app</option>
          </select>
        </div>
        <div class="f-row">
          <span class="f-label">Webhook</span>
          <input class="input" id="ding_webhook" autocomplete="off">
        </div>
        <div class="f-row">
          <span class="f-label">加签 Secret</span>
          <input class="input" id="ding_secret" type="password" autocomplete="new-password">
        </div>
        <div class="f-row">
          <span class="f-label">Client ID</span>
          <input class="input" id="ding_client_id" autocomplete="off">
        </div>
        <div class="f-row">
          <span class="f-label">Client Secret</span>
          <input class="input" id="ding_client_secret" type="password" autocomplete="new-password">
        </div>
        <div class="f-row">
          <span class="f-label">Robot Code</span>
          <input class="input" id="ding_robot_code" autocomplete="off">
        </div>
        <div class="f-row">
          <span class="f-label">Agent ID</span>
          <input class="input" id="ding_agent_id" autocomplete="off">
        </div>
        <div class="f-row">
          <span class="f-label">开放会话 ID</span>
          <input class="input" id="ding_ocid" autocomplete="off">
        </div>
        <div class="f-row">
          <span class="f-label">用户 ID 列表</span>
          <input class="input" id="ding_user_ids" autocomplete="off" placeholder="逗号分隔">
        </div>
        <label class="switch-row"><input type="checkbox" id="ding_receive"><span class="switch-label">接收消息</span></label>
        <div class="f-row">
          <span class="f-label">推送标签</span>
          <input class="input" id="ding_push_tag" autocomplete="off">
        </div>
        <div class="f-row">
          <span class="f-label">关键词</span>
          <input class="input" id="ding_keyword" autocomplete="off">
        </div>
        <label class="switch-row"><input type="checkbox" id="ding_at_all"><span class="switch-label">@ 所有人</span></label>
        <div class="btn-row">
          <button class="btn btn-primary" id="dingSave">保存配置</button>
          <button class="btn" id="dingTest">测试消息</button>
          <button class="btn" id="dingSelftest">一键自检</button>
          <button class="btn" id="dingContacts">浏览通讯录</button>
          <button class="btn" id="dingLogin">扫码授权登录</button>
        </div>
        <div class="f-row" style="margin-top:8px;">
          <button class="btn" id="dingStream">连接 Stream</button>
          <input class="input" id="dingGroupText" placeholder="发到群里的消息内容">
          <button class="btn" id="dingGroupSend">发到群里</button>
        </div>
      </div>

      <div class="card">
        <div class="card-title">dws 工作台</div>
        <div class="btn-row">
          <button class="btn" id="dwsLogin">dws 登录</button>
          <button class="btn" id="dwsLoginDevice">设备码登录</button>
          <button class="btn" id="dwsConversations">查看会话</button>
        </div>
        <div class="f-row" style="margin-top:8px;">
          <input class="input" id="dwsTarget" placeholder="目标 (target)">
          <input class="input" id="dwsContent" placeholder="内容 (content)">
          <button class="btn" id="dwsSend">发送</button>
        </div>
      </div>
    </div>

    <div class="vpanel" data-panel="update" hidden>
      <div class="card">
        <div class="card-title">自动更新</div>
        <div class="f-row">
          <span class="f-label">仓库</span>
          <input class="input" id="upd_repo" autocomplete="off" placeholder="owner/repo">
        </div>
        <label class="switch-row"><input type="checkbox" id="upd_check_on_start"><span class="switch-label">启动时检查更新</span></label>
        <div class="f-row">
          <span class="f-label">Token</span>
          <input class="input" id="upd_token" type="password" autocomplete="new-password">
        </div>
        <div class="btn-row">
          <button class="btn btn-primary" id="updSave">保存</button>
          <button class="btn" id="updCheck">检查更新</button>
          <button class="btn" id="updApply">下载并安装</button>
        </div>
      </div>
    </div>

    <div class="vpanel" data-panel="mcp" hidden>
      <div class="card">
        <div class="card-title">MCP</div>
        <div class="f-row">
          <span class="f-label">配置文件</span>
          <span class="hint" id="mcpPath" style="word-break:break-all;">加载中…</span>
        </div>
        <div class="btn-row">
          <button class="btn" id="mcpOpen">打开配置文件</button>
          <button class="btn" id="mcpScan">扫描工具</button>
          <button class="btn" id="mcpReload">重新载入</button>
        </div>
      </div>
    </div>
  `;
}

// 设置脱敏值：若值为 **** 则不回填，用 placeholder 提示已保存
function setMasked(root, sel, v, placeholder) {
  const el = $(sel, root);
  if (!el) return;
  if (v && String(v).indexOf('****') !== -1) {
    el.value = '';
    el.placeholder = '已保存(' + v + ')';
  } else {
    el.value = v || '';
    el.placeholder = placeholder || '';
  }
}

function populateDing(root) {
  const c = dingConfig || {};
  setChk(root, '#ding_enabled', c.enabled);
  setVal(root, '#ding_mode', c.mode || 'webhook');
  setVal(root, '#ding_webhook', c.webhook);
  setMasked(root, '#ding_secret', c.secret, '加签 Secret');
  setVal(root, '#ding_client_id', c.client_id);
  setMasked(root, '#ding_client_secret', c.client_secret, 'Client Secret');
  setVal(root, '#ding_robot_code', c.robot_code);
  setVal(root, '#ding_agent_id', c.agent_id);
  setVal(root, '#ding_ocid', c.open_conversation_id);
  setVal(root, '#ding_user_ids', c.user_ids);
  setChk(root, '#ding_receive', c.receive_enabled);
  setVal(root, '#ding_push_tag', c.push_tag);
  setVal(root, '#ding_keyword', c.keyword);
  setChk(root, '#ding_at_all', c.at_all);
}

function populateUpd(root) {
  const c = updConfig || {};
  setVal(root, '#upd_repo', c.repo);
  setChk(root, '#upd_check_on_start', c.check_on_start);
  setMasked(root, '#upd_token', c.token, 'Token');
}

// 收集钉钉配置：脱敏的 secret 类字段仅在用户重新输入（非空、不含 ****）时才覆盖
function collectDingConfig(root) {
  const c = Object.assign({}, dingConfig);
  c.enabled = $('#ding_enabled', root).checked;
  c.mode = $('#ding_mode', root).value;
  c.webhook = $('#ding_webhook', root).value.trim();
  c.client_id = $('#ding_client_id', root).value.trim();
  c.robot_code = $('#ding_robot_code', root).value.trim();
  c.agent_id = $('#ding_agent_id', root).value.trim();
  c.open_conversation_id = $('#ding_ocid', root).value.trim();
  c.user_ids = $('#ding_user_ids', root).value.trim();
  c.receive_enabled = $('#ding_receive', root).checked;
  c.push_tag = $('#ding_push_tag', root).value.trim();
  c.keyword = $('#ding_keyword', root).value.trim();
  c.at_all = $('#ding_at_all', root).checked;

  const sec = $('#ding_secret', root).value.trim();
  if (sec && sec.indexOf('****') === -1) c.secret = sec;
  const csec = $('#ding_client_secret', root).value.trim();
  if (csec && csec.indexOf('****') === -1) c.client_secret = csec;
  return c;
}

function collectUpdConfig(root) {
  const c = Object.assign({}, updConfig);
  c.repo = $('#upd_repo', root).value.trim();
  c.check_on_start = $('#upd_check_on_start', root).checked;
  const tok = $('#upd_token', root).value.trim();
  if (tok && tok.indexOf('****') === -1) c.token = tok;
  return c;
}

async function refresh(root, force) {
  try {
    const d = await rpc('dingtalk.get_config', {});
    dingConfig = (d && d.config) || {};
    $('#intHint', root).textContent = '';
  } catch (e) {
    $('#intHint', root).textContent = '加载失败';
  }
  try {
    const d = await rpc('update.get_config', {});
    updConfig = (d && d.config) || {};
  } catch (e) {
    updConfig = {};
  }
  try {
    const d = await rpc('mcp.path', {});
    setText(root, '#mcpPath', (d && d.path) || '（未设置）');
  } catch (e) {
    setText(root, '#mcpPath', '加载失败');
  }

  if (!formLoaded || force) {
    populateDing(root);
    populateUpd(root);
    formLoaded = true;
  }
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

  // 钉钉：保存（真实写回）
  $('#dingSave', root).addEventListener('click', async () => {
    try {
      const d = await rpc('dingtalk.save_config', { config: collectDingConfig(root) });
      if (d && d.ok === false) { toast('保存失败：' + (d.error || '未知错误')); return; }
      toast('钉钉配置已保存');
      await refresh(root, true);
    } catch (e) {
      toast('保存失败：' + e.message);
    }
  });

  // 钉钉：异步动作（test/selftest/contacts/login），调完只提示后台执行
  const dingAsync = (id, method) => {
    $(id, root).addEventListener('click', async () => {
      try { await rpc(method, { config: collectDingConfig(root) }); toast('已提交，后台执行中'); }
      catch (e) { toast('操作失败：' + e.message); }
    });
  };
  dingAsync('#dingTest', 'dingtalk.test');
  dingAsync('#dingSelftest', 'dingtalk.selftest');
  dingAsync('#dingContacts', 'dingtalk.contacts');
  dingAsync('#dingLogin', 'dingtalk.login');

  $('#dingGroupSend', root).addEventListener('click', async () => {
    const text = $('#dingGroupText', root).value.trim();
    if (!text) { toast('请填写消息内容'); return; }
    try { await rpc('dingtalk.group_send', { config: collectDingConfig(root), text }); toast('已提交，后台执行中'); }
    catch (e) { toast('发送失败：' + e.message); }
  });

  $('#dingStream', root).addEventListener('click', async () => {
    const enable = !streamOn;
    try {
      await rpc('dingtalk.stream_toggle', { config: collectDingConfig(root), enable });
      streamOn = enable;
      $('#dingStream', root).textContent = streamOn ? '断开 Stream' : '连接 Stream';
      toast('已提交，后台执行中');
    } catch (e) {
      toast('操作失败：' + e.message);
    }
  });

  // dws 工作台
  const dwsAction = (id, method, args) => {
    $(id, root).addEventListener('click', async () => {
      try { await rpc(method, args || {}); toast('已提交，后台执行中'); }
      catch (e) { toast('操作失败：' + e.message); }
    });
  };
  dwsAction('#dwsLogin', 'dws.login', { device: false });
  dwsAction('#dwsLoginDevice', 'dws.login', { device: true });
  dwsAction('#dwsConversations', 'dws.conversations', {});

  $('#dwsSend', root).addEventListener('click', async () => {
    const target = $('#dwsTarget', root).value.trim();
    const content = $('#dwsContent', root).value.trim();
    if (!target || !content) { toast('请填写 target 和 content'); return; }
    try { await rpc('dws.send', { target, content }); toast('已提交，后台执行中'); }
    catch (e) { toast('发送失败：' + e.message); }
  });

  // 自动更新
  $('#updSave', root).addEventListener('click', async () => {
    try {
      const d = await rpc('update.save_config', { config: collectUpdConfig(root) });
      if (d && d.ok === false) { toast('保存失败：' + (d.error || '未知错误')); return; }
      toast('更新配置已保存');
      await refresh(root, true);
    } catch (e) {
      toast('保存失败：' + e.message);
    }
  });
  const updAsync = (id, method) => {
    $(id, root).addEventListener('click', async () => {
      try { await rpc(method, {}); toast('已提交，后台执行中'); }
      catch (e) { toast('操作失败：' + e.message); }
    });
  };
  updAsync('#updCheck', 'update.check');
  updAsync('#updApply', 'update.apply');

  // MCP
  $('#mcpOpen', root).addEventListener('click', async () => {
    try { await rpc('mcp.open_file', {}); }
    catch (e) { toast('打开失败：' + e.message); }
  });
  $('#mcpScan', root).addEventListener('click', async () => {
    try { await rpc('mcp.scan', {}); toast('已开始扫描，后台执行中'); }
    catch (e) { toast('操作失败：' + e.message); }
  });
  $('#mcpReload', root).addEventListener('click', async () => {
    try { await rpc('mcp.reload', {}); toast('已重新载入'); }
    catch (e) { toast('操作失败：' + e.message); }
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
