# v9.19.0：DeepSeek 网页版宿主从「拉 Chrome」改为「内嵌 QtWebEngine」

> 本文是 v9.19.0 的增补记录。00~04 是 v9.14~v9.18 期间的设计与验收文档，
> 描述的 "拉起本机 Chrome + CDP" 路线仍然保留（作为回退通道），但**默认已不再使用**。

## 为什么改

- **多一个浏览器窗口/进程**：原来的做法要在后台静默拉起一个真 Chrome（`--headless=new`），
  冷启动约 45 秒；用户任务管理器里会多出一串 `chrome.exe`，还容易和用户自己正在用的 Chrome
  抢同一个 profile。
- **我们本来就有内核**：v9.18.0 为了「浏览器页 / web_visit」已经打进了 `PyQt6-WebEngine`，
  完全可以自己当宿主，不必再去借 Chrome。

## 怎么改的（关键点）

1. **换宿主不换驱动**
   `app/deepseek_web/web_driver.py`（JS 状态机、轮询、防重发、流式读取）**一行没改**，
   因为它只依赖标准 CDP；变化只在「谁提供这个 CDP 端点」。

2. **CDP 端口只有一个来源**
   `app/webengine.py` 里定义 `REMOTE_DEBUG_PORT = 9333` 与 `enable_remote_debugging()`，
   在 `QApplication` 创建**之前**设好 `QTWEBENGINE_REMOTE_DEBUGGING`，
   QtWebEngine 就会在 `http://127.0.0.1:9333/json` 暴露标准 CDP。
   `main.py` 在导入任何 GUI 模块前调用它。

3. **内嵌宿主 `app/deepseek_web/embedded.py`**
   - 命名 profile `QWebEngineProfile("deepseek-web")` + 显式 `setPersistentStoragePath`
     + `ForcePersistentCookies`，否则默认 profile 是 off-the-record，退出即丢登录态；
   - view 用 `WA_DontShowOnScreen`，后台常驻但 Chromium 仍认为页面 visible；
   - 需要人工登录时 `show_login()` 才把窗口显示出来。

4. **双引擎 + 自动回退**
   `service.py` 支持 `embedded`（默认）与 `chrome`。以下任一情况自动回退到 Chrome 并在日志里说明：
   `/json` 40 秒内没有 DeepSeek 页面 target、CDP 连不上、内核导入失败。

5. **QtWebEngine 的两条硬规则**（踩坑，必须遵守）
   - 必须在 `QApplication` **之前** import，否则 exit 127；
   - **`QApplication` 的 argv 不能是空列表**。`QApplication([])` 之后一建
     `QWebEngineProfile` / `QWebEngineView`，进程会**静默**死掉（脚本里 exit 127，
     测试框架里 0xC0000409），无栈无 stderr。一律写
     `QApplication(sys.argv[:1] or ["AIWorkbench"])`。

## 验收

`tests/probe_embedded_host.py`（离线、不需要登录）7 步全过：

1. 设端口后 `/json` 出现 DeepSeek page target；
2. `EmbeddedHost.start()` 成功且启动耗时 ~2 秒（原 Chrome 路线 ~45 秒）；
3. `CDPClient` WebSocket 连上；
4. `Page.navigate` 返回 ok；
5. `Runtime.evaluate("location.href")` 读回，UA 已去 `QtWebEngine` 指纹；
6. `WebDriver(embedded=True)` 的 `probe_state` 在未登录时**明确**返回
   `not_logged_in` 并给出中文操作指引（不会空等 40 秒、不会崩）；
7. profile 落盘目录存在且 `isOffTheRecord() == False`。
