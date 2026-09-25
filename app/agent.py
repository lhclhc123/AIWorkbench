# -*- coding: utf-8 -*-
"""Agent 工具执行：文件读写 / 文档生成 / 系统操作 / 联网 / 记忆 / 计划 / MCP。

安全模型（用户已授权）：
- 读：任意盘符只读；
- 写：除系统关键目录（Windows / Program Files / ProgramData / 本程序目录）外均可写；
- 删除、杀进程、危险代码：必须经用户确认，删除只进回收站；
- 命令执行：必须经用户确认，危险命令直接拦截。
"""
import os
import re
import sys
import json
import time
import shlex
import shutil
import fnmatch
import zipfile
import tempfile
import subprocess

_TOOL_RE = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.DOTALL)

# 「桌面 / 文档 / 下载 / 图片…」别名 -> Windows 真实路径
from . import folders as _folders  # noqa: E402
from . import winproc as _winproc  # noqa: E402（子进程一律走它，避免弹黑窗口）
# 工作总结
from . import worklog as _worklog  # noqa: E402

# 定时任务的文案工具（describe / next_text 在 agent 里要直接用）
from . import scheduler as _sched_mod  # noqa: E402

# 已知工具名（用于识别"跑偏"的退化写法）
KNOWN_TOOLS = (
    # 读 / 理解
    "read_file", "list_dir", "read_document", "read_image", "search_files",
    "fetch_url", "system_info", "now", "search_history", "window_list",
    # 写 / 生成
    "write_file", "create_document", "file_op", "open_path", "archive",
    # 执行 / 系统
    "run_command", "run_python", "list_processes", "kill_process",
    "clipboard", "screenshot", "notify",
    # 联网
    "web_search", "download_file", "http_request",
    # 记忆 / 计划 / 知识库
    "remember", "recall", "update_plan", "build_index", "search_knowledge",
    # 语音
    "transcribe_audio", "speak_text",
    # 钉钉
    "dingtalk_push", "dingtalk_status", "dingtalk",
    # 图片 / 图表
    "image_op", "create_chart",
    # 提醒 / 更新
    "reminder", "check_update",
    # 技能 / 定时任务 / 子代理
    "list_skills", "use_skill", "schedule_task", "list_tasks",
    "cancel_task", "run_task", "spawn_agent",
    # 工作总结
    "write_worklog", "read_worklog",
    # MCP
    "mcp_list", "mcp_call",
)

# 模型偶尔会用「别名 / 技能名 / 近似名」当工具名（实测：会把技能 slug
# 直接当工具名调）。这里做一次友好归一，既能自动路由，也能让日志/界面
# 记录下"实际执行的到底是谁"。
TOOL_ALIAS = {
    "skill": "use_skill", "load_skill": "use_skill",
    "get_skill": "use_skill", "read_skill": "use_skill",
    "list_skill": "list_skills", "skills": "list_skills",
    "schedule": "schedule_task", "task": "schedule_task",
    "cron": "schedule_task", "automation": "schedule_task",
    "tasks": "list_tasks", "subagent": "spawn_agent",
    "agent": "spawn_agent", "delegate": "spawn_agent",
    "chart": "create_chart", "make_chart": "create_chart",
    "doc": "create_document", "document": "create_document",
    "speak": "speak_text", "tts": "speak_text",
    "asr": "transcribe_audio", "stt": "transcribe_audio",
    "search": "web_search", "http": "http_request",
    "fetch": "fetch_url", "python": "run_python",
    "bash": "run_command", "shell": "run_command",
    "worklog": "write_worklog", "work_log": "write_worklog",
    "summary": "write_worklog", "diary": "write_worklog",
    "工作总结": "write_worklog", "read_log": "read_worklog",
    "worklog_read": "read_worklog", "list_log": "read_worklog",
}

# 每轮工具结果回喂时附带，强制模型继续用标准格式
TOOL_FORMAT_HINT = (
    "提醒：如果需要继续调用工具，必须在单独一行严格输出：\n"
    "<tool_call>{\"name\":\"工具名\",\"arguments\":{...}}</tool_call>\n"
    "不要只写工具名、不要只丢一段 JSON、不要把调用包在别的文字里。\n"
    "不要重复刚才已经执行过的同一个调用；也不要自己编造工具返回结果，\n"
    "更不要以「[工具 ... 返回]」开头替系统写结果——只有系统回喂的才是真实结果。\n"
    "如果任务已经完成，就直接给出最终答复，不要再输出任何工具调用。"
)


def _coerce(obj):
    """把解析出来的 JSON 规范化成 {name, arguments}。"""
    if not isinstance(obj, dict):
        return None
    name = str(obj.get("name") or "").strip()
    if not name:
        return None
    args = obj.get("arguments")
    if args is None:
        args = obj.get("args")
    if not isinstance(args, dict):
        args = {}
    return {"name": name, "arguments": args}


# 这些 key 的值是「路径」：里面的 \ 一律当分隔符，不能当转义（否则 C:\temp 变 TAB）
_PATH_KEYS = {"path", "paths", "dst", "src", "dest", "destination", "file",
              "filename", "filepath", "dir", "directory", "folder", "cwd",
              "root", "target", "save_as", "out", "output", "output_path"}
_HEXD = set("0123456789abcdefABCDEF")


def _repair_json(s):
    r"""把模型写坏的 JSON 转义修回来。

    实测重灾区（本机真实日志里出现过）：
      {"path": "D:\"}            -> \ 把结尾引号转义掉了，json.loads 直接失败
      {"path": "桌面\a.docx"}     -> \a 是非法转义
      {"path": "C:\temp\x.txt"}  -> \t 会被解析成 TAB，路径就废了

    策略：
      * 路径类 key 的值：所有 \ 都当字面分隔符（\t/\n 也照样当分隔符）；
      * 其他字符串：非法转义补一个 \；合法转义（\n \t \" \\ \uXXXX）保持原样；
      * "\" 后面紧跟引号时，用「引号后面是不是 } ] , : 或结尾」判断它是
        「路径结尾 + 字符串结束」还是「转义引号」。
    """
    out = []
    i, n = 0, len(s)
    in_str = False
    path_mode = False
    pending_key = None
    while i < n:
        ch = s[i]
        if not in_str:
            if ch != '"':
                out.append(ch)
                i += 1
                continue
            # 进入字符串：先判断它是不是一个 key（后面紧跟冒号）
            k, buf = i + 1, []
            while k < n:
                c = s[k]
                if c == "\\" and k + 1 < n and s[k + 1] == '"':
                    buf.append('"')
                    k += 2
                    continue
                if c == '"':
                    break
                buf.append(c)
                k += 1
            m = k + 1
            while m < n and s[m] in " \t\r\n":
                m += 1
            is_key = m < n and s[m] == ":"
            if is_key:
                pending_key = "".join(buf).strip().lower()
                path_mode = False
            else:
                path_mode = pending_key in _PATH_KEYS
            out.append(ch)
            in_str = True
            i += 1
            continue
        # ---- 字符串内部 ----
        if ch == "\\":
            nxt = s[i + 1] if i + 1 < n else ""
            if nxt == '"':
                m = i + 2
                while m < n and s[m] in " \t\r\n":
                    m += 1
                after = s[m] if m < n else ""
                if after in "}],:" or after == "":
                    # 其实是「路径末尾的反斜杠 + 字符串结束引号」
                    out.append("\\\\")
                    out.append('"')
                    in_str = False
                    path_mode = False
                    i += 2
                    continue
                out.append('\\"')
                i += 2
                continue
            if nxt == "u" and not path_mode:
                hx = s[i + 2:i + 6]
                if len(hx) == 4 and all(c in _HEXD for c in hx):
                    out.append(s[i:i + 6])
                    i += 6
                    continue
                out.append("\\\\")
                i += 1
                continue
            if nxt in "\\" and not path_mode:
                out.append("\\\\")
                i += 2
                continue
            if nxt in "/bfnrt" and not path_mode:
                out.append(ch)
                out.append(nxt)
                i += 2
                continue
            if nxt == "":
                out.append("\\\\")
                i += 1
                continue
            # 非法转义，或路径模式下的一律当字面
            out.append("\\\\")
            i += 1
            continue
        if ch == '"':
            out.append(ch)
            in_str = False
            path_mode = False
            i += 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _loads_lenient(chunk):
    """先严格解析，失败再用 _repair_json 修一次。"""
    try:
        return json.loads(chunk)
    except Exception:
        pass
    try:
        return json.loads(_repair_json(chunk))
    except Exception:
        return None


# 明显是"多行内容被写成一整行"的痕迹：出现字面量 \n 且完全没有真实换行
_LITERAL_NL_RE = re.compile(r"\\[nrt]")


def normalize_text_content(text, path=""):
    r"""把"换行被双重转义"的内容修回来。

    真实故障：模型把 JSON 里的换行写成 `\\n`（双重转义），json.loads 之后
    内容里是一个**字面的反斜杠 + n**，落盘的 .py 就是"一整行"。
    这里检测到「有字面 \n/\t 且几乎没有真实换行」时，把它们还原成真换行/TAB。

    只在「确实像被转义坏了」时才动手，避免误伤正文里正常的 \n 说明文字。
    """
    if not text or not isinstance(text, str):
        return text
    real_nl = text.count("\n")
    lit = _LITERAL_NL_RE.findall(text)
    if not lit:
        return text
    # 已经有较多真实换行 -> 认为格式正常，不碰
    if real_nl >= max(3, len(lit) // 2):
        return text
    # 代码/文本类扩展名，或字面 \n 数量明显偏多，才做还原
    ext = os.path.splitext(str(path or ""))[1].lower()
    code_like = ext in (".py", ".js", ".ts", ".java", ".c", ".cpp", ".h", ".hpp",
                        ".cs", ".go", ".rs", ".sh", ".bat", ".ps1", ".sql", ".html",
                        ".css", ".json", ".yaml", ".yml", ".toml", ".ini", ".xml")
    if not code_like and (real_nl > 0 or len(lit) < 4):
        return text
    out = (text.replace("\\r\\n", "\n").replace("\\n", "\n")
               .replace("\\r", "\n").replace("\\t", "\t"))
    return out


def _iter_json_objects(text, limit=25):
    """在文本里找出所有可解析的 JSON 对象（应对模型不按格式输出的情况）。"""
    starts = [i for i, ch in enumerate(text) if ch == "{"][:limit]
    ends = [i for i, ch in enumerate(text) if ch == "}"]
    for s in starts:
        for e in reversed(ends):
            if e <= s:
                continue
            obj = _loads_lenient(text[s:e + 1])
            if obj is None:
                continue
            yield obj
            break


def _json_objects(text, limit=40):
    """提取文本中所有能被解析的 {...} 片段（越长的优先）。"""
    objs = []
    starts = [i for i, ch in enumerate(text) if ch == "{"][:limit]
    ends = [i for i, ch in enumerate(text) if ch == "}"]
    for s in starts:
        for e in reversed(ends):
            if e <= s:
                continue
            obj = _loads_lenient(text[s:e + 1])
            if obj is None:
                continue
            objs.append((s, e + 1, obj))
            break
    objs.sort(key=lambda x: x[1] - x[0], reverse=True)
    return objs


def parse_tool_call(text):
    """从助手文本里尽力提取工具调用，返回 {name, arguments} 或 None。

    依次尝试：
      1) 标准 <tool_call>{...}</tool_call>
      2) ``` 围栏 / 裸文本里的 JSON（带 name 字段）
      3) 没有 name 字段的裸 JSON：用附近提到的工具名补齐
      4) "工具名\n{参数}" 或 "[工具 read_file {...}]" 这类退化写法
    拿到之后，会用消息里的 ```代码块``` 修补被写坏的长文本参数。
    """
    call = _parse_tool_call_once(text)
    if not call:
        return None
    return _salvage_from_fence([call], text)[0]


def _parse_tool_call_once(text):
    """真正的解析逻辑（不含代码块兜底）。"""
    if not text:
        return None

    # 1) 标准标签
    for m in _TOOL_RE.findall(text):
        obj = _loads_lenient(m)
        call = _coerce(obj) if obj is not None else None
        if call:
            return call

    stripped = re.sub(r"```(?:\w+)?", "", text)

    # 2) 带 name 的 JSON 对象
    for _s, _e, obj in _json_objects(stripped):
        call = _coerce(obj)
        if call:
            return call

    # 3) 裸参数 JSON + 上下文里出现的工具名（参数键必须像工具参数，避免误伤普通回答）
    _ARG_KEYS = {"path", "command", "content"}
    mentioned = [t for t in KNOWN_TOOLS if t in stripped]
    if len(mentioned) == 1:
        for _s, _e, obj in _json_objects(stripped):
            if isinstance(obj, dict) and obj and not obj.get("name") \
                    and set(obj.keys()) <= _ARG_KEYS:
                return {"name": mentioned[0], "arguments": obj}

    # 4) 退化写法：工具名 + 参数（可能带「工具」「调用」等前缀和方括号）
    m = re.search(
        r"[\[（(]?\s*(?:工具|调用|tool)?\s*[:\-]?\s*([A-Za-z_]\w*)\s*[\r\n ]*(\{.*?\})\s*[\]）)]?",
        stripped, re.DOTALL)
    if m and m.group(1) in KNOWN_TOOLS:
        args = _loads_lenient(m.group(2))
        if isinstance(args, dict):
            return {"name": m.group(1), "arguments": args}
    # 5) 严格 JSON 全挂了（模型常把 Windows 路径转义写坏）-> 正则兜底
    for chunk in ([m for m in _TOOL_RE.findall(text)] + [stripped]):
        call = _loose_call(chunk)
        if call:
            return call

    # 4b) 只有工具名没有参数（如 list_dir 空参数）
    m2 = re.search(r"[\[（(]\s*工具\s+([A-Za-z_]\w*)\s*[\]）)]", stripped)
    if m2 and m2.group(1) in KNOWN_TOOLS:
        return {"name": m2.group(1), "arguments": {}}
    return None


# 用户明显想让 AI「真的对自己的电脑做点什么」时，AI 只丢代码不给结果 -> 用这条纠正
EXECUTE_HINT = (
    "你刚才没有真正执行任何工具，或者自己编造了工具返回内容——这是不允许的，"
    "用户只会看到真实执行的结果。\n"
    "请立刻真正调用工具：\n"
    "读文件用 <tool_call>{\"name\":\"read_file\",\"arguments\":{\"path\":\"文件名\"}}</tool_call>\n"
    "列目录用 <tool_call>{\"name\":\"list_dir\",\"arguments\":{\"path\":\".\"}}</tool_call>\n"
    "跑命令用 <tool_call>{\"name\":\"run_command\",\"arguments\":{\"command\":\"命令\"}}</tool_call>\n"
    "然后基于系统回喂的真实结果作答，绝对不要自己编造结果。"
)

# 工具刚报错就给出"结果" -> 提醒不许编造
NO_FABRICATE_HINT = (
    "刚才那个工具调用**失败了**（返回了 [错误]），你并没有拿到任何真实数据。\n"
    "现在请你：\n"
    "1) 如实告诉用户这一步失败了、失败原因是什么；\n"
    "2) 换一种可行的方式重试（例如换一个路径、改用 run_command）；\n"
    "严禁凭空编造目录列表、文件内容或任何看起来像结果的东西。"
)

# 循环跑完还没给出答复 -> 最后一次机会，让它收个尾
WRAPUP_HINT = (
    "工具调用阶段已结束。现在请直接给用户的最终答复：\n"
    "说明你实际做成了什么、哪一步失败了、失败原因、以及建议用户下一步怎么做。\n"
    "不要继续调用工具，不要编造结果，也不要留空。"
)

# 用户要"真的写入/修改文件"，但模型只读不写、甚至说"模拟环境无法修改" -> 强制它真写
WRITE_NUDGE_HINT = (
    "这个任务需要你真正地创建或修改文件，但你还没有调用过任何写入类工具。\n"
    "你是在用户本机真实运行的程序，完全可以写文件；请立刻真正调用工具：\n"
    "- 写文本/代码：<tool_call>{\"name\":\"write_file\",\"arguments\":{\"path\":\"文件名\",\"content\":\"完整内容\"}}</tool_call>\n"
    "- 生成 Word/Excel/PPT/PDF：<tool_call>{\"name\":\"create_document\",\"arguments\":"
    "{\"path\":\"报告.docx\",\"content\":\"# 标题\\n\\n正文…\"}}</tool_call>\n"
    "然后基于真实写入结果答复用户。\n"
    "不要说「模拟环境无法修改」「无法直接修改文件」——那是错误的，你确实能写。"
)

# 模型嘴上说"我已经创建了 xxx 文件"，但其实一个写入工具都没调 —— 这种假成功必须当场揭穿
FAKE_WRITE_HINT = (
    "注意：你刚才回复里声称「已经创建/写入了文件」，但你实际上**没有调用过任何写入工具**，"
    "文件并不存在。不要在没执行工具的情况下声称完成了操作。\n"
    "现在请真正调用工具把文件写出来；写完之后，你只能依据工具回传的「真实路径」来回答用户。"
)

# 用户意图里暗示"要动真格"的词
_ACTION_WORDS = ("查询", "查一下", "查看", "列出", "统计", "扫描", "检查", "看看",
                 "清理", "删除", "运行", "执行", "打开", "帮我", "算一下", "分析",
                 "新建", "创建", "写入", "写个", "写一个", "生成", "保存", "修改",
                 "改一下", "改成", "rename", "重命名", "复制", "移动", "整理",
                 "搜索", "搜一下", "截图", "抓取", "下载", "记住", "记一下",
                 "计划", "拆分", "索引", "建个索引", "压缩", "解压", "导出",
                 "整理一下", "调用", "检查一下")
# 这些是"我只是想要一段代码/解释"，不该被当成要真动手
_CODE_REQUEST_WORDS = ("怎么写", "如何写", "示例代码", "给个例子", "举个例子",
                       "解释", "什么是", "为什么", "原理", "区别", "介绍一下",
                       "用法", "教程", "怎么写代码", "写一段代码", "写个函数",
                       "写个脚本给我", "帮我写个代码")
_TARGET_WORDS = ("电脑", "本机", "这台", "系统", "磁盘", "硬盘", "目录", "文件夹",
                 "文件", "工作区", "进程", "内存", "cpu", "CPU", "端口", "环境变量",
                 "文档", "表格", "幻灯片", "PPT", "ppt", "演示文稿", "图片", "截图",
                 "屏幕", "网页", "网址", "资料", "剪贴板", "知识库")

# 用户明显要"真正写入/修改文件"的词（用于识别模型只读不写、临阵退缩的情况）
_WRITE_WORDS = ("新建", "创建", "写入", "写一个", "写个", "写一", "生成文件",
                "修改", "改成", "改一下", "改为", "保存", "重命名", "复制",
                "移动", "追加", "写回", "更新", "写进", "写到", "写个文件",
                "建个文件", "新建一个文件", "创建文件",
                "生成一个", "生成一份", "生成个", "做个", "做一份", "做一张",
                "导出", "写一份", "写份", "生成文档", "生成表格", "生成ppt",
                "生成PPT", "生成报告", "生成一份",
                # 实测补充：用户说"写程序/写代码/开发"时，模型常常只在聊天里贴一大段代码，
                # 一个文件都不落地。这些词必须能触发「写入催办」。
                "开发", "编写", "写程序", "写代码", "写脚本", "写一段程序", "写一段代码",
                "写个程序", "写个代码", "写个小程序", "写个小工具", "写个脚本",
                "编个程序", "编个脚本", "做个程序", "做个小程序", "做个小工具",
                "做个脚本", "做个小工具", "写一个小程序", "写一个小工具",
                "实现一个", "实现个", "敲代码", "写出来", "帮我搭", "搭一个",
                "保存到本地", "存到本地", "保存到电脑", "存到电脑", "保存到文件",
                "保存到桌面", "存到桌面", "保存为文件", "落盘", "给个文件",
                "生成代码", "生成程序", "生成脚本", "生成一个程序", "生成一个脚本",
                "代码给我保存", "把代码保存")
# 用户明显要"真正读取/列出/查询"数据（用于识别"没查就下结论"的编造）
_READ_WORDS = ("列出", "列一下", "列", "查看", "查看一下", "查一下", "查", "读取",
               "读一下", "读", "统计", "扫描", "检查", "看看", "查询", "浏览",
               "显示", "找一下", "搜索", "罗列")


def _dedupe(calls):
    """去掉连续重复的同一个调用（模型常把同一个调用写两遍）。"""
    out = []
    seen = set()
    for c in calls:
        key = (c.get("name"), json.dumps(c.get("arguments", {}), sort_keys=True,
                                         ensure_ascii=False))
        if key in seen:
            continue
        seen.add(key)
        out.append(c)
    return out


def parse_tool_calls(text, limit=4):
    """提取一条回复里的**全部**工具调用。

    模型经常一次发两个（如同时列 D: 和 E:），只执行第一个会导致另一个没数据、
    模型随后开始编造——所以这里必须全部取出来依次执行。
    """
    if not text:
        return []
    calls = []
    for chunk in _TOOL_RE.findall(text):
        obj = _loads_lenient(chunk)
        c = _coerce(obj) if obj is not None else None
        if not c:
            c = _loose_call(chunk)
        if c:
            calls.append(c)
    if calls:
        calls = _dedupe(calls)[:limit]
        return _salvage_from_fence(calls, text)
    single = parse_tool_call(text)
    return [single] if single else []


def synthesize_write_from_fence(text, default_name=None):
    """模型只丢了一个代码块、压根没调 write_file 时，帮它凑一个写入调用。

    只在「用户确实要求写文件 / 写程序」的场景由上层调用，
    路径默认落到工作区 files/ 下（模型也可以自己给路径）。
    """
    code, lang = extract_fence(text)
    if not code.strip():
        return None
    if not default_name:
        default_name = {"python": "main", "py": "main", "html": "index",
                        "css": "style", "javascript": "main", "js": "main",
                        "sql": "query", "json": "data", "markdown": "note",
                        "md": "note"}.get(lang, "code")
    ext = _LANG_EXT.get(lang, ".txt")
    return {"name": "write_file",
            "arguments": {"path": default_name + ext, "content": code},
            "_synthesized": True}


def wants_real_action(user_text):
    """用户这句话是不是想让 AI 对自己的电脑/文件真的做点什么。"""
    t = (user_text or "")
    low = t.lower()
    if any(w in t for w in _CODE_REQUEST_WORDS):
        return False  # 只是要代码/解释，不需要真的动手
    return (any(w in t for w in _ACTION_WORDS)
            and any(w in t for w in _TARGET_WORDS))


def needs_write_action(user_text):
    """用户这句话是不是要求真正写入/修改文件（用于识别"只读不写"的退缩）。"""
    t = (user_text or "")
    # 「这段代码怎么写 / 什么是 xx / 解释一下」只是在问，不是要落盘；
    # 但如果同一句话里明确出现了「保存 / 落盘 / 写到文件」，那还是要落盘。
    if any(w in t for w in _CODE_REQUEST_WORDS):
        if not any(w in t for w in ("保存", "落盘", "写到文件", "写进文件", "存到",
                                    "输出到文件", "保存成", "生成文件", "导出")):
            return False
    return any(w in t for w in _WRITE_WORDS)


def needs_read_action(user_text):
    """用户这句话是不是要求真正去读取/列出/查询数据（未执行工具就下结论=编造）。"""
    t = (user_text or "")
    return any(w in t for w in _READ_WORDS)


# ---------- 完成度看门狗：任务没真做完，就不许收尾 ----------

# 最终答复里出现这些话 = 在推卸/放弃，必须打回
_GIVEUP_PHRASES = (
    "你可以手动", "请您手动", "你自己复制", "请自行", "手动操作", "手动创建",
    "手动复制", "无法直接", "我无法", "我不能", "建议你打开", "由于无法",
    "很抱歉，我无法", "请你自己", "需要你手动",
)


def looks_like_giveup(text):
    """答复里是不是在让用户自己干 / 宣布自己干不了。"""
    t = (text or "")
    return any(p in t for p in _GIVEUP_PHRASES)


def completion_gaps(user_text, tools_used, write_done, verified_after_write,
                    plan_steps, final_text):
    """系统级「到底做完没有」检查，返回还没做到的事（字符串列表）。

    这是让 AI 能像真 Agent 一样"咬着任务不放"的关键：
    不看模型嘴上怎么说，只看**工具真实执行过什么、文件真实落盘了没有**。
    """
    gaps = []
    tools_used = set(tools_used or [])
    t = (user_text or "")

    if needs_write_action(t) and not write_done:
        gaps.append("用户要写文件，但**没有成功写出任何文件**")
    if needs_project_flow(t):
        if not write_done:
            gaps.append("用户要开发一个程序/项目，但**还没有写出任何代码文件**")
        elif not verified_after_write:
            gaps.append("代码写出来了，但**还没有真的运行/编译验证过**")
        if "system_info" not in tools_used and "run_python" not in tools_used \
                and "run_command" not in tools_used:
            gaps.append("还没有做过**环境检查**（system_info / run_python）")
        if "update_plan" not in tools_used:
            gaps.append("还没有用 update_plan 列出任务清单")
    if needs_read_action(t) and not (tools_used & {"list_dir", "read_file",
                                                   "read_document", "search_files",
                                                   "list_processes", "system_info",
                                                   "list_tasks", "fetch_url",
                                                   "web_search", "read_worklog"}):
        gaps.append("用户要查看/列出数据，但**一次都没真的去读**")

    pending = [s for s in (plan_steps or []) if s.get("status") != "done"]
    if pending:
        gaps.append(f"任务清单里还有 {len(pending)} 步没标完成："
                    + "、".join(str(s.get("text"))[:14] for s in pending[:4]))

    # 交付说明关：活干完了还不够，得让用户拿到手就能用。
    # 实测模型常在最后一句敷衍成"没有其他文件了/可以认为已完成"，
    # 用户根本不知道文件在哪、怎么跑 —— 这里强制补齐「在哪 + 怎么运行」。
    if needs_project_flow(t) and not gaps and final_text:
        ft = final_text or ""
        has_where = (".py" in ft or ".exe" in ft or "路径" in ft
                     or "工作区" in ft or "文件夹" in ft or "目录" in ft)
        has_how = ("运行" in ft or "python" in ft.lower() or "双击" in ft
                   or "命令" in ft or "启动" in ft)
        missing = []
        if not has_where:
            missing.append("文件在哪（完整路径）")
        if not has_how:
            missing.append("怎么运行（具体命令）")
        if missing:
            gaps.append("最终答复里没写清楚" + "、".join(missing)
                        + "——交付说明不完整，用户拿到手不知道怎么用")

    if not gaps and looks_like_giveup(final_text or ""):
        gaps.append("你的答复在让用户自己动手 / 宣布自己做不了——"
                    "这是不允许的。请换工具、换路径、换方法重试，直到真的做成")

    return gaps


COMPLETION_HINT = (
    "系统检查发现这一轮**还没有真正做完**，以下是还差的事：\n"
    "{gaps}\n\n"
    "请继续用工具把它们做完（可以换路径、换工具、换方法），"
    "做完之前不要给用户最终答复，也不要让用户自己动手。"
)

JUDGE_PROMPT = (
    "你是任务完成度裁判。只输出一行 JSON，不要任何解释。\n"
    "判断依据：用户的要求是否**真的**被满足——以「工具真实执行过什么」为准，"
    "不能因为回复写得像完成了就算完成。\n"
    '输出格式：{"done": true/false, "missing": "还差什么（20 字以内，完成就空字符串）"}\n'
)


def judge_payload(user_text, done_things, final_text):
    """给裁判用的消息体。"""
    return [
        {"role": "system", "content": JUDGE_PROMPT},
        {"role": "user",
         "content": (f"用户的要求：{str(user_text)[:600]}\n\n"
                     f"本轮真实做过的事：{done_things}\n\n"
                     f"AI 准备给用户的最终答复：{str(final_text)[:900]}")},
    ]
# 用户要的是"真的独立做完一个项目"：检查环境 -> 列任务 -> 生成 -> 校验 -> 交付。
_PROJECT_VERBS = ("开发", "做一个", "做个", "写一个", "写个", "搞一个", "搞个",
                  "搭一个", "搭个", "实现一个", "实现个", "做一个完整的")
_PROJECT_NOUNS = ("项目", "程序", "软件", "工具", "应用", "app", "APP", "系统",
                  "游戏", "脚本", "爬虫", "管理器", "计算器", "编辑器", "播放器",
                  "平台", "网站", "网页", "界面")


def needs_project_flow(user_text):
    """用户这句话是不是「要你独立开发出一个成品」。

    只在这种情况下才强制走 环境检查→计划→生成→自校验→交付 五步，
    普通的"写个文件/生成个文档"不会被拖进这套流程。
    """
    t = (user_text or "")
    if not t:
        return False
    # 只是问怎么写 / 要示例代码 -> 不是要开发
    if any(w in t for w in _CODE_REQUEST_WORDS):
        return False
    verb = any(w in t for w in _PROJECT_VERBS) or "开发" in t
    noun = any(w in t for w in _PROJECT_NOUNS)
    return bool(verb and noun)


# 五步工作流的催办（哪步缺就催哪步）
PROJECT_STEP_HINTS = {
    "env": (
        "用户要的是「开发出一个能用的东西」，但你还没有做**环境检查**。\n"
        "请先真的调用工具确认本机环境，**优先用不弹确认框的工具**：\n"
        "- <tool_call>{\"name\":\"system_info\",\"arguments\":{}}</tool_call>\n"
        "  （CPU / 内存 / 各盘剩余空间）\n"
        "- <tool_call>{\"name\":\"run_python\",\"arguments\":{\"code\":"
        "\"import sys\\nprint(sys.version)\\n\"}}</tool_call>\n"
        "  （确认 Python 版本与可用库；run_python 不会弹确认框，"
        "run_command 会弹确认框、能不用就不用）\n"
        "不要在没看清环境的情况下假设本机装了什么。"
    ),
    "plan": (
        "用户要的是「独立开发出一个成品」，但你还没有把**任务清单**列出来。\n"
        "请立刻用 update_plan 把阶段和要产出的具体文件列清楚，例如：\n"
        "<tool_call>{\"name\":\"update_plan\",\"arguments\":{\"steps\":"
        "[\"检查环境\",\"编写 main.py\",\"自测跑通\",\"给出运行说明\"]}}</tool_call>\n"
        "列完再继续执行，每完成一步都要回来更新状态。"
    ),
    "verify": (
        "你已经把代码写出来了，但**还没有真正验证过它能不能跑**。"
        "没跑通的程序不算交付。\n"
        "请立刻用 run_python **真的执行一次**（编译 / 语法检查 / 跑个例子都算），"
        "看到报错就改，改到通过为止，然后把真实的运行输出写进最终答复。\n"
        "（run_python 不弹确认框；只有确实需要 shell 命令时才用 run_command。）"
    ),
}


# ---------- 多要求任务的「做全」检查 ----------

# 用户明确要求「记住点什么」
_MEMORY_WORDS = ("记住", "记一下", "帮我记", "记到记忆", "记进记忆", "记住我",
                 "帮我记住", "存到记忆", "记录一下我", "别忘了")
# 用户明确要求「列计划 / 分步骤」
_PLAN_WORDS = ("列个计划", "列一下计划", "制定计划", "做个计划", "任务计划",
               "拆成", "拆分", "分几步", "列一下步骤", "列出步骤", "步骤清单",
               # 实测补充：真实用户/测试脚本更常说下面这些说法，
               # 漏掉会导致「该列计划却没列」的催办不触发。
               "计划", "update_plan", "列出计划", "列个步骤", "先规划", "规划一下",
               "分步", "一步步", "按步骤")

# 形如 "1. xxx" / "- xxx" 的条目
_REQ_ITEM_RE = re.compile(r"(?m)^\s*(?:\d+[.)、]|[-*•])\s+\S")
# 形如 "一、xxx" / "第一，xxx"
_REQ_CN_RE = re.compile(r"(?m)^\s*(?:第[一二三四五六七八九十]+[、，,.]|[一二三四五六七八九十]+[、.])\s*\S")


def count_requests(user_text):
    """粗略统计用户一条消息里提了几个要求（用于检查"是不是只做了一部分"）。"""
    t = user_text or ""
    n = len(_REQ_ITEM_RE.findall(t))
    if n < 2:
        n = max(n, len(_REQ_CN_RE.findall(t)))
    if n >= 2:
        return min(n, 8)
    # 没有条目符号：看有没有"三件事/两个任务/几件事"这类说法
    m = re.search(r"([一二三四五六七八九十两\d]+)\s*(?:件事|个任务|个要求|个步骤|项)", t)
    if m:
        cn = {"一": 1, "两": 2, "二": 2, "三": 3, "四": 4, "五": 5,
              "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
        g = m.group(1)
        v = int(g) if g.isdigit() else cn.get(g, 0)
        if v >= 2:
            return min(v, 8)
    return n


def needs_remember(user_text):
    t = (user_text or "")
    return any(w in t for w in _MEMORY_WORDS)


def needs_plan(user_text):
    t = (user_text or "")
    return any(w in t for w in _PLAN_WORDS)


# 用户一条消息里提了多个要求，但 AI 只做了一部分 -> 催它做全（只催一次）
PART_NUDGE_HINT = (
    "用户这一条消息里提出了**多个要求**，而你目前只完成了其中一部分。\n"
    "请对照上面对话里用户的原始要求逐条检查，把**还没做的**继续用工具做完，例如：\n"
    "- 生成 Word/Excel/PPT/PDF：<tool_call>{\"name\":\"create_document\","
    "\"arguments\":{\"path\":\"文件.docx\",\"content\":\"# 标题\\n\\n正文\"}}</tool_call>\n"
    "- 记住信息：<tool_call>{\"name\":\"remember\",\"arguments\":"
    "{\"text\":\"要记住的内容\",\"category\":\"偏好与习惯\"}}</tool_call>\n"
    "- 维护任务计划：<tool_call>{\"name\":\"update_plan\",\"arguments\":"
    "{\"steps\":[\"第一步\",\"第二步\"]}}</tool_call>\n"
    "全部做完后再统一总结。如果确实每一条都已经做完了，就用分条的方式说明"
    "每一条分别是怎么完成的。"
)

# 用户要求「记住」但 AI 没调 remember
REMEMBER_NUDGE_HINT = (
    "用户明确要求你**记住**某些信息，但你还没有调用 remember 工具，什么都没记下来。\n"
    "请立刻调用：<tool_call>{\"name\":\"remember\",\"arguments\":"
    "{\"text\":\"要记住的完整内容\",\"category\":\"偏好与习惯\"}}</tool_call>\n"
    "（category 可填：用户与身份 / 偏好与习惯 / 项目与约定 / 重要结论 / 待办与计划）"
)

# 用户要求「列计划」但 AI 没调 update_plan
PLAN_NUDGE_HINT = (
    "用户要求把任务列成计划/步骤，但你还没有调用 update_plan，界面上什么都没显示。\n"
    "请立刻调用：<tool_call>{\"name\":\"update_plan\",\"arguments\":"
    "{\"steps\":[\"第一步\",\"第二步\",\"第三步\"]}}</tool_call>\n"
    "之后每完成一步，都要再调一次 update_plan，把该步标成 done、下一步标成 doing。"
)

# 计划还有步骤没标记完成 -> 催它收尾（否则界面上进度一直停在 0%）
PLAN_FINALIZE_HINT = (
    "你的任务计划里还有步骤没有标记为完成，界面上的进度还停在 0%，看起来像没做完。\n"
    "请对照实际情况处理：\n"
    "- 如果这些步骤其实**已经做完了**，立刻调用 update_plan，把每一步的 status 都改成 done，"
    "让进度变成 100%；\n"
    "- 如果确实**还没做完**，就继续把没做的用工具做完，然后再更新计划。\n"
    "格式：<tool_call>{\"name\":\"update_plan\",\"arguments\":{\"steps\":"
    "[{\"text\":\"第一步\",\"status\":\"done\"},{\"text\":\"第二步\",\"status\":\"done\"}]}}</tool_call>"
)


def gave_code_only(text):
    """助手只给了代码（代码块/命令行），没给出任何执行结果。"""
    if not text:
        return False
    if "```" in text:
        return True
    return bool(re.search(r"^\s*(?:>>>|\$|C:\\>|PS\s*C:\\>)", text, re.MULTILINE))


_FAKE_RE = re.compile(
    r"^\s*[\[（(]?\s*(?:系统已执行|系统回传|系统执行)?\s*工具\s*[^\n]{0,40}?(返回|输出|结果)",
    re.MULTILINE)


def faked_tool_result(text):
    """模型自己编造了「工具返回」内容（没有真的调用），必须打回。"""
    return bool(_FAKE_RE.search(text or ""))


# 工具结果回喂后附的话：只要求"基于真实结果直接回答"，
# 不再复述一堆格式纪律（实测会把模型带偏去评论指令本身，而不是回答用户）
ANSWER_NOW_HINT = (
    "以上是工具执行后回传的真实结果，可以继续用它们干活。\n"
    "判断标准只有一条：**用户交代的事到底做完了没有**。\n"
    "  · 还没做完 -> 继续调用工具（换方法、换路径也要做），不要急着回答；\n"
    "  · 确实做完了 -> 再给最终答复，并写清产出在哪、怎么用。\n"
    "绝对不要编造结果，也不要评论这些说明文字。\n"
    "注意：不要因为「已经调用过几次工具」就收工，那是偷懒；"
    "没做完就停下来等于没做。"
)

# 模型不去答问题，反而对着系统说明"表态"——必须打回
_META_TALK_WORDS = ("感谢您的提醒", "感谢你的提醒", "感谢您的指导", "感谢你的指导",
                    "感谢您的指正", "感谢指正", "已收到提醒", "收到您的提醒",
                    "按照正确的格式", "按照指定的格式", "正确的格式提供",
                    "指定格式进行", "严格遵守这些规则", "严格遵守规则",
                    "我会注意", "我明白了", "不会重复执行", "不会编造工具",
                    "不会编造结果", "以上是我的", "希望这能帮到", "如有 other")


def is_meta_talk(text):
    """回复对象变成了系统说明本身，而不是用户的问题。"""
    if not text:
        return False
    return any(w in text for w in _META_TALK_WORDS)


def tool_result_feedback(name, result):
    """把工具真实结果包装成回喂消息（措辞刻意不像助手口吻，降低被模仿的概率）。"""
    return (f"（系统已执行工具 {name}，以下是它回传的真实输出，请勿复述这个标题）\n"
            f"{result}\n\n{ANSWER_NOW_HINT}")


def looks_like_tool_attempt(text):
    """文本看起来想调用工具但格式不对 —— 这种情况要纠正重试，而不是当成最终答复。"""
    if not text:
        return False
    if parse_tool_call(text):
        return False
    low = text.lower()
    return any(t in low for t in KNOWN_TOOLS) and ("{" in text or "工具" in text)


# 模型"嘴上说已经写好了文件"的常见说法（它其实一个写入工具都没调）
_CLAIM_WRITE_RE = re.compile(
    r"(?:已经|已|我)?\s*(?:成功)?\s*"
    r"(?:创建|新建|写入|写好|建好|生成|保存|存放|存到|写入到|保存到|写了)"
    r"(?:了|好|完成|完毕|成功)?"
    r"[^。！？\n]{0,40}?"
    r"(?:文件|文档|\.txt|\.md|\.docx|\.xlsx|\.pptx|\.pdf|\.py|\.csv|\.json|"
    r"桌面|下载|文档|E:|D:|C:|/|\\\\)"
)


def claims_wrote_file(text):
    """模型声称「已经创建/写入了文件」。用它来抓"假成功"。

    配合 needs_write_action：用户要求写文件、但模型只在嘴上说写好了 —— 必须打回重试。
    """
    t = text or ""
    if not t:
        return False
    return bool(_CLAIM_WRITE_RE.search(t))

# 危险命令拦截（命中则直接拒绝）
_DANGER_PATTERNS = [
    r"\brm\s+-rf?\b", r"\brmdir\s+/s", r"\bdel\s+/[fq]", r"\bformat\s+[a-z]:",
    r"\bshutdown\b", r"\bhalt\b", r"\breboot\b", r"\bmkfs", r"\bdd\s+if=",
    r":\(\)\s*\{", r"\bdiskpart\b", r"\bcipher\s+/w", r"\bchkdsk\b\s*/f",
    r">\s*/dev/sd", r"\btruncate\b", r"\bsudo\s+rm",
]


def _decode(data):
    """Windows 中文命令输出可能是 GBK，按 utf-8 -> gbk -> 容错顺序解码，绝不抛异常。"""
    if not data:
        return ""
    if isinstance(data, str):
        return data
    for enc in ("utf-8", "gbk", "cp936", "latin-1"):
        try:
            return data.decode(enc)
        except Exception:
            continue
    return data.decode("utf-8", errors="replace")


def _unescape(s):
    """模型常把 Windows 路径写成 D:\\\\ 或 D:\\\\ 之类的怪转义，这里宽容还原。"""
    return s.replace("\\\\", "\\").replace('\\"', '"')


def _decode_html(content: bytes, headers=None, declared=None):
    r"""把网页字节解成文本，尽量不出乱码。

    顺序：
      1) 响应头里的 charset；
      2) HTML 里的 <meta charset> / <meta http-equiv=Content-Type>；
      3) 直接按 UTF-8 严格解码（现在绝大多数站点是 UTF-8）；
      4) requests 的 apparent_encoding；
      5) gbk / gb18030 / big5 / latin-1 兜底。

    实测教训：以前直接 `r.encoding = r.apparent_encoding`，遇到 UTF-8 中文页
    会被猜成 ISO-8859-1，整页变成 "8.8ç�­ Â· é�’æ˜¥…"，模型也就跟着乱答。
    """
    if not content:
        return "", "empty"
    # 1) 响应头
    cands = []
    if declared:
        cands.append(declared)
    head = ""
    try:
        head = (headers.get("Content-Type") or "") if headers else ""
    except Exception:
        head = ""
    m = re.search(r"charset=([\w\-]+)", head, re.I)
    if m:
        cands.append(m.group(1))
    # 2) HTML meta（只看前 8KB）
    try:
        sniff = content[:8192].decode("ascii", "ignore")
    except Exception:
        sniff = ""
    for pat in (r'<meta[^>]+charset\s*=\s*["\']?\s*([\w\-]+)',
                r'<meta[^>]+content\s*=\s*["\'][^"\']*charset=([\w\-]+)'):
        m2 = re.search(pat, sniff, re.I)
        if m2:
            cands.append(m2.group(1))
            break
    # 3) UTF-8 优先（无 BOM/无声明时，UTF-8 严格解码成功基本就是 UTF-8）
    try:
        content.decode("utf-8")
        cands.append("utf-8")
    except Exception:
        pass
    # 4) apparent_encoding
    try:
        import requests
        cands.append(requests.utils.get_encoding_from_headers(head) or "")
    except Exception:
        pass
    cands += ["gb18030", "utf-8", "big5", "latin-1"]
    for c in cands:
        if not c:
            continue
        try:
            txt = content.decode(c, "strict")
        except Exception:
            continue
        if _looks_mojibake(txt):
            continue
        return txt, c
    # 全都不行：用 replace 保证有内容
    return content.decode("utf-8", "replace"), "utf-8/replace"


_MOJI = ("Ã", "Â", "ã€", "â€", "å", "æ", "ç", "è", "é", "ï¿½", "\ufffd")


def _looks_mojibake(txt):
    """粗略判断是不是「UTF-8 被按单字节解码」产生的乱码。

    两条判据取其一：
      - 出现的"类 Latin-1 高位字符"（å æ ç è é Ã Â …）数量多；
      - 或者这类字符在整段里的占比偏高（短样本也能抓住）。
    """
    if not txt:
        return True
    sample = txt[:3000]
    bad = sum(sample.count(x) for x in _MOJI)
    if bad >= 8:
        return True
    if "\ufffd" in sample:
        return True
    # 短样本兜底：即使绝对数量少，但占比高（如 12 字里有 2 个）也判为乱码
    if bad >= 2 and len(sample) <= 400 and bad / max(len(sample), 1) > 0.12:
        return True
    if bad >= 6 and bad / max(len(sample), 1) > 0.06:
        return True
    return False


def _loose_call(text):
    """JSON 严格解析失败时的最后兜底：用正则把 name / 参数抠出来。

    ⚠️ 注意：这个兜底只适合**短参数**（path / command 这种一行就写完的）。
    长文本参数（content / code / markdown）只要里面出现了 \" 转义，
    正则就会在第一个 \" 处截断，把半截代码写进文件还报「成功」——
    实测真实故障：186 字节的残缺 bank_simulation.py。
    所以这里给结果打上 _loose 标记，交给 _salvage_from_fence() 用
    消息里真实换行的 ```代码块``` 覆盖掉长文本参数。
    """
    m = re.search(r'"name"\s*:\s*"([A-Za-z_]\w*)"', text)
    if not m:
        return None
    name = m.group(1)
    args = {}
    # 值用 [^"]* 而不是严格转义匹配：模型写坏转义时（如 "D:\"）严格模式会直接失配
    for k, v in re.findall(r'"(\w+)"\s*:\s*"([^"]*)"', text):
        if k == "name":
            continue
        args[k] = _unescape(v)
    return {"name": name, "arguments": args, "_loose": True}


# 长文本参数：这些一律不能信「宽松正则」抠出来的半截值
_LONG_TEXT_KEYS = ("content", "code", "markdown", "text", "source", "body", "html")


def extract_fence(text):
    """取出消息里最大的那个 ```代码块```，返回 (代码, 语言)。

    为什么要它：让模型把长代码写进「真实换行」的围栏代码块里，
    就完全绕开了 JSON 字符串转义（\\n / 双重转义 / 反斜杠丢失）这一整类坑。
    """
    if not text:
        return "", ""
    best, best_lang = "", ""
    for m in re.finditer(r"```([A-Za-z0-9_+#.-]*)[ \t]*\r?\n(.*?)(?:\r?\n)?```",
                         text, re.DOTALL):
        code = m.group(2)
        if len(code) > len(best):
            best, best_lang = code, (m.group(1) or "").strip().lower()
    return best, best_lang


# 语言 -> 猜个扩展名（模型没给 path 时兜底）
_LANG_EXT = {
    "python": ".py", "py": ".py", "python3": ".py", "javascript": ".js",
    "js": ".js", "typescript": ".ts", "ts": ".ts", "java": ".java",
    "c": ".c", "cpp": ".cpp", "c++": ".cpp", "cs": ".cs", "csharp": ".cs",
    "go": ".go", "rust": ".rs", "html": ".html", "css": ".css",
    "json": ".json", "yaml": ".yml", "yml": ".yml", "sql": ".sql",
    "sh": ".sh", "bash": ".sh", "powershell": ".ps1", "bat": ".bat",
    "xml": ".xml", "markdown": ".md", "md": ".md",
}

# 「截断保护」生效的代码类扩展名（文档类不适用：Markdown 天然不配平）
_CODE_EXTS = {".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".c", ".h", ".cpp",
              ".hpp", ".cs", ".go", ".rs", ".sh", ".bat", ".ps1", ".sql",
              ".html", ".css", ".json", ".yaml", ".yml", ".toml", ".ini",
              ".xml"}


def _looks_truncated(content, path="", eof_only=False):
    """内容看起来是不是「被截断 / 转义坏掉」的半成品。

    判据（按可靠度排序）：
      1) 空内容；
      2) .py：compile 报的都是「没闭合 / 提前结束」这类 EOF 错——典型的截断；
      3) 通用启发式：括号、引号明显不配对。

    eof_only=True 时只认「截断」类错误（run_python 用：语法错让它自己报，
    真要截断了才拦，避免把"模型写错代码"和"内容被截断"混为一谈）。
    """
    if not isinstance(content, str) or not content.strip():
        return True
    ext = os.path.splitext(str(path or ""))[1].lower()
    if ext == ".py":
        try:
            compile(content, "<check>", "exec")
            return False
        except SyntaxError as e:
            msg = (e.msg or "")
            eof = any(k in msg for k in ("never closed", "unexpected EOF",
                                         "unterminated", "was never closed",
                                         "end of file"))
            if eof:
                return True
            return not eof_only
        except Exception:
            return False
    # 非 Python：只做很保守的配平检查，避免误伤
    pairs = [("(", ")"), ("[", "]"), ("{", "}")]
    for a, b in pairs:
        if abs(content.count(a) - content.count(b)) > 2:
            return True
    if content.count('"""') % 2 == 1:
        return True
    return False


def _salvage_from_fence(calls, text):
    """把消息里 ```代码块``` 的真实代码，补进被写坏的长文本参数。

    只在「参数缺失 / 明显是半成品」时动手，正常解析出来的内容绝不覆盖。
    """
    code, lang = extract_fence(text)
    if not code:
        return calls
    for c in calls:
        name = c.get("name")
        args = c.setdefault("arguments", {})
        key = None
        if name == "write_file":
            key = "content"
        elif name == "create_document":
            for k in ("content", "markdown"):
                if args.get(k):
                    key = k
                    break
            key = key or "content"
        elif name == "run_python":
            key = "code" if "code" in args or "source" not in args else "source"
        if key is None:
            continue
        cur = args.get(key) or ""
        # 宽松模式抠出来的长文本 + 明显半成品 -> 一律用代码块覆盖
        bad = c.get("_loose") and key in _LONG_TEXT_KEYS
        if bad or _looks_truncated(cur, args.get("path") or ""):
            args[key] = code
            if name == "write_file" and not str(args.get("path") or "").strip():
                args["path"] = "code" + _LANG_EXT.get(lang, ".txt")
    for c in calls:
        c.pop("_loose", None)
    return calls


def _normalize_rel(path):
    """规范化相对路径。

    工具的根目录本身就是工作区的 files/，但模型（和用户）经常写成
    "files/notes.txt"，直接 join 会变成 files/files/notes.txt —— 多套一层。
    这里把重复的开头 "files" 去掉。
    """
    raw = (path or "").strip()
    if not raw:
        return "."
    norm = raw.replace("/", os.sep)
    parts = [x for x in norm.split(os.sep) if x not in ("", ".")]
    if parts and parts[0].lower() == "files":
        parts = parts[1:]
    if not parts:
        return "."
    return os.sep.join(parts)


def _inside(files_dir, path):
    """判断给定路径是否仍落在工作区 files/ 沙箱内（用于标注"工作区外只读"）。"""
    try:
        p = os.path.normpath(os.path.join(files_dir, _normalize_rel(path)))
        base = os.path.abspath(files_dir)
        return p == base or p.startswith(base + os.sep)
    except Exception:
        return False


def _safe_path(files_dir, path, allow_outside=False):
    """把用户给的路径解析为绝对路径。

    - 「桌面 / 文档 / 下载 / 图片…」会先翻译成 Windows 真实路径；
    - 越界（不在 files/ 内）时：只读工具传 allow_outside=True 才放行；
      写入类操作一律拒绝，保证写操作始终锁在工作区沙箱里。
    """
    raw = _folders.resolve((path or "").strip())
    if not raw:
        raw = "."
    if os.path.isabs(raw) or (os.path.splitdrive(raw)[0]):
        # 绝对路径（D:\、D:\软件）走这条
        p = os.path.normpath(raw)
        base = os.path.abspath(files_dir)
        if p == base or p.startswith(base + os.sep):
            return p
        if allow_outside:
            return p
        raise ValueError("路径越界：写操作只能发生在工作区 files/ 目录内")
    p = os.path.normpath(os.path.join(files_dir, _normalize_rel(raw)))
    base = os.path.abspath(files_dir)
    if p == base or p.startswith(base + os.sep):
        return p
    # 走到这里说明是相对路径，但解析后仍在沙箱外（如 "..\\..\\Windows"）
    if allow_outside:
        abs_p = os.path.abspath(p)
        if os.path.isabs(abs_p) and os.path.splitdrive(abs_p)[0]:
            return os.path.normpath(abs_p)
        raise ValueError(f"路径越界：只读工具无法解析该路径，收到 {path!r}")
    raise ValueError("路径越界：写操作只能发生在工作区 files/ 目录内")


def _is_dangerous(cmd):
    c = cmd.lower()
    for pat in _DANGER_PATTERNS:
        if re.search(pat, c):
            return True
    return False


# 禁止写入的系统关键目录：用户已授权放开普通目录写入，但 OS 与本程序本体仍锁死，
# 避免误伤操作系统或被覆盖导致程序无法启动。
_SYSTEM_WRITE_GUARD = [
    r"C:\Windows", r"C:\Program Files", r"C:\Program Files (x86)",
    r"C:\ProgramData", r"C:\Windows.old",
]
_PROG_DIR = os.path.join(os.environ.get("LOCALAPPDATA", ""),
                         "Programs", "WorkBuddy")


# ---------------------------------------------------------------------------
# 只读命令白名单：这类命令不改任何东西，不该每次都弹确认框打扰用户。
# （用户反馈"不管执行什么命令都弹一堆小窗口"，其中一多半就是查版本/列目录）
# ---------------------------------------------------------------------------
_READONLY_CMD_PATTERNS = [
    r"^\s*python\s+(-V|--version|-c\s+[\"']?import\b)",
    r"^\s*python3?\s+-m\s+pip\s+(list|show|freeze)\b",
    r"^\s*pip\s+(list|show|freeze)\b",
    r"^\s*(dir|ls|echo|type|cat|where|which|ver|whoami|hostname)\b",
    r"^\s*git\s+(status|log|diff|branch|show|remote\s+-v)\b",
    r"^\s*tasklist\b",
    r"^\s*(systeminfo|ipconfig)\s*/all\b",
]
# 只要带了这些"会改东西/会串命令"的符号，就一律不算只读，必须问
_NOT_READONLY_TOKENS = (">", ">>", "|", "&", "&&", "del ", "rm ", "format",
                        "shutdown", "taskkill", "move ", "copy ", "ren ")


# ---------------------------------------------------------------------------
# 会「新开一个窗口」的命令：这类命令才是用户看到的"啪一下冒出来、不到一秒又消失"。
# 默认拦掉并告诉 AI 该用什么替代（它拿不到输出，开了也没意义）。
# ---------------------------------------------------------------------------
_WINDOW_SPAWN_PATTERNS = [
    (r"(^|[\s&|])start\s+(?!/[bB])", "start（会另起一个窗口运行）"),
    (r"cmd(\.exe)?\s+/k", "cmd /k（会打开一个常驻的命令行窗口）"),
    (r"(^|\s|&)explorer(\.exe)?\s", "explorer（会打开资源管理器窗口）"),
    (r"(^|\s|&)notepad(\.exe)?\s", "notepad（会打开记事本窗口）"),
    (r"(^|\s|&)msiexec\s", "msiexec（会弹出安装界面）"),
]


def window_spawning(cmd):
    """返回这条命令会新开窗口的原因，不开窗则返回空字符串。"""
    c = (cmd or "")
    for pat, why in _WINDOW_SPAWN_PATTERNS:
        if re.search(pat, c, re.IGNORECASE):
            return why
    return ""


def is_readonly_command(cmd):
    """这条命令是不是"只看看不动手"——是就不用弹确认框。"""
    c = (cmd or "").strip()
    if not c:
        return False
    low = c.lower()
    for t in _NOT_READONLY_TOKENS:
        if t in low:
            return False
    for pat in _READONLY_CMD_PATTERNS:
        if re.match(pat, low, re.IGNORECASE):
            return True
    return False


def _is_system_path(path):
    p = os.path.normcase(os.path.normpath(path or ""))
    for d in _SYSTEM_WRITE_GUARD:
        d2 = os.path.normcase(os.path.normpath(d))
        if p == d2 or p.startswith(d2 + os.sep):
            return True
    prog = os.path.normcase(os.path.normpath(_PROG_DIR))
    if p == prog or p.startswith(prog + os.sep):
        return True
    return False


def _human_size(n):
    try:
        n = float(n)
    except Exception:
        return str(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


def _to_recycle_bin(path):
    """把文件/目录移入 Windows 回收站（可恢复）。成功返回 True。"""
    try:
        import ctypes
        from ctypes import wintypes

        class SHFILEOPSTRUCTW(ctypes.Structure):
            _fields_ = [
                ("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT),
                ("pFrom", wintypes.LPCWSTR), ("pTo", wintypes.LPCWSTR),
                ("fFlags", ctypes.c_uint16), ("fAnyOperationsAborted", wintypes.BOOL),
                ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", wintypes.LPCWSTR),
            ]

        FO_DELETE = 3
        FOF_ALLOWUNDO = 0x40
        FOF_NOCONFIRMATION = 0x10
        FOF_SILENT = 0x4
        op = SHFILEOPSTRUCTW()
        op.wFunc = FO_DELETE
        op.pFrom = path + "\0\0"
        op.pTo = None
        op.fFlags = FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT
        res = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
        return res == 0
    except Exception:
        return False


# ---------------------------------------------------------------- 新工具辅助

# Python 代码里出现这些 = 有破坏性，执行前必须用户确认
_PY_DANGER = re.compile(
    r"(shutil\.rmtree|os\.remove|os\.unlink|os\.rmdir|os\.removedirs|"
    r"\brm\s+-rf|format\s+[a-z]:|subprocess\.(run|Popen|call).*(rm|del|format)|"
    r"open\s*\([^)]*['\"]w['\"].*Windows|winreg|ctypes\.windll|"
    r"shutdown|taskkill|diskpart)", re.I)


def _py_dangerous(code):
    return bool(_PY_DANGER.search(code or ""))


def _find_python():
    """找一个真实可用的 Python 解释器。

    注意：打包成 exe 后 sys.executable 指向本程序自己，直接拿它执行会再弹一个窗口，
    所以冻结状态下一律走 PATH / py launcher 查找。
    """
    if not getattr(sys, "frozen", False):
        exe = sys.executable
        if exe and os.path.isfile(exe):
            return exe
    for name in ("python.exe", "python3.exe", "python", "python3"):
        p = shutil.which(name)
        if p:
            return p
    launcher = shutil.which("py")
    if launcher:
        return launcher
    # 常见安装位置兜底
    for cand in (
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\Python\Python312\python.exe"),
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\Python\Python313\python.exe"),
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\Python\Python311\python.exe"),
        r"C:\Python312\python.exe",
        r"C:\Python313\python.exe",
    ):
        if cand and os.path.isfile(cand):
            return cand
    return None


def _toast(title, message):
    """弹一个 Windows 桌面通知。成功返回 True。多级降级，绝不抛异常。"""
    title = (title or "AI 工作台")[:60]
    message = (message or "")[:220]
    # 方案一：PowerShell + WinRT Toast
    try:
        ps = (
            "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, "
            "ContentType=WindowsRuntime] > $null;"
            "$t=[Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent("
            "[Windows.UI.Notifications.ToastTemplateType]::ToastText02);"
            "$x=$t.GetElementsByTagName('text');"
            f"$x.Item(0).AppendChild($t.CreateTextNode({json.dumps(title, ensure_ascii=False)})) > $null;"
            f"$x.Item(1).AppendChild($t.CreateTextNode({json.dumps(message, ensure_ascii=False)})) > $null;"
            "$n=[Windows.UI.Notifications.ToastNotification]::new($t);"
            "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("
            "'AI 工作台').Show($n);"
        )
        r = _winproc.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                          "-WindowStyle", "Hidden", "-Command", ps],
                         capture_output=True, timeout=20)
        if r.returncode == 0:
            return True
    except Exception:
        pass
    # 方案二：非阻塞的 msg 命令
    try:
        r = _winproc.run(["msg", "*", f"{title}：{message}"],
                         capture_output=True, timeout=10)
        if r.returncode == 0:
            return True
    except Exception:
        pass
    return False


def _list_windows():
    """用 Win32 API 列出当前有标题的可见窗口。"""
    out = []
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        EnumWindows = user32.EnumWindows
        EnumWindowsProc = ctypes.WINFUNCTYPE(
            ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

        def cb(hwnd, _):
            try:
                if not user32.IsWindowVisible(hwnd):
                    return True
                n = user32.GetWindowTextLengthW(hwnd)
                if n <= 0:
                    return True
                buf = ctypes.create_unicode_buffer(n + 1)
                user32.GetWindowTextW(hwnd, buf, n + 1)
                title = buf.value.strip()
                if title:
                    pid = wintypes.DWORD()
                    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                    out.append((title, int(pid.value)))
            except Exception:
                pass
            return True

        EnumWindows(EnumWindowsProc(cb), 0)
    except Exception:
        pass
    return out


def _grab_screen(path, region=None):
    """截取全屏（或指定区域）保存到 path。成功返回 True。"""
    try:
        try:
            import mss
            with mss.mss() as sct:
                mon = None
                if region and len(region) == 4:
                    l, t, w, h = [int(x) for x in region]
                    mon = {"left": l, "top": t, "width": w, "height": h}
                else:
                    mon = sct.monitors[1] if len(sct.monitors) > 1 else sct.monitors[0]
                shot = sct.grab(mon)
                from PIL import Image
                img = Image.frombytes("RGB", shot.size, shot.rgb)
                img.save(path)
            return True
        except Exception:
            from PIL import ImageGrab
            img = ImageGrab.grab()
            img.save(path)
            return True
    except Exception:
        return False


def _screen_size():
    """返回主屏分辨率字符串，例如 1920x1080。"""
    try:
        try:
            import mss
            with mss.mss() as sct:
                m = sct.monitors[1] if len(sct.monitors) > 1 else sct.monitors[0]
                return f"{m['width']}x{m['height']}"
        except Exception:
            pass
        import ctypes
        u = ctypes.windll.user32
        u.SetProcessDPIAware()
        return f"{u.GetSystemMetrics(0)}x{u.GetSystemMetrics(1)}"
    except Exception:
        return "未知"


# ---------------------------------------------------------------------------
# 图片处理 / 图表 / HTTP / 时间解析（v9 新增工具的底层实现）
# ---------------------------------------------------------------------------
_IMG_EXTS = (".png", ".jpg", ".jpeg", ".bmp", ".webp", ".gif", ".tiff")


def _image_op(src, dst, op, args):
    """PIL 图片处理。返回结果文字。"""
    from PIL import Image
    if not os.path.exists(src):
        raise ValueError(f"图片不存在：{src}")
    if not src.lower().endswith(_IMG_EXTS):
        raise ValueError("不是受支持的图片格式")
    img = Image.open(src)
    op = (op or "").lower() or "compress"
    before = os.path.getsize(src)

    if op == "resize":
        w = args.get("width") or args.get("w")
        h = args.get("height") or args.get("h")
        w = int(w) if w else None
        h = int(h) if h else None
        if not w and not h:
            raise ValueError("resize 需要 width 或 height")
        if w and not h:
            h = max(1, int(img.height * w / img.width))
        if h and not w:
            w = max(1, int(img.width * h / img.height))
        img = img.resize((w, h), Image.LANCZOS)
    elif op == "rotate":
        img = img.rotate(-float(args.get("angle") or 90), expand=True)
    elif op == "flip":
        d = (args.get("direction") or "h").lower()
        img = img.transpose(Image.FLIP_LEFT_RIGHT if d.startswith("h")
                            else Image.FLIP_TOP_BOTTOM)
    elif op == "crop":
        box = (int(args.get("left") or 0), int(args.get("top") or 0),
               int(args.get("right") or img.width), int(args.get("bottom") or img.height))
        img = img.crop(box)
    elif op == "watermark":
        from PIL import ImageDraw, ImageFont
        text = str(args.get("text") or "AI 工作台")
        pos = (args.get("pos") or "br").lower()
        draw = ImageDraw.Draw(img, "RGBA")
        size = max(16, int(min(img.size) * 0.035))
        font = None
        for fp in (r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\simhei.ttf",
                   r"C:\Windows\Fonts\simsun.ttc"):
            if os.path.exists(fp):
                try:
                    font = ImageFont.truetype(fp, size)
                    break
                except Exception:
                    continue
        if font is None:
            font = ImageFont.load_default()
        bbox = draw.textbbox((0, 0), text, font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        pad = int(size * 0.6)
        coords = {
            "br": (img.width - tw - pad, img.height - th - pad * 2),
            "bl": (pad, img.height - th - pad * 2),
            "tr": (img.width - tw - pad, pad),
            "tl": (pad, pad),
            "c": ((img.width - tw) // 2, (img.height - th) // 2),
        }
        x, y = coords.get(pos, coords["br"])
        draw.rectangle([x - pad // 2, y - pad // 2, x + tw + pad // 2, y + th + pad // 2],
                       fill=(0, 0, 0, 110))
        draw.text((x, y), text, font=font, fill=(255, 255, 255, 235))

    # 保存
    dst = dst or _derive_img_path(src, op)
    os.makedirs(os.path.dirname(dst) or ".", exist_ok=True)
    save_kw = {}
    ext = os.path.splitext(dst)[1].lower()
    quality = int(args.get("quality") or (70 if op == "compress" else 85))
    quality = max(1, min(95, quality))
    if ext in (".jpg", ".jpeg"):
        if img.mode in ("RGBA", "P", "LA"):
            img = img.convert("RGB")
        save_kw = {"quality": quality, "optimize": True, "progressive": True}
    elif ext == ".png":
        save_kw = {"optimize": True}
    elif ext == ".webp":
        save_kw = {"quality": quality}
    img.save(dst, **save_kw)
    after = os.path.getsize(dst)
    saved = (1 - after / before) * 100 if before else 0
    if saved >= 0:
        delta = f"体积缩小 {saved:.0f}%"
    else:
        delta = f"体积增大 {-saved:.0f}%"
    return (f"已{op}图片：{dst}（{img.width}x{img.height}，"
            f"{_human_size(before)} → {_human_size(after)}，{delta}）")


def _derive_img_path(src, op):
    base, ext = os.path.splitext(src)
    ext = ext or ".jpg"
    if op == "compress" and ext.lower() in (".png", ".bmp"):
        ext = ".jpg"
    return f"{base}_{op}{ext}"


def _create_chart(path, chart, labels, values, title=""):
    """用 PIL 画柱状/折线/饼图（不引入 matplotlib，打包体积小）。"""
    from PIL import Image, ImageDraw, ImageFont
    labels = [str(x) for x in (labels or [])]
    vals = []
    for v in (values or []):
        try:
            vals.append(float(v))
        except Exception:
            vals.append(0.0)
    if not labels or not vals or len(labels) != len(vals):
        raise ValueError("labels 与 values 必须非空且长度一致")

    W, H = 1000, 620
    bg, fg, grid = (255, 255, 255), (33, 37, 41), (222, 226, 230)
    palette = ["#2196F3", "#FF7043", "#4CAF50", "#FFC107", "#9C27B0",
               "#00BCD4", "#E91E63", "#795548", "#607D8B", "#8BC34A"]
    img = Image.new("RGB", (W, H), bg)
    d = ImageDraw.Draw(img)

    def _font(size, bold=False):
        for fp in (r"C:\Windows\Fonts\msyhbd.ttc" if bold else r"C:\Windows\Fonts\msyh.ttc",
                   r"C:\Windows\Fonts\simhei.ttf", r"C:\Windows\Fonts\simsun.ttc"):
            if os.path.exists(fp):
                try:
                    return ImageFont.truetype(fp, size)
                except Exception:
                    continue
        return ImageFont.load_default()

    f_title, f_lab, f_val = _font(28, True), _font(18), _font(16, True)
    if title:
        tw = d.textbbox((0, 0), title, font=f_title)[2]
        d.text(((W - tw) / 2, 24), title, font=f_title, fill=fg)

    top, bottom, left, right = 110, H - 90, 90, W - 50
    chart = (chart or "bar").lower()
    maxv = max(vals) or 1
    minv = min(min(vals), 0)
    span = (maxv - minv) or 1

    if chart == "pie":
        total = sum(abs(v) for v in vals) or 1
        cx, cy, r = W // 2, (top + bottom) // 2 + 20, 190
        start = -90.0
        for i, (lb, v) in enumerate(zip(labels, vals)):
            sweep = 360.0 * abs(v) / total
            color = palette[i % len(palette)]
            d.pieslice([cx - r, cy - r, cx + r, cy + r], start, start + sweep,
                       fill=color, outline="white", width=2)
            mid = (start + sweep / 2) * 3.14159265 / 180
            lx, ly = cx + (r + 34) * (1 if mid > -1.57 and mid < 1.57 else -1), cy + 0
            ty = cy - (len(labels) * 14) // 2 + i * 28
            d.rectangle([W - 300, ty + 4, W - 282, ty + 22], fill=color)
            d.text((W - 274, ty), f"{lb}  {v:g}（{abs(v)/total*100:.0f}%）",
                   font=f_lab, fill=fg)
            start += sweep
    else:
        # 坐标轴
        d.line([left, top, left, bottom], fill=grid, width=2)
        d.line([left, bottom, right, bottom], fill=grid, width=2)
        for i in range(5):
            y = bottom - (bottom - top) * i / 4
            d.line([left, y, right, y], fill=grid, width=1)
            d.text((14, y - 10), f"{minv + span * i / 4:g}", font=f_lab, fill=(120, 120, 120))
        n = len(labels)
        slot = (right - left) / n
        pts = []
        for i, (lb, v) in enumerate(zip(labels, vals)):
            cx = left + slot * (i + 0.5)
            y = bottom - (v - minv) / span * (bottom - top)
            color = palette[i % len(palette)]
            if chart == "line":
                pts.append((cx, y))
            else:
                bw = slot * 0.55
                d.rectangle([cx - bw / 2, y, cx + bw / 2, bottom], fill=color)
            lbx = d.textbbox((0, 0), lb, font=f_lab)[2]
            d.text((cx - lbx / 2, bottom + 12), lb, font=f_lab, fill=fg)
            txt = f"{v:g}"
            tw2 = d.textbbox((0, 0), txt, font=f_val)[2]
            d.text((cx - tw2 / 2, y - 26 if chart == "line" else y + 6), txt,
                   font=f_val, fill=(255, 255, 255) if chart == "bar" and y + 22 < bottom
                   else (60, 60, 60))
        if chart == "line" and pts:
            d.line(pts, fill=palette[0], width=4, joint="curve")
            for (x, y), v in zip(pts, vals):
                d.ellipse([x - 6, y - 6, x + 6, y + 6], fill=palette[0], outline="white", width=2)

    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    if not path.lower().endswith((".png", ".jpg", ".jpeg")):
        path += ".png"
    img.save(path)
    return f"已生成{chart}图：{path}（{W}x{H}，共 {len(vals)} 组数据）"


_WHEN_RE = re.compile(r"^\s*(?:(\d+)\s*(?:秒|s)\s*(?:后|以后)?|"
                      r"(\d+)\s*(?:分钟|分|min|m)\s*(?:后|以后)?|"
                      r"(\d+)\s*(?:小时|时|h)\s*(?:后|以后)?)\s*$")


def parse_when(when, now=None):
    """把「10分钟后 / 2小时以后 / 18:30 / 明天9点」解析成时间戳。

    返回 (时间戳, 人类可读说明)；解析不了抛 ValueError。
    """
    import datetime as _dt
    now = now or _dt.datetime.now()
    s = (when or "").strip()
    if not s:
        raise ValueError("when 不能为空，例如 \"10分钟后\" 或 \"今天18:30\"")
    m = _WHEN_RE.match(s)
    if m:
        sec = int(m.group(1) or 0) + int(m.group(2) or 0) * 60 + int(m.group(3) or 0) * 3600
        if sec <= 0:
            raise ValueError("时间要大于 0")
        t = now + _dt.timedelta(seconds=sec)
        return t, f"{sec} 秒后（{t.strftime('%H:%M:%S')}）"
    # 绝对时间
    day = now.date()
    body = s
    hint_pm = False
    for kw, delta in (("明天", 1), ("后天", 2), ("今天", 0), ("今晚", 0),
                      ("今早", 0), ("下午", 0), ("晚上", 0)):
        if body.startswith(kw):
            day = day + _dt.timedelta(days=delta)
            body = body[len(kw):]
            if kw in ("今晚", "下午", "晚上"):
                hint_pm = True
            break
    body = body.replace("点", ":").replace("时", ":").replace("分", "").strip(": ")
    hm = re.match(r"^(\d{1,2})(?::(\d{1,2}))?$", body)
    if hm:
        hh, mm = int(hm.group(1)), int(hm.group(2) or 0)
        if hint_pm and hh < 12:
            hh += 12          # 「今晚8点」「下午3点」
        if 0 <= hh < 24 and 0 <= mm < 60:
            t = _dt.datetime.combine(day, _dt.time(hh, mm))
            if t <= now:
                t = t + _dt.timedelta(days=1)
            return t, t.strftime("%m-%d %H:%M")
    raise ValueError(f"看不懂的时间写法：{when!r}（支持 \"10分钟后\"、\"今天18:30\"、\"明天9点\"）")


class AgentRunner:
    def __init__(self, files_dir: str, vision_cb=None, workspace_path=None,
                 search_cb=None, plan_cb=None, mcp_manager=None,
                 speak_cb=None, dingtalk_cb=None, update_cb=None,
                 reminder_cb=None, keys=None, subagent_cb=None,
                 dingtalk_api_cb=None):
        self.files_dir = files_dir
        # vision_cb(image_path, prompt) -> str，用于图片识别（由 UI 层注入，带 LLM 客户端）
        self.vision_cb = vision_cb
        # search_cb(query) -> str，联网搜索（由 UI 层注入，用带联网能力的模型）
        self.search_cb = search_cb
        # plan_cb(steps, note) -> str，更新计划面板（由 UI 层注入）
        self.plan_cb = plan_cb
        # MCP 管理器（可为 None，此时 mcp_* 工具会给出友好提示）
        self.mcp_manager = mcp_manager
        # speak_cb(text, engine) -> str，语音朗读（UI 注入）
        self.speak_cb = speak_cb
        # dingtalk_cb(text, title, at_all) -> str，钉钉推送（UI 注入）
        self.dingtalk_cb = dingtalk_cb
        # dingtalk_api_cb(action, args) -> str，钉钉连接器的高级操作
        # （查通讯录 / 发工作通知 / 发群 / 自检，UI 注入）
        self.dingtalk_api_cb = dingtalk_api_cb
        # update_cb() -> str，检查更新（UI 注入）
        self.update_cb = update_cb
        # reminder_cb(text, when) -> str，设置提醒（UI 注入）
        self.reminder_cb = reminder_cb
        # subagent_cb(prompt, max_steps) -> str，子代理委派（UI 注入，独立上下文跑）
        self.subagent_cb = subagent_cb
        # API Keys（语音识别等直接走网络的工具要用）
        self.keys = dict(keys or {})
        # 工作区根目录（文件沙箱 files/ 的上一层）；记忆、索引、追踪都放这里
        self.ws_path = os.path.abspath(workspace_path or
                                       os.path.dirname(os.path.abspath(files_dir)))
        self._mem = None
        self._kb = None
        self._trace = None
        self._skills = None
        self._sched = None
        # 本轮真实写出来的文件（路径/大小/是否存在），供工作总结与完成自检
        self.written = []
        os.makedirs(files_dir, exist_ok=True)

    # ---------- 懒加载子系统 ----------
    @property
    def memory(self):
        if self._mem is None:
            from . import memory as memory_mod
            self._mem = memory_mod.MemoryStore(self.ws_path)
        return self._mem

    @property
    def kb(self):
        if self._kb is None:
            from . import knowledge as kb_mod
            self._kb = kb_mod.KnowledgeBase(self.ws_path)
        return self._kb

    @property
    def trace(self):
        if self._trace is None:
            from . import trace as trace_mod
            self._trace = trace_mod.TraceLog(self.ws_path)
        return self._trace

    @property
    def skills(self):
        if self._skills is None:
            from . import skills as skills_mod
            self._skills = skills_mod.SkillStore(self.ws_path)
        return self._skills

    @property
    def scheduler(self):
        if self._sched is None:
            from . import scheduler as sched_mod
            self._sched = sched_mod.Scheduler(self.ws_path)
        return self._sched

    def attach_subsystems(self, vision_cb=None, search_cb=None, plan_cb=None,
                          mcp_manager=None, speak_cb=None, dingtalk_cb=None,
                          update_cb=None, reminder_cb=None, keys=None,
                          subagent_cb=None, dingtalk_api_cb=None):
        """工作区切换后重新挂接回调。"""
        if vision_cb is not None:
            self.vision_cb = vision_cb
        if search_cb is not None:
            self.search_cb = search_cb
        if plan_cb is not None:
            self.plan_cb = plan_cb
        if mcp_manager is not None:
            self.mcp_manager = mcp_manager
        if speak_cb is not None:
            self.speak_cb = speak_cb
        if dingtalk_cb is not None:
            self.dingtalk_cb = dingtalk_cb
        if dingtalk_api_cb is not None:
            self.dingtalk_api_cb = dingtalk_api_cb
        if update_cb is not None:
            self.update_cb = update_cb
        if reminder_cb is not None:
            self.reminder_cb = reminder_cb
        if subagent_cb is not None:
            self.subagent_cb = subagent_cb
        if keys is not None:
            self.keys = dict(keys)

    def _resolve(self, raw):
        """路径解析：绝对路径直接用，相对路径相对工作区 files/。"""
        raw = (raw or "").strip()
        if os.path.isabs(raw) or os.path.splitdrive(raw)[0]:
            return os.path.normpath(raw)
        return os.path.normpath(os.path.join(self.files_dir, _normalize_rel(raw)))

    def _resolve_write(self, raw):
        """写入类路径解析 + 系统目录拦截。返回 (路径, 错误信息)。"""
        raw = _folders.resolve((raw or "").strip())
        if not raw or raw == "." or raw.endswith(("/", "\\")):
            return None, (f"path 必须是一个文件名，不能是目录：{raw!r}。"
                          f"例：out/report.docx 或 桌面\\周报.docx")
        if os.path.isabs(raw) or os.path.splitdrive(raw)[0]:
            path = os.path.normpath(raw)
        else:
            path = os.path.normpath(os.path.join(self.files_dir, _normalize_rel(raw)))
        if _is_system_path(path):
            return None, (f"出于安全，禁止写入系统关键目录：{path}。"
                          f"请换到其他位置（桌面、D:、E:、你的项目文件夹都行）。")
        if os.path.isdir(path):
            return None, f"{path} 是一个目录，请换一个文件名"
        parent = os.path.dirname(path)
        if parent and os.path.exists(parent) and not os.path.isdir(parent):
            return None, (f"上级路径 {os.path.basename(parent)} 已经是一个文件，"
                          f"无法在它下面再创建文件")
        return path, None

    def _system_info(self):
        import platform
        lines = [f"系统：{platform.system()} {platform.release()}（{platform.version()}）",
                 f"处理器：{platform.processor() or '未知'}   逻辑核心：{os.cpu_count()}"]
        try:
            import ctypes

            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

            m = MEMORYSTATUSEX()
            m.dwLength = ctypes.sizeof(m)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
            tot = m.ullTotalPhys / 1073741824
            avail = m.ullAvailPhys / 1073741824
            lines.append(f"内存：可用 {avail:.1f} GB / 共 {tot:.1f} GB（已用 {m.dwMemoryLoad}%）")
        except Exception:
            pass
        try:
            import string
            for d in string.ascii_uppercase:
                root = d + ":\\"
                if os.path.exists(root):
                    try:
                        u = shutil.disk_usage(root)
                        lines.append(f"{root} 剩余 {u.free / 1073741824:.1f} GB / 共 {u.total / 1073741824:.1f} GB")
                    except Exception:
                        pass
        except Exception:
            pass
        return "系统信息：\n" + "\n".join(lines)

    def _note_written(self, path, size=0):
        """记下「这一轮真的写出来了什么」，供工作总结 / 完成自检使用。"""
        try:
            self.written.append({"path": path, "size": int(size or 0),
                                 "exists": os.path.isfile(path)})
        except Exception:
            pass

    def canonical_name(self, name):
        """把模型给的（可能是别名 / 技能名 / 近似名）工具名归一成真正会执行的那个名字。

        用途：界面 / 日志记录"实际执行的是谁"，避免把技能 slug 记成工具名。
        """
        raw = (name or "").strip()
        if not raw:
            return raw
        if raw in KNOWN_TOOLS:
            return raw
        low = raw.lower().replace("-", "_")
        if low in KNOWN_TOOLS:
            return low
        t = TOOL_ALIAS.get(low)
        if t:
            return t
        try:
            if self.skills.find(raw):
                return "use_skill"
        except Exception:
            pass
        return raw

    def execute(self, call: dict, confirm_cb=None):
        """执行一个工具调用，返回结果字符串；同时写入工具追踪日志（可观测性）。"""
        if not isinstance(call, dict):
            return "[错误] 工具调用格式不对，应为 {name, arguments}"
        name = (call.get("name") or "").strip()
        args = call.get("arguments") or {}
        if not isinstance(args, dict):
            args = {}
        t0 = time.time()
        out = self._dispatch(name, args, confirm_cb)
        try:
            self.trace.add(
                self.canonical_name(name), args, out,
                ok=not str(out).startswith(("[错误]", "[拒绝]", "[取消]")),
                ms=int((time.time() - t0) * 1000))
        except Exception:
            pass
        return out

    def _dispatch(self, name, args, confirm_cb):
        """真正干活的派发逻辑。confirm_cb(cmd)->bool 用于命令/删除/杀进程确认。"""
        try:
            if name == "read_file":
                outside = not _inside(self.files_dir, args.get("path", ""))
                path = _safe_path(self.files_dir, args.get("path", ""), allow_outside=True)
                if not os.path.isfile(path):
                    return f"[错误] 文件不存在：{args.get('path')}（解析为 {path}）"
                size = os.path.getsize(path)
                try:
                    with open(path, "r", encoding="utf-8", errors="replace") as f:
                        content = f.read()
                except Exception as e:
                    return f"[错误] 读取失败：{e}"
                if len(content) > 20000:
                    content = content[:20000] + "\n...（已截断，超过 20000 字）"
                tag = "（工作区外只读）" if outside else ""
                return f"文件 {path}{tag}（{size} 字节）内容：\n```\n{content}\n```"

            elif name == "list_dir":
                outside = not _inside(self.files_dir, args.get("path", "."))
                path = _safe_path(self.files_dir, args.get("path", "."), allow_outside=True)
                if not os.path.isdir(path):
                    return f"[错误] 目录不存在或不是目录：{args.get('path')}（解析为 {path}）"
                try:
                    names = sorted(os.listdir(path))
                except PermissionError:
                    return f"[错误] 没有权限访问该目录：{path}"
                except Exception as e:
                    return f"[错误] 列出目录失败：{e}"
                entries = []
                for e in names:
                    full = os.path.join(path, e)
                    try:
                        if os.path.isdir(full):
                            entries.append("📁 " + e + "/")
                        else:
                            entries.append(f"📄 {e}  （{_human_size(os.path.getsize(full))}）")
                    except Exception:
                        entries.append("📄 " + e)
                if not entries:
                    entries = ["（空）"]
                tag = "（工作区外只读）" if outside else ""
                return f"目录 {path}{tag} 的内容（共 {len(names)} 项）：\n" + "\n".join(entries)

            elif name == "write_file":
                raw = _folders.resolve((args.get("path") or "").strip())
                # 末尾带分隔符（out/、out\）说明把它当目录用了，直接拒绝，
                # 否则会造出一个名叫 out 的文件，把后面所有 out/xxx 都堵死
                if (not raw or raw == "." or raw.endswith(("/", "\\"))):
                    return (f"[错误] path 必须是一个文件名，不能是目录：{raw!r}。"
                            f"例：out/deep.txt 或 桌面\\a.txt")
                # 用户已授权：允许写入工作区之外（桌面 / D: / E: / 项目文件夹等），
                # 但系统关键目录仍禁止写入，避免误伤操作系统或本程序。
                if os.path.isabs(raw) or os.path.splitdrive(raw)[0]:
                    path = os.path.normpath(raw)
                else:
                    path = os.path.normpath(os.path.join(
                        self.files_dir, _normalize_rel(raw)))
                if _is_system_path(path):
                    return (f"[拒绝] 出于安全，禁止写入系统关键目录：{path}。"
                            f"请换到其他位置（桌面、D:、E:、你的项目文件夹都行）。")
                if os.path.isdir(path):
                    return f"[错误] {path} 是一个目录，不能用 write_file 写入，请换文件名"
                parent = os.path.dirname(path)
                if parent and os.path.exists(parent) and not os.path.isdir(parent):
                    return (f"[错误] 上级路径 {os.path.basename(parent)} 已经是一个文件，"
                            f"无法在它下面再创建文件")
                content = args.get("content", "")
                # 模型经常把换行双重转义成字面量 \n -> 落盘的代码就"一整行"。
                # 落盘前统一还原成真换行/TAB（只在这一看就是被转义坏的情况下动手）。
                if isinstance(content, str):
                    content = normalize_text_content(content, path)
                    # 半成品保护：被截断/转义写坏的代码绝不落盘，也绝不报「成功」。
                    # （否则会出现"186 字节的残缺 .py + 一句成功提示"这种最坏结果）
                    _ext = os.path.splitext(path)[1].lower()
                    if content.strip() and _ext in _CODE_EXTS \
                            and _looks_truncated(content, path):
                        return ("[错误] 这份内容看起来是**被截断 / 转义写坏的半成品**"
                                f"（{len(content)} 字符），我没有写入，"
                                "以免给你一个坏文件。\n"
                                "请改用更可靠的两步写法重发一次：\n"
                                "1) 先单独输出一行：\n"
                                "<tool_call>{\"name\":\"write_file\",\"arguments\":"
                                "{\"path\":\"" + (path or "files\\\\code.py") + "\"}}</tool_call>\n"
                                "2) 紧接着跟一个 ```代码块```，把**完整代码**放进去"
                                "（真实换行，不要写成 \\\\n，不要把长代码塞进 JSON）。\n"
                                "系统会自动把代码块的内容当作文件内容。")
                try:
                    os.makedirs(parent, exist_ok=True)
                    with open(path, "w", encoding="utf-8") as f:
                        f.write(content)
                except Exception as e:
                    return f"[错误] 写入失败：{type(e).__name__}: {e}（目标 {path}）"
                # 写完立刻复核：文件真的在、字节数对，才敢说成功
                if not os.path.isfile(path):
                    return f"[错误] 写入后没找到文件：{path}（可能被安全软件拦截）"
                size = os.path.getsize(path)
                self._note_written(path, size)
                return (f"[成功] 已写入 {len(content)} 字符 → 完整路径：{path}"
                        f"（{size} 字节，已确认落盘）")

            elif name == "read_worklog":
                days = int(args.get("days") or 1)
                ws_ = self.ws_path
                if days <= 1:
                    txt = _worklog.read_day(ws_)
                    if not txt:
                        return ("[成功] 今天的总结还空着（还没干过活）。"
                                f"文件位置：{_worklog.day_path(ws_)}")
                    return f"[成功] 今天的总结（{_worklog.day_path(ws_)}）：\n{txt[:6000]}"
                out = []
                for day, _sz in _worklog.list_days(ws_)[:days]:
                    out.append(f"===== {day} =====\n{_worklog.read_day(ws_, day)}")
                return ("[成功] 最近 %d 天总结：\n" % min(days, len(out))
                        + "\n\n".join(out))[:8000]

            elif name == "write_worklog":
                req = (args.get("request") or args.get("title") or "").strip()
                summ = (args.get("summary") or args.get("content") or "").strip()
                if not req and not summ:
                    return "[错误] 需要 request（做了什么）或 summary（结论）"
                block, err = _worklog.append(
                    self.ws_path, source=args.get("source") or "对话",
                    request=req, summary=summ,
                    tools=args.get("tools") or [],
                    files=args.get("files") or [],
                    ok=bool(args.get("ok", True)),
                    extra_notes=args.get("notes") or "")
                if err:
                    return err
                return (f"[成功] 已追加一条工作总结 → {_worklog.day_path(self.ws_path)}"
                        f"\n\n{block}")

            elif name == "run_command":
                cmd = args.get("command", "")
                if not cmd.strip():
                    return "[错误] 命令为空"
                if _is_dangerous(cmd):
                    return "[拒绝] 该命令过于危险，已被拦截（删除/格式化/关机等）。"
                # 会新开窗口的命令一律拦掉：这类命令才是"啪一下冒个窗口又消失"的元凶
                # （start / cmd /k / explorer 都会另起一个可见窗口，而且往往瞬间退出）
                _wn = window_spawning(cmd)
                _st = getattr(self, "settings", None) or {}
                if _wn and _st.get("block_new_windows", True):
                    return ("[拒绝] 这条命令会新开一个窗口："
                            f"{_wn}\n"
                            "新开的窗口你看不清就消失了，也拿不到输出。请改成：\n"
                            "· 要运行脚本 -> 直接用 run_python，或 `python xxx.py`\n"
                            "· 要打开文件/文件夹 -> 用 open_path 工具\n"
                            "· 确实要弹窗口 -> 让用户在设置里关掉「禁止弹出新窗口」")
                if confirm_cb is not None:
                    if not confirm_cb(cmd):
                        return "[取消] 用户拒绝执行该命令。"
                try:
                    try:
                        to = float(args.get("timeout") or 60)
                    except Exception:
                        to = 60.0
                    to = max(5, min(to, 600))   # 打包 exe 这类长命令可给到 10 分钟
                    proc = _winproc.run(cmd, shell=True, cwd=self.files_dir,
                                        capture_output=True, timeout=to)
                    out = _decode(proc.stdout) + _decode(proc.stderr)
                    if len(out) > 8000:
                        out = out[:8000] + "\n...（输出已截断）"
                    return (f"命令：`{cmd}`\n退出码：{proc.returncode}\n"
                            f"超时上限：{int(to)} 秒\n输出：\n```\n{out}\n```")
                except subprocess.TimeoutExpired:
                    return ("[错误] 命令执行超时。如果是 PyInstaller 打包这类长命令，"
                            "请在 arguments 里带 timeout=600 再试一次。")
                except Exception as e:
                    return f"[错误] 命令执行失败：{e}"

            elif name == "read_document":
                from . import docread as _dr
                raw = (args.get("path") or "").strip()
                path = _safe_path(self.files_dir, raw, allow_outside=True)
                if not os.path.isfile(path):
                    return f"[错误] 文件不存在：{raw}（解析为 {path}）"
                kind = _dr.file_kind(path)
                if _dr.is_image(path):
                    return (f"[提示] {path} 是图片，请改用 read_image 工具识别其中的文字/画面。")
                try:
                    text = _dr.read_document(path)
                except Exception as e:
                    return f"[错误] 解析 {kind} 失败：{e}"
                return (f"已解析 {kind}：{path}（{_human_size(os.path.getsize(path))}）\n"
                        f"提取到的内容如下：\n```\n{text}\n```")

            elif name == "read_image":
                from . import docread as _dr
                raw = (args.get("path") or "").strip()
                path = _safe_path(self.files_dir, raw, allow_outside=True)
                if not os.path.isfile(path):
                    return f"[错误] 图片不存在：{raw}（解析为 {path}）"
                if not _dr.is_image(path):
                    return f"[提示] {path} 不是图片文件，请用 read_document 解析。"
                if not self.vision_cb:
                    return "[错误] 当前没有可用的视觉模型，无法识别图片。"
                prompt = (args.get("prompt") or
                          "请识别这张图片：逐字提取其中的全部文字（保留原有排版，表格尽量还原），"
                          "并简要描述画面主要内容。用中文回答。")
                try:
                    text = self.vision_cb(path, prompt)
                except Exception as e:
                    return f"[错误] 图片识别失败：{e}"
                return f"已识别图片：{path}（{_human_size(os.path.getsize(path))}）\n{text}"

            elif name == "search_files":
                from . import docread as _dr
                base = _safe_path(self.files_dir, args.get("path") or ".", allow_outside=True)
                if not os.path.isdir(base):
                    return f"[错误] 目录不存在：{base}"
                pattern = (args.get("pattern") or "").strip()
                keyword = (args.get("keyword") or "").strip()
                if not pattern and not keyword:
                    return "[错误] 请至少提供 pattern（文件名通配，如 *.docx）或 keyword（内容关键词）"
                try:
                    maxn = int(args.get("max") or 50)
                except Exception:
                    maxn = 50
                skip = {"node_modules", "__pycache__", ".git", "$RECYCLE.BIN",
                        "System Volume Information", "AppData", ".venv", "venv"}
                hits, scanned = [], 0
                for root, dirs, files in os.walk(base):
                    dirs[:] = [d for d in dirs if d not in skip and not d.startswith(".")]
                    for fn in files:
                        if len(hits) >= maxn:
                            break
                        p = os.path.join(root, fn)
                        scanned += 1
                        if pattern and fnmatch.fnmatch(fn.lower(), pattern.lower()):
                            hits.append(f"📄 {os.path.relpath(p, base)}")
                            continue
                        if keyword and _dr.ext_of(p) in _dr.TEXT_EXTS:
                            try:
                                if os.path.getsize(p) > 2 * 1024 * 1024:
                                    continue
                                with open(p, "r", encoding="utf-8", errors="ignore") as f:
                                    if keyword.lower() in f.read().lower():
                                        hits.append(f"📄 {os.path.relpath(p, base)}  （内容含“{keyword}”）")
                            except Exception:
                                continue
                    if len(hits) >= maxn:
                        break
                if not hits:
                    return f"在 {base} 下没有找到匹配项（已扫描 {scanned} 个文件）。"
                return (f"在 {base} 下找到 {len(hits)} 项（已扫描 {scanned} 个文件）：\n"
                        + "\n".join(hits))

            elif name == "file_op":
                op = (args.get("op") or "").strip().lower()
                src = self._resolve(args.get("src") or "")
                dst = self._resolve(args.get("dst") or "") if (args.get("dst") or "").strip() else ""
                if op not in ("copy", "move", "rename", "delete"):
                    return "[错误] op 必须是 copy / move / rename / delete 之一"
                if not os.path.exists(src):
                    return f"[错误] 源不存在：{src}"
                if op == "delete":
                    if confirm_cb is not None and not confirm_cb("删除（移入回收站）：" + src):
                        return "[取消] 用户拒绝删除。"
                    if _to_recycle_bin(src):
                        return f"[成功] 已移入回收站（可恢复）：{src}"
                    return f"[错误] 移入回收站失败：{src}"
                if not dst:
                    return "[错误] 该操作需要 dst 参数（目标路径/新名字）"
                if os.path.exists(dst) and not os.path.isdir(dst):
                    return f"[错误] 目标已存在，为避免覆盖已中止：{dst}"
                try:
                    if op == "copy":
                        if os.path.isdir(src):
                            shutil.copytree(src, dst, dirs_exist_ok=True)
                        else:
                            os.makedirs(os.path.dirname(dst) or ".", exist_ok=True)
                            shutil.copy2(src, dst)
                        return f"[成功] 已复制：{src} -> {dst}"
                    # move / rename
                    os.makedirs(os.path.dirname(dst) or ".", exist_ok=True)
                    shutil.move(src, dst)
                    word = "重命名" if op == "rename" else "移动"
                    return f"[成功] 已{word}：{src} -> {dst}"
                except Exception as e:
                    return f"[错误] {op} 失败：{e}"

            elif name == "open_path":
                target = self._resolve(args.get("path") or "")
                if not os.path.exists(target):
                    return f"[错误] 路径不存在：{target}"
                try:
                    os.startfile(target)  # 仅 Windows
                except Exception as e:
                    return f"[错误] 打开失败：{e}"
                return f"[成功] 已用系统默认程序打开：{target}"

            elif name == "fetch_url":
                url = (args.get("url") or "").strip()
                if not url.startswith(("http://", "https://")):
                    return "[错误] url 必须以 http:// 或 https:// 开头"
                try:
                    import requests
                    r = requests.get(url, timeout=20,
                                     headers={"User-Agent": "Mozilla/5.0 (AIWorkbench)"})
                except Exception as e:
                    return f"[错误] 抓取失败：{e}"
                if r.status_code != 200:
                    return f"[错误] HTTP {r.status_code} {url}"
                html, used_enc = _decode_html(r.content, r.headers,
                                              r.encoding)
                title = ""
                text = html
                try:
                    from bs4 import BeautifulSoup
                    try:
                        soup = BeautifulSoup(html, "lxml")
                    except Exception:
                        soup = BeautifulSoup(html, "html.parser")
                    if soup.title and soup.title.string:
                        title = soup.title.string.strip()
                    for t in soup(["script", "style", "noscript"]):
                        t.decompose()
                    text = soup.get_text("\n")
                except Exception:
                    text = re.sub(r"<[^>]+>", " ", html)
                text = re.sub(r"[ \t]+", " ", text)
                text = re.sub(r"\n{3,}", "\n\n", text).strip()
                if len(text) > 8000:
                    text = text[:8000] + "\n...（正文已截断）"
                return (f"网页 {url}\n标题：{title}\n"
                        f"编码：{used_enc}\n正文：\n```\n{text}\n```")

            elif name == "system_info":
                return self._system_info()

            # ============================ 时间 ============================
            elif name == "now":
                import datetime
                n = datetime.datetime.now()
                wd = "一二三四五六日"[n.weekday()]
                return (f"当前时间：{n.strftime('%Y-%m-%d %H:%M:%S')}（星期{wd}）\n"
                        f"时区：{time.strftime('%Z')}（UTC{time.strftime('%z')}）\n"
                        f"Unix 时间戳：{int(n.timestamp())}")

            # ======================== 文档生成（Word/Excel/PPT/PDF）========================
            elif name == "create_document":
                from . import docwrite
                raw = (args.get("path") or "").strip()
                content = args.get("content") or args.get("markdown") or ""
                if not str(content).strip():
                    return "[错误] content 为空，请把要写进文档的 Markdown 正文放进 content"
                path, err = self._resolve_write(raw)
                if err:
                    return f"[错误] {err}"
                if not docwrite.supported(path):
                    return (f"[错误] 只支持生成 .docx / .xlsx / .pptx / .pdf，"
                            f"收到的是 {os.path.splitext(path)[1] or '（无扩展名）'}")
                # 同样：文档正文里的换行若被双重转义，生成出来的东西会糊成一坨
                if isinstance(content, str):
                    content = normalize_text_content(content, path)
                try:
                    label, size = docwrite.create(path, str(content),
                                                  title=args.get("title"),
                                                  base_dir=os.path.dirname(path))
                except Exception as e:
                    return f"[错误] 生成 {docwrite.format_label(path)} 失败：{e}"
                if not os.path.exists(path):
                    return f"[错误] 生成后未找到文件，可能写入失败：{path}"
                self._note_written(path, size)
                return (f"[成功] 已生成{label}：{path}（{_human_size(size)}，"
                        f"已确认落盘）\n内容格式：Markdown（# 标题 / ## 二级 / - 列表 / "
                        f"| 表格 | / ```代码块```）")

            # ============================ 联网 ============================
            elif name == "web_search":
                q = (args.get("query") or args.get("q") or "").strip()
                if not q:
                    return "[错误] query 为空"
                if not self.search_cb:
                    return ("[错误] 当前没有可用的联网搜索通道。"
                            "请在顶部勾选「联网搜索」或换一个支持联网的模型端点。")
                try:
                    out = self.search_cb(q)
                except Exception as e:
                    return f"[错误] 联网搜索失败：{e}"
                if not out:
                    return f"[错误] 联网搜索「{q}」没有返回内容"
                return f"联网搜索「{q}」的结果：\n{out}"

            elif name == "download_file":
                url = (args.get("url") or "").strip()
                raw = (args.get("path") or "").strip()
                if not url.startswith(("http://", "https://")):
                    return "[错误] url 必须以 http:// 或 https:// 开头"
                if not raw:
                    name = url.split("?")[0].rstrip("/").split("/")[-1] or "download.bin"
                    raw = name
                path, err = self._resolve_write(raw)
                if err:
                    return f"[错误] {err}"
                try:
                    import requests
                    with requests.get(url, stream=True, timeout=(10, 60),
                                      headers={"User-Agent": "Mozilla/5.0 (AIWorkbench)"}) as r:
                        if r.status_code != 200:
                            return f"[错误] HTTP {r.status_code} {url}"
                        total = int(r.headers.get("Content-Length") or 0)
                        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
                        got = 0
                        with open(path, "wb") as f:
                            for chunk in r.iter_content(65536):
                                if chunk:
                                    f.write(chunk)
                                    got += len(chunk)
                                    if got > 512 * 1024 * 1024:
                                        return "[错误] 文件超过 512MB，已中止下载"
                except Exception as e:
                    return f"[错误] 下载失败：{e}"
                self._note_written(path, got)
                return (f"[成功] 已下载到 {path}（{_human_size(got)}"
                        + (f" / 共 {_human_size(total)}" if total else "") + "）")

            # ======================== 代码执行 ========================
            elif name == "run_python":
                code = args.get("code") or args.get("source") or ""
                if not code.strip():
                    return "[错误] code 为空"
                if isinstance(code, str):
                    code = normalize_text_content(code, "x.py")
                if _looks_truncated(code, "x.py", eof_only=True):
                    return ("[错误] 这段代码看起来是**被截断 / 转义写坏的半成品**，"
                            "我没有执行它。\n"
                            "请改用更可靠的两步写法：先给一行 "
                            "<tool_call>{\"name\":\"run_python\",\"arguments\":{}}</tool_call>，"
                            "紧接着跟一个 ```python 代码块``` 放完整代码（真实换行）。")
                if _py_dangerous(code):
                    if confirm_cb is not None and not confirm_cb(
                            "执行 Python 代码（检测到删除/系统级操作）：\n" + code[:800]):
                        return "[取消] 用户拒绝执行该代码。"
                exe = _find_python()
                if not exe:
                    return ("[错误] 本机没有找到可用的 Python 解释器，无法执行代码。"
                            "可以改用 run_command 执行系统命令。")
                try:
                    tf = tempfile.NamedTemporaryFile("w", suffix=".py", delete=False,
                                                     encoding="utf-8")
                    tf.write(code)
                    tf.close()
                except Exception as e:
                    return f"[错误] 无法创建临时脚本：{e}"
                try:
                    try:
                        to = float(args.get("timeout") or 60)
                    except Exception:
                        to = 60
                    to = max(5, min(to, 300))
                    proc = _winproc.run([exe, tf.name], cwd=self.files_dir,
                                        capture_output=True, timeout=to)
                    out = _decode(proc.stdout) + _decode(proc.stderr)
                    if len(out) > 8000:
                        out = out[:8000] + "\n...（输出已截断）"
                    return (f"已执行 Python 代码（解释器：{exe}，工作目录：{self.files_dir}）\n"
                            f"退出码：{proc.returncode}\n输出：\n```\n{out}\n```")
                except subprocess.TimeoutExpired:
                    return "[错误] Python 代码执行超时"
                except Exception as e:
                    return f"[错误] Python 代码执行失败：{e}"
                finally:
                    try:
                        os.remove(tf.name)
                    except Exception:
                        pass

            # ======================== 界面 / 系统交互 ========================
            elif name == "clipboard":
                action = (args.get("action") or "read").strip().lower()
                try:
                    import pyperclip
                except Exception as e:
                    return f"[错误] 剪贴板组件不可用：{e}"
                try:
                    if action in ("read", "get", "读", "读取"):
                        text = pyperclip.paste() or ""
                        if len(text) > 4000:
                            text = text[:4000] + "\n...（已截断）"
                        return f"剪贴板当前内容（{len(text)} 字）：\n```\n{text}\n```"
                    if action in ("write", "set", "copy", "写", "写入", "复制"):
                        t = args.get("text")
                        if t is None:
                            return "[错误] 写入剪贴板需要 text 参数"
                        pyperclip.copy(str(t))
                        return f"[成功] 已写入剪贴板（{len(str(t))} 字）"
                    return "[错误] action 必须是 read 或 write"
                except Exception as e:
                    return f"[错误] 剪贴板操作失败：{e}"

            elif name == "screenshot":
                raw = (args.get("path") or "").strip()
                if not raw:
                    raw = "screenshot_" + time.strftime("%Y%m%d_%H%M%S") + ".png"
                if not os.path.splitext(raw)[1]:
                    raw += ".png"
                path, err = self._resolve_write(raw)
                if err:
                    return f"[错误] {err}"
                region = args.get("region")
                if not _grab_screen(path, region if isinstance(region, list) else None):
                    return "[错误] 截屏失败（可能没有桌面会话权限）"
                size = os.path.getsize(path) if os.path.exists(path) else 0
                res = (f"[成功] 已截屏：{path}（{_human_size(size)}）\n"
                       f"屏幕分辨率：{_screen_size()}")
                analyze = args.get("analyze", args.get("ocr"))
                if str(analyze).lower() in ("true", "1", "yes", "是") and self.vision_cb:
                    try:
                        text = self.vision_cb(
                            path, "这是屏幕截图。请提取画面中的主要文字内容，"
                                  "并简要说明当前屏幕上显示的是什么。")
                        res += f"\n\n【画面识别】\n{text}"
                    except Exception as e:
                        res += f"\n\n（画面识别失败：{e}）"
                return res

            elif name == "notify":
                title = args.get("title") or "AI 工作台"
                message = args.get("message") or args.get("text") or ""
                if not message:
                    return "[错误] message 为空"
                if _toast(title, message):
                    return f"[成功] 已弹出桌面通知：{title}"
                return ("[提示] 桌面通知未能弹出（系统限制），但消息已记录："
                        f"{title} - {message}")

            elif name == "list_processes":
                try:
                    import psutil
                except Exception as e:
                    return f"[错误] 需要 psutil 才能列进程：{e}"
                flt = (args.get("filter") or args.get("name") or "").strip().lower()
                try:
                    top = int(args.get("top") or 25)
                except Exception:
                    top = 25
                rows = []
                for p in psutil.process_iter(["pid", "name", "cpu_percent",
                                              "memory_info", "status"]):
                    try:
                        info = p.info
                        nm = info.get("name") or ""
                        if flt and flt not in nm.lower():
                            continue
                        mem = (info.get("memory_info").rss / 1048576
                               if info.get("memory_info") else 0)
                        rows.append((mem, info.get("pid"), nm,
                                     info.get("cpu_percent") or 0.0,
                                     info.get("status") or ""))
                    except Exception:
                        continue
                rows.sort(reverse=True)
                if not rows:
                    return "没有匹配的进程。"
                lines = [f"{'PID':>7}  {'内存MB':>8}  {'CPU%':>6}  名称/状态"]
                for mem, pid, nm, cpu, st in rows[:top]:
                    lines.append(f"{pid:>7}  {mem:>8.1f}  {cpu:>6.1f}  {nm}（{st}）")
                total = len(rows)
                return (f"进程列表（按内存排序，共匹配 {total} 个，显示前 "
                        f"{min(top, total)} 个）：\n```\n" + "\n".join(lines) + "\n```")

            elif name == "kill_process":
                target = args.get("pid") or args.get("name")
                if target is None or str(target).strip() == "":
                    return "[错误] 需要 pid 或 name 参数"
                try:
                    import psutil
                except Exception as e:
                    return f"[错误] 需要 psutil 才能结束进程：{e}"
                victims = []
                try:
                    if str(target).strip().isdigit():
                        p = psutil.Process(int(str(target).strip()))
                        victims = [p]
                    else:
                        nm = str(target).strip().lower()
                        for p in psutil.process_iter(["pid", "name"]):
                            try:
                                if nm in (p.info.get("name") or "").lower():
                                    victims.append(p)
                            except Exception:
                                continue
                except Exception as e:
                    return f"[错误] 找不到进程：{e}"
                if not victims:
                    return f"[错误] 没有找到匹配的进程：{target}"
                desc = "、".join(f"{v.pid} {v.name()}" for v in victims[:8])
                if confirm_cb is not None and not confirm_cb("结束以下进程：" + desc):
                    return "[取消] 用户拒绝结束进程。"
                done, fail = [], []
                for v in victims:
                    try:
                        v.terminate()
                        done.append(f"{v.pid} {v.name()}")
                    except Exception as e:
                        fail.append(f"{v.pid}: {e}")
                msg = f"[成功] 已结束 {len(done)} 个进程：" + "、".join(done)
                if fail:
                    msg += "\n失败：" + "；".join(fail)
                return msg

            elif name == "window_list":
                wins = _list_windows()
                if not wins:
                    return "没有枚举到可见窗口（或当前会话没有桌面权限）。"
                lines = [f"{'PID':>7}  窗口标题"]
                for t, pid in wins[:60]:
                    lines.append(f"{pid:>7}  {t}")
                return (f"当前可见窗口（共 {len(wins)} 个）：\n```\n"
                        + "\n".join(lines) + "\n```")

            # ======================== 记忆 ========================
            elif name == "remember":
                text = args.get("text") or args.get("content") or ""
                if not str(text).strip():
                    return "[错误] text 为空，请给出要记住的内容"
                added, cat = self.memory.remember(str(text), args.get("category"))
                if added:
                    return (f"[成功] 已记入长期记忆（分类：{cat}）：{text}\n"
                            f"当前记忆概况：{self.memory.summary_text()}")
                return f"[提示] 这条内容已经在记忆里了，未重复添加（分类：{cat}）"

            elif name == "recall":
                q = (args.get("query") or args.get("keyword") or "").strip()
                hits = self.memory.search(q, limit=int(args.get("limit") or 20))
                if not hits:
                    return (f"长期记忆里没有找到与「{q}」相关的内容。"
                            if q else "长期记忆目前是空的。")
                lines = [f"- 【{c}】{t}" for c, t in hits]
                return (f"长期记忆检索「{q}」，命中 {len(hits)} 条：\n"
                        + "\n".join(lines))

            # ======================== 计划 ========================
            elif name == "update_plan":
                steps = args.get("steps")
                note = args.get("note")
                if not steps:
                    return "[错误] steps 为空，请给出步骤列表"
                if self.plan_cb:
                    try:
                        return self.plan_cb(steps, note)
                    except Exception as e:
                        return f"[错误] 更新计划失败：{e}"
                from . import plan as plan_mod
                p = plan_mod.apply_update(None, steps, note)
                return "计划已更新：\n" + plan_mod.to_markdown(p)

            # ======================== 知识库 ========================
            elif name == "build_index":
                raw = (args.get("path") or ".").strip()
                base = _safe_path(self.files_dir, raw, allow_outside=True)
                if not os.path.isdir(base):
                    return f"[错误] 目录不存在：{base}"
                try:
                    st = self.kb.build(base, max_files=int(args.get("max_files") or 2000))
                except Exception as e:
                    return f"[错误] 建索引失败：{e}"
                return (f"[成功] 已建立知识库索引\n"
                        f"扫描目录：{st['root']}\n"
                        f"入库文件：{st['files']} 个（跳过 {st['skipped']} 个）\n"
                        f"文本片段：{st['chunks']} 片\n"
                        f"之后可以用 search_knowledge 检索。")

            elif name == "search_knowledge":
                q = (args.get("query") or "").strip()
                if not q:
                    return "[错误] query 为空"
                st = self.kb.stats()
                if not st["chunks"]:
                    return ("[提示] 知识库还是空的，请先用 build_index 对某个目录建索引"
                            "（例如 build_index 并指定 path 为你的资料文件夹）。")
                try:
                    topk = int(args.get("topk") or 5)
                except Exception:
                    topk = 5
                hits = self.kb.search(q, topk=max(1, min(topk, 12)))
                if not hits:
                    return f"知识库里没有与「{q}」相关的内容（已索引 {st['chunks']} 片）。"
                blocks = []
                for h in hits:
                    blocks.append(f"[{h['score']}] {h['path']}（第{h['part']+1}段）\n"
                                  f"{h['snippet']}")
                return (f"知识库检索「{q}」，命中 {len(hits)} 段"
                        f"（索引共 {st['files']} 个文件）：\n\n" + "\n\n".join(blocks))

            # ======================== 历史对话检索 ========================
            elif name == "search_history":
                kw = (args.get("keyword") or args.get("query") or "").strip()
                if not kw:
                    return "[错误] keyword 为空"
                cdir = os.path.join(self.ws_path, "conversations")
                if not os.path.isdir(cdir):
                    return "还没有任何历史对话。"
                low = kw.lower()
                hits = []
                for fn in os.listdir(cdir):
                    if not fn.endswith(".json"):
                        continue
                    try:
                        with open(os.path.join(cdir, fn), "r", encoding="utf-8") as f:
                            d = json.load(f)
                    except Exception:
                        continue
                    for m in d.get("messages", []):
                        c = m.get("content") or ""
                        if low in c.lower():
                            idx = c.lower().find(low)
                            seg = c[max(0, idx - 60): idx + 120].replace("\n", " ")
                            hits.append(f"[{d.get('title','未命名')}] "
                                        f"{'你' if m.get('role')=='user' else 'AI'}：…{seg}…")
                            break
                    if len(hits) >= 20:
                        break
                if not hits:
                    return f"历史对话里没有找到「{kw}」。"
                return (f"历史对话检索「{kw}」，命中 {len(hits)} 个对话：\n"
                        + "\n".join("- " + h for h in hits))

            # ======================== 压缩 / 解压 ========================
            elif name == "archive":
                op = (args.get("op") or "").strip().lower()
                if op in ("zip", "压缩", "pack"):
                    src = self._resolve(args.get("src") or "")
                    if not os.path.exists(src):
                        return f"[错误] 源不存在：{src}"
                    raw = (args.get("dst") or "").strip()
                    if not raw:
                        raw = os.path.basename(src.rstrip("\\/")) + ".zip"
                    if not raw.lower().endswith(".zip"):
                        raw += ".zip"
                    dst, err = self._resolve_write(raw)
                    if err:
                        return f"[错误] {err}"
                    try:
                        if os.path.isdir(src):
                            base = os.path.dirname(dst)
                            stem = os.path.basename(dst)[:-4]
                            path = shutil.make_archive(os.path.join(base, stem),
                                                       "zip", root_dir=src)
                        else:
                            os.makedirs(os.path.dirname(dst) or ".", exist_ok=True)
                            with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as z:
                                z.write(src, os.path.basename(src))
                            path = dst
                    except Exception as e:
                        return f"[错误] 压缩失败：{e}"
                    return f"[成功] 已压缩：{path}（{_human_size(os.path.getsize(path))}）"
                if op in ("unzip", "解压", "extract"):
                    src = self._resolve(args.get("src") or "")
                    if not os.path.isfile(src):
                        return f"[错误] 压缩包不存在：{src}"
                    raw = (args.get("dst") or "").strip()
                    if raw:
                        out = self._resolve(raw)
                    else:
                        out = os.path.join(os.path.dirname(src),
                                           os.path.splitext(os.path.basename(src))[0])
                    try:
                        with zipfile.ZipFile(src) as z:
                            z.extractall(out)
                    except Exception as e:
                        return f"[错误] 解压失败：{e}"
                    try:
                        n = sum(len(fs) for _, _, fs in os.walk(out))
                    except Exception:
                        n = 0
                    return f"[成功] 已解压到 {out}（{n} 个文件）"
                return "[错误] op 必须是 zip 或 unzip"

            # ======================== MCP ========================
            elif name == "mcp_list":
                mgr = self.mcp_manager
                if not mgr:
                    return "[提示] 未启用 MCP 支持。"
                cfg = mgr.configured()
                if not cfg:
                    return ("[提示] 还没有配置 MCP 服务器。可在工作区新建 mcp.json，"
                            "格式：{\"mcpServers\":{\"名字\":{\"command\":\"可执行文件\","
                            "\"args\":[\"...\"]}}}")
                tools = mgr.list_all_tools()
                if not tools:
                    return (f"配置了 {len(cfg)} 个 MCP 服务器（{', '.join(cfg)}），"
                            f"但没有取到工具。"
                            + (f" 最后错误：{mgr.last_error}" if mgr.last_error else ""))
                lines = [f"- [{t['server']}] {t['name']}：{(t['description'] or '')[:90]}"
                         for t in tools]
                return (f"可用 MCP 工具（{len(tools)} 个，来自 "
                        f"{len({t['server'] for t in tools})} 个服务器）：\n"
                        + "\n".join(lines)
                        + "\n\n调用方式：mcp_call，参数 server / tool / arguments。")

            elif name == "mcp_call":
                mgr = self.mcp_manager
                if not mgr:
                    return "[提示] 未启用 MCP 支持。"
                server = (args.get("server") or "").strip()
                tool = (args.get("tool") or args.get("name") or "").strip()
                if not server or not tool:
                    return "[错误] 需要 server 和 tool 参数"
                arguments = args.get("arguments") or args.get("args") or {}
                if not isinstance(arguments, dict):
                    arguments = {}
                try:
                    out = mgr.call(server, tool, arguments)
                except Exception as e:
                    return f"[错误] MCP 调用异常：{e}"
                if len(out) > 6000:
                    out = out[:6000] + "\n...（结果已截断）"
                return f"MCP {server}/{tool} 返回：\n{out}"

            # ---------------- 语音（v9）----------------
            elif name == "transcribe_audio":
                from . import asr as asr_mod
                raw = (args.get("path") or args.get("file") or "").strip()
                if not raw:
                    return "[错误] 需要 path 参数（音频文件路径）"
                p = self._resolve(raw)
                if not os.path.exists(p):
                    return f"[错误] 音频文件不存在：{p}"
                try:
                    text, backend = asr_mod.transcribe(p, self.keys or {})
                except Exception as e:
                    return f"[错误] 语音识别失败：{e}"
                return (f"已转写音频 {os.path.basename(p)}"
                        f"（{asr_mod.wav_duration(p):.1f} 秒，引擎：{backend}）：\n{text}")

            elif name == "speak_text":
                text = (args.get("text") or args.get("content") or "").strip()
                if not text:
                    return "[错误] 需要 text 参数"
                eng = (args.get("engine") or "").strip() or None
                if self.speak_cb is None:
                    return "[提示] 语音朗读未接入（UI 回调缺失）。"
                try:
                    msg = self.speak_cb(text, eng)
                    return msg or f"正在朗读（{len(text)} 字）"
                except Exception as e:
                    return f"[错误] 朗读失败：{e}"

            # ---------------- 钉钉（v9）----------------
            elif name == "dingtalk_push":
                text = (args.get("text") or args.get("content") or "").strip()
                if not text:
                    return "[错误] 需要 text 参数"
                if self.dingtalk_cb is None:
                    return "[提示] 钉钉通道未接入（请到「设置 → 钉钉」配置后启用）。"
                try:
                    return self.dingtalk_cb(text, (args.get("title") or "").strip(),
                                            bool(args.get("at_all")))
                except Exception as e:
                    return f"[错误] 钉钉推送失败：{e}"

            elif name == "dingtalk_status":
                if self.dingtalk_cb is None:
                    return "[提示] 钉钉通道未接入。"
                try:
                    return self.dingtalk_cb("", "__status__", False)
                except Exception as e:
                    return f"[错误] {e}"

            # ---------------- 钉钉连接器（v9.3：查通讯录 / 发消息 / 工作通知 / 自检）----
            elif name == "dingtalk":
                act = str(args.get("action") or "status").strip().lower()
                if self.dingtalk_api_cb is None:
                    return ("[提示] 钉钉连接器未接入。请到「集成 → 钉钉」里填 "
                            "AppKey / AppSecret 并启用。")
                try:
                    return self.dingtalk_api_cb(act, args)
                except Exception as e:
                    return f"[错误] 钉钉「{act}」失败：{e}"

            # ---------------- 图片 / 图表（v9）----------------
            elif name == "image_op":
                raw = (args.get("path") or "").strip()
                if not raw:
                    return "[错误] 需要 path 参数"
                src = self._resolve(raw)
                op = (args.get("op") or "compress").strip().lower()
                dst_raw = (args.get("dst") or "").strip()
                dst = None
                if dst_raw:
                    dst, err = self._resolve_write(dst_raw)
                    if err:
                        return f"[错误] {err}"
                try:
                    return "[成功] " + _image_op(src, dst, op, args)
                except Exception as e:
                    return f"[错误] 图片处理失败：{e}"

            elif name == "create_chart":
                raw = (args.get("path") or "chart.png").strip()
                path, err = self._resolve_write(raw)
                if err:
                    return f"[错误] {err}"
                try:
                    return "[成功] " + _create_chart(
                        path, args.get("chart") or "bar",
                        args.get("labels") or [], args.get("values") or [],
                        str(args.get("title") or ""))
                except Exception as e:
                    return f"[错误] 生成图表失败：{e}"

            # ---------------- 提醒 / 更新（v9）----------------
            elif name == "reminder":
                text = (args.get("text") or args.get("content") or "").strip()
                when = (args.get("when") or args.get("time") or "").strip()
                if not text:
                    return "[错误] 需要 text（提醒内容）"
                if not when:
                    return "[错误] 需要 when（如 \"10分钟后\"、\"今天18:30\"）"
                try:
                    ts, human = parse_when(when)
                except Exception as e:
                    return f"[错误] {e}"
                if self.reminder_cb is None:
                    return "[提示] 提醒功能未接入。"
                try:
                    return self.reminder_cb(text, ts,
                                            bool(args.get("dingtalk")), human)
                except Exception as e:
                    return f"[错误] 设置提醒失败：{e}"

            elif name == "check_update":
                if self.update_cb is None:
                    return "[提示] 更新检查未接入。"
                try:
                    return self.update_cb()
                except Exception as e:
                    return f"[错误] 检查更新失败：{e}"

            # ---------------- 通用 HTTP（v9）----------------
            elif name == "http_request":
                url = (args.get("url") or "").strip()
                if not url:
                    return "[错误] 需要 url 参数"
                if not url.lower().startswith(("http://", "https://")):
                    return "[错误] url 必须以 http:// 或 https:// 开头"
                method = (args.get("method") or "GET").upper()
                headers = args.get("headers") or {}
                if not isinstance(headers, dict):
                    headers = {}
                body = args.get("body") or args.get("data")
                payload = None
                if body is not None:
                    if isinstance(body, (dict, list)):
                        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
                        headers.setdefault("Content-Type", "application/json")
                    else:
                        payload = str(body).encode("utf-8")
                headers.setdefault("User-Agent",
                                   "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AIWorkbench/9")
                # 用 requests（会自动走系统代理）；本机 urllib 会被中间代理拦截，
                # 所以 urllib 只作最后的兜底。
                try:
                    import requests as _rq
                    resp = _rq.request(method, url, headers=headers, data=payload,
                                       timeout=30, allow_redirects=True)
                    raw = _decode_html(resp.content[:300000], resp.headers,
                                       resp.encoding)[0]
                    code = resp.status_code
                    where = f"（最终地址 {resp.url}）" if resp.url != url else ""
                except Exception:
                    try:
                        import urllib.request
                        req = urllib.request.Request(url, data=payload,
                                                     headers=headers, method=method)
                        with urllib.request.urlopen(req, timeout=30) as r:
                            raw = r.read(300000).decode("utf-8", "ignore")
                            code = r.status
                        where = ""
                    except Exception as e:
                        return f"[错误] 请求失败：{type(e).__name__}: {e}"
                head = raw[:4000]
                if len(raw) > 4000:
                    head += f"\n…（共 {len(raw)} 字符，已截断）"
                tag = "成功" if 200 <= code < 400 else f"HTTP {code}"
                return f"[{tag}] HTTP {method} {url}{where} → {code}\n{head}"

            # ---------------- v9.1：技能 / 定时任务 / 子代理 ----------------
            elif name == "list_skills":
                items = self.skills.list_all()
                if not items:
                    return ("[成功] 暂无可用技能。可以把 SKILL.md 放进工作区的 "
                            "skills/<名字>/ 目录来自建技能。")
                lines = [f"[成功] 共 {len(items)} 个技能：", ""]
                for s in items:
                    src = "内置" if s["source"] == "builtin" else "自建"
                    lines.append(f"· {s['name']}（{s['slug']}）[{src}]")
                    if s.get("description"):
                        lines.append(f"  说明：{s['description']}")
                    if s.get("when"):
                        lines.append(f"  何时用：{s['when']}")
                lines.append("")
                lines.append("要按某个技能做事，调 use_skill(name) 取出它的执行步骤。")
                return "\n".join(lines)

            elif name == "use_skill":
                key = (args.get("name") or args.get("skill")
                       or args.get("slug") or "").strip()
                if not key:
                    return "[错误] 需要 name 参数（技能名或 slug）"
                s = self.skills.find(key)
                if not s:
                    names = "、".join(x["name"] for x in self.skills.list_all())
                    return (f"[错误] 没找到技能 {key!r}。可用技能：{names}。"
                            f"也可以调 list_skills 看完整列表。")
                body = self.skills.body_of(s)
                return (f"[成功] 已加载技能「{s['name']}」——"
                        f"接下来严格按下面的步骤执行，不要跳步：\n\n{body}")

            elif name == "schedule_task":
                prompt = (args.get("prompt") or args.get("task")
                          or args.get("content") or "").strip()
                if not prompt:
                    return "[错误] 需要 prompt 参数：到期要执行的自然语言任务"
                kind = (args.get("kind") or args.get("repeat") or "daily").lower()
                alias = {"一次": "once", "一次性": "once", "间隔": "interval",
                         "每小时": "hourly", "每天": "daily", "每日": "daily",
                         "每周": "weekly"}
                kind = alias.get(kind, kind)
                wk = args.get("weekdays") or args.get("days")
                try:
                    t = self.scheduler.add(
                        name=(args.get("name") or prompt[:20]),
                        prompt=prompt, kind=kind,
                        at=args.get("at") or args.get("time") or "09:00",
                        weekdays=wk,
                        interval_min=args.get("interval_min")
                        or args.get("every_minutes") or 60,
                        run_at=args.get("run_at") or args.get("datetime") or "",
                        notify_dingtalk=bool(args.get("notify_dingtalk")
                                             or args.get("dingtalk")),
                    )
                except ValueError as e:
                    return f"[错误] {e}"
                return (f"[成功] 已创建定时任务「{t['name']}」（id={t['id']}）\n"
                        f"触发规则：{_sched_mod.describe(t)}\n"
                        f"下次执行：{_sched_mod.next_text(t)}\n"
                        f"任务内容：{t['prompt']}"
                        + ("\n钉钉推送：开" if t.get("notify_dingtalk") else ""))

            elif name == "list_tasks":
                items = self.scheduler.list_all()
                if not items:
                    return "[成功] 当前没有任何定时任务。"
                lines = [f"[成功] 共 {len(items)} 个定时任务：", ""]
                for t in items:
                    flag = "启用" if t.get("enabled") else "已停"
                    lines.append(
                        f"· [{t['id']}] {t['name']}（{flag}）")
                    lines.append(f"  规则：{_sched_mod.describe(t)}"
                                 f"　下次：{_sched_mod.next_text(t)}")
                    lines.append(f"  内容：{t['prompt'][:70]}")
                    if t.get("last_status"):
                        lines.append(f"  上次：{t['last_status']}"
                                     f"（已跑 {t.get('run_count', 0)} 次）")
                return "\n".join(lines)

            elif name == "cancel_task":
                tid = (args.get("id") or args.get("task_id")
                       or args.get("name") or "").strip()
                if not tid:
                    return "[错误] 需要 id 参数（可先 list_tasks 查）"
                hit = None
                for t in self.scheduler.list_all():
                    if t["id"] == tid or tid in (t.get("name") or ""):
                        hit = t
                        break
                if not hit:
                    return f"[错误] 没找到任务 {tid!r}"
                self.scheduler.remove(hit["id"])
                return f"[成功] 已删除定时任务「{hit['name']}」（id={hit['id']}）"

            elif name == "run_task":
                tid = (args.get("id") or args.get("task_id")
                       or args.get("name") or "").strip()
                hit = None
                for t in self.scheduler.list_all():
                    if t["id"] == tid or (tid and tid in (t.get("name") or "")):
                        hit = t
                        break
                if not hit:
                    return f"[错误] 没找到任务 {tid!r}"
                _, res = self.scheduler.run_now(hit["id"])
                return (f"[成功] 已立即执行一次「{hit['name']}」：\n"
                        f"{str(res)[:1500]}")

            elif name == "spawn_agent":
                task = (args.get("task") or args.get("prompt") or "").strip()
                if not task:
                    return "[错误] 需要 task 参数：交给子代理去做的事"
                if not self.subagent_cb:
                    return ("[错误] 当前运行环境没有挂接子代理执行器"
                            "（子代理需要模型客户端支持）。")
                steps = args.get("max_steps") or args.get("steps") or 6
                try:
                    steps = max(2, min(12, int(float(steps))))
                except Exception:
                    steps = 6
                res = self.subagent_cb(task, steps)
                return f"[成功] 子代理完成（最多 {steps} 步）：\n{res}"

            else:
                low = (name or "").lower().replace("-", "_")
                target = TOOL_ALIAS.get(low)
                if target and target != name:
                    # 走 execute() 而不是 _dispatch()，让追踪日志记下"实际执行的是谁"
                    return self.execute({"name": target, "arguments": args},
                                        confirm_cb)
                # 名字像某个技能（slug 或技能名）-> 自动换成 use_skill 取步骤
                try:
                    s = self.skills.find(name)
                except Exception:
                    s = None
                if s:
                    return self.execute(
                        {"name": "use_skill", "arguments": {"name": s["slug"]}},
                        confirm_cb)
                names = ", ".join(sorted(KNOWN_TOOLS))
                return (f"[错误] 未知工具：{name}。可用工具只有：{names}。"
                        f"请从这份清单里挑一个重新调用。"
                        f"如果要做的是某个技能的流程，用 use_skill 传技能名。")
        except ValueError as e:
            return f"[错误] {e}"
        except Exception as e:
            return f"[错误] 工具执行异常：{e}"
