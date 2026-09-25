# -*- coding: utf-8 -*-
"""技能系统（Skill）—— 对标 WorkBuddy / Claude Skills 的可加载指令包。

一个技能 = 一段「遇到这类任务就按这个套路做」的专业指令。
模型平时看不到技能正文（省上下文），只在判断任务匹配时用 `use_skill` 取出来照着做。

来源有两处：
1. 内置技能：写在下面的 BUILTIN 里，随程序走，开箱即用；
2. 用户技能：工作区 `skills/<目录名>/SKILL.md`，带 YAML 头部（name / description /
   when），可以自己写、也可以从别处拷进来。

SKILL.md 头部格式（宽松解析，不依赖 yaml 库）：

    ---
    name: 我的技能
    description: 一句话说明
    when: 什么时候用
    ---

    正文：分步骤的指令……
"""
import os
import re

# ------------------------------------------------------------------ 内置技能
BUILTIN = [
    {
        "slug": "weekly-report",
        "name": "周报 / 日报生成",
        "description": "把零散的工作记录整理成结构清晰、有数据支撑的周报或日报。",
        "when": "用户说「写周报」「写日报」「汇总这周做的事」「工作总结」时",
        "steps": [
            "先问清时间范围与需要突出的重点；用户没给就说清你按什么范围整理。",
            "把素材分成四块：本周完成 / 进行中 / 遇到的问题 / 下周计划。",
            "每条写成「做了什么 + 产出了什么（尽量带数字）」，「完成」不要写「参与了」。",
            "遇到的问题必须配一句「已采取/准备采取的办法」，不要只抱怨。",
            "如果工作区文件里有日志、记录、表格，先用 read_document / search_files 真读一遍再写，不要凭空编。",
            "最后用 create_document 输出 .docx，标题带日期范围；需要的话同内容再出一份 .pdf。",
        ],
    },
    {
        "slug": "doc-typeset",
        "name": "文档排版与生成",
        "description": "把内容排成规范的 Word / PDF 文档，标题层级、列表、表格、代码块齐全。",
        "when": "用户要「做成 Word」「生成 PDF」「排版」「出一份正式文档」时",
        "steps": [
            "正文用 Markdown 写：# / ## / ### 控制标题层级，- 或 1. 控制列表，| 表格 | 控制表格，``` 控制代码块。",
            "文档结构默认：一级标题（文档名）→ 二三级标题分节 → 表格/要点 → 结论。",
            "表格列别超过 6 列，列名要短；数字右对齐写清楚单位。",
            "用 create_document(path, content, title) 生成，扩展名决定格式：.docx / .xlsx / .pptx / .pdf。",
            "生成后一定用 read_document 回读一次核实，确认内容没丢、表格没塌。",
        ],
    },
    {
        "slug": "data-analysis",
        "name": "数据分析与图表",
        "description": "对表格/数据做统计、找规律，并配可视化图表。",
        "when": "用户给了一堆数据 / 一个表格，问「分析一下」「画个图」「趋势怎么样」时",
        "steps": [
            "先用 read_document 把表格真读出来，别用记忆里的数字。",
            "确定口径：总量、均值、最大最小、同比/环比、占比，按数据形态挑。",
            "要算具体数字时用 run_python 真算（禁止口算），把结果贴出来。",
            "用 create_chart 出图：趋势用 line、对比用 bar、占比用 pie（饼图别超过 6 项）。",
            "结论要写成「发现了什么 + 可能的原因 + 建议动作」，图表只负责呈现。",
        ],
    },
    {
        "slug": "code-review",
        "name": "代码审查",
        "description": "逐段读代码，找出真问题并给出可直接替换的修改建议。",
        "when": "用户说「审查代码」「帮我看看这段代码」「有没有 bug」时",
        "steps": [
            "先把文件真读进来（read_file / search_files 定位），一次只看一个文件。",
            "按四类挑问题：正确性（会算错/会崩）、边界（空值/越界/并发）、安全（注入/明文密钥/危险命令）、可维护性。",
            "每条问题给出：文件:行号、症状、为什么会出问题、可直接替换的代码。",
            "没找到问题时明确说「这几处是好的」，不要为了凑数硬编。",
            "最后给出优先级排序：先修哪个。",
        ],
    },
    {
        "slug": "meeting-notes",
        "name": "会议纪要整理",
        "description": "把散乱的会议内容整理成决议、待办、责任人三件套。",
        "when": "用户给了会议记录 / 录音转写，要「整理纪要」时",
        "steps": [
            "先分段：议题、讨论要点、结论、待办。",
            "结论必须明确「决定了什么」，模糊的讨论移到要点里，不要混进结论。",
            "每条待办写成「做什么 + 谁 + 什么时候前」，缺信息就标「待确认」而不是编造。",
            "涉及数字与时间要逐条核对原文。",
            "输出用 create_document 出 .docx，待办部分用表格。",
        ],
    },
    {
        "slug": "deep-research",
        "name": "资料调研",
        "description": "围绕一个主题上网检索、交叉验证，产出带来源的结论。",
        "when": "用户问「查一下」「调研」「现在最新的是什么」「对比几个方案」时",
        "steps": [
            "先把问题拆成 2~4 个可检索的子问题（不同关键词），逐个 web_search。",
            "搜索结果里只采信有明确来源的；同一条事实至少两个来源对得上才写进结论。",
            "每条关键结论后面注明来源（域名或标题），不要写「网上说」。",
            "如果搜索没找到，如实说「没有检索到」，不要用训练时的旧印象冒充最新信息。",
            "最后按「结论 → 依据 → 不确定性」三段输出。",
        ],
    },
    {
        "slug": "sheet-clean",
        "name": "表格清洗",
        "description": "处理脏表格：去重、补空、统一格式、规范列名。",
        "when": "用户给了混乱的表格要「清理」「整理」「去重」「格式统一」时",
        "steps": [
            "先 read_document 读原表，把列名和几行样例列出来确认理解无误。",
            "检查四类问题：重复行、空值、格式不一致（日期/数字/单位）、列名不规范。",
            "每类问题给出处理规则，并说明会改动多少行。",
            "用 run_python 真跑清洗（读原表 → 输出新表），不要手写结果。",
            "输出新文件时不覆盖原文件，文件名加 _cleaned 后缀。",
        ],
    },
    {
        "slug": "ppt-outline",
        "name": "演示文稿大纲",
        "description": "把一个主题组织成有起承转合、可直接讲的 PPT。",
        "when": "用户要「做个 PPT」「演示文稿」「讲稿」时",
        "steps": [
            "先定三件事：听众是谁、讲多久、讲完要对方做什么。",
            "页数按「每页 1 分钟」估；结构用封面 → 背景/问题 → 核心内容（3~5 页）→ 方案 → 结论/行动。",
            "每页只放一个观点，标题写成结论句（「XX 提升了 30%」而不是「XX 情况」）。",
            "正文用 Markdown 写，一级标题当页标题，二级以下当要点，用 create_document 出 .pptx。",
            "需要配图时用 create_chart 出图，图要在要点提出之后才有意义。",
        ],
    },
    {
        "slug": "bug-hunt",
        "name": "故障排查",
        "description": "按证据链定位程序/系统异常，先复现再改。",
        "when": "用户说「报错了」「跑不起来」「打不开」「不生效」时",
        "steps": [
            "先要现场信息：完整报错、复现步骤、什么时候开始的、之前改过什么。",
            "复现优先于猜测：能跑就跑（run_python / run_command），把真实输出贴出来。",
            "排查顺序：环境（依赖/版本/路径）→ 输入（数据是否合法）→ 逻辑（边界分支）→ 资源（权限/磁盘/端口）。",
            "找到根因后给出修改方案，并说明「怎么验证已经修好」。",
            "改代码前先备份原文件。",
        ],
    },
]

_FM = re.compile(r"^---\s*\n(.*?)\n---\s*\n?", re.DOTALL)


def _parse_front(text, fallback_name=""):
    meta, body = {}, text or ""
    m = _FM.match(body)
    if m:
        head = m.group(1)
        body = body[m.end():]
        for line in head.splitlines():
            line = line.strip()
            if not line or ":" not in line:
                continue
            k, v = line.split(":", 1)
            meta[k.strip().lower()] = v.strip().strip('"').strip("'")
    return meta, body.strip()


class SkillStore:
    """技能仓库：内置 + 工作区用户技能。"""

    def __init__(self, workspace_path=None):
        self.ws = os.path.abspath(workspace_path) if workspace_path else None
        self.dir = os.path.join(self.ws, "skills") if self.ws else None

    # ---------- 列表 ----------
    def list_all(self):
        """返回全部技能（内置在前，用户在后），每项含 slug/name/description/when/source。"""
        out = []
        for s in BUILTIN:
            out.append({
                "slug": s["slug"], "name": s["name"],
                "description": s["description"], "when": s["when"],
                "source": "builtin",
            })
        for s in self._user_skills():
            out.append(s)
        return out

    def _user_skills(self):
        out = []
        if not self.dir or not os.path.isdir(self.dir):
            return out
        for name in sorted(os.listdir(self.dir)):
            d = os.path.join(self.dir, name)
            if not os.path.isdir(d):
                continue
            f = os.path.join(d, "SKILL.md")
            if not os.path.isfile(f):
                continue
            try:
                with open(f, "r", encoding="utf-8") as fh:
                    raw = fh.read()
            except Exception:
                continue
            meta, body = _parse_front(raw, name)
            out.append({
                "slug": meta.get("slug") or name,
                "name": meta.get("name") or name,
                "description": meta.get("description") or body.split("\n", 1)[0][:80],
                "when": meta.get("when") or meta.get("trigger") or "",
                "source": "user",
                "path": f,
            })
        return out

    def find(self, key):
        """按 slug 或名称模糊查找（大小写不敏感、支持中文包含匹配）。"""
        key = (key or "").strip()
        if not key:
            return None
        k = key.lower()
        items = self.list_all()
        # 1) slug / name 完全相等
        for s in items:
            if s["slug"].lower() == k or s["name"].lower() == k:
                return s
        # 2) slug / name 包含
        for s in items:
            if k in s["slug"].lower() or k in s["name"].lower() or \
               s["slug"].lower() in k or s["name"].lower() in k:
                return s
        return None

    # ---------- 取正文 ----------
    def body_of(self, skill):
        """返回技能正文（步骤列表拼成 Markdown）。"""
        slug = skill["slug"]
        for s in BUILTIN:
            if s["slug"] == slug:
                lines = [f"# 技能：{s['name']}", "", f"**适用场景**：{s['when']}", "",
                         "## 执行步骤（按顺序做）", ""]
                for i, st in enumerate(s["steps"], 1):
                    lines.append(f"{i}. {st}")
                return "\n".join(lines)
        p = skill.get("path")
        if p and os.path.isfile(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    _, body = _parse_front(f.read())
                return body or "(技能内容为空)"
            except Exception as e:
                return f"(技能读取失败：{e})"
        return "(找不到技能内容)"

    # ---------- 安装 ----------
    def install(self, slug, text):
        """把一份 SKILL.md 内容装进工作区 skills/<slug>/SKILL.md。"""
        if not self.dir:
            raise RuntimeError("未绑定工作区，无法安装技能")
        safe = re.sub(r"[^\w\-.]+", "-", (slug or "skill")).strip("-") or "skill"
        d = os.path.join(self.dir, safe)
        os.makedirs(d, exist_ok=True)
        p = os.path.join(d, "SKILL.md")
        with open(p, "w", encoding="utf-8") as f:
            f.write(text or "")
        return p

    def summary_lines(self):
        """给提示词用的极简清单（只有名字和适用场景，正文不进上下文）。"""
        out = []
        for s in self.list_all():
            when = (s.get("when") or "").strip()
            out.append(f"- {s['name']}（{s['slug']}）"
                       + (f"：{when}" if when else ""))
        return out
