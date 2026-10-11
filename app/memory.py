# -*- coding: utf-8 -*-
"""跨会话长期记忆：工作区根目录的 MEMORY.md。

- AI 通过 remember 工具写入，通过 recall 工具检索；
- 每轮对话开始时，把记忆摘要注入系统提示词，让 AI「记得」用户是谁、偏好什么；
- 用户可在界面上直接查看/编辑/清空。
"""
import os
import re
import time

from .workspace import internal_path

DEFAULT_CATEGORIES = ["用户与身份", "偏好与习惯", "项目与约定", "重要结论", "待办与计划"]

# 「待办与计划」这一类**不**自动注入 system 提示词（根因 B 修复，见 prompt_block）
TODO_CATEGORY = "待办与计划"

# 「未完成状态」类条目：跨分类也一律**不**自动注入（根因 B 修复）。
#   实测真正把模型带偏的那条 ——「P1576 题目代码在 …p1576.py 中，但**只完成了
#   一半**」—— 并不在「待办与计划」下，而在「项目与约定」下。这类"半成品 /
#   没做完"的记录一旦每轮进 prompt，配合系统提示词里"做到底"的压力就会被当成
#   这一轮的欠账。只挡注入：MEMORY.md 本身、recall/search 检索都不受影响。
_UNFINISHED_RE = re.compile(
    r"(只完成(?:了)?一半|未完成|未做完|没(?:有)?做完|"
    r"还没(?:有)?(?:做|写|完成|改|补)|半成品|"
    r"(?:待|需|尚需)(?:完成|补完|重写|重建|修复|继续))")

_HEAD_RE = re.compile(r"^##\s+(.+?)\s*$")


class MemoryStore:
    def __init__(self, workspace_path: str):
        self.path = os.path.join(workspace_path, "MEMORY.md")
        self._cache = None
        self._mtime = 0

    # ---------- 基础读写 ----------
    def exists(self):
        return os.path.isfile(self.path)

    def load_text(self):
        if not os.path.isfile(self.path):
            return ""
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                return f.read()
        except Exception:
            return ""

    def save_text(self, text):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            f.write(text or "")
        self._cache = None

    # ---------- 结构化 ----------
    def parse(self):
        """解析成 {分类: [条目, ...]}，保持顺序。

        ⚠ 必须跳过「文件前言」：dump() 写出的开头是
        `# 长期记忆` + `> 由 AI 工作台自动维护，最后更新：<时间>`。
        这两行既不是 `## 分类` 标题、也不是空行，旧代码会把它们当成
        「用户与身份」的普通条目；下一次 dump() 又把它们写回成 `- ...`，
        而那句更新时间每写一次就变一次、去重抓不住 —— 于是每保存一轮就多
        两条垃圾，累积成几十条（真实发生过）。现在：进入任何 `## 分类`
        之前的内容一律忽略；任何 `#`/`>` 行也一律忽略。
        """
        text = self.load_text()
        data = {}
        cur = None
        for line in text.splitlines():
            m = _HEAD_RE.match(line)
            if m:
                cur = m.group(1).strip()
                data.setdefault(cur, [])
                continue
            s = line.strip()
            if not s or s.startswith(("#", ">")):
                continue          # 前言/标题/引用一律不是条目
            if cur is None:
                continue          # 还没进入任何 ## 分类，忽略正文
            if s.startswith(("- ", "* ", "• ")):
                s = s[2:].strip()
            # 去掉 bullet 标记后仍是标题/引用的（存量脏数据 `- # 长期记忆`
            # `- > 由 AI…`）也丢掉，这样重新 dump 一次即可自动清干净
            if not s or s.startswith(("#", ">")):
                continue
            data[cur].append(s)
        return data

    def dump(self, data):
        lines = ["# 长期记忆", "",
                 f"> 由 AI 工作台自动维护，最后更新："
                 f"{time.strftime('%Y-%m-%d %H:%M:%S')}", ""]
        for cat, items in data.items():
            lines.append(f"## {cat}")
            lines.append("")
            for it in items:
                lines.append(f"- {it}")
            lines.append("")
        self.save_text("\n".join(lines).rstrip() + "\n")

    @staticmethod
    def _norm(s):
        return re.sub(r"\s+", "", (s or "")).lower()

    def remember(self, text, category=None):
        """记一条。完全重复的条目会被忽略，返回 (是否新增, 分类)。"""
        text = (text or "").strip()
        if not text:
            return False, category or DEFAULT_CATEGORIES[0]
        cat = (category or "").strip() or DEFAULT_CATEGORIES[0]
        data = self.parse()
        data.setdefault(cat, [])
        key = self._norm(text)
        for items in data.values():
            for it in items:
                if self._norm(it) == key:
                    return False, cat
        data[cat].append(text)
        self.dump(data)
        return True, cat

    def forget(self, keyword):
        """删除包含关键词的条目，返回删除条数。"""
        kw = self._norm(keyword)
        if not kw:
            return 0
        data = self.parse()
        n = 0
        for cat in list(data.keys()):
            keep = [it for it in data[cat] if kw not in self._norm(it)]
            n += len(data[cat]) - len(keep)
            data[cat] = keep
        if n:
            self.dump(data)
        return n

    def search(self, keyword, limit=20):
        """按关键词检索条目，返回 [(分类, 条目)]。"""
        kw = self._norm(keyword)
        data = self.parse()
        if not kw:
            out = []
            for cat, items in data.items():
                for it in items:
                    out.append((cat, it))
                    if len(out) >= limit:
                        return out
            return out
        out = []
        for cat, items in data.items():
            for it in items:
                if kw in self._norm(it) or self._norm(it) in kw:
                    out.append((cat, it))
                    if len(out) >= limit:
                        return out
        return out

    def clear(self):
        self.save_text("")

    # ---------- 注入提示词 ----------
    def prompt_block(self, max_chars=1800):
        """生成要拼进系统提示词的一段文字；没有记忆则返回空串。

        ★ v9.18.0 行为修复（根因 B + 根因 C）：

        B) 主记忆原来**整块**每轮注入 system 提示词。而真实工作区的 MEMORY.md
           里堆着大量历史「未完成」状态（例：「P1576 代码在 D:\\工作\\files\\
           p1576.py，**只完成了一半**」「用正确的 4 空格缩进重建文件」），
           再乘上默认 system 提示词里「做到底，不许半途收尾」的强压力 ——
           模型把记忆里的半成品当成**这一轮的欠账**，用户只是问了句「你是谁」，
           助手却自作主张跑去补完旧任务（真实事故）。现在：
             · 「待办与计划」这一类**不再自动注入**（改成只能用 recall 显式
               检索时才可见；下方只留一行"有 N 条未列出、用户问起再说"的提示）；
             · 稳定事实（用户与身份 / 偏好与习惯 / 项目与约定 / 重要结论）照常注入；
             · 开头写死护栏：记忆只是历史背景，用户本轮没明确要求就**不许执行**。

        C) 截断的 break 原来只跳出内层条目循环，外层分类循环继续跑，
           于是**每个分类都补一行**「…（记忆较长，已截断）」。
           现在用 truncated 标志干净退出两层，只留**一条**截断提示。
        """
        data = self.parse()
        # ① 「待办与计划」不自动注入（只在 recall 检索时才可见）
        todo_n = len(data.get(TODO_CATEGORY) or [])
        data = {k: v for k, v in data.items()
                if v and k.strip() != TODO_CATEGORY}
        # ② 记载"半成品/没做完"的条目，跨分类也不注入（见 _UNFINISHED_RE）
        hidden = 0
        filtered = {}
        for k, items in data.items():
            keep = []
            for it in items:
                if _UNFINISHED_RE.search(it):
                    hidden += 1
                    continue
                keep.append(it)
            if keep:
                filtered[k] = keep
        data = filtered
        if not any(data.values()) and not (todo_n or hidden):
            return ""
        lines = ["# 五、长期记忆（你之前记住的、跨会话有效的信息）",
                 "以下是你在过往对话中主动记下的内容，请自然地把它们用在回答里"
                 "（例如称呼、偏好、项目约定），不要生硬复述：",
                 "",
                 "⚠ 护栏（必须遵守）：以上只是**历史背景和长期事实**，"
                 "**不是**这一轮的任务。",
                 "· 用户**这一轮**没有提出的要求，一律不许去做。记忆里记着什么"
                 "半成品、什么没做完的事，都只是记录，**不等于**用户现在要你去补完。",
                 "· 只有用户在**本轮这条消息里明确**要求继续/去办，才允许动手；"
                 "否则就当这些条目不存在，只回答用户这一轮问的这一件事。",
                 "· 纯身份介绍 / 闲聊 / 提问（例如「你是谁」「你能做什么」）："
                 "只作简短回答，**禁止**调用任何工具去翻旧账、去补历史任务。",
                 ""]
        total = 0
        truncated = False
        for cat, items in data.items():
            if not items or truncated:
                break
            lines.append(f"【{cat}】")
            for it in items:
                line = "- " + it
                if total + len(line) > max_chars:
                    truncated = True
                    break
                lines.append(line)
                total += len(line)
            if truncated:
                break
            lines.append("")
        if truncated:
            lines.append("- …（记忆较长，已截断）")
            lines.append("")
        if todo_n or hidden:
            _n = todo_n + hidden
            lines.append(
                f"（另有 {_n} 条「待办 / 未完成状态」的记忆**没有**列在这里。"
                "只有当用户**本轮明确**问起待办 / 明确要求继续某件事时，"
                "才用 recall 检索它们；除此之外一律不许主动执行。）")
            lines.append("")
        lines.append("如果你发现了值得长期记住的新信息（用户偏好、项目约定、"
                     "重要结论、明确的待办），请主动调用 remember 工具记下来；"
                     "需要回忆细节时可以调用 recall 检索。")
        return "\n".join(lines)

    def summary_text(self):
        """给界面用的一段纯文本摘要。"""
        data = self.parse()
        if not any(data.values()):
            return "（暂无长期记忆）"
        out = []
        for cat, items in data.items():
            if items:
                out.append(f"【{cat}】共 {len(items)} 条")
        return "；".join(out)


def build_default_memory_file(workspace_path):
    """首次使用时生成一个带说明的空记忆文件。"""
    p = os.path.join(workspace_path, "MEMORY.md")
    if os.path.exists(p):
        return p
    os.makedirs(workspace_path, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        f.write(
            "# 长期记忆\n\n"
            "> 这是 AI 工作台的长期记忆文件。AI 会在对话中自动参考这里的内容，\n"
            "> 也会在每轮对话结束后**自动把值得记住的信息写进来**；\n"
            "> 你也可以直接编辑它。\n\n"
            "## 用户与身份\n\n"
            "## 偏好与习惯\n\n"
            "## 项目与约定\n\n"
            "## 重要结论\n\n"
            "## 待办与计划\n")
    return p


# ==================== 自动记忆（不依赖模型是否愿意调 remember） ====================

# 规则层：从用户这句话里认出「值得长期记住」的信息
_AUTO_RULES = [
    ("偏好与习惯", [r"我(?:更喜欢|喜欢|习惯|倾向|一般用|常用|只用|不要|不喜欢)",
                    r"以后(?:都|请|别|不要|要)", r"下次(?:都|请|别|不要|要)",
                    r"(?:记住|记一下|帮我记住)(?:我)?",
                    r"我(?:讨厌|反感)", r"别用|不要用|少用"]),
    ("用户与身份", [r"我(?:叫|是|在)(?:[^，。；\n]{1,20})",
                    r"我是(?:初三|初二|初一|高三|高二|高一|小学|初中|高中)",
                    r"我的?(?:名字|昵称|班级|学校|身份)",
                    r"我(?:今年|现在)(?:读|上|在)"]),
    ("项目与约定", [r"我(?:们)?(?:项目|工作区|仓库|文件夹)(?:放在|在|位于|叫)",
                    r"以后(?:这个|该)?项目",
                    r"用(?:中文|简体中文|英文)(?:回答|回复|界面)?",
                    r"统一(?:放在|用|命名)"]),
    ("待办与计划", [r"(?:明天|后天|下周|下个月|周[一二三四五六日]|今天晚些)",
                    r"提醒我", r"要记得", r"别忘了", r"待办"]),
]

# 这些词太宽泛，命中了也别记（否则记忆会被垃圾填满）
_AUTO_SKIP = [
    "帮我", "给我", "看一下", "查一下", "列一下", "写一份", "生成",
    "怎么做", "是什么", "为什么", "能不能", "可以吗", "？", "?",
]

_STRONG = ("记住", "记一下", "帮我记住", "以后都", "以后请", "下次都",
           "不要用", "别用", "我喜欢", "我习惯", "我讨厌")


def auto_candidates(user_text):
    """从用户的话里挑出值得长期记住的短句，返回 [(分类, 文本)]。

    规则很保守：宁可少记，也不要把闲聊塞进长期记忆。

    实测情况：用户常把「我是谁」和「我的偏好」写在同一句里，例如
    「我叫小明，以后都用中文回答我」。只归一个分类会丢信息，所以
    一句命中多个分类时会**按逗号拆成子句**分别归类。
    """
    text = (user_text or "").strip()
    if not text or len(text) > 400:
        return []

    def _cats_of(s):
        return [cat for cat, pats in _AUTO_RULES
                if any(re.search(p, s) for p in pats)]

    def _allowed(s):
        """整句级过滤：明确的强信号放行；否则含闲聊词就跳过。"""
        if any(k in s for k in _STRONG):
            return True
        return not any(k in s for k in _AUTO_SKIP)

    out = []
    for seg in re.split(r"[。！？!?\n；;]", text):
        s = seg.strip()
        if not (4 <= len(s) <= 120) or not _allowed(s):
            continue
        cats = _cats_of(s)
        if len(cats) <= 1:
            if cats:
                out.append((cats[0], s))
            continue
        # 命中多个分类 -> 按逗号/顿号拆子句，各自归类
        got = []
        for cl in re.split(r"[，,、]", s):
            cl = cl.strip()
            if not (4 <= len(cl) <= 120) or not _allowed(cl):
                continue
            cc = _cats_of(cl)
            if cc:
                got.append((cc[0], cl))
        out.extend(got if got else [(cats[0], s)])

    # 去重、限量
    seen, res = set(), []
    for cat, s in out:
        k = MemoryStore._norm(s)
        if k in seen:
            continue
        seen.add(k)
        res.append((cat, s))
    return res[:4]


def auto_log_path(workspace_path):
    # ★ v9.18.0：自动记忆流水属于机器内部数据，藏进 <工作区>/.awb/
    #   （老工作区还没迁移时 internal_path 会自动回退到根目录的 memory_auto.jsonl）
    return internal_path(workspace_path, "memory_auto.jsonl")


def log_auto(workspace_path, items, source="对话"):
    """把「自动写入了什么」记一笔，界面可以展示、用户可追溯。"""
    if not items:
        return
    try:
        rec = {"t": time.strftime("%Y-%m-%d %H:%M:%S"),
               "source": source,
               "items": [{"cat": c, "text": t} for c, t in items]}
        with open(auto_log_path(workspace_path), "a", encoding="utf-8") as f:
            f.write(__import__("json").dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


def recent_auto_log(workspace_path, n=30):
    import json
    p = auto_log_path(workspace_path)
    if not os.path.isfile(p):
        return []
    out = []
    try:
        with open(p, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except Exception:
                    continue
    except Exception:
        return []
    return out[-int(n):][::-1]


def extract_json_facts(raw):
    """从模型返回的文本里抠出 [{'category':..,'text':..}]。

    容忍 ```json 围栏和夹带的解释文字。
    """
    import json
    if not raw:
        return []
    s = raw.strip()
    s = re.sub(r"```(?:json)?", "", s)
    m = re.search(r"\[.*\]", s, re.DOTALL)
    if not m:
        return []
    try:
        data = json.loads(m.group(0))
    except Exception:
        return []
    out = []
    if isinstance(data, list):
        for it in data:
            if isinstance(it, dict):
                t = str(it.get("text") or it.get("memory") or "").strip()
                c = str(it.get("category") or it.get("cat") or "").strip()
                if t:
                    out.append((c, t))
            elif isinstance(it, str) and it.strip():
                out.append(("", it.strip()))
    return out[:6]

