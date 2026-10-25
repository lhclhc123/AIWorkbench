# v9.21.2 · 彻底全 Web 化改造方案（实现规格）

> 目标：把除「内嵌浏览器」外的所有页面收进**单一 SPA**（`awb://ui/index.html`），
> 网页自带导航，删除 Qt 侧栏（NavRail），彻底消灭「左侧两个任务栏」。

---

## 1. 结论与范围

| 页面 | 现状 | 目标 | 说明 |
| --- | --- | --- | --- |
| chat（对话） | Web（index.html） | Web（SPA 主视图） | 已有 |
| assistant（钉钉助手） | Web（im.html，独立 bridge） | 合并进 SPA | 逻辑移植 im.js |
| skills / tasks / memory / tools / about | Qt | Web | 5 简单页 |
| settings / integrations | Qt | Web | 2 复杂页 |
| browser（内嵌浏览器） | Qt | **保留 Qt** | QtWebEngine 内嵌内核 + 请求拦截，无法网页化；由 RPC 切回 Qt stack |
| bg（并行任务）/ team（团队） | Qt | Web 占位视图 | 保留功能入口 |

**核心决策**：
1. 单一 SPA：`index.html` 内 12 个 `<section class="page">`，前端 JS 切换，`embedded` 模式**取消**（网页自己显示 titlebar + 导航）。
2. 后端通用 RPC 分发器 `_web_rpc_dispatch` 扩展 7 组方法（复用现有 `rpc_request`/`send_rpc_result` 契约）。
3. Qt 侧栏 `NavRail` / `SidePanel` 删除；`browser` 页导航走 `rpc('nav_to', {key:'browser'})` → 后端 `_open_page('browser')` 切 Qt stack。
4. 每页后端方法统一命名：`<page>.<action>`（如 `skills.list`、`tasks.create`、`memory.get_text`）。

---

## 2. 前端结构（`app/webui/`）

```
index.html            # SPA 壳：titlebar + sidebar(导航+会话列表) + 12 个 section
css/chat.css          # 现有（对话样式）
css/im.css            # 现有（钉钉样式）
css/pages.css         # 新增：7 个新页通用样式（表单/表格/列表）
js/app.js             # 改造：导航切换 + 视图懒加载
js/bridge.js          # 不变（rpc 已就绪）
js/store.js           # 不变
js/views/chat.js      # 现有
js/views/im.js        # 从 im.js 移植为视图模块
js/views/skills.js    # 新增
js/views/tasks.js     # 新增
js/views/memory.js    # 新增
js/views/tools.js     # 新增
js/views/about.js     # 新增
js/views/settings.js  # 新增
js/views/integrations.js # 新增
```

**导航**：sidebar 顶部一段「导航区」，复用 `PAGES` 顺序渲染按钮；点击切 `section.page`。
「浏览器」点击时 `rpc('nav_to', {key:'browser'})` 通知后端切 Qt。

---

## 3. 后端 RPC 扩展（`main_window._web_rpc_dispatch`）

新增 method 分支（每个返回 dict，复用现有 `self.memory/trace/skills/scheduler/mcp/ding/client/dsw/speaker/settings` 对象）：

### skills
- `skills.list()` → `[{source,name,slug,path,when,desc}]`
- `skills.get_body(slug)` → `{slug, body}`
- `skills.create({slug,name,when,desc,body})` → `{ok}`
- `skills.delete(slug)` → `{ok, error?}`
- `skills.open_dir()` → `{ok}`

### tasks
- `tasks.list()` → `[{id,name,kind,at,weekdays,interval_min,run_at,notify_dingtalk,enabled,run_count,last_status,last_result, describe, next}]`
- `tasks.enums()` → `{kinds, kind_labels}`
- `tasks.create(payload)` / `tasks.update(id, {enabled})` / `tasks.delete(id)` / `tasks.run_now(id)`

### memory
- `memory.get_text()` → `{text, stats}`
- `memory.save_text(text)` / `memory.clear()`
- `memory.auto_logs(limit=60)` / `memory.worklog()` / `memory.open_worklog_dir()`

### tools (TracePage)
- `trace.list(limit=200)` / `trace.stats()` / `trace.clear()`

### about
- `app.about_info()` / `app.diag_info()` / `app.check_update()`

### settings（复杂）
- `settings.get()` / `settings.save(dict)` / `settings.enums()`（providers/asr/tts_engines/edge_voices）
- `settings.probe_providers()`（异步）/ `settings.tts_test(engine)`
- `dsw.status()` / `dsw.launch()` / `dsw.selftest()` / `dsw.retry()` / `dsw.show_window()` / `dsw.hide_window()`
- `gh.test(token)`

### integrations（复杂）
- `dingtalk.get_config()` / `dingtalk.save_config(cfg)` / `dingtalk.test(cfg)` / `dingtalk.selftest(cfg)` / `dingtalk.contacts(cfg)` / `dingtalk.login(cfg)` / `dingtalk.group_send(cfg,text)` / `dingtalk.stream_toggle(cfg,enable)`
- `dws.login(device)` / `dws.conversations()` / `dws.messages(cid)` / `dws.send(target,content)` / `dws.status()`
- `update.get_config()` / `update.save_config(cfg)` / `update.check()` / `update.apply()`
- `mcp.path()` / `mcp.reload()` / `mcp.scan()` / `mcp.open_file()`

### nav
- `nav_to({key})` → 后端 `_open_page(key)`（供「浏览器」切 Qt）

---

## 4. 实施顺序（分阶段，每阶段可验证）

- **阶段 1**：SPA 导航骨架 + 后端 RPC 框架 + `memory` 范式页（端到端验证模式跑通）
- **阶段 2**：5 简单页（skills/tasks/tools/about 补全，memory 已在阶段1）
- **阶段 3**：2 复杂页（settings/integrations）+ im 合并进 SPA
- **阶段 4**：删 Qt 侧栏，browser 走 RPC 切回，全量回归

---

## 5. 回退

`settings["ui_engine"]` 仍是 `qt|web` 总开关；删侧栏只影响 `web` 分支，`qt` 分支完整保留。
