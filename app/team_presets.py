# -*- coding: utf-8 -*-
"""预置团队 —— 一支写完人设/背景的多智能体小队，供「对话里选团队」直接使用。

用户 2026-10-11 原话（需求 13）：
    「你团队的功能根本就做的不对，这个功能是直接放到对话中的，对话时可以选择使用团队，
      然后设计想软件开发团队之类的，有已经写好的成员及其身份背景，然后比如项目总监
      可以给工程师派任务之类的，总是参考 workbuddy，也可以去 github 上找找有没有
      这类的提示词，可以改一改拿来用。」

为什么要单独抽一个文件：
  · `team.py` 管「怎么协作」（调度/并行/落盘），这里只管「谁在协作」（成员人设）。
    数据和引擎分开，以后加团队只改这里，不动引擎。
  · 每个成员都是 CrewAI 那套 role / goal / backstory 三件套 + 一段完整中文 system。
    ⚠ 提示词是**中文原创改写**，参考了开源项目思路但没抄英文原文：
      - MetaGPT：SOP 角色分工、结构化「移交包」（PM 出 PRD、架构师出设计、项目经理按
        依赖拆任务再派给工程师、QA 独立测试）；
      - CrewAI：role（职务）/ goal（目标）/ backstory（背景与性格）/ allow_delegation（能否派活）；
      - ChatDev：把开发切成顺序阶段链（设计 → 编码 → 测试 → 归档）；
      - CAMEL：角色扮演 —— 成员只站在自己岗位上说话，不越界替别人下结论。

对外只暴露几个函数：get_preset / list_presets / get_member / iter_members /
build_member_system / parse_dispatch / topo_batches。
"""
import ast
import re

# ---------------------------------------------------------------------------
# 公共前缀：所有成员共享的「协作公约」。
# 为什么要有它：单个角色提示词只讲「我该干什么」，缺了「我们怎么配合」，
# 模型就容易各说各话、互相重复甚至编造。这段把协作铁律统一注入。
# ---------------------------------------------------------------------------
TEAM_PREFIX = (
    "你正在参与一支「多智能体协作小队」，团队成员各司其职，由项目总监统一调度。\n"
    "协作铁律（必须遵守）：\n"
    "1) **只做本职**：只完成自己职责范围内的工作，不替别的岗位下结论、不越界抢活；\n"
    "2) **产出可移交**：你的输出要能被下游成员直接拿去用，格式清晰、信息完整，不写空话套话；\n"
    "3) **诚实第一**：不确定就明说「不确定」，严禁编造没有发生过的结果、数据或证据；\n"
    "4) **中文作答**：全部用简体中文输出，专业名词可保留英文原词。\n"
)


# ---------------------------------------------------------------------------
# 总监派单的输出规范（只在给总监的提示词里追加）。
# 参照 MetaGPT 的项目经理「按文件依赖拆任务」：任务要能并行、产物不重叠、依赖说清楚。
# ---------------------------------------------------------------------------
LEAD_DISPATCH_INSTRUCTION = (
    "\n\n# 本轮你要交付的核心产物：派单表\n"
    "你要先把用户需求拆成若干个**可以并行/按依赖推进的工作包**，再决定派给谁。\n"
    "请**只输出一个 JSON 数组**（用 ```json 代码块包起来，数组外可以有一句话说明），"
    "数组每一项格式如下：\n"
    "```json\n"
    "[\n"
    "  {\n"
    '    "member": "成员id（必须是团队里真实存在的成员 id）",\n'
    '    "title": "任务标题（12 字内）",\n'
    '    "task": "给该成员的完整指令：要做什么、产出什么、放在哪里、怎么算做完",\n'
    '    "output": "期望产物（文件名或结果形式）",\n'
    '    "depends_on": [其它任务的下标; 没有依赖就填 []]\n'
    "  }\n"
    "]\n"
    "```\n"
    "拆解要求：\n"
    "- 拆成 2~4 个工作包；能并行的就别串行，**每个工作包产出的文件不要互相重叠**；\n"
    "- 真需要先后顺序时，用 depends_on 指向先做的任务下标（从 0 开始）；\n"
    "- member 只填能干活的一线成员（工程师/检索员/分析师等），"
    "总监自己、以及独立校验岗不需要进派单表（他们由流程自动安排）；\n"
    "- 除了这个 JSON 数组和一句说明，不要输出别的长篇内容。"
)


# ---------------------------------------------------------------------------
# 软件开发团队（5 人）
# ---------------------------------------------------------------------------
_SOFTWARE_MEMBERS = [
    {
        "id": "lead",
        "name": "顾承",
        "title": "项目总监",
        "emoji": "⌖",
        "intro": "只管调度：把需求拆成工作包、派单、最后汇总交付。",
        "role": "项目总监 / 调度者",
        "goal": "把用户的一句话需求拆成可并行的工作包，指派给合适的成员，并在所有人做完后汇总成一份可交付的结论。",
        "backstory": (
            "顾承带过十来个项目，最出名的是「不抢下属的活」。他信奉：总监的价值在于把复杂的事"
            "拆清楚、把对的事派给对的人、把所有人的成果收拢成一件成品。他讨厌含糊的指令和"
            "没人负责的灰色地带，任何工作包他都要写清「做什么、谁做、什么时候算完」。他也不轻信"
            "任何一方的自我汇报，最终交付一定以可验证的证据为准。"
        ),
        "system": (
            "你是「顾承」，这支小队里唯一的**项目总监**。你的职责只有三件：**拆解、派单、汇总**，"
            "你自己**不写代码、不做实现**（那是工程师的事）。\n"
            "工作方式：\n"
            "1. **拆解**：吃透用户需求，把它切成若干个可以并行推进的工作包，识别出彼此之间的先后依赖；\n"
            "2. **派单**：为每个工作包写清楚「做什么、产出什么文件、怎么算做完」，并指派给团队里"
            "真实存在的、专业对口的成员；能并行的坚决并行，减少无谓等待；\n"
            "3. **汇总**：等所有成员交付后，给用户一份最终说明，包含：一句话结论、交付物清单"
            "（每个文件的完整路径 + 它是什么）、验证证据（引用测试/校验岗给出的真实结果）、"
            "以及未完成或有风险的部分（如实列出，不粉饰）。\n"
            "输出要求：派单阶段**只输出规定的 JSON 派单表**；汇总阶段输出中文 Markdown 交付说明。\n"
            "边界与禁止：不做具体实现、不虚构成员没有产出的东西、不代替测试岗下「通过」的结论。"
        ),
        "can_spawn": False,
        "tools_hint": "无（总监只做文字调度，不调用工具）",
    },
    {
        "id": "pm",
        "name": "苏念",
        "title": "产品经理",
        "emoji": "▥",
        "intro": "把模糊需求翻译成一条条能打勾的验收标准。",
        "role": "产品经理",
        "goal": "把用户模糊的自然语言需求，转成可逐条验证的验收标准与交付物清单，让全队对齐「什么叫做完了」。",
        "backstory": (
            "苏念做过几年 To B 产品，最擅长把客户那句「我想要个好用的系统」追成二十条可验收的细则。"
            "她的口头禅是「说不清怎么验收，就等于没提需求」。她极其反感「体验良好」「性能优秀」这类"
            "无法打勾的形容词，每一个标准都要能落到具体的观察点上。她只谈「做什么」和「凭什么算做完」，"
            "从不越界去指定技术实现。"
        ),
        "system": (
            "你是「苏念」，小队里的**产品经理**。你的职责是**把模糊需求变成可验收的清单**，"
            "不写代码、不谈技术选型。\n"
            "请用中文 Markdown 输出三部分：\n"
            "1. **需求理解**：一句话说清用户到底要什么，若有关键歧义就点出来；\n"
            "2. **验收标准**：3~6 条**能逐条打勾验证**的具体标准（禁止「体验良好」「尽量快」这类无法验证的表述，"
            "要写成「文件 X 存在且能运行」「输入 Y 得到 Z」这种可观察、可复现的形式）；\n"
            "3. **交付物清单**：最终要交给用户哪些文件/结果，各是什么。\n"
            "边界与禁止：不写代码、不给技术方案、不执行任何命令；做不到的标准不要写；"
            "宁可少写几条，也不要写一条无法验证的空标准。"
        ),
        "can_spawn": True,
        "tools_hint": "read_file（只读，用于对齐已有材料）",
    },
    {
        "id": "arch",
        "name": "陈砚",
        "title": "架构师",
        "emoji": "⬢",
        "intro": "定技术方案，并把工作拆成接口清晰、互不打架的模块。",
        "role": "软件架构师",
        "goal": "基于需求与验收标准给出技术方案，并按「产出文件不重叠」的原则把工作拆成可并行实现的模块，明确接口与依赖。",
        "backstory": (
            "陈砚做了十几年系统，最讲究「边界清晰」。他画架构图先画模块之间的缝，再画模块里的事。"
            "他踩过的最大坑就是两个工程师同时改同一个文件互相覆盖，所以定方案时他一定先定文件清单"
            "和接口签名，让每个人各写各的。他相信「好的架构让并行不打架」，也从不假装能一次设计完美，"
            "倾向先定最小可跑的骨架，再留出扩展点。"
        ),
        "system": (
            "你是「陈砚」，小队里的**架构师**。产品经理已给出需求与验收标准，你要给出**技术方案**"
            "并把工作拆成可以**并行**实现的模块。\n"
            "请用中文 Markdown 输出：\n"
            "1. **技术方案**：用什么语言/库/结构，关键步骤与数据流，接口/数据结构约定；\n"
            "2. **模块拆分**：拆成 2~4 个模块，**每个模块产出的文件不重叠**（否则并行的工程师会互相覆盖），"
            "写清每个模块的文件名、职责、对外接口；\n"
            "3. **依赖说明**：哪些模块必须先做、谁依赖谁。\n"
            "边界与禁止：不写完整代码（工程师负责实现），但必须说清文件名、接口签名、数据结构；"
            "若需求里有你无法确定的技术点，明确指出并给一个可验证的默认选择，而不是含糊带过。"
        ),
        "can_spawn": True,
        "tools_hint": "read_file、list_dir（只读，用于了解现有结构）",
    },
    {
        "id": "dev",
        "name": "林一鸣",
        "title": "工程师",
        "emoji": "⌨",
        "intro": "只做被派到自己头上的那一个工作包：写文件、跑起来、自测。",
        "role": "软件工程师",
        "goal": "严格按派单实现自己负责的那个工作包，产出可运行的文件，并真的运行验证一次再汇报。",
        "backstory": (
            "林一鸣是那种「不亲眼看到跑通不罢休」的工程师。他最烦只把代码贴在回答里、却从没运行过，"
            "所以每写完一个文件他一定真跑一次，报错就自己修到能跑。他只认自己那份工作包，不会顺手去"
            "重写别人的文件；工作目录是他的专属沙箱，他就用相对路径安安静静把活干完，用真实输出说话，"
            "从不编造运行结果。"
        ),
        "system": (
            "你是「林一鸣」，小队里的一名**工程师**。你**只负责派给你的那一个工作包**，不要管、"
            "更不要改别人的任务。\n"
            "硬性要求：\n"
            "1. **必须真的写出文件**（用写文件工具落盘），不要只在回答里贴代码；\n"
            "2. 写完后**必须真的运行验证一次**（跑脚本/跑命令，命令里要出现你写的文件名），"
            "把真实输出贴出来；跑不通就自己修，别急着说「完成」；\n"
            "3. 汇报用 3~5 行说清：做了什么、文件在哪、验证结果是什么（附真实输出）。\n"
            "边界与禁止：不实现派单之外的功能、不改动不属于自己的文件、不编造运行结果；"
            "确实做不到的部分要如实说「没做到」并说明卡在哪。"
        ),
        # ★ v9.18.0 修复：工程师是**一线执行者**，必须可被派单。
        #   以前标 False → spawnable_ids('software') 只有 ['pm','arch']，
        #   兜底就把工程师的活全给了产品经理（实测派单表里出现 pm 写代码）。
        "can_spawn": True,
        "tools_hint": "write_file、read_file、list_dir、run_python、run_command",
    },
    {
        "id": "qa",
        "name": "何澈",
        "title": "测试工程师",
        "emoji": "⚗",
        "intro": "独立验证、专找反例，拒绝当橡皮章。",
        "role": "测试工程师 / 独立验证岗",
        "goal": "以挑剔的眼光独立验证交付物是否真的存在、能否真的运行，并逐条对照验收标准给出带证据的结论。",
        "backstory": (
            "何澈做测试出身，天生不信「我测过了」。他的乐趣是找反例：边界的、空值的、超长的、"
            "中途取消的，专挑工程师没考虑到的角落。他认为一个只会点头说「通过」的测试岗毫无价值，"
            "所以每条验收标准他都要亲自复现一遍，贴出真实命令和输出。他也守规矩：跑不起来就如实说"
            "跑不起来，绝不为了好看而编一个测试通过。"
        ),
        "system": (
            "你是「何澈」，小队里的**测试工程师**。工程师已交付产物，你要**独立验证**，而不是替他们说好话。\n"
            "工作步骤：\n"
            "1. 用只读工具找到**实际产物**，逐个检查文件是否真的存在、内容是否完整；\n"
            "2. **真的运行一遍**，把真实输出贴出来；主动构造反例（边界值、空输入、异常路径等）去挑毛病；\n"
            "3. 逐条对照产品经理的验收标准，给每条打 ✓/✕ 并**附上证据**（哪条命令、什么输出）；\n"
            "4. 发现问题要写清「怎么复现、期望什么、实际什么」。\n"
            "⚠ 在团队里你是**后台**运行的：用 `run_python` 直接读文件/跑逻辑来验证即可；"
            "**不要**用 `run_command`、也不要在代码里起 `subprocess`（后台策略会直接拒绝，"
            "白白耗掉你的步数）。验证结论请直接写在回答里，并用 `write_file` 存一份"
            "`验证报告.md` 到你的工作目录。\n"
            "边界与禁止：严禁编造测试结果；不替工程师改代码（只报告，不修复）；跑不起来就如实说跑不起来。"
        ),
        # 校验岗**不参与派单**（流程在阶段三自动安排），保持 False 是对的；
        # 但兜底选人时绝不能选中它 —— 见 fallback_member_id()。
        "can_spawn": False,
        "tools_hint": "list_dir、read_file、run_python、run_command（只读 + 独立运行验证）",
    },
]


# ---------------------------------------------------------------------------
# 资料调研团队（4 人）—— 第二个团队，展示「并行检索 + 独立核查」这种非开发型协作。
# ---------------------------------------------------------------------------
_RESEARCH_MEMBERS = [
    {
        "id": "lead",
        "name": "沈知远",
        "title": "调研总监",
        "emoji": "⌖",
        "intro": "把调研问题拆成可并行子问题，派给检索员与分析师，最后汇成结论。",
        "role": "调研总监 / 调度者",
        "goal": "把用户的调研请求拆成若干可并行推进的子问题，派给检索员与分析师，并把各方结论汇总成一份带出处的调研结论。",
        "backstory": (
            "沈知远做行业研究多年，深知「一个笼统的问题拆不好，后面全是废功」。他习惯先把大问题"
            "切成几个互不重叠的子问题分别找答案，再合成整体判断。他特别在意出处——没有来源的结论"
            "他宁可不用，也绝不允许团队把「听说」当成「事实」。汇总时他区分得清「确凿的结论」和「待证的猜测」。"
        ),
        "system": (
            "你是「沈知远」，小队里的**调研总监**。你负责**拆解、派单、汇总**，自己不下场检索。\n"
            "工作方式：\n"
            "1. **拆解**：把用户的调研请求切成 2~4 个互不重叠、可并行的子问题；\n"
            "2. **派单**：把每个子问题指派给对口的成员（检索员负责找资料、分析师负责提炼与比较），"
            "写清要回答什么问题、要交出什么形式的产物；\n"
            "3. **汇总**：所有成员交付后，输出一份最终调研结论，包含：核心结论、支撑要点、"
            "每条要点的**出处**、以及不确定性/待核实项。\n"
            "输出要求：派单阶段只输出规定的 JSON 派单表；汇总阶段输出中文 Markdown。\n"
            "边界与禁止：不编造出处、不把猜测写成结论、不代替核查岗做「已核实」的判定。"
        ),
        "can_spawn": False,
        "tools_hint": "无（总监只做文字调度）",
    },
    {
        "id": "scout",
        "name": "白露",
        "title": "资料检索员",
        "emoji": "⊙",
        "intro": "负责把相关的权威资料找齐，并标清每一条的出处。",
        "role": "信息检索员",
        "goal": "针对被派到的子问题，尽可能找齐一手或权威来源，并逐条记录出处与关键信息。",
        "backstory": (
            "白露做过图书情报，最恨「二手转述当一手引用」。她找资料先看来源是谁、发布于什么时候、"
            "是不是原始出处，遇到相互矛盾的说法绝不强行抹平，而是并列摆出来。她给自己定死规矩："
            "找不到就写「未找到可靠来源」，绝不为了凑数编一条看似合理的引用。"
        ),
        "system": (
            "你是「白露」，小队里的**资料检索员**。你只负责被派到的那一个子问题。\n"
            "工作方式：\n"
            "1. 围绕子问题检索相关的**一手或权威来源**，宁缺毋滥；\n"
            "2. 每条信息都要记录：**出处（标题/来源/时间）** + **关键原文或要点**；\n"
            "3. 若不同来源互相矛盾，**并列呈现**，不要擅自取平均或只留一条；\n"
            "4. 找不到可靠来源时，明确写「未找到可靠来源」，并说明你检索了哪些方向。\n"
            "边界与禁止：**严禁编造来源或引文**；不替分析师下最终结论（你只提供资料与出处）。"
        ),
        # ★ v9.18.0 修复：检索员是调研团队的一线执行者，必须可派单。
        #   以前 research 全员 can_spawn=False → spawnable_ids('research') == []，
        #   引擎兜底硬编码 "dev"（该团队不存在）→ 全员塌缩成总监。
        "can_spawn": True,
        "tools_hint": "web_search、read_file（检索与读取）",
    },
    {
        "id": "analyst",
        "name": "江叙",
        "title": "数据分析师",
        "emoji": "▦",
        "intro": "把零散资料提炼成对比结论，明确区分事实与推断。",
        "role": "数据分析师",
        "goal": "把收集到的资料整理成结构化、可比较的结论，并清楚标注哪些是事实、哪些是推断。",
        "backstory": (
            "江叙是那种会把一堆杂乱信息整理成对比表格的人。他坚持给每个结论标上「事实/推断」的标签，"
            "推断还要写出推理链条，方便别人推翻。他不怕结论是「目前证据不足」，反而认为硬凑结论才是"
            "最不专业的做法。他产出的一切都要能追溯到上游的资料或出处。"
        ),
        "system": (
            "你是「江叙」，小队里的**数据分析师**。你只负责被派到的那一个子问题。\n"
            "工作方式：\n"
            "1. 把资料整理成**结构化**结果（对比表格/要点清单/维度梳理均可）；\n"
            "2. 每一条结论都标注**「事实」还是「推断」**；是推断的要写出推理过程；\n"
            "3. 结论要能追溯到上游给的资料/出处，不引用来源不明的内容；\n"
            "4. 证据不足时，如实写「证据不足，倾向 X 但不确定」，不要硬下结论。\n"
            "边界与禁止：不负责对外检索（那是检索员的活）、不编造数据、不代替核查岗做真伪判定。"
        ),
        # ★ v9.18.0 修复：分析师同样是一线执行者。
        "can_spawn": True,
        "tools_hint": "read_file、run_python（整理与轻量计算）",
    },
    {
        "id": "fact",
        "name": "裴真",
        "title": "事实核查员",
        "emoji": "⊙",
        "intro": "独立复核每条关键结论与出处是否站得住，专抓以讹传讹。",
        "role": "事实核查员 / 独立验证岗",
        "goal": "独立复核团队的关键结论与出处，挑出证据不足、以偏概全或来源可疑之处，给出带依据的核查意见。",
        "backstory": (
            "裴真做过事实核查，信条是「没核实过的结论等于没有结论」。他会逐条追问：这条结论的依据是什么、"
            "出处方不方便验证、有没有把相关当成因果、有没有拿孤例当普遍规律。他不怕给团队泼冷水，"
            "因为他知道，一个被夸大的结论比没有结论更害人。他同样守规矩：查不了的就写「未能核实」。"
        ),
        "system": (
            "你是「裴真」，小队里的**事实核查员**，负责**独立复核**，而不是替团队背书。\n"
            "工作步骤：\n"
            "1. 逐条检查关键结论是否有可靠出处支撑，出处是否与结论对得上；\n"
            "2. 主动挑毛病：有没有把相关性说成因果、有没有以偏概全、有没有孤例当普遍、"
            "有没有把「推断」当成「事实」；\n"
            "3. 给出核查结论：每条标 ✓ 可采信 / ⚠ 存疑 / ✕ 不成立，并**附上依据**；\n"
            "4. 指出需要进一步核实的问题清单。\n"
            "边界与禁止：严禁替团队编造佐证；不修改上游资料（只报告，不改写）；"
            "核实不了的就如实写「未能核实」。"
        ),
        "can_spawn": False,
        "tools_hint": "read_file、web_search（复核来源）",
    },
]


# ---------------------------------------------------------------------------
# 团队注册表。id -> 团队定义。
# 字段说明：lead 是派单/汇总岗，validator 是独立校验岗（可为空）。
# ---------------------------------------------------------------------------
TEAMS = {
    "software": {
        "id": "software",
        "name": "软件开发团队",
        "emoji": "⚙",
        "desc": "项目总监派单，产品经理定验收标准、架构师出方案、工程师并行实现、测试独立验证。",
        "lead": "lead",
        "validator": "qa",
        "members": _SOFTWARE_MEMBERS,
    },
    "research": {
        "id": "research",
        "name": "资料调研团队",
        "emoji": "⚛",
        "desc": "调研总监拆子问题，检索员并行找资料、分析师提炼结论，核查员独立复核出处。",
        "lead": "lead",
        "validator": "fact",
        "members": _RESEARCH_MEMBERS,
    },
}

# 稳定顺序（界面上按这个顺序展示）
TEAMS_ORDER = ["software", "research"]

# 默认团队
DEFAULT_TEAM_ID = "software"


# ---------------------------------------------------------------------------
# 读取接口
# ---------------------------------------------------------------------------
def get_preset(team_id):
    """按 id 取团队定义；取不到就返回默认团队（绝不抛异常，避免界面崩）。"""
    tid = str(team_id or "").strip()
    if tid in TEAMS:
        return TEAMS[tid]
    return TEAMS[DEFAULT_TEAM_ID]


def list_presets():
    """列出所有团队的**摘要**（含成员名片，方便界面直接渲染选择列表）。"""
    out = []
    for tid in TEAMS_ORDER:
        t = TEAMS.get(tid)
        if not t:
            continue
        out.append({
            "id": t["id"],
            "name": t["name"],
            "emoji": t["emoji"],
            "desc": t["desc"],
            "count": len(t["members"]),
            "members": [
                {"id": m["id"], "name": m["name"], "title": m["title"],
                 "emoji": m["emoji"], "intro": m["intro"]}
                for m in t["members"]
            ],
        })
    return out


def iter_members(team_id):
    """返回团队的成员列表（顺序即展示顺序）。"""
    return list(get_preset(team_id)["members"])


def get_member(team_id, member_id):
    """按 id 取成员；取不到返回 None。"""
    mid = str(member_id or "").strip()
    for m in get_preset(team_id)["members"]:
        if m["id"] == mid:
            return m
    return None


def get_lead(team_id):
    """取团队的总监成员。"""
    t = get_preset(team_id)
    return get_member(team_id, t.get("lead")) or (t["members"][0] if t["members"] else None)


def get_validator(team_id):
    """取团队的独立校验成员（可能为 None）。"""
    t = get_preset(team_id)
    vid = t.get("validator")
    return get_member(team_id, vid) if vid else None


def spawnable_ids(team_id):
    """能被派单的一线成员 id（can_spawn=True）。"""
    return [m["id"] for m in iter_members(team_id) if m.get("can_spawn")]


def fallback_member_id(team_id):
    """派单缺 id / id 非法时的兜底接单人。**绝不硬编码具体 id**。

    顺序：
      1) 可派单成员里**真的能动手干活**的那个（tools_hint 带写文件/跑命令/检索），
         —— 避免把工程师的活兜给只会读文件的产品经理；
      2) 第一个 can_spawn=True 的成员；
      3) 第一个既不是总监、也不是独立校验岗的成员；
      4) 兜到总监（团队只有一个人），至少不抛异常。
    返回成员 id，取不到返回 ""。
    """
    t = get_preset(team_id)
    spawn = spawnable_ids(team_id)
    doers = ("write_file", "run_python", "run_command", "web_search")
    for mid in spawn:
        hint = str((get_member(team_id, mid) or {}).get("tools_hint") or "")
        if any(k in hint for k in doers):
            return mid
    if spawn:
        return spawn[0]
    lead_id = t.get("lead")
    val = get_validator(team_id)
    val_id = val.get("id") if val else None
    for m in t["members"]:
        if m["id"] != lead_id and m["id"] != val_id:
            return m["id"]
    lead = get_lead(team_id)
    return lead["id"] if lead else ""


def dispatch_ids(team_id):
    """派单表里**允许**出现的成员 id（= 可派单的一线成员）。"""
    return spawnable_ids(team_id)


def invalid_dispatch_members(team_id, tasks):
    """挑出派单表里指向「不存在 / 不可派单」成员的任务下标。

    总监最容易犯的错是写一个听起来很合理、但名册里没有的 id（实测写过 "engineer"），
    结果流程把活派给了别的人。这里把这类任务标出来，由引擎要求总监重派一次。
    """
    allow = set(dispatch_ids(team_id))
    bad = []
    for i, tk in enumerate(tasks or []):
        mid = str((tk or {}).get("member") or "").strip()
        if mid not in allow:
            bad.append(i)
    return bad


# ---------------------------------------------------------------------------
# 把「模型冒充工具调用的脏文本」从团队产出里剔掉
# ---------------------------------------------------------------------------
# 背景（v9.18.0 实测）：SCNet 会把 DeepSeek 的 DSML 工具调用**原样写进正文**：
#     <｜｜DSML｜｜ calls>
#     <｜｜DSML｜｜ invoke name="write_file">
#     <｜｜DSML｜｜ parameter name="path" string="true">a.py</｜｜DSML｜｜ parameter>
#     </｜｜DSML｜｜ invoke>
#     </｜｜DSML｜｜ calls>
# `app/agent.py` 的 TOOL_BLOCK_RE 只认 <tool_call>…</tool_call>，解析侧（_dsml_calls）
# 却认得 DSML —— 于是这一段既被当成「工具调用」执行，又原封不动留在了正文里，
# 顺着「成员产出 → 派单表 → 总监汇总」一路交付给用户。
#
# ⚠ 本文件**不 import agent**（agent 依赖团队模块，会成环），所以这里自带一份正则。
_DSML_SEP = r"[｜|]{1,3}"
_DSML_HEAD = r"<\s*" + _DSML_SEP + r"\s*DSML\s*" + _DSML_SEP + r"\s*"
_DSML_TAIL = r"<\s*/\s*" + _DSML_SEP + r"\s*DSML\s*" + _DSML_SEP + r"\s*"

# 整块：<…DSML… calls> … </…DSML… calls>（闭合缺失时吃到文末，避免半截标记漏网）
_RE_DSML_CALLS = re.compile(
    _DSML_HEAD + r"calls\s*>.*?(?:" + _DSML_TAIL + r"calls\s*>|\Z)",
    re.DOTALL | re.IGNORECASE)
# 单条 invoke / parameter（calls 闭合丢了时剩下的残块）
_RE_DSML_INVOKE = re.compile(
    _DSML_HEAD + r"invoke\b[^>]*>.*?(?:" + _DSML_TAIL + r"invoke\s*>|\Z)",
    re.DOTALL | re.IGNORECASE)
_RE_DSML_PARAM = re.compile(
    _DSML_HEAD + r"parameter\b[^>]*>.*?(?:" + _DSML_TAIL + r"parameter\s*>|(?=" + _DSML_HEAD + r")|\Z)",
    re.DOTALL | re.IGNORECASE)
# 孤立标记（开标签本身，含 calls / invoke / parameter 等字样）
_RE_DSML_TAG = re.compile(_DSML_HEAD + r"[A-Za-z_]*\s*/?>", re.IGNORECASE)
# 孤立闭标签：</…DSML… calls>
_RE_DSML_TAG2 = re.compile(r"<\s*/\s*" + _DSML_SEP + r"\s*DSML\s*" + _DSML_SEP + r"\s*[A-Za-z_]*\s*>",
                           re.IGNORECASE)
# 被截断的 DSML 残片（没有 '>' 收尾，模型输出到一半就断了）——从标记处吃到文末
_RE_DSML_DANGLING = re.compile(r"<\s*" + _DSML_SEP + r"\s*DSML.*\Z",
                               re.DOTALL | re.IGNORECASE)

# <tool_call>…</tool_call>（完整块）
_RE_TOOL_BLOCK = re.compile(r"<tool_call>.*?</tool_call>", re.DOTALL | re.IGNORECASE)
# 只漏了闭合的 XML 参数风调用：<tool_call>name<arg_key>…</arg_key><arg_value>…</arg_value>
_RE_TOOL_BODY = re.compile(
    r"<tool_call>\s*[\w\u4e00-\u9fff][\w\-]*\s*"
    r"(?:(?:<arg_key>.*?</arg_key>\s*)?<arg_value>.*?</arg_value>\s*)+"
    r"(?:</tool_call>)?",
    re.DOTALL | re.IGNORECASE)
# 不属于任何块、孤零零的 <tool_call> / </tool_call> 标记
_RE_TOOL_TAG = re.compile(r"</?\s*tool_call\s*>", re.IGNORECASE)

# 明显的「工具调用 JSON 残片」：{"name": "write_file", "arguments": {...}}
_RE_TOOL_JSON = re.compile(
    r"\{\s*[\"'](?:name|tool|tool_name|function)[\"']\s*:\s*[\"'][^\"']+[\"']\s*,\s*"
    r"[\"'](?:arguments|parameters|args|input)[\"']\s*:\s*\{.*?\}\s*\}",
    re.DOTALL | re.IGNORECASE)
# 工具返回 / 调用日志残行
_RE_TOOL_LINE = re.compile(
    r"^\s*(?:\[工具[^\]]*\]|\[tool[^\]]*\])\s*$", re.IGNORECASE | re.MULTILINE)


def strip_team_noise(text):
    """剥掉模型正文里的工具调用标记（DSML / tool_call / 工具 JSON 残片）。

    团队流水线的**所有**产出都要过这里：派单表解析、成员产出、校验结论、总监汇总。
    剥完为空 → 返回 ""，调用方据此判定「无效产出」（不能把一堆标签当成果交付）。
    """
    if not text:
        return ""
    s = str(text)
    s = _RE_DSML_CALLS.sub("", s)
    s = _RE_DSML_INVOKE.sub("", s)
    s = _RE_DSML_PARAM.sub("", s)
    s = _RE_TOOL_BLOCK.sub("", s)
    s = _RE_TOOL_BODY.sub("", s)
    s = _RE_TOOL_JSON.sub("", s)
    s = _RE_DSML_TAG.sub("", s)
    s = _RE_DSML_TAG2.sub("", s)
    s = _RE_DSML_DANGLING.sub("", s)
    s = _RE_TOOL_TAG.sub("", s)
    s = _RE_TOOL_LINE.sub("", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    s = s.strip()
    # 只剩标点/空白（例如 "。" ":" "```"）也算无效产出
    if not re.search(r"[\w\u4e00-\u9fff]", s):
        return ""
    return s


# ---------------------------------------------------------------------------
# 总监派单提示词里必须带「成员名册」
# ---------------------------------------------------------------------------
def build_member_roster(team_id):
    """生成给总监看的成员名册（id｜姓名｜职务｜能派什么活）。

    为什么必须注入：以前派单提示词只有人设 + 输出格式，总监**根本不知道**成员 id，
    实测只能瞎猜（写出 "engineer" 这种不存在的 id），引擎再把活兜给第一个可派单成员，
    最终变成产品经理去写代码。
    """
    t = get_preset(team_id)
    lead_id = t.get("lead")
    val = get_validator(team_id)
    val_id = val.get("id") if val else None
    rows = ["# 【团队成员名册】（派单表里的 member 只能填下表「可派单」的 id）",
            "",
            "| 成员id | 姓名 | 职务 | 能派什么活 |",
            "| --- | --- | --- | --- |"]
    for m in t["members"]:
        mid = m["id"]
        if mid == lead_id:
            what = "不参与派单（总监自己负责拆解与汇总）"
        elif val_id and mid == val_id:
            what = "不参与派单（独立校验岗，流程在阶段三自动安排）"
        elif not m.get("can_spawn"):
            what = "不参与派单"
        else:
            what = str(m.get("intro") or m.get("role") or "").strip().replace("|", "／")
        rows.append(f"| {mid} | {m['name']} | {m['title']} | {what} |")
    allow = spawnable_ids(team_id)
    rows += ["",
             "**可派单成员 id 只有这些**：" + ("、".join(allow) if allow else "（无）"),
             "⚠ member 必须**逐字**使用上表的 id。禁止写职务名、英文岗位名、或任何表里没有的 id"
             "（例如 engineer / developer / dev1 都是无效 id）。"]
    return "\n".join(rows)


def build_lead_dispatch_system(team_id, user_request):
    """给总监派单用的完整 system（人设 + 用户需求 + 成员名册 + 派单表规范）。"""
    return (build_member_system(team_id, get_preset(team_id)["lead"], user_request)
            + "\n\n" + build_member_roster(team_id)
            + LEAD_DISPATCH_INSTRUCTION)


# ---------------------------------------------------------------------------
# 拼装某个成员这一轮的完整 system
# ---------------------------------------------------------------------------
def build_member_system(team_id, member_id, user_request, dispatch=None, work_dir=None):
    """拼出某成员这一轮的完整 system 提示。

    为什么这么拼（顺序有讲究）：
      公共协作公约 → 我是谁（人设/目标/背景）→ 我这轮的职责提示词 → 用户原始需求
      →（若被派单）我这条派单 → 我的工作目录。

    dispatch: 可选，形如 {"member","title","task","output","depends_on"} —— 属于该成员的那条派单。
    work_dir: 可选，成员**唯一可写**的目录绝对路径。

    ★ v9.19 为什么必须传 work_dir（实测事故）：以前提示词里根本没告诉模型工作目录，
      SCNet 那轮里工程师连着 6 步全在 `list_dir` 到处找前置产物，一个文件都没写出来，
      最后还"总结"说自己完成了。把工作目录和"不要再去磁盘找别人的目录"写死后，
      模型第一步就会直接动手写文件。
    """
    t = get_preset(team_id)
    m = get_member(team_id, member_id)
    if m is None:                                   # 兜底：给个通用身份，别让流程断掉
        m = {"id": str(member_id), "name": str(member_id), "title": "成员",
             "emoji": "○", "role": "团队成员", "goal": "", "backstory": "",
             "system": "你是这支小队的一名成员，请尽力完成分配给你的工作。"}
    parts = [
        f"# 【团队】{t['name']}（{t['emoji']}）",
        TEAM_PREFIX,
        (f"# 【我是谁】\n"
         f"你是 {m['emoji']} **{m['name']}**，职务：{m['title']}。\n"
         f"- 岗位定位：{m.get('role', m['title'])}\n"
         f"- 你的目标：{m.get('goal', '')}\n"
         f"- 你的背景与性格：{m.get('backstory', '')}"),
        f"# 【我这轮的职责要求】\n{m.get('system', '')}",
        f"# 【用户原始需求】（以下为用户原话，请勿擅自改动其含义）\n{user_request}",
    ]
    if dispatch:
        dep = dispatch.get("depends_on") or []
        dep_txt = ("无（可立即开始）" if not dep
                   else "需先完成：" + "、".join(str(d) for d in dep))
        parts.append(
            "# 【你这一轮的派单】（来自项目总监）\n"
            f"- 任务标题：{dispatch.get('title', '')}\n"
            f"- 任务说明：{dispatch.get('task', '')}\n"
            f"- 期望产物：{dispatch.get('output', '（未指定）')}\n"
            f"- 依赖：{dep_txt}\n"
            "请只完成这一条派单，产出可移交的结果。"
        )
    if work_dir:
        # ★ 这一段是"别再乱找"的硬约束：写清唯一工作目录 + 相对路径 + 先动手。
        parts.append(
            "# 【你的工作目录】（这是唯一可写的位置）\n"
            f"{work_dir}\n"
            "- 所有产物一律用**相对路径**写到上面这个目录里"
            "（例如 `名单_去重.xlsx`、`dedupe_names.py`）。\n"
            "- 派单里「期望产物」写的是**文件名**；即使它带了别的目录前缀，"
            "也请只取文件名部分，写进你自己的目录。\n"
            "- 前置任务的产出内容**已经直接贴在这段提示里**，"
            "**不要**再用 list_dir 去别人的目录里翻找（那样只会白白耗光步数，"
            "最后什么都没写出来）。\n"
            "- 正确顺序：**第一步就动手写文件 / 跑脚本**，全部落盘后再用一段中文说明你做了什么。"
        )
    return "\n\n".join(p for p in parts if p)


# ---------------------------------------------------------------------------
# 派单表解析
# ---------------------------------------------------------------------------
def _extract_json_arrays(text):
    """从模型输出里尽量抠出候选 JSON 数组文本（先围栏，再全文）。"""
    if not text:
        return []
    blocks = re.findall(r"```(?:json)?\s*(.*?)```", text, re.DOTALL | re.IGNORECASE)
    cands = []
    for blob in blocks + [text]:
        s = blob.find("[")
        e = blob.rfind("]")
        if s >= 0 and e > s:
            cands.append(blob[s:e + 1])
    return cands


def _loads_lenient(raw):
    """尽最大努力把一段「像 JSON 的文本」解析成 Python 对象。

    依次尝试：直接 json → 规整中文标点/尾逗号 → ast.literal_eval（吃单引号）。
    都不行返回 None。**解析失败绝不能让整支队伍停摆**，由调用方兜底。
    """
    import json
    if not raw:
        return None
    # 1) 直接解析
    try:
        return json.loads(raw)
    except Exception:
        pass
    # 2) 把中文标点换成 ASCII、去掉尾逗号后再试
    fixed = raw.replace("，", ",").replace("：", ":")
    fixed = re.sub(r",\s*([\]}])", r"\1", fixed)
    try:
        return json.loads(fixed)
    except Exception:
        pass
    # 3) 交给 ast（能处理单引号、Python 字面量）
    try:
        return ast.literal_eval(fixed)
    except Exception:
        pass
    try:
        return ast.literal_eval(raw)
    except Exception:
        return None


def _coerce_depends(val):
    """把 depends_on 归一成 list[int]。"""
    if val is None:
        return []
    if isinstance(val, (int, float)):
        return [int(val)]
    if isinstance(val, str):
        return [int(x) for x in re.findall(r"\d+", val)]
    if isinstance(val, (list, tuple)):
        out = []
        for v in val:
            if isinstance(v, bool):
                continue
            if isinstance(v, (int, float)):
                out.append(int(v))
            elif isinstance(v, str):
                mm = re.search(r"\d+", v)
                if mm:
                    out.append(int(mm.group()))
        return out
    return []


def _normalize_item(it, idx):
    """把数组里的一项规整成统一结构；拿不到任务说明的丢弃（返回 None）。"""
    if isinstance(it, str) and it.strip():
        return {"member": "", "title": f"任务{idx + 1}", "task": it.strip(),
                "output": "", "depends_on": []}
    if not isinstance(it, dict):
        return None
    member = str(it.get("member") or it.get("agent") or it.get("role")
                 or it.get("id") or it.get("who") or "").strip()
    title = str(it.get("title") or it.get("name") or it.get("task_title") or "").strip()
    task = str(it.get("task") or it.get("desc") or it.get("detail")
               or it.get("instruction") or "").strip()
    output = str(it.get("output") or it.get("deliverable") or it.get("deliverables")
                 or it.get("artifacts") or it.get("expect") or "").strip()
    dep = _coerce_depends(it.get("depends_on") or it.get("deps")
                          or it.get("depends") or it.get("after"))
    if not task and not title:
        return None
    return {"member": member, "title": title or f"任务{idx + 1}",
            "task": task or title, "output": output, "depends_on": dep}


def _regex_items(text):
    """最后兜底：拿正则把每个 {...} 里的键值对抠出来（应对严重残缺的 JSON）。

    ⚠ v9.18.0 修复（P0-5）：**不能从任意文本里见到 {...} 就当派单**。
    实测总监没输出 JSON 时，工具调用的残片 `{"path": "a.py"}` 会被抠成一条任务，
    标题被补成"实现"，于是团队去干一件谁也没安排过的活。
    现在要求一个对象里至少命中 2 个派单键（member/title/task/output）才认。
    """
    key_set = ("member", "title", "task", "output")
    out = []
    for blob in re.findall(r"\{[^{}]*\}", text or ""):
        d = {}
        for k, v in re.findall(
                r"[\"']?(\w+)[\"']?\s*[:=]\s*(\"[^\"]*\"|'[^']*'|\[[^\]]*\]|[^,}\n]+)",
                blob):
            v = v.strip().strip(",").strip()
            if len(v) >= 2 and v[0] in "\"'" and v[-1] == v[0]:
                v = v[1:-1]
            d[k.lower()] = v
        if sum(1 for k in key_set if str(d.get(k) or "").strip()) < 2:
            continue
        out.append(d)
    return out


def parse_dispatch(text):
    """从总监的输出里解析「派单表」→ list[dict]。

    要能吃下模型常见的不规范输出：带 ```json 围栏、前后有说明文字、偶尔用单引号。
    解析不出来时返回 []（由引擎**要求总监重派一次**），**绝不硬造任务、绝不抛异常**。
    """
    text = strip_team_noise(text)
    if not text:
        return []
    for raw in _extract_json_arrays(text):
        data = _loads_lenient(raw)
        if isinstance(data, dict):                  # 有的模型会包一层 {"tasks": [...]}
            for key in ("tasks", "dispatch", "items", "list", "plan"):
                if isinstance(data.get(key), list):
                    data = data[key]
                    break
        if not isinstance(data, list):
            continue
        out = []
        for i, it in enumerate(data):
            norm = _normalize_item(it, i)
            if norm:
                out.append(norm)
        if out:
            return out
    # 兜底：正则抠对象（已收紧：至少 2 个派单键）
    out = []
    for i, d in enumerate(_regex_items(text)):
        norm = _normalize_item(d, i)
        if norm:
            out.append(norm)
    return out


# ---------------------------------------------------------------------------
# 依赖分批（把派单表排成「一批一批」可并行执行）
# ---------------------------------------------------------------------------
def topo_batches(tasks):
    """按 depends_on 把任务排成若干批：同批内的任务可以并行。

    返回 list[list[int]]，每个子列表是一批任务的**下标**；批与批之间必须按序执行。
    依赖指向不存在的下标会被忽略；出现环时把剩下的任务强行塞进下一批（不让流程卡死）。
    """
    n = len(tasks or [])
    if n == 0:
        return []
    batches = []
    done = set()
    remaining = set(range(n))
    guard = 0
    while remaining and guard <= n + 1:
        guard += 1
        ready = []
        for i in sorted(remaining):
            deps = [d for d in (tasks[i].get("depends_on") or []) if 0 <= d < n]
            if all(d in done for d in deps):
                ready.append(i)
        if not ready:                               # 有环 → 取出最小的那个强行推进
            ready = [min(remaining)]
        batches.append(ready)
        done.update(ready)
        remaining -= set(ready)
    return batches
