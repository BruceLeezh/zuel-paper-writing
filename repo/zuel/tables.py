"""导出文件解析：中文库题录与 CSMAR 面板数据。

这两类数据都在 C 级源里（需机构订阅），本模块只读用户自己导出的文件，不联网。
解析不出的内容原样保留并标注，绝不猜测字段含义。
"""
from __future__ import annotations

import csv
import io
import os
import re
from datetime import date

from .model import Item

# ── 通用读取 ────────────────────────────────────────────────

# 前三个按顺序试。latin-1 能解开任意字节，放最后当兜底，
# 但必须过 _printable_ratio 检查，否则乱码会被当正常数据往下算。
_CODECS = ("utf-8-sig", "gb18030", "utf-8", "latin-1")
_PRINTABLE_MIN = 0.95           # 可打印字符占比下限


def _text_score(text: str) -> float:
    """文本"像不像正常文本"：可打印字符（含中文、常见标点）占比。"""
    if not text:
        return 1.0
    ok = sum(1 for ch in text
             if ch.isprintable() or ch in "\r\n\t" or ord(ch) > 0x2E80)
    return ok / len(text)


def _text(path: str) -> tuple[str, str]:
    """读文本，返回 (内容, 实际编码)。

    兜底编码（latin-1）解出的文本若可打印占比过低，判定为乱码并报错，
    免得把乱码当数据算出一份看起来正常的画像。
    """
    for c in _CODECS:
        try:
            with io.open(path, "r", encoding=c) as f:
                text = f.read()
        except UnicodeDecodeError:
            continue
        if "\x00" in text or _text_score(text) < _PRINTABLE_MIN:
            raise ValueError(
                f"编码识别失败：用 {c} 解出的内容中可打印字符不足 "
                f"{int(_PRINTABLE_MIN * 100)}%（或含空字节），"
                f"很可能是二进制或损坏的文件。请在 WPS 里另存为 UTF-8 或 GBK 后重试"
            )
        return text, c
    raise ValueError("无法识别编码，请在 WPS 里另存为 UTF-8 或 GBK")


# ── 题录：知网 / 万方 / 维普 导出 ──────────────────────────

def sniff_kind(text: str) -> str:
    """判断导出格式：endnote / refworks / gbt。"""
    head = text[:2000]
    if re.search(r"(?m)^%[ATJDNVPRX]\s", head):
        return "endnote"
    if re.search(r"(?m)^(RT|A1|T1|JO|YR)\s", head):
        return "refworks"
    if re.search(r"\[(?:M|J|D|C|N|R|S|Z|A|G|EB)(?:/OL)?\]", head):
        return "gbt"
    return "unknown"


_EN_KIND = {
    "Journal Article": "期刊论文", "Thesis": "学位论文", "Book": "专著",
    "Book Section": "析出文献", "Conference Paper": "会议论文",
    "Conference Proceedings": "会议论文", "Report": "报告",
    "Web Page": "电子资源", "Newspaper Article": "报纸", "Patent": "专利",
}


def _blocks(text: str) -> list[str]:
    return [b for b in re.split(r"\n\s*\n", text.strip()) if b.strip()]


def _from_endnote(text: str, src: str) -> list[Item]:
    out = []
    for blk in _blocks(text):
        f: dict[str, list[str]] = {}
        for ln in blk.splitlines():
            m = re.match(r"^%([A-Z0-9@])\s+(.*)$", ln.strip())
            if m:
                f.setdefault(m.group(1), []).append(m.group(2).strip())
        if not f.get("T"):
            continue
        out.append(Item(src=src, field="literature", title=f["T"][0],
                        people=f.get("A", []),
                        when=(f.get("D") or [""])[0],
                        ident=(f.get("R") or [""])[0],
                        note=(f.get("X") or [""])[0][:400],
                        raw={"journal": (f.get("J") or [""])[0],
                             "volume": (f.get("V") or [""])[0],
                             "issue": (f.get("N") or [""])[0],
                             "pages": (f.get("P") or [""])[0],
                             "kind": (f.get("0") or [""])[0],
                             "kind_zh": _EN_KIND.get((f.get("0") or [""])[0], "")}))
    return out


def _from_refworks(text: str, src: str) -> list[Item]:
    out = []
    for blk in _blocks(text):
        f: dict[str, list[str]] = {}
        for ln in blk.splitlines():
            m = re.match(r"^([A-Z][A-Z0-9])\s+(.*)$", ln.strip())
            if m:
                f.setdefault(m.group(1), []).append(m.group(2).strip())
        if not f.get("T1"):
            continue
        out.append(Item(src=src, field="literature", title=f["T1"][0],
                        people=f.get("A1", []),
                        when=(f.get("YR") or [""])[0],
                        ident=(f.get("DO") or [""])[0],
                        note=(f.get("AB") or [""])[0][:400],
                        raw={"journal": (f.get("JO") or [""])[0],
                             "volume": (f.get("VO") or [""])[0],
                             "issue": (f.get("IS") or [""])[0],
                             "pages": (f.get("SP") or [""])[0],
                             "kind": (f.get("RT") or [""])[0],
                             "kind_zh": _EN_KIND.get((f.get("RT") or [""])[0], "")}))
    return out


_DOI_IN = re.compile(r"(?<![\w.])10\.\d{4,9}/[^\s，；。]+")
_DOI_TAIL = re.compile(r"\s*(?:DOI\s*[:：]?\s*)?10\.\d{4,9}/[^\s，；。]+.*$", re.I)


def _take_doi(text: str) -> str:
    """从著录尾部取 DOI（GB/T 7714 允许把 DOI 附在最后）。取不到返回空串。"""
    m = _DOI_IN.search(text or "")
    return m.group(0).rstrip(".;,，。；") if m else ""


_GBT = re.compile(r"^(?P<authors>[^\.]+?)\.\s*(?P<title>.+?)"
                  r"\[(?P<kind>[A-Z]{1,2}(?:/OL)?)\]\s*\.?\s*(?P<rest>.+)$")

KIND_ZH = {"J": "期刊论文", "M": "专著", "D": "学位论文", "C": "会议论文",
           "N": "报纸", "R": "报告", "S": "标准", "Z": "其他", "EB": "电子资源",
           "A": "析出文献", "G": "汇编", "P": "专利"}

# 西文姓名「姓, 名缩写」：Smith, J. / van der Berg, A. B.
_EN_NAME = re.compile(r"^[A-Z][A-Za-z\-'’]+(?:\s+[a-z][A-Za-z\-'’]+)*\s*,\s*[A-Z]\.")


def split_authors(s: str) -> list[str]:
    """切作者。中文按全角/半角逗号、顿号切；西文先把「姓, 名缩写」拼回去。

    直接按逗号切会把 Smith, J. 切成两个作者，往后的著录全错位。
    """
    parts = [p.strip() for p in re.split(r"[,，、;；]", s) if p.strip()]
    out: list[str] = []
    i = 0
    while i < len(parts):
        cur = parts[i]
        # 「姓」+「名缩写」被逗号切开时，下一位是单个大写字母加点
        if i + 1 < len(parts) and re.fullmatch(r"[A-Z]\.(?:\s*[A-Z]\.)*", parts[i + 1]):
            cur = f"{cur}, {parts[i + 1]}"
            i += 1
        out.append(cur)
        i += 1
    return out


def _from_gbt(text: str, src: str) -> list[Item]:
    out, bad = [], []
    for ln in text.splitlines():
        s = re.sub(r"^\[\d+\]\s*", "", ln.strip())
        if not s:
            continue
        m = _GBT.match(s)
        if not m:
            bad.append(s)
            continue
        people = split_authors(m.group("authors"))
        rest = m.group("rest")
        doi = _take_doi(rest)
        rest = _DOI_TAIL.sub("", rest).strip().rstrip(".。;；,，")
        year = re.search(r"(19|20)\d{2}", rest)
        kind = m.group("kind")[:2] if m.group("kind").startswith("EB") else m.group("kind")[:1]
        # 起止页单独立一个键：completeness 的缺页检查与 verify 的页码比对
        # 都读 raw["pages"]，GB/T 条目不提取的话这两处永远查不到。
        pm = re.search(r"[:：]\s*(\d+\s*[-–—~]\s*\d+)\s*$", rest)
        pages = pm.group(1).replace(" ", "") if pm else ""
        out.append(Item(src=src, field="literature", title=m.group("title").strip(),
                        people=people, when=year.group(0) if year else "",
                        ident=doi,
                        raw={"kind": m.group("kind"),
                             "kind_zh": KIND_ZH.get(kind, "未分类"),
                             "pages": pages,
                             "rest": rest}))
    if bad:
        out.append(Item(src=src, field="literature", title="[未能解析的条目]",
                        raw={"unparsed": bad}))
    return out


def read_export(path: str, kind: str = "auto", src: str = "cnki") -> tuple[list[Item], str]:
    """读题录导出文件，返回 (条目列表, 识别到的格式)。"""
    text, _ = _text(path)
    fmt = sniff_kind(text) if kind == "auto" else kind
    if fmt == "endnote":
        return _from_endnote(text, src), fmt
    if fmt == "refworks":
        return _from_refworks(text, src), fmt
    if fmt == "gbt":
        return _from_gbt(text, src), fmt
    raise ValueError(
        f"认不出这种导出格式：{fmt}。本工具读三种文本导出——知网/万方的 EndNote"
        f"（%0 %A %T 开头）、RefWorks（RT A1 T1 开头）、GB/T 7714 著录式；"
        f"Web of Science 的 FN/PT 标签格式与 .docx/.pdf 不支持，"
        f"请在原库里重新导出为上述三种之一")


# ── 面板：CSMAR 导出 ────────────────────────────────────────

FIELD_ZH = {
    "Stkcd": "股票代码", "Symbol": "证券代码", "ShortName": "股票简称",
    "Accper": "会计期间", "Enddt": "截止日期", "Statco": "统计截止日期",
    "Typrep": "报表类型（A=合并，B=母公司）",
    "Indcd": "行业代码", "Indnme": "行业名称",
    "Listdt": "上市日期", "Ipodate": "上市日期",
    "TotalAssets": "资产总计", "TotalLiab": "负债合计",
    "Revenue": "营业收入", "OperRev": "营业收入", "NetProfit": "净利润",
    "ROA": "总资产收益率", "ROE": "净资产收益率", "Lev": "资产负债率",
    "Yretwd": "年个股回报率（含现金红利再投资）",
    "Dretwd": "日个股回报率（含现金红利再投资）",
    "Trddt": "交易日期", "Clsprc": "收盘价", "Opnprc": "开盘价",
    "Dnshrtrd": "成交量", "Dnvaltrd": "成交额",
    "Top1": "第一大股东持股比例", "Dual": "两职合一", "Indep": "独立董事比例",
}

_HEAD_HINTS = {"stkcd", "symbol", "accper", "enddt", "statco", "trddt",
               "证券代码", "股票代码", "会计期间"}
_TIME_COLS = {"accper", "enddt", "statco", "trddt", "listdt", "ipodate", "year", "date"}
_ENTITY_COLS = {"stkcd", "symbol", "证券代码", "股票代码"}


def _rows_from_csv(path: str, codec: str) -> list[list[str]]:
    with io.open(path, "r", encoding=codec, newline="") as f:
        sample = f.read(4096)
        f.seek(0)
        try:
            d = csv.Sniffer().sniff(sample, delimiters=",\t;|") if sample else csv.excel
        except csv.Error:
            d = csv.excel
        return [r for r in csv.reader(f, d)]


def _read_rows(path: str) -> tuple[list[list[str]], str]:
    """读成行列表，返回 (行, 实际编码)。latin-1 兜底同样要过可打印检查。"""
    last: Exception | None = None
    for c in _CODECS:
        try:
            rows = _rows_from_csv(path, c)
        except UnicodeDecodeError as exc:
            last = exc
            continue
        flat = "".join(str(x) for r in rows[:200] for x in r)
        if "\x00" in flat or _text_score(flat) < _PRINTABLE_MIN:
            raise ValueError(
                f"编码识别失败：用 {c} 解出的内容中可打印字符不足 "
                f"{int(_PRINTABLE_MIN * 100)}%（或含空字节），"
                f"很可能是二进制或损坏的文件。请在 WPS 里另存为 UTF-8 或 GBK 后重试"
            )
        return rows, c
    if last is not None:
        raise ValueError(
            f"编码识别失败（已试 {', '.join(_CODECS)}）：{last}。"
            f"请在 WPS 里另存为 UTF-8 或 GBK 后重试"
        )
    return [], ""


def _rows_from_xlsx(path: str) -> list[list[str]]:
    try:
        from openpyxl import load_workbook
    except ImportError as exc:
        raise ValueError("读 .xlsx 需要 openpyxl：pip install openpyxl") from exc
    try:
        wb = load_workbook(path, read_only=True, data_only=True)
    except Exception as exc:
        # 改名的旧 .xls、下载到一半的文件、根本不是 Excel 的文件都会到这里。
        # 不能把 zipfile.BadZipFile 直接抽出去让用户看栈。
        raise ValueError(
            f"读不了 .xlsx（{type(exc).__name__}: {exc}）。"
            f"文件可能不是真正的 Excel 或已损坏，请在 WPS 里另存为 .xlsx / .csv 后重试"
        ) from exc
    ws = wb.active
    rows = [["" if c is None else str(c) for c in r] for r in ws.iter_rows(values_only=True)]
    wb.close()
    return rows


def _find_header(rows: list[list[str]]) -> int:
    """CSMAR 导出前几行常是"本数据来源于…"的说明，不能当表头。"""
    for i, r in enumerate(rows[:12]):
        cells = [str(c).strip().lower() for c in r if str(c).strip()]
        if len(cells) >= 3 and any(c in _HEAD_HINTS for c in cells):
            return i
    return 0


def read_panel(path: str) -> dict:
    """读 CSMAR 导出文件，返回表头与数据行。"""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".xls":
        raise ValueError("不支持旧版 .xls，请在 WPS 里另存为 .xlsx 或 .csv")
    if ext in (".xlsx", ".xlsm"):
        rows, codec = _rows_from_xlsx(path), "xlsx"
    else:
        rows, codec = _read_rows(path)
    if not rows or not any(str(c).strip() for r in rows for c in r):
        raise ValueError("文件是空的，或没有任何非空单元格")
    h = _find_header(rows)
    head = [c.strip() for c in rows[h]]
    if not any(head):
        raise ValueError("没找到表头行，请确认导出文件是否完整")
    body = [r for r in rows[h + 1:]
            if any(str(c).strip() for c in r) and not _is_desc_row(r, head)]
    return {"head": head, "body": body, "codec": codec}


def _is_desc_row(row: list[str], head: list[str]) -> bool:
    """CSMAR 表头下一行常是字段中文名（"证券代码,统计截止日期,报表类型…"）。

    它没有数字，也不该计入观测数或报表类型分布。判定条件收得很紧：
    整行不含数字，且至少一半单元格是中文/非 ASCII，或与字段中文名对得上。
    真实数据行里代码、日期、数值必带数字，不会被误删。
    """
    vals = [str(c).strip() for c in row]
    filled = [v for v in vals if v]
    if not filled:
        return False
    if any(re.search(r"\d", v) for v in filled):
        return False
    zh = set(FIELD_ZH.values())
    same = sum(1 for v in filled if v in zh)
    if same >= max(1, len(filled) - 1):
        return True
    nonascii = sum(1 for v in filled if not v.isascii())
    return nonascii * 2 >= len(filled)


def _col(head: list[str], name: str) -> int:
    """按字段代码找列；中文表头（CSMAR 也能导出中文名）同样认。"""
    want = {name.strip().lower()}
    zh = FIELD_ZH.get(name, "")
    if zh:
        want.add(zh.split("（")[0].split("(")[0].strip().lower())
    for i, h in enumerate(head):
        if h.strip().lower() in want:
            return i
    return -1


def profile(head: list[str], body: list[str]) -> dict:
    """数据画像：所有数字都出自文件本身，不做推断。"""
    n = len(body)
    miss = {}
    for i, h in enumerate(head):
        blank = sum(1 for r in body if i >= len(r) or not str(r[i]).strip())
        miss[h] = round(blank / n * 100, 2) if n else 0.0

    # 报表类型分布：CSMAR 默认导出里 Typrep 常同时含 A（合并）与 B（母公司），
    # 不筛选会让样本量翻倍、回归结果全错。这里只统计，不下结论。
    rep = {}
    idx = _col(head, "Typrep")
    if idx >= 0:
        for r in body:
            if idx < len(r):
                v = str(r[idx]).strip()
                if v:
                    rep[v] = rep.get(v, 0) + 1

    span = {}
    for name in _TIME_COLS:
        idx = _col(head, name)
        if idx < 0:
            continue
        ys = []
        for r in body:
            if idx < len(r):
                m = re.search(r"(19|20)\d{2}", str(r[idx]))
                if m:
                    ys.append(int(m.group(0)))
        if ys:
            span[head[idx]] = {"min": min(ys), "max": max(ys), "years": len(set(ys))}

    ent = {}
    for name in _ENTITY_COLS:
        idx = _col(head, name)
        if idx >= 0:
            vals = {str(r[idx]).strip() for r in body if idx < len(r) and str(r[idx]).strip()}
            if vals:
                ent[head[idx]] = len(vals)

    seen, dup = set(), 0
    for r in body:
        k = tuple(str(c).strip() for c in r)
        if k in seen:
            dup += 1
        else:
            seen.add(k)

    return {"rows": n, "cols": len(head), "missing": miss, "span": span,
            "entities": ent, "dup": dup, "report_type": rep,
            "thin": {k: v for k, v in miss.items() if v >= 30}}


_REPORT_ZH = {"A": "合并报表", "B": "母公司报表"}


_ZH2CODE: dict[str, str] = {}
for _code, _zh in FIELD_ZH.items():
    _ZH2CODE.setdefault(_zh.split("（")[0].split("(")[0].strip(), _code)


def field_map(head: list[str]) -> list[tuple[str, str]]:
    """表头 → 字段含义。中文表头也回填出对应的 CSMAR 字段代码，便于对照字段说明。"""
    out = []
    for h in head:
        key = h.strip()
        note = FIELD_ZH.get(key)
        if not note:
            code = _ZH2CODE.get(key)
            note = f"{code}：{FIELD_ZH[code]}" if code else "未收录，需核对字段说明"
        out.append((h, note))
    return out


def report_type_warning(prof: dict) -> str:
    """Typrep 有两类及以上时报出来。返回空串表示无问题。"""
    rep = prof.get("report_type") or {}
    if len(rep) < 2:
        return ""
    items = "、".join(f"{k}={_REPORT_ZH.get(k, '未识别')} {v} 行"
                      for k, v in sorted(rep.items()))
    return (f"\n\n⚠ 报表类型不唯一：{items}。"
            f"CSMAR 默认同时导出合并与母公司两套报表，不筛选 Typrep 会让样本量翻倍、"
            f"回归结果全部失真。做公司层面分析只保留 A（合并）；用 B（母公司）要说明理由。")


def describe(prof: dict, src_note: str = "CSMAR") -> str:
    """生成"数据来源与样本"段落骨架。已核实的填实数，没做的处理一律留占位符。"""
    span = ""
    if prof["span"]:
        k, v = next(iter(prof["span"].items()))
        span = f"，{FIELD_ZH.get(k, k)}覆盖 {v['min']}—{v['max']} 年"
    ent = ""
    if prof["entities"]:
        k, c = next(iter(prof["entities"].items()))
        ent = f"，涉及 {c} 家公司" if k.strip().lower() in _ENTITY_COLS else f"，涉及 {c} 个{k}取值"

    warn = ""
    if prof["thin"]:
        items = "、".join(f"{k}（{v}%）" for k, v in prof["thin"].items())
        warn = f"\n\n注意：{items} 缺失率超过 30%。先确认是数据结构如此，还是导出时漏选。"
    if prof["dup"]:
        warn += f"\n\n文件内有 {prof['dup']} 行完全重复，导入统计软件前先查是不是重复导出。"
    warn += report_type_warning(prof)

    return (f"本文使用{src_note}的原始导出数据，共 {prof['rows']} 条观测、{prof['cols']} 个字段"
            f"{span}{ent}。\n\n"
            f"数据处理按以下步骤进行：① 剔除 [待填：金融业/ST/退市] 样本；"
            f"② 剔除关键变量缺失的观测 [待填：剔除后剩余 N]；"
            f"③ 对连续变量在 1% 与 99% 分位缩尾 [已做/未做]。"
            f"最终样本为 [待填 N] 个公司年度观测。\n\n"
            f"数据获取日期：[待填，建议写 {date.today().isoformat()} 或实际取数日]。"
            f"变量定义与字段口径见附录。{warn}")
