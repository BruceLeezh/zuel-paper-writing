"""论文文本检测：22 条确定性规则。

设计思路：先把文本切成行、段、句三级，再让每条规则只做一件事。
规则表驱动，新增规则只需写一个函数并登记，不必改动主流程。
所有判定基于字面特征（词表、正则、结构信号），不调用模型——模型自己检查自己等于没检查。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from .model import Level, Problem

# ── 词表 ────────────────────────────────────────────────────

# 绝对化表述。学术写作里出现即提示，因为这类词用修辞代替了证据。
ABSOLUTE = [
    (r"毫无疑问|毋庸置疑|无可置疑", "改成「有理由认为」，并把理由写出来"),
    (r"众所周知", "改成「学界普遍认为」并给出处"),
    (r"不言而喻", "论文里没有不言而喻，需要论证"),
    (r"显而易见", "改成「由上文可以看出」"),
    (r"无疑(?![义])", "「无疑」用结论代替了推理，删掉并补理由"),
    (r"必然", "改成「很可能」或「在多大程度上」"),
    (r"极其|极为", "程度副词不能当证据，给出可比的量"),
    (r"完全(?:是|证明了|说明了)", "加限定，避免绝对化"),
    (r"无可辩驳", "删掉这类修辞"),
]

# 强断言词（比 ABSOLUTE 窄，只用于"缺论据"判断）
STRONG = re.compile(r"(显然|极其|无疑|毋庸置疑|不言自明|必然地|确凿无疑)")

# 论据信号。注意不要收录单字「据」——「数据」里的「据」会造成大面积漏报。
EVIDENCE = re.compile(
    r"(不仅|而且|第一|第二|首先|其次|一方面|另一方面|例如|比如|"
    r"根据|依据|据统计|数据显示|研究表明)"
)

# 论证连接词
LINK = {
    "因此", "所以", "故而", "由此可见", "正因为", "之所以", "因为", "由于",
    "这意味着", "这说明", "这表明", "可见", "然而", "但是", "不过", "尽管如此",
    "虽然", "但", "却", "不仅如此", "更重要的是", "进一步", "更为关键的是",
    "而且", "在此基础上", "由此出发", "相比之下", "与之不同", "相反", "反之",
    "另一方面", "换言之", "也就是说", "具体而言", "这一点可以从", "从中可以看出",
    "值得注意的是",
}

# 弱衔接：不是连接词，但能说明本句与上文有承接。中文常靠指代与引用衔接。
WEAK = re.compile(
    r"（\s*\d{4}\s*）|\(\s*\d{4}\s*\)|\[\d+\]"
    r"|本文|本研究|笔者|这一|上述|前者|后者|分歧在于|理由在于|与之相对"
)

# 并列段开头
PARALLEL_START = [r"^同时[,，]", r"^此外[,，]", r"^另外[,，]", r"^还有[,，]",
                  r"^也[,，]", r"^并且[,，]", r"^以及"]

# 「（张三，2020）」「（Smith, 2020）」与「[3]」这类已经标明出处的写法
_AUTHOR_YEAR = re.compile(r"[（(][^（）()]{1,30}[，,]\s*(?:19|20)\d{2}\s*[）)]|\[\d+\]")

# 引号内的文字是引文，不是作者自己的措辞
_QUOTED = re.compile(r"「[^」]*」|『[^』]*』|“[^”]*”|\"[^\"]*\"")

# 结果段的数据信号：满足一半以上时，L-01 不再按"只下判断"报
_DATA_SIGNAL = re.compile(
    r"表\s*\d|图\s*\d|列\s*[（(]|系数|样本|观测|变量|回归|显著|百分|个公司|个年度|数据"
)

# 段落终止：这是新的标题行，不是引言正文
_SECTION_STOP = re.compile(
    r"^(#{1,6}\s|第[一二三四五六七八九十]+[章节]|[一二三四五六七八九十]+、|\d+(?:\.\d+)*[\s.、])"
)

# 口语化
CASUAL = [
    (r"其实", "改成「实际上」或直接陈述"),
    (r"说白了", "过于口语，删或改写"),
    (r"大家都知道", "改成「学界普遍认为」并给出处"),
    (r"很明显", "用具体论证替代"),
    (r"当然了", "改成「诚然」或「固然」"),
    (r"这个问题很有意思", "直接说明问题的学术意义"),
    (r"笔者觉得", "改成「本文认为」"),
    (r"总之就是", "改成「概言之」"),
]

# GB/T 7714-2015 文献类型标识
DOC_TYPES = r"\[(?:M|J|D|C|N|P|R|S|Z|A|G|EB)(?:/OL)?\]"

# 同一概念的不同写法（本校场景常用）
SYNONYMS = [
    ("数字化转型", "数字化改造", "数字化升级"),
    ("融资成本", "融资代价"),
    ("企业创新", "公司创新"),
    ("显著性", "显著度"),
    ("内生性", "内生问题"),
    ("稳健性", "鲁棒性"),
    ("营商环境", "商业环境"),
    ("ESG 评级", "ESG 评分", "ESG 等级"),
]

# 结构要素：必须作为标题或标签出现，不能只凭正文出现这几个字。
# 末尾必须是冒号或行尾——否则「文献综述」会被误当成「参考文献」，
# 真缺参考文献的论文就会被判通过（假阴性）。
# 前置：Markdown 井号，或 Word 里手打的「六、」「1.」序号；两者可同时出现（「# 六、参考文献」）。
_PREFIX = r"(?:(?:#{1,6}|[一二三四五六七八九十]+\s*[、.．]|\d+\s*[、.．])\s*)*"
_OPEN = r"[【\[［]?\s*"
_HEAD = r"(?m)^\s{0,6}" + _PREFIX + _OPEN
_TAIL = r"\s*[】\]］]?\s*(?:[:：]|$)"

STRUCT = [
    ("摘要", _HEAD + r"(?i:abstract|(?:内容|中文)?摘\s*要)" + _TAIL),
    ("关键词", _HEAD + r"(?i:key\s*words?|(?:中文)?关\s*键\s*词)" + _TAIL),
    ("参考文献", _HEAD + r"(?i:references?|bibliography|(?:参考)?文\s*献"
                     r"(?:\s*[（(][^）)]*[）)])?)" + _TAIL),
]

# 参考文献区起始：与 STRUCT 共用同一份模式，避免「标题认了、F-05 不查」。
_REF_HEAD = re.compile(STRUCT[2][1])
# 参考文献区之后遇到这些行，说明已经进入下一节。
_NEXT_SECTION = re.compile(
    r"^(?:#{1,6}\s+\S|第[一二三四五六七八九十]+[章节]|"
    r"[一二三四五六七八九十]+\s*[、.．]|附\s*录)")

# 论点句：主语 + 论断动词的组合，不用固定短语表。
# 固定词表只认「本文认为」这类写法，「本研究提出」「文章主张」「核心命题是」全漏。
_THESIS_RE = re.compile(
    r"(?:本文|本研究|文章|笔者|本项研究|论文)"
    r"[^。；;\n]{0,12}?"
    r"(?:论证|认为|主张|提出|指出|检验|考察|探讨|试图|旨在|拟|的核心命题|的核心观点|的论点)"
)


def has_thesis(text: str) -> bool:
    """引言里有没有明确的论点句。"""
    return bool(_THESIS_RE.search(text))


# ── 文本切分 ────────────────────────────────────────────────

@dataclass
class Doc:
    text: str
    lines: list[str]
    paras: list[list[str]]                 # 段 = 若干行
    sents: list[tuple[int, str]]           # (行号, 句子)

    @classmethod
    def of(cls, text: str) -> "Doc":
        lines = text.split("\n")
        paras: list[list[str]] = []
        cur: list[str] = []
        for ln in lines:
            s = ln.strip()
            if not s:
                if cur:
                    paras.append(cur)
                    cur = []
                continue
            cur.append(s)
        if cur:
            paras.append(cur)

        sents: list[tuple[int, str]] = []
        for i, ln in enumerate(lines, 1):
            for piece in re.split(r"[。！？]", ln):
                p = piece.strip()
                if len(p) > 5:
                    sents.append((i, p))
        return cls(text=text, lines=lines, paras=paras, sents=sents)


def _is_heading(s: str) -> bool:
    return bool(re.match(r"^(#|>|\[|第[一二三四五六七八九十]+[章节]|\d+[\.、])", s))


# ── 规则实现 ────────────────────────────────────────────────

def r1_01(d: Doc) -> list[Problem]:
    out = []
    for i, ln in enumerate(d.lines, 1):
        for _ in re.finditer(r"(有学者|有研究者|有人|据研究)(指出|认为|发现|表明|提出)", ln):
            # 同句已给出（作者，年份）或 [n] 的，作者是谁说得清，只是措辞啰嗦，
            # 不按"模糊引用"判必改——否则学生照提示会删掉本来正确的引注。
            if _AUTHOR_YEAR.search(ln):
                continue
            out.append(Problem("R1-01", Level.BLOCK,
                               "模糊引用：写了「有学者指出」但没说是谁",
                               i, ln.strip(), "补作者与出处，或删掉这个引用"))
    return out


def r1_02(d: Doc) -> list[Problem]:
    return [Problem("R1-02", Level.NOTE, "这里有占位符，定稿前要补上",
                    i, ln.strip(), "按占位提示补具体信息")
            for i, ln in enumerate(d.lines, 1)
            # `panel` 生成的骨架用「[待填：…]」，与手写的「[待补充」都要认，
            # 否则自家造出来的占位符反而不报。
            if re.search(r"[\[【［]\s*待\s*(?:补充|查证|填)", ln)]


def r1_03(d: Doc) -> list[Problem]:
    out, now = [], date.today().year
    for i, ln in enumerate(d.lines, 1):
        for y in re.findall(r"[（(]\s*((?:19|20)\d{2})\s*[)）]", ln):
            # 「（2035）年远景目标」是政策目标年份，不是引用年份；
            # 引用年份写作（作者，年）或（2099），年份后不接「年」。
            if re.search(rf"[（(]\s*{y}\s*[)）]\s*年", ln):
                continue
            if int(y) > now:
                out.append(Problem("R1-03", Level.BLOCK,
                                   f"引用了 {y} 年，晚于当前年份 {now}",
                                   i, ln.strip(), "核对年份，多半是编的"))
    return out


def r3_01(d: Doc) -> list[Problem]:
    out = []
    for i, ln in enumerate(d.lines, 1):
        for pat, tip in ABSOLUTE:
            m = re.search(pat, ln)
            if m:
                out.append(Problem("R3-01", Level.WARN,
                                   f"过度断言：用了「{m.group()}」",
                                   i, ln.strip(), tip))
    return out


def t_01(d: Doc) -> list[Problem]:
    out = []
    for group in SYNONYMS:
        hit = {}
        for i, ln in enumerate(d.lines, 1):
            for w in group:
                if w in ln:
                    hit.setdefault(w, []).append(i)
        if len(hit) > 1:
            where = "；".join(f"「{w}」第{hit[w][0]}行" for w in hit)
            out.append(Problem("T-01", Level.WARN,
                               "同一概念用了不同写法", 0, where,
                               f"全文统一为「{group[0]}」"))
    return out


def f_01(d: Doc) -> list[Problem]:
    return [Problem("F-01", Level.BLOCK, "空脚注：编号后面没内容",
                    i, ln.strip(), "补内容或删掉编号")
            for i, ln in enumerate(d.lines, 1)
            if re.fullmatch(r"[①②③④⑤⑥⑦⑧⑨⑩]", ln.strip())]


def f_02(d: Doc) -> list[Problem]:
    out = []
    for i, ln in enumerate(d.lines, 1):
        if re.match(r"^\[\d+\]\s+\S", ln) and not re.search(DOC_TYPES, ln):
            out.append(Problem("F-02", Level.WARN,
                               "参考文献条目缺文献类型标识",
                               i, ln.strip()[:80],
                               "补 [M]专著 [J]期刊 [D]学位论文 [C]会议 "
                               "[N]报纸 [R]报告 [S]标准 [Z]法规 [EB/OL]网络"))
    return out


def f_03(d: Doc) -> list[Problem]:
    out = []
    for i, ln in enumerate(d.lines, 1):
        s = ln.strip()
        if re.match(r"^(\[\d+\]|[①②③④⑤⑥⑦⑧⑨⑩])", s):
            continue
        bare = re.sub(r"[（(][^）)]*[）)]", "", s)
        if re.search(r"[\u4e00-\u9fff][,.;:?!]", bare):
            out.append(Problem("F-03", Level.WARN,
                               "中文里用了英文标点", i, s,
                               "改成全角：，。；：？！"))
    return out


def f_04(d: Doc) -> list[Problem]:
    cn = any(re.match(r"^第[一二三四五六七八九十]+[章节]", ln.strip()) for ln in d.lines)
    num = any(re.match(r"^\d+(\.\d+)*\s+\S", ln.strip()) for ln in d.lines)
    if cn and num:
        return [Problem("F-04", Level.WARN, "标题编号两套体系混用", 0, "",
                        "全文统一：要么「第一章」，要么「1./1.1」")]
    return []


def _ref_numbers(d: Doc) -> tuple[list[tuple[int, int]], int]:
    """扫参考文献区，返回 ([(序号, 行号)], 标题所在行号)。没找到标题返回 ([], 0)。"""
    out = []
    started = False
    start_line = 0
    for i, ln in enumerate(d.lines, 1):
        s = ln.strip()
        if not started and _REF_HEAD.match(s):
            started = True
            start_line = i
            continue
        if started and _NEXT_SECTION.match(s) and not re.match(r"^\[\d+\]", s):
            break                       # 进入下一节，停
        for m in re.finditer(r"^\s*\[(\d+)\]", s) if started else []:
            out.append((int(m.group(1)), i))
    return out, start_line


def f_05(d: Doc) -> list[Problem]:
    """参考文献编号链：缺号、重号、序号不连续，以及正文与文末的双向对应。

    正文标 [n] 而文末没有第 n 条（或反过来：文末有条目正文从未引用），
    都是最容易被导师一眼看出的硬伤。反向检查只在正文用了 [n] 编号时
    生效——作者-年份体例不查。只在能识别出参考文献区时才检查。
    """
    refs, ref_line = _ref_numbers(d)
    if not refs:
        return []
    nums = [n for n, _ in refs]
    out = []
    dup = sorted({n for n in nums if nums.count(n) > 1})
    if dup:
        out.append(Problem("F-05", Level.WARN,
                           f"参考文献编号重复：{dup}", 0, "",
                           "同一条文献只保留一个编号，正文引用处同步改"))
    uniq = sorted(set(nums))
    expect = list(range(1, len(uniq) + 1))
    if uniq != expect:
        missing = [n for n in expect if n not in uniq][:20]
        extra = [n for n in uniq if n > len(uniq)][:20]
        detail = []
        if missing:
            detail.append(f"缺号 {missing}")
        if extra:
            detail.append(f"超出条目数的编号 {extra}")
        out.append(Problem("F-05", Level.WARN,
                           "参考文献编号不连续：" + "；".join(detail), 0, "",
                           "编号从 1 连续排到 n，不跳号、不从 0 起"))

    # 正文标了 [n]，文末却没有第 n 条——导师一眼能看出的硬伤。
    cited: set[int] = set()
    for idx, ln in enumerate(d.lines, 1):
        s = ln.strip()
        # 参考文献区里的条目行本身以 [n] 开头，不能当成正文引用。
        if ref_line and idx >= ref_line and re.match(r"^\[\d+\]", s):
            continue
        for m in re.finditer(r"\[(\d{1,2})(?:\s*[-–—~,，、]\s*(\d{1,2}))?\]", s):
            a = int(m.group(1))
            b = int(m.group(2)) if m.group(2) else a
            if 1 <= a <= b <= 99:
                cited.update(range(a, b + 1))
    ghost = sorted(n for n in cited if n not in uniq)
    if ghost:
        out.append(Problem("F-05", Level.WARN,
                           f"正文引用了文末没有的编号：{ghost[:10]}", 0, "",
                           "补上对应条目，或改正文标注；编号必须与文末条目一一对应"))

    # 反方向：文末有条目、正文从未引用。只在正文确实用了 [n] 编号时才查——
    # 作者-年份体例的稿子正文没有 [n]，编号对应关系本就不成立，查了全是误报。
    if cited:
        dead = sorted(n for n in uniq if n not in cited)
        if dead:
            out.append(Problem("F-05", Level.WARN,
                               f"文末有条目但正文没引过：{dead[:10]}", 0, "",
                               "顺序编码制要求一一对应：没引的删掉，或在正文补上引用"))
    return out


def s_01(d: Doc) -> list[Problem]:
    out = []
    for i, ln in enumerate(d.lines, 1):
        for pat, tip in CASUAL:
            m = re.search(pat, ln)
            if m:
                out.append(Problem("S-01", Level.WARN,
                                   f"语体偏口语：用了「{m.group()}」",
                                   i, ln.strip(), tip))
    return out


def s_02(d: Doc) -> list[Problem]:
    """自称是否统一。「本研究」与「本文」同义，不算混用；「笔者」是允许的第二选择。"""
    found = {}
    for i, ln in enumerate(d.lines, 1):
        # 引用块与引号里的「我们」是原作者的话，不是本文自称
        if ln.strip().startswith(">"):
            continue
        ln = _QUOTED.sub("", ln)
        for w in ("本文", "笔者", "我们", "我"):
            if w == "我" and not re.search(r"我(?:认为|以为|将|的|在本|试图|主张|发现)", ln):
                continue
            if w in ln:
                found.setdefault(w, []).append(i)
    if len(found) > 1:
        return [Problem("S-02", Level.WARN,
                        "自称不统一：" + "、".join(f"「{k}」" for k in found),
                        0, "", "全文统一用「本文」（法学可用「笔者」），不要和「我们」混用")]
    return []


def l_01(d: Doc) -> list[Problem]:
    """连续 5 句既无连接词、也无承接标记，视为堆砌断言。

    按句数判断，不按行数——Markdown 与 Word 导出的一整段常只占一行。
    """
    out = []
    for para in d.paras:
        body = [p for p in para if not _is_heading(p)]
        if not body:
            continue
        sents = []
        for s in body:
            for piece in re.split(r"[。！？]", s):
                if len(piece.strip()) > 5:
                    sents.append(piece.strip())
        if len(sents) < 5:
            continue
        # 结果段本来就是一串数据陈述，不是"只下判断"：数据信号过半即跳过
        if sum(1 for s in sents if _DATA_SIGNAL.search(s)) * 2 >= len(sents):
            continue
        streak = best = 0          # 不叫 run：模块里 run() 是主入口，同名易误读
        for s in sents:
            ok = any(c in s for c in LINK) or bool(WEAK.search(s))
            streak = 0 if ok else streak + 1
            best = max(best, streak)
        if best >= 5:
            out.append(Problem("L-01", Level.WARN,
                               f"有 {best} 句连续只下判断、没有推进",
                               0, body[0][:60],
                               "补「因为/这意味着/之所以」，或把观察改成论证"))
    return out


def l_02(d: Doc) -> list[Problem]:
    out, streak = [], 0
    for para in d.paras:
        if para and any(re.match(p, para[0]) for p in PARALLEL_START):
            streak += 1
        else:
            if streak >= 3:
                out.append(Problem("L-02", Level.WARN,
                                   f"连续 {streak} 段以并列词开头", 0, "",
                                   "改成递进：提观察 → 析原因 → 得推论"))
            streak = 0
    if streak >= 3:
        out.append(Problem("L-02", Level.WARN, f"连续 {streak} 段以并列词开头", 0, "",
                           "改成递进：提观察 → 析原因 → 得推论"))
    return out


def l_03(d: Doc) -> list[Problem]:
    out = []
    for i, ln in enumerate(d.lines, 1):
        s = ln.strip()
        if not s or not re.search(r"[\u201c\u201d\u300c\u300d\u300e\u300f][\s。]*$", s):
            continue
        nxt = ""
        for j in range(i, min(i + 3, len(d.lines))):
            if d.lines[j].strip():
                nxt = d.lines[j].strip()
                break
        if nxt and re.match(r"^(此外|同时|另外|与此同时|除此之外|值得一提的是"
                            r"|[\u201c\u300c])", nxt):
            out.append(Problem("L-03", Level.WARN,
                               "引文后面没解释，直接换了话题",
                               i, s, "引文后补一句：这段话说明了什么、怎么支撑你的论点"))
    return out


def l_04(d: Doc) -> list[Problem]:
    out = []
    heads = [i for i, ln in enumerate(d.lines, 1)
             if re.match(r"^(第[一二三四五六七八九十]+[章节]|#{1,3}\s+\d)", ln.strip())]
    for idx, h in enumerate(heads):
        if idx == 0:
            continue
        first = ""
        for j in range(h + 1, min(h + 6, len(d.lines) + 1)):
            s = d.lines[j - 1].strip()
            if s and not re.match(r"^(第|#|\d+\.)", s):
                first = s
                break
        if not first:
            continue
        if re.match(r"^[A-Z\u4e00-\u9fff]+（.*?）是|^在\d{4}年|^关于.{2,6}的研究", first):
            out.append(Problem("L-04", Level.NOTE,
                               "章节一开头就铺背景，没接上一章", h, first[:60],
                               "从上一章留下的问题切入，别从定义或背景讲起"))
    return out


def l_05(d: Doc) -> list[Problem]:
    out = []
    for i, ln in enumerate(d.lines, 1):
        m = re.search(r"(由于|因为|鉴于)(.{4,30})(并非|不是|并不|并未|未必是|绝非|所认为的|所谓的)", ln)
        if not m:
            continue
        win = ln[max(0, m.start() - 20):m.end() + 40]
        if not re.search(r"[①②③④⑤⑥⑦⑧⑨⑩]|\[\d+\]|（.*?\d{4}.*?）|\(.*?\d{4}.*?\)", win):
            out.append(Problem("L-05", Level.WARN,
                               "因果句的前提是个判断，但没论证",
                               i, ln.strip(), "先证这个前提成立，再用它推结论"))
    return out


def l_06(d: Doc) -> list[Problem]:
    out = []
    narr = re.compile(r"(?:发生|出现|建立|成立|颁布|发动|开始|结束|到达|返回|出版|发表|"
                      r"签署|宣布|任命|组建|迁移|抵达|爆发|写道|记载|描述|讲述|描绘|"
                      r"记录|引述|转述|复述|设置|进入|离开)")
    anal = re.compile(r"(?:表明|说明|揭示|意味着|体现|反映|证明|论证|暗示|"
                      r"可见|因此|由此|换言之|之所以|正因为|由此可见|呈现出|"
                      r"构成了|指向了|折射出|回应了|挑战了|颠覆了)")
    for i, ln in enumerate(d.lines, 1):
        for s in re.split(r"[。！？]", ln.strip()):
            # 阈值 80 字：中文学术长句多在 60–90 字，100 字会漏掉绝大多数
            if len(s) < 80:
                continue
            if len(narr.findall(s)) >= 4 and not anal.findall(s):
                out.append(Problem("L-06", Level.NOTE,
                                   "长句全是叙述，没有分析",
                                   i, s[:60], "叙述压缩，后面补一句「这意味着什么」"))
    return out


def l_07(d: Doc) -> list[Problem]:
    out = []
    for i, ln in enumerate(d.lines, 1):
        s = ln.strip()
        for head in ("由此可见", "综上所述", "综上", "总之", "概言之", "可见"):
            if head in s and s.count("、") >= 4:
                out.append(Problem("L-07", Level.NOTE,
                                   f"总结句堆了 {s.count('、') + 1} 个并列概念",
                                   i, s[:60], "检查这些概念前文是否都论证过，没论证的删掉"))
                break
    return out


def l_08(d: Doc) -> list[Problem]:
    out = []
    for i, ln in enumerate(d.lines, 1):
        s = ln.strip()
        for m in STRONG.finditer(s):
            w = m.group()
            win = s[max(0, m.start() - 60):m.end() + 60]
            if len(EVIDENCE.findall(win)) < 2:
                out.append(Problem("L-08", Level.WARN,
                                   f"「{w}」前后没有足够论据",
                                   i, win, "删掉强度词，或补出推理过程"))
    return out


def st_01(d: Doc) -> list[Problem]:
    out = []
    for name, pat in STRUCT:
        if not re.search(pat, d.text):
            out.append(Problem("ST-01", Level.WARN if name == "参考文献" else Level.NOTE,
                               f"没找到「{name}」", 0, "", f"补上{name}部分"))
    return out


def st_02(d: Doc) -> list[Problem]:
    body, on = [], False
    for ln in d.lines:
        s = ln.strip()
        if not on:
            # 只认标题或短标签，避免正文里「如引言所述」把后半篇吞进来
            if re.search(r"引言|绪论|导论|Introduction", s, re.I) and (
                    _is_heading(s) or len(s) <= 12):
                on = True
            continue
        # 遇到下一个标题（含 Markdown 的 #/##）就收尾；否则整篇都会被算作引言，
        # 结论里的「本文认为」也会被当成引言论点句（假阴性）。
        if _SECTION_STOP.match(s):
            break
        body.append(ln)
    if body and not has_thesis("\n".join(body)):
        return [Problem("ST-02", Level.WARN, "引言里没看到明确的论点句", 0, "",
                        "引言末尾写一句「本文以…为样本，检验…」或「本文认为…」")]
    return []


RULES = [r1_01, r1_02, r1_03, r3_01, t_01, f_01, f_02, f_03, f_04, f_05,
         s_01, s_02, l_01, l_02, l_03, l_04, l_05, l_06, l_07, l_08,
         st_01, st_02]

ORDER = {Level.BLOCK: 0, Level.WARN: 1, Level.NOTE: 2}


def run(text: str) -> list[Problem]:
    """跑全部 22 条规则。单条崩溃不影响其余，并在结果里明确告知。"""
    doc = Doc.of(text)
    out: list[Problem] = []
    broken: list[str] = []
    for fn in RULES:
        try:
            out.extend(fn(doc))
        except Exception as exc:
            broken.append(f"{fn.__name__}({type(exc).__name__})")
    if broken:
        out.append(Problem("SYS-01", Level.NOTE,
                           f"{len(broken)} 条规则没跑成，本次结果不完整",
                           0, "; ".join(broken), "把文本分段重跑，其余结果仍有效"))
    out.sort(key=lambda p: (ORDER.get(p.level, 9), p.line))
    return out
