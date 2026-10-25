// views/skills.js —— 技能页：技能列表 + 正文预览 + 新建/删除/打开目录
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

// 模块级状态：视图只 build 一次，之后每次 init 仅刷新数据，故用模块变量保持选中态
let skills = [];
let currentSlug = null;

function build(root) {
  root.innerHTML = `
    <div class="page-head">
      <h2>技能</h2>
      <span class="page-hint" id="skillsHint"></span>
      <span class="spacer"></span>
      <button class="btn btn-primary" id="skillsNew">新建技能</button>
      <button class="btn" id="skillsOpenDir">打开技能目录</button>
      <button class="btn btn-danger" id="skillsDel">删除此技能</button>
      <button class="btn" id="skillsReload">刷新</button>
    </div>
    <div class="card" id="skillsForm" hidden>
      <div class="card-title">新建技能</div>
      <div class="inline-form">
        <input class="input grow" id="skillsSlug" placeholder="slug（英文短名，唯一）">
        <input class="input grow" id="skillsName" placeholder="名称">
      </div>
      <div class="inline-form" style="margin-top:8px;">
        <input class="input grow" id="skillsDesc" placeholder="描述（description）">
      </div>
      <textarea class="input" id="skillsBody" placeholder="正文（markdown）" style="margin-top:8px;min-height:140px;"></textarea>
      <div class="inline-form" style="margin-top:8px;">
        <button class="btn btn-primary" id="skillsSave">保存</button>
        <button class="btn" id="skillsCancel">取消</button>
      </div>
    </div>
    <div style="display:flex;gap:12px;flex:1;min-height:0;">
      <div class="card" style="flex:0 0 320px;width:320px;display:flex;flex-direction:column;min-height:0;margin-top:0;">
        <div class="card-title">技能列表</div>
        <div class="simple-list" id="skillsList" style="flex:1;overflow-y:auto;"></div>
      </div>
      <div class="card" style="flex:1;min-height:0;display:flex;flex-direction:column;margin-top:0;">
        <div class="card-title">正文预览 <span class="hint" id="skillsBodyMeta"></span></div>
        <div class="readonly-body rich" id="skillsBodyView" style="flex:1;">选择左侧技能查看正文</div>
      </div>
    </div>
  `;
}

function renderList(root) {
  const list = $('#skillsList', root);
  list.innerHTML = '';
  if (!skills.length) {
    list.innerHTML = '<div class="empty">暂无技能</div>';
    return;
  }
  for (const s of skills) {
    const name = s.name || s.slug || '（未命名）';
    const builtin = /内置|builtin/i.test(String(s.source || ''));
    const row = document.createElement('div');
    row.className = 'simple-row' + (s.slug === currentSlug ? ' is-active' : '');
    row.dataset.slug = s.slug || '';

    const grow = document.createElement('div');
    grow.className = 'grow';
    const title = document.createElement('div');
    title.className = 'row-title';
    title.textContent = name;
    const sub = document.createElement('div');
    sub.className = 'row-sub';
    sub.textContent = s.desc || s.path || '';
    grow.appendChild(title);
    grow.appendChild(sub);

    const pill = document.createElement('span');
    pill.className = 'pill' + (builtin ? ' on' : '');
    pill.textContent = builtin ? '内置' : '自建';

    row.appendChild(grow);
    row.appendChild(pill);
    row.addEventListener('click', () => selectSkill(root, s.slug));
    list.appendChild(row);
  }
}

async function selectSkill(root, slug) {
  currentSlug = slug;
  renderList(root);
  $('#skillsBodyMeta', root).textContent = slug;
  const view = $('#skillsBodyView', root);
  view.textContent = '加载中…';
  try {
    const d = await rpc('skills.get_body', { slug });
    if (d && d.ok === false) {
      view.textContent = '加载失败：' + (d.error || '未知错误');
    } else {
      view.textContent = d.body || '（空正文）';
    }
  } catch (e) {
    view.textContent = '加载失败：' + e.message;
  }
}

async function refresh(root) {
  try {
    const d = await rpc('skills.list', {});
    skills = Array.isArray(d.skills) ? d.skills : [];
    renderList(root);
    $('#skillsHint', root).textContent = `共 ${skills.length} 个技能`;
  } catch (e) {
    $('#skillsHint', root).textContent = '加载失败';
    $('#skillsList', root).innerHTML = '<div class="empty">加载失败</div>';
  }
}

function wire(root) {
  $('#skillsNew', root).addEventListener('click', () => {
    $('#skillsForm', root).hidden = false;
  });
  $('#skillsCancel', root).addEventListener('click', () => {
    $('#skillsForm', root).hidden = true;
  });
  $('#skillsSave', root).addEventListener('click', async () => {
    const slug = $('#skillsSlug', root).value.trim();
    const name = $('#skillsName', root).value.trim();
    const desc = $('#skillsDesc', root).value.trim();
    const body = $('#skillsBody', root).value;
    if (!slug) { toast('请填写 slug'); return; }
    const fm = ['---'];
    if (name) fm.push(`name: ${name}`);
    if (desc) fm.push(`description: ${desc}`);
    fm.push('---');
    const text = fm.join('\n') + '\n\n' + body;
    try {
      const d = await rpc('skills.create', { slug, text });
      if (d && d.ok === false) { toast('创建失败：' + (d.error || '未知错误')); return; }
      toast('技能已创建');
      $('#skillsForm', root).hidden = true;
      $('#skillsSlug', root).value = '';
      $('#skillsName', root).value = '';
      $('#skillsDesc', root).value = '';
      $('#skillsBody', root).value = '';
      await refresh(root);
    } catch (e) {
      toast('创建失败：' + e.message);
    }
  });
  $('#skillsOpenDir', root).addEventListener('click', async () => {
    try { await rpc('skills.open_dir', {}); toast('已打开技能目录'); }
    catch (e) { toast('打开失败：' + e.message); }
  });
  $('#skillsDel', root).addEventListener('click', async () => {
    if (!currentSlug) { toast('请先选择要删除的技能'); return; }
    if (!confirm(`确定删除技能「${currentSlug}」？此操作不可撤销。`)) return;
    try {
      const d = await rpc('skills.delete', { slug: currentSlug });
      if (d && d.ok === false) { toast('删除失败：' + (d.error || '未知错误')); return; }
      toast('已删除');
      currentSlug = null;
      $('#skillsBodyView', root).textContent = '选择左侧技能查看正文';
      $('#skillsBodyMeta', root).textContent = '';
      await refresh(root);
    } catch (e) {
      toast('删除失败：' + e.message);
    }
  });
  $('#skillsReload', root).addEventListener('click', () => refresh(root));
}

export async function init(root) {
  if (!root.dataset.built) {
    build(root);
    root.dataset.built = '1';
    wire(root);
  }
  await refresh(root);
}
