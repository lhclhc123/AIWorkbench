# v9.21 · Web UI 改造方案（供拍板）

> 目标：解决「界面不够美观」。本文只谈**怎么改、改多少、有什么风险、能不能回退**，不含代码改动。
> 配套原型：`docs/ui/mockup-v921.html`（单文件、离线可开、深/浅色 + 两个页面可交互）。

---

## 1. 结论先说

**能走本地网页这条路，而且比想象中便宜。**

推荐 **方案 B：保留 PyQt6 外壳，把「对话页 + 钉钉助手页」换成 `QWebEngineView` 里的本地网页**。
通信**全部走 QWebChannel**，流式 token 按 **50ms 合批**推送，**不额外开 HTTP 端口**。

一句话理由：**WebEngine 的包体成本在 v9.19.1 就已经付过了**（内核、`QtWebEngineProcess.exe`、
`resources/`、`translations/` 都已经打进包，`PyQt6.QtWebChannel` 也早就在
`build_v9200.py:98` 的 hidden imports 里），而 Python 侧的能力（Agent 循环、工具、任务池、
团队、IM 底座）本来就与界面解耦——所以这次改造**只新增前端资源和一层桥，几乎不动业务代码**。

改造范围（一期）：

| 动作 | 位置 | 规模 |
| --- | --- | --- |
| 新增前端资源 | `app/webui/`（约 10 个文件） | 大（主要工作量在这里） |
| 新增桥接层 | `app/webui_bridge.py`（新文件，约 300～400 行） | 中 |
| 新增协议注册 | `app/webui_host.py`（新文件，约 120 行） | 小 |
| 改主窗口 | `app/gui/main_window.py` 里对话页/助理页各加一个分支 | 小 |
| 改打包 | `build_v9210.py` 加一条 `--add-data app/webui` | 小 |
| 回退开关 | `settings["ui_engine"] = "qt" \| "web"` | 小 |

**预计观感提升集中在对话页**——而对话页正是用户 80% 以上时间停留的地方。这是这笔投入值得做的根本原因。

---

## 2. 三条路线对比

| | **A 纯 Web（Electron / Tauri 整壳）** | **B 混合（Qt 外壳 + 对话区 QWebEngineView）** | **C 继续精修 QSS** |
| --- | --- | --- | --- |
| 视觉天花板 | 最高（等价浏览器） | 高（页面内是完整浏览器能力；外壳仍是 Qt 风格） | 中（Qt 没有多层阴影、没有过渡动画、没有 `flex-wrap`、不支持 `backdrop-filter`） |
| 改动量 | **极大**：11 个页面全部重写 + 托盘/快捷键/单实例/语音/拖拽全部重做 + Python 与前端之间重新定一套 IPC | **小～中**：只重写 2 个页面，其余 9 页原样不动 | 小，但很快撞天花板 |
| 包体 | Electron 方案 **+120～180MB**，且变成「Node 运行时 + Python 运行时」双运行时；Tauri 轻但要引 Rust 工具链 | **+0**（WebEngine 已在包里），只多几百 KB 前端资源 | +0 |
| 风险 | **最高**：一次性推翻已经稳定的托盘/快捷键/单实例/打包链路；出问题没有中间态 | **中低**：新旧界面并存，可用开关一键切回 | 低 |
| 保住现有能力 | 全部要重接（含 `app/webengine.py` 的离屏抓包、CDP 9333、托盘菜单、`InstanceGuard`） | **全部保留**，Qt 侧一行逻辑都不用改 | 全部保留 |
| 回退成本 | 无法回退（等于换一个程序） | **一个设置项 / 一个命令行参数** | 不需要回退 |
| 结论 | 不做 | **推荐（一期）** | 只作为过渡，不作为终点 |

**为什么不是 A（哪怕它视觉上限最高）**：现在这套程序的复杂度不在界面，而在**壳**——
托盘常驻、全局快捷键、单实例唤醒、语音输入、文件拖拽、WebEngine 抓包、自动更新、Windows 隐藏控制台。
这些每一条都在 Qt 侧调通了，重写成 Electron 等于把 v9.0 到 v9.20 的踩坑记录重踩一遍，
而收益只是「同样的网页，换了个容器」。**用 A 换来的视觉提升，B 也能拿到 90%，成本却只有 A 的 1/5。**

**还要说清 B 的真实短板**（不粉饰）：一期做完后，**左侧栏、顶部标题条、其余 9 个页面仍然是 Qt 风格**，
会出现「一半精致、一半粗糙」的割裂感。应对办法见 §3 的「终态 B+」：
把 B 当跳板，二期/三期把外壳也搬到 Web，最终收敛成「整窗一个 WebView + Python 只做后台」——
那时视觉上等价于 A，但**不用换运行时，也不用重接托盘和快捷键**。

---

## 3. 推荐方案 B 的架构

```
┌──────────────────────────────────────────────────────────────────┐
│ PyQt6 进程（主线程）                                              │
│                                                                  │
│  托盘菜单 · 全局快捷键 · 单实例 InstanceGuard · 语音输入           │
│  原生文件对话框 · 通知 · 自动更新 · 窗口最小化/还原                 │
│                                                                  │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │ QWebEngineView（对话页 / 助理页）                           │  │
│  │                                                            │  │
│  │   静态资源：awb://ui/index.html  （自定义 scheme）           │  │
│  │   前端：HTML / CSS / 原生 ES module                         │  │
│  │   视图：对话流 · 思维链 · 工具胶囊 · 助理三标签              │  │
│  │                                                            │  │
│  │        ▲ 事件（下行）              │ 命令（上行）            │  │
│  │        └──────── QWebChannel ──────┘                       │  │
│  └────────────────────────┬───────────────────────────────────┘  │
│                           │                                       │
│  ┌────────────────────────┴───────────────────────────────────┐  │
│  │ 桥（新增 app/webui_bridge.py）                              │  │
│  │  · 只做「翻译 + 限流 + 生命周期」，不含业务逻辑              │  │
│  │  · 上行命令转成 Qt 信号，交给 MainWindow 现有槽函数          │  │
│  │  · 下行把 ChatWorker / AssistantService 的信号转成 JS 事件   │  │
│  └────────────────────────┬───────────────────────────────────┘  │
│                           │                                       │
│  ┌────────────────────────┴───────────────────────────────────┐  │
│  │ 已有 Python 能力（一行不改）                                 │  │
│  │ ChatWorker(stream) · AgentRunner · tools · taskman · team    │  │
│  │ app/im/*（hub / store / policy / tags）· workspace · llm      │  │
│  └────────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────┘
```

**关键点：桥很薄。** 它不重新实现任何业务，只做三件事：

1. **上行**：`send(msg)` / `stop()` / `regenerate(id)` / `pick_file()` / `im_delete(ids)` →
   转成 Qt 信号 → 调用 `MainWindow` 上**已经存在**的槽（`_send`、`_on_send_clicked`、
   `_im_delete_msgs`……）。
2. **下行**：把 `ChatWorker.token / reason / tool_start / tool_result / finished / phase`
   与 `AssistantService.incoming` 转成 JS 的 `dispatch(type, payload)`。
3. **兜底**：页面加载失败、通道断开、开关切回 Qt。

**不要做的事**（这一条比做什么更重要）：不要在桥里写「怎么调模型」「怎么判断该不该回消息」。
一旦业务逻辑漏进桥，就会变成「Qt 版和 Web 版两套行为」，日后必然分叉。

---

## 4. 通信方案怎么选

| 方案 | 流式契合度 | 安全面 | 依赖 | 结论 |
| --- | --- | --- | --- | --- |
| **QWebChannel** | 中（要自己合批） | **最好**：不走网络栈，没有端口，外部程序连不上 | 已有（`QtWebChannel.pyd` 已装、已打包） | **选它** |
| 本地 HTTP + SSE | **最好**（`EventSource` 原生流式） | 差一档：要随机端口 + `token_urlsafe` + `Origin` 白名单 + 防本机其它进程乱调 + 防进程残留 | `aiohttp` 或 `http.server` + 线程 | 一期不做，留给「远程访问」 |
| WebSocket | 好 | 同 HTTP（一样要端口和鉴权） | 额外依赖（`websockets`） | 不做 |
| 自定义 scheme `app://` | 不能通信 | 无 | 无 | 只用来**加载静态资源** |
| `file://` 直开 | — | — | 无 | **不要用**，见下 |

### 4.1 为什么不用 `file://` 直接打开页面

这是最容易踩的一个坑：**`file://` 下 ES module 会被 CORS 拦掉**（origin 是 `null`），
`fetch()` 读本地 JSON 也会失败，`localStorage` 行为不确定。要么退回「一个巨型非 module 脚本」，
要么加 `--allow-file-access-from-files`（等于把整个文件系统对页面敞开，不可接受）。

### 4.2 推荐做法：注册 `awb://` 自定义 scheme 承载静态资源

在 `QApplication` 创建**之前**注册，然后用一个 `QWebEngineUrlSchemeHandler` 把
`awb://ui/xxx` 映射到 `app/webui/xxx`：

```python
# 必须在 QApplication 之前
scheme = QWebEngineUrlScheme(b"awb")
scheme.setFlags(QWebEngineUrlScheme.Flag.SecureScheme |
                QWebEngineUrlScheme.Flag.LocalAccessAllowed)
QWebEngineUrlScheme.registerScheme(scheme)
```

好处：**有正常 origin**（ES module、`fetch`、`localStorage` 都能用）、**零端口**、
**不用构建**（文件改了刷新就生效）、打包时只要把 `app/webui/` 整个目录 `--add-data` 进去。

`qwebchannel.js` 也不用从网上下：**它就在 Qt 的资源里**。实测本机
`:/qtwebchannel/qwebchannel.js` 存在，**16,510 字节**（`QFile` 可读，
前提是先 `import PyQt6.QtWebChannel`）。桥在 scheme handler 里把它当 `/js/vendor/qwebchannel.js`
吐出去即可——**不引 CDN、不手工维护第三方文件**。

### 4.3 QWebChannel 的三个真实坑（必须按这个写）

1. **不要在 `@pyqtSlot` 里做耗时活**。槽函数跑在 UI 线程，一个 `time.sleep` 就是整窗卡死。
   桥的槽只做「打包参数 → `emit` 信号」，真正的活交给已有的 `ChatWorker(QThread)`。
2. **流式必须合批**。逐 token 走一次 JSON 序列化 + 跨进程 IPC，40 token/s 时 UI 线程会被打满。
   做法：桥里攒一个缓冲区，`QTimer` 每 **50ms** flush 一次（一帧 16ms 的 3 倍，肉眼看仍是逐字）。
   代价只是把 40 次/秒压到 20 次/秒，**消息体不变**。
3. **页面重载后对象会失效**。`QWebChannel` 的 `objects` 只对当前页面实例有效；
   刷新/切页后必须重新 `new QWebChannel(qt.webChannelTransport, ...)`。
   前端 `bridge.js` 要在 `beforeunload` 时把未完成的请求标记为失败，避免"永远转圈"。

### 4.4 以后要支持「网页版远程访问」怎么加

**别现在做，但别把路堵死。** 做法是把前端与传输层隔开：

```
views/*.js  →  store.js  →  bridge.js  →  transport-qwebchannel.js   （现在）
                                       →  transport-http-sse.js      （将来）
```

`bridge.js` 只暴露 `call(name, args)` 和 `on(type, fn)` 两个 API，视图层永远不碰 `qt.webChannelTransport`。
将来要远程访问时，新增一个 `aiohttp` 服务（绑 `127.0.0.1` 或用户显式开启的局域网地址 +
`secrets.token_urlsafe(32)` + `Origin` 白名单 + 只允许 `POST`），把 `transport` 换掉即可，
**视图和桥一行不用改**。

> 顺带提醒一个现有事实：`main.py` 启动时（`from app.webengine import enable_remote_debugging`，
> 当前版本在第 31～33 行）都会打开 CDP 调试口
> （`QTWEBENGINE_REMOTE_DEBUGGING=9333`）。**本机任意程序都能连上它控制页面**。
> 这不是本次改造引入的，但如果将来真的加 HTTP 服务，这个口子应该一并收掉
> （仅在 `--debug` 时开启）。

---

## 5. 前端技术选型

### 5.1 结论：**不引框架**

原生 ES module + 少量 Web Components + 一个自写的 ~50 行响应式 store。
理由按重要性排序：

1. **不能有构建链**。这个项目是「一个人维护 + PyInstaller 打包 + 必须离线」。
   引入 Vue/React 就意味着 `package.json` + `node_modules` + `vite build` + 产物提交，
   以后每次改一行 CSS 都要记住先 build——这是把简单问题复杂化。
2. **不需要框架的能力**。这个界面的状态极简单：一个 `messages` 数组、一个 `currentConv`、
   一个 `theme`、一个 `imRows`。没有深层嵌套路由，没有复杂表单联动。
   React 的 diff 在这里换不来任何东西，反而要多背 40KB runtime。
3. **Web Components 够用**。`<msg-bubble>`、`<tool-capsule>`、`<think-card>` 三个自定义元素
   就能把重复结构收干净，原生 `customElements.define` 不需要任何依赖。

**什么时候该改主意**：组件数超过 ~40 个、或者需要「同一份视图同时服务桌面与手机网页」时，
再上 Vue 3（`petite-vue` 也行）。现在不到这个量级。

### 5.2 硬约束

- **禁止 CDN**：所有 JS/CSS/图标/字体必须是本地文件。图标用内联 SVG，
  字体只用系统字体（Windows 自带）。
- **禁止远程字体**：`"Microsoft YaHei UI"` 就够，不要为了「高级感」引 Inter——
  中文会掉字形，退回宋体/黑体，反而更丑。
- **资源路径全部相对**（`./css/base.css`），不要写 `/css/base.css`，
  否则 `awb://ui/index.html` 之外的地方加载会 404。

### 5.3 目录结构

```
app/webui/
├── index.html                 唯一入口（一个壳 + 两个 page 容器）
├── css/
│   ├── tokens.css             两套主题变量（唯一改色的地方）
│   ├── base.css               reset / 字体 / 滚动条 / 通用控件
│   ├── chat.css               对话页
│   └── assistant.css          助理页
├── js/
│   ├── app.js                 启动、路由（page 切换）、键盘
│   ├── store.js               ~50 行：state + subscribe + patch
│   ├── bridge.js              call/on，传输无关
│   ├── transport-qwebchannel.js
│   ├── views/chat.js
│   ├── views/assistant.js
│   └── components/{msg,tool-capsule,think-card}.js
└── assets/icons/*.svg         （也可全部内联，二选一）
```

---

## 6. 视觉升级具体怎么做

### 6.1 先看清「现在为什么不精致」

对着 `app/themes.py` 和现有 QSS 看，问题不是配色，是**三件事**：

1. **圆角有 5 套**：8 / 9 / 10 / 12 / 20 混用（`QPushButton` 10、`QComboBox` 10、
   `cardBtn` 8、`QuickCard` 12、`MicBtn` 20）。眼睛说不出哪里怪，但会觉得"不整体"。
2. **层次靠描边**：几乎所有容器都是 `1px solid #e3e6ea`，没有阴影，于是卡片和背景"贴"在一起，
   只能靠线分隔——这正是"廉价感"的主要来源。
3. **没有过渡**：Qt QSS 不支持 `transition`，展开/收起/切页都是硬跳。

### 6.2 设计 token（拿 `app/themes.py` 的色值当基线，统一收敛）

**圆角**：只留 3 档 —— `6px`（小控件）/ `9px`（卡片、按钮、胶囊）/ `14px`（输入卡、浮层）。
**间距**：4 的倍数：4 / 8 / 12 / 16 / 20 / 24 / 32。
**字阶**：11（辅助）/ 12（次要）/ 13.5（正文）/ 15（小标题）/ 20（页面标题）。

| Token | 浅色 | 深色 | 用途 |
| --- | --- | --- | --- |
| `--bg` | `#f5f6f8` | `#16181c` | 主内容底 |
| `--surface` | `#ffffff` | `#1f2329` | 卡片 / 输入卡 |
| `--surface-2` | `#eef0f3` | `#262b32` | 胶囊 / hover |
| `--sidebar` | `#fafbfc` | `#1a1d22` | 侧栏 |
| `--border` | `#e4e7eb` | `#2b3138` | 常规分隔（1px） |
| `--border-strong` | `#d5dae0` | `#3a414a` | hover / 输入框 |
| `--text` | `#191c1f` | `#e7ebf0` | 正文 |
| `--text-2` | `#5b6470` | `#a3adb9` | 次要 |
| `--text-3` | `#8b95a1` | `#77818d` | 时间 / 提示 |
| `--accent` | `#2f6feb` | `#5b8ef7` | 主色（比现在的 `#2196F3` 更沉、更"工具感"） |
| `--accent-soft` | `#e8f0fe` | `rgba(91,142,247,.14)` | 选中底 |
| `--user-bubble` | `#2f6feb` | `#3468cf` | 用户气泡 |
| `--ok` / `--warn` / `--danger` | `#17864a` / `#b4741a` / `#d13b3b` | `#3fb950` / `#d29922` / `#f85149` | 状态 |
| `--shadow-1` | `0 1px 2px rgba(16,24,40,.06), 0 1px 3px rgba(16,24,40,.04)` | `0 1px 2px rgba(0,0,0,.5)` | 卡片 |
| `--shadow-2` | `0 8px 24px -6px rgba(16,24,40,.14)` | `0 10px 28px -8px rgba(0,0,0,.6)` | 输入卡 / 浮层 |

**三个"立刻能看出变化"的点**（按性价比排序）：

1. **统一 8px 栅格 + 3 档圆角**。这一条不改任何颜色，观感就能提升一档。
2. **层次改成「柔和阴影 + 1px 描边」双保险**。卡片浮起来 1～2px 的感觉，
   比加一条深色边线高级得多；描边只用来定边界，颜色要淡。
3. **关键动效**：消息进入 `opacity+translateY(6px)` 180ms、工具胶囊展开 `grid-template-rows`
   或 `max-height` 160ms、页面切换 120ms 淡入。**只动 `transform` / `opacity`**，
   不动 `width/height/top`（避免每帧重排）。

**必须点名的一条性能红线**：`app/webengine.py:57` 默认就带 `--disable-gpu`
（软件渲染）。这意味着：

- **不要用 `backdrop-filter: blur()`**（毛玻璃在软件渲染下会明显掉帧，全屏滚动时尤其糟）；
- **不要做"阴影动画"**（`box-shadow` 过渡是逐帧重绘）；
- **大面积 `filter: blur()` 一律不用**；渐变可以用，但别铺满整屏。

原型里的所有效果都是在「无 GPU」前提下也能跑满 60fps 的做法。

### 6.3 消息列表必须做虚拟滚动

这是 Web 化最容易翻车的地方，**必须点名**：现在 `main_window._render()` 是重建整串
`MessageCard`，500 条消息时 Qt 会卡但还能忍；换成 DOM 之后，500 条富文本 + 代码块
会直接让首次渲染掉到几百毫秒、滚动掉帧。

做法（二选一，推荐第一种）：

- **固定行高窗口化**：只渲染可视区 ± 10 条，上下用两个 spacer `div` 撑高度。
  实现约 80 行，缺点是消息高度不等时需要预估（可以先按「文本行数估算」再校正）。
- **`content-visibility: auto` + `contain-intrinsic-size`**：两行 CSS 就能让浏览器
  跳过屏外元素的渲染与布局。**这是最省事的起步方案**，先上它，
  等真的出现万条消息再换窗口化。

配套：流式期间**不要**每 50ms 重建整条消息的 DOM，只更新一个 `Text` 节点的
`nodeValue`（React 的 diff 在这里反而是负担）。

### 6.4 中文字体栈

```css
--font: "Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI",
        system-ui, sans-serif;
--mono: "Cascadia Mono", "Consolas", "Courier New", monospace;
```

**不要**把 `Inter` / `-apple-system` 放主位：Windows 上前者缺中文字形（会 fallback 到宋体，
字重和西文对不上），后者在 Windows 上不存在。中文正文建议 `13.5px / line-height 1.75`——
比现在 Qt 的 15px 更紧凑，但行距更松，读起来更"现代"。

---

## 7. 分三期落地

统一开关（**每期都必须可用**）：

```
settings["ui_engine"] = "qt" | "web"      # 默认 "qt"
命令行 --ui-engine=qt 覆盖设置             # 防止设置写坏导致打不开
环境变量 AWB_WEBUI=0 再兜一层              # 最外层保险
```

开关切换后**不需要重启**（重建 stack 里对应的那一页即可），但为了简单，一期可以要求重启
——**可靠性优先于体验**。

### 一期：对话页（目标：让用户"看见差距"）

- **改**：新增 `app/webui/**`、`app/webui_host.py`、`app/webui_bridge.py`；
  `main_window._build_shell()` 里按开关决定 `self.page_chat` 用 Qt 版还是 Web 版；
  `build_v9210.py` 加一条 `--add-data`。
- **交付**：欢迎态 / 消息流（用户气泡、助手无气泡）/ 工具胶囊 / 思维链卡片 /
  流式输出 / 停止 / 重答 / 附件条 / 团队条 / 模型选择 / 输入框 / 主题跟随。
- **不做**：虚拟滚动可以先用 `content-visibility` 顶；Markdown 渲染先只支持
  标题/列表/加粗/代码块/表格（用自写的 60 行渲染器，或复用 Python 侧
  `markdown_render.py` 的输出直接喂 HTML——**推荐后者，保证和 Qt 版一致**）。
- **回退**：`ui_engine=qt`。

### 二期：助理页（钉钉助手三标签）

- **改**：`AssistantPage` 外包一层分支；桥增加 `im.*` 命令组
  （`rows / delete / save_settings / whitelist_add / whitelist_remove / scan_tags`）。
- **交付**：双栏消息（勾选、删除按钮禁用/启用）、设置表单、标签扫描与编辑。
- **注意**：`app/im/*` 一个字节都不要动，**前端只做渲染与收集**；
  「该不该回」「怎么回」仍然在 `policy.py` / `hub.py` 里。
- **回退**：同上（两页独立开关：`ui_engine_chat` / `ui_engine_im`，便于灰度）。

### 三期：外壳 Web 化（B → B+，可选）

- **改**：`main_window` 改为「整窗一个 `QWebEngineView`」，侧栏/标题条/其余 9 页逐步迁进 Web；
  Qt 只保留托盘、快捷键、单实例、原生孩子对话框。
- **交付**：视觉完全统一，不再有 Qt/Web 拼接感。
- **前置条件**：一期二期的桥证明稳定（至少跑满一个版本周期）。
- **回退**：仍保留 `ui_engine=qt` 整套旧界面，**至少保留两个版本再删**。

---

## 8. 风险清单

| 风险 | 真实表现 | 应对 |
| --- | --- | --- |
| **GPU 崩 / 白屏** | 某些核显机器上 Chromium 渲染进程直接挂 | 现有代码**已经默认 `--disable-gpu --no-sandbox`**（`app/webengine.py:59-64`），这条风险已被历史踩平；再加一个 `AWB_WEBUI_GPU=1` 给想开硬件加速的人 |
| **首屏白屏** | `QWebEngineView` 建出来到首帧之间有 200～600ms 空白 | 先显示 Qt 的骨架/占位，`loadFinished` 后再切换；加载**超时 3 秒**就自动回退 Qt 页面并提示 |
| **中文输入法** | QWebEngine 在 Windows 上原生支持 IME，但候选框定位、`compositionend` 与 `Enter` 发送的时序要验证 | 发送逻辑判断 `event.isComposing`；**这正是必须实测的一条**，不能只靠"官方支持" |
| **拖拽文件** | 网页里 `drop` 拿到的是 `File` 对象，**拿不到真实路径**，而 `tools` 需要路径 | 在 Qt 侧给 `QWebEngineView` 装事件过滤器，`dragEnter/drop` 里取 `QMimeData.urls()`，通过桥把路径推给页面（页面只负责显示附件条） |
| **剪贴板** | `navigator.clipboard` 在非 https 上下文可能不可用 | 复制走桥：`QGuiApplication.clipboard().setText()`。**别依赖浏览器 API** |
| **托盘菜单仍在 Qt 侧** | 网页点不到托盘 | 托盘菜单只发命令给网页（`show_page('chat')` / `new_conversation()`），不在网页里模拟托盘 |
| **滚动性能** | 500 条消息 + 代码块，软件渲染下掉帧 | `content-visibility` 起步，必要时上窗口化；流式只改文本节点 |
| **内存** | 现在 `BrowserPage` 是懒加载，加了 Web 对话页后**启动就常驻一个 Chromium**，实测大约 +150～250MB | 接受它（这是 Web 化的固定成本）；如果不可接受，一期可以先只做「对话页」，且启动时不建 `BrowserPage` |
| **调试** | 网页出问题没法 print | `view.page().setDevToolsPage(other_view.page())` 开独立 devtools 窗口；仅 `--debug` 时开。注意 devtools 会抢焦点 |
| **回退** | 一旦 Web 页起不来，用户就进不去 | 三重开关（设置 / 命令行 / 环境变量），且**加载失败自动回退**，不依赖用户操作 |
| **新旧行为分叉** | Web 版和 Qt 版表现不一致 | 桥只做翻译；Markdown 渲染复用 Python 侧输出；两版共用同一份 `settings` 与 `workspace` |

---

## 9. 工作量与取舍

| 模块 | 相对工作量 | 为什么 |
| --- | --- | --- |
| 桥接层（`webui_bridge.py` + `bridge.js`） | **中** | 代码量不大，但通道/生命周期/合批/重载恢复最容易埋坑，是全局风险点 |
| 对话页前端 | **大** | 消息流、流式、工具胶囊、思维链、附件、Markdown、滚动，占全案 60% 的活 |
| 助理页前端 | **中** | 结构固定（三标签 + 双栏 + 表单），但细节多（勾选/删除/白名单/标签） |
| 主题 token 对齐 | **小～中** | 两套 CSS 变量，要和 `themes.py` 语义一一对应，否则切主题会"色不对" |
| 外壳接入（main_window 分支） | **小** | 加分支 + 一个开关，不动既有逻辑 |
| 打包 | **小** | 一条 `--add-data` + 自检里加一项 `awb://ui/index.html` 能否打开 |
| 其余 9 页 | **大** | 三期才做；如果永远不做，B 就停在"半新半旧" |

### 值不值得做

**值得，但要按这个顺序做：先只做对话页，做完让用户看，再决定要不要继续。**

理由：观感收益的绝大部分在对话页；而对话页正好是**唯一一个可以独立替换、不影响其它 9 页**的页面。
一期做完如果用户觉得"就这"，损失只是一份前端资源和一个开关，删掉即可；
如果觉得"对，就是这个感觉"，二期三期的路是通的，而且**不需要换运行时**。

**反过来，如果一上来就做 A（Electron）**：做完了发现托盘/快捷键/单实例有一堆边角问题，
那时候已经没有"切回 Qt"这个选项了。这是我不建议 A 的核心原因——**不是它做不出来，
而是它把一次可回退的实验变成了一次不可回退的迁移。**

---

## 附录：本方案依据的已核实事实

| 事实 | 核实方式 |
| --- | --- |
| `PyQt6.QtWebChannel` 已安装 | `site-packages/PyQt6/QtWebChannel.pyd` 存在 |
| `QtWebChannel` 已进打包清单 | `build_v9200.py:98` 的 `hidden` 列表 |
| WebEngine 运行时资源已进包 | `build_v9200.py:148` `_qtwebengine_add_data()`（`QtWebEngineProcess.exe` + `resources/` + `translations/`） |
| `qwebchannel.js` 无需外链 | `QFile(":/qtwebchannel/qwebchannel.js")` 可读，16,510 字节（需先 import `PyQt6.QtWebChannel`） |
| 已默认软件渲染 | `app/webengine.py:59-64` `QTWEBENGINE_CHROMIUM_FLAGS` 含 `--disable-gpu --no-sandbox` |
| CDP 调试口常开 | `main.py:31-33` `enable_remote_debugging()`，端口 `9333` |
| 现有主题 token 基线 | `app/themes.py:10`（LIGHT）/ `:38`（DARK） |
| 页面清单共 12 项 | `app/gui/pages.py:32` `PAGES` |
| 单实例已有 | `app/single_instance.py` + `main.py:434` 起的 `InstanceGuard` |
| 流式信号已具备 | `app/gui/chat_worker.py:175` `token` / `:176` `reason` / `:187` `phase` |
