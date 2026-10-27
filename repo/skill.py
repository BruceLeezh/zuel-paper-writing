"""人文社科论文写作助手 · 工程版命令行。

    python skill.py check  论文片段.md          文本检测（22 条规则；也直接读 .docx）
    python skill.py sources                     数据源清单与实测状态
    python skill.py find   --source arxiv --q "asset pricing" --cat q-fin.PM
    python skill.py quote  --code sh600519
    python skill.py macro  --indicator NY.GDP.MKTP.CD
    python skill.py cites  知网导出.txt          解析题录（EndNote/RefWorks/GB-T）
    python skill.py panel  资产负债表.csv        CSMAR 数据画像

网络不通时加 --direct（绕代理）或 --skip-verify（跳证书校验，仅调试）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import zipfile
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from zuel import checker, feeds, tables, verify  # noqa: E402
from zuel.model import Level  # noqa: E402
from zuel.net import NetError  # noqa: E402

MARK = {Level.BLOCK: "[必改]", Level.WARN: "[建议]", Level.NOTE: "[提示]"}

BINARY = {".doc", ".pdf", ".xlsx", ".xls", ".pptx", ".zip", ".caj"}

# 解压上限：论文正文到不了这个量级，防的是解压炸弹（.docx 是压缩包，
# 解压后的大小不受文件本身大小限制）。
_MAX_DOCX_XML = 32 * 1024 * 1024

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _docx_text(path: str) -> str:
    """从 .docx 抽出段落文本，用标准库解包，不引入 python-docx。

    .docx 就是一个 zip，正文在 word/document.xml。逐段落取 w:t，制表符与
    换行也保留——检测规则靠行号定位，段与段之间必须断开，否则整篇会变成
    一行，所有行级规则集体失效。
    """
    try:
        with zipfile.ZipFile(path) as z:
            with z.open("word/document.xml") as fh:
                xml = fh.read(_MAX_DOCX_XML + 1)
    except FileNotFoundError:
        raise
    except (zipfile.BadZipFile, KeyError, OSError):
        raise SystemExit("[不支持] 打不开 .docx 的正文（不是有效 Word 文件，"
                         "或它是旧的 .doc 改名）。请另存为 .docx 后重试。")
    if len(xml) > _MAX_DOCX_XML:
        raise SystemExit(f"[不支持] 正文 XML 解压后超过 {_MAX_DOCX_XML // 1024 // 1024}MB"
                         " 上限，已中止。")
    # ElementTree 对 DTD 实体展开攻击没有防护；论文正文用不到 DTD，
    # 出现即拒绝，不给恶意文档进解析器的机会。
    if b"<!DOCTYPE" in xml or b"<!ENTITY" in xml:
        raise SystemExit("[不支持] 正文 XML 含 DTD/实体定义，已拒绝解析。")
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        raise SystemExit("[不支持] .docx 里的正文 XML 损坏，无法解析。")

    # 文本框里的段落嵌在正文段落内部，直接遍历 w:p 会把同一段文字抽两遍，
    # 也会把页眉页脚式的内容当正文。统一按 README 的边界：文本框不纳入检测。
    boxes = list(root.iter(_W + "txbxContent"))
    skip = {id(n) for b in boxes for n in b.iter()}

    lines = []
    for para in root.iter(_W + "p"):
        if id(para) in skip:
            continue
        buf = []
        for node in para.iter():
            if id(node) in skip:
                continue
            if node.tag == _W + "t":
                buf.append(node.text or "")
            elif node.tag == _W + "tab":
                buf.append("\t")
            elif node.tag in (_W + "br", _W + "cr"):
                buf.append("\n")
        lines.append("".join(buf))
    return "\n".join(lines)


def _read_text(path: str) -> str:
    # 先判存在再判格式：不存在的 .pdf 该报「文件不存在」，
    # 报成「二进制格式」会把路径错误误诊成格式错误。
    if not os.path.exists(path):
        raise FileNotFoundError(2, "No such file or directory", path)
    ext = os.path.splitext(path)[1].lower()
    if ext in BINARY:
        raise SystemExit(f"[不支持] {ext} 是二进制格式。请先在 WPS 里导出为 .docx 或 .md / .txt。")
    if ext == ".docx":
        text = _docx_text(path)
        if not text.strip():
            raise SystemExit("[空文档] 没抽到正文，可能全是图片或文本框。请导出为 .md 再试。")
        return text
    try:
        # utf-8-sig：WPS/记事本另存常带 BOM，不剥掉的话首行「摘要」永远认不出。
        with open(path, "r", encoding="utf-8-sig") as f:
            t = f.read()
    except UnicodeDecodeError:
        raise SystemExit("[编码错误] 不是 UTF-8，请另存为 UTF-8 后重试。")
    if "\x00" in t[:2000]:
        raise SystemExit("[不支持] 文件含二进制内容，疑似不是纯文本。")
    return t


def cmd_check(a) -> int:
    problems = checker.run(_read_text(a.file))
    if a.json:
        print(json.dumps([p.as_dict() for p in problems], ensure_ascii=False, indent=2))
        # 与文本模式保持同一契约：有必改则退出码 1，方便 CI 直接判。
        return 1 if any(p.level is Level.BLOCK for p in problems) else 0
    if not problems:
        print("未发现问题。")
        return 0
    block = sum(1 for p in problems if p.level is Level.BLOCK)
    warn = sum(1 for p in problems if p.level is Level.WARN)
    note = sum(1 for p in problems if p.level is Level.NOTE)
    print(f"共 {len(problems)} 条：{block} 必改 / {warn} 建议 / {note} 提示\n")
    for p in problems:
        loc = f" 第{p.line}行" if p.line else ""
        print(f"{MARK.get(p.level, '?')} {p.code}{loc}  {p.what}")
        if p.where:
            print(f"        {p.where[:70]}")
        if p.how:
            print(f"        改法：{p.how}")
    return 1 if block else 0


def cmd_sources(a) -> int:
    if a.json:
        print(json.dumps({"summary": feeds.summary(), "sources": feeds.SOURCES},
                         ensure_ascii=False, indent=2))
        return 0
    print(f"{'源':<18}{'领域':<12}{'级':<4}{'适配器':<7}{'实测'}")
    print("-" * 88)
    for s in feeds.SOURCES:
        mark = "有" if s.get("adapter") else "-"
        print(f"{s['name']:<18}{s['field']:<12}{s['level']:<4}{mark:<7}{s['tested']}")
    st = feeds.summary()
    print("-" * 88)
    print(f"共 {st['total']} 个源：可直连 {st['direct']} 个，已实现适配器 {st['adapters']} 个；"
          f"C 级（{st['by_field'].get('law', 0)} 个法律源等）走导出解析或人工检索")
    return 0


def _outlet(i) -> str:
    """把题录的出处拼成一行：刊名, 卷(期): 页码。缺项不补空括号。"""
    if i.raw.get("rest"):
        return str(i.raw["rest"]).strip().rstrip(".")
    j = str(i.raw.get("journal") or "").strip()
    if not j:
        return ""
    v = str(i.raw.get("volume") or "").strip()
    n = str(i.raw.get("issue") or "").strip()
    p = str(i.raw.get("pages") or "").strip()
    s = j + (f", {v}" if v else "") + (f"({n})" if n else "")
    return s + (f": {p}" if p else "")


def cmd_find(a) -> int:
    items = feeds.fetch_items(a.source, a.q, a.limit,
                              mail=a.mail, cat=a.cat, country=a.country)
    if a.json:
        print(json.dumps([i.as_dict() for i in items], ensure_ascii=False, indent=2))
        return 0
    print(f"命中 {len(items)} 条（{a.source}）")
    for i in items:
        print(f"\n· {i.title}")
        if i.people:
            print(f"  作者：{', '.join(i.people[:3])}{' 等' if len(i.people) > 3 else ''}")
        if i.when:
            print(f"  年份：{i.when}")
        oj = _outlet(i)
        if oj:
            print(f"  出处：{oj}")
        if i.ident:
            print(f"  标识符：{i.ident}")
        if i.note:
            print(f"  摘要：{i.note[:100]}…")
        print(f"  取数于：{i.pulled}")
    return 0


def cmd_quote(a) -> int:
    if a.source == "eastmoney":
        if not a.allow_unofficial:
            raise SystemExit("[已关闭] 东方财富是非官方接口，确认可用后加 --allow-unofficial 再试。")
        try:
            item = feeds.eastmoney(a.code)
        except NetError as exc:
            raise NetError(f"{exc}（东方财富为非官方接口，时不常可用；"
                           f"要稳定取价请改用 --source sina）")
        print(json.dumps(item.as_dict(), ensure_ascii=False, indent=2))
        return 0
    for i in feeds.sina([a.code]):
        print(f"{i.title}（{i.ident}）")
        for k, v in i.raw.items():
            print(f"  {k}: {v}")
        print(f"  取数于：{i.pulled}")
    return 0


def cmd_macro(a) -> int:
    items = feeds.worldbank(a.indicator, a.country, a.limit)
    meta = feeds.worldbank_meta(a.indicator)
    if meta:
        print(f"指标：{meta.get('name')}")
        if meta.get("def"):
            print(f"口径：{str(meta['def'])[:160]}…")
        print()
    for i in items:
        print(f"{i.when}  {i.raw.get('value')}")
    print(f"\n来源 {a.country} / {a.indicator}，取数于 {items[0].pulled if items else '-'}")
    return 0


def cmd_cites(a) -> int:
    items, fmt = tables.read_export(a.file, a.format, a.source)
    parsed = [i for i in items if "unparsed" not in i.raw]
    if a.json:
        print(json.dumps([i.as_dict() for i in items], ensure_ascii=False, indent=2))
        return 0
    print(f"识别为 {fmt}，解析出 {len(parsed)} 条"
          f"{'（另有 ' + str(len(items) - len(parsed)) + ' 行未识别）' if len(items) != len(parsed) else ''}\n")
    for i in items:
        print(f"· {i.title}")
        if i.people:
            print(f"  作者：{', '.join(i.people)}")
        if i.when:
            print(f"  年份：{i.when}")
        if i.raw.get("kind_zh"):
            print(f"  类型：{i.raw['kind_zh']}")
        oj = _outlet(i)
        if oj:
            print(f"  出处：{oj}")
        if i.ident:
            print(f"  标识符：{i.ident}")
        if "unparsed" in i.raw:
            print(f"  [{len(i.raw['unparsed'])} 条没认出来，原样保留]")
            for ln in i.raw["unparsed"][:5]:
                print(f"    {ln[:70]}")
    return 0


def cmd_verify(a) -> int:
    items, fmt = tables.read_export(a.file, a.format, a.source)
    # 「未能解析的条目」不是一条文献：不参与核验与自查，只报行数。
    parsed = [i for i in items if "unparsed" not in i.raw]
    res = verify.verify_items(parsed, limit=a.limit)
    unparsed_lines = len(items) - len(parsed)
    if a.json:
        print(json.dumps({"format": fmt, "unparsed_lines": unparsed_lines, **res},
                         ensure_ascii=False, indent=2))
        return 1 if any(r["level"] == "block" for r in res["problems"]) else 0

    print(f"识别为 {fmt}，共 {res['total']} 条"
          f"{'（另有 ' + str(unparsed_lines) + ' 行未识别，不计入）' if unparsed_lines else ''}")
    print(f"可核验 {res['checked']} 条（有 DOI）；无标识符 {res['skipped']} 条；"
          f"标识符不是 DOI {res['not_doi']} 条")
    if res["failed"]:
        print(f"请求失败 {len(res['failed'])} 条（网络问题，可稍后重跑）")
    if res.get("truncated"):
        print(f"达到 --limit {a.limit}，还有 {res['truncated']} 条未核验（调大 --limit 再跑）")
    print()

    if res["checked"] == 0:
        print("没有可核验的条目——本题录里没有 DOI。")
        print("中文期刊多数不注册 DOI，这种情况只能人工核封面页：作者、年期、页码逐项对。")
    elif not res["problems"]:
        print("已核条目未发现字段冲突。")
    else:
        mark = {"block": "必查", "warn": "存疑"}
        for row in res["problems"]:
            print(f"[{mark.get(row['level'], '?')}] {row['title'][:46]}")
            for it in row["issues"]:
                print(f"       · {it['what']}：{it['detail']}")
                print(f"         改法：{it['how']}")

    # 没 DOI 的条目核不了真假，但能查出题录自身缺什么。
    if res["selfcheck"]:
        print(f"\n无 DOI 条目自查（{len(res['selfcheck'])} 条有硬伤；只查题录完整性，"
              f"不代表内容属实，内容仍需回原文核）：")
        for row in res["selfcheck"]:
            print(f"  · {row['title'][:46]}")
            for it in row["issues"]:
                print(f"    - {it['what']}：{it['detail']}；{it['how']}")

    unverified = res["skipped"] + res["not_doi"]
    if unverified:
        print(f"\n注意：{unverified} 条没有 DOI，本工具核不了，不等于没问题。")
    return 1 if any(r["level"] == "block" for r in res["problems"]) else 0


def cmd_panel(a) -> int:
    data = tables.read_panel(a.file)
    prof = tables.profile(data["head"], data["body"])
    if a.json:
        print(json.dumps({"codec": data["codec"], "profile": prof,
                          "fields": [{"code": c, "zh": z}
                                     for c, z in tables.field_map(data["head"])],
                          "draft": tables.describe(prof)},
                         ensure_ascii=False, indent=2))
        return 0
    print(f"文件：{a.file}\n编码：{data['codec']}")
    print(f"观测：{prof['rows']} 行 × {prof['cols']} 列")
    for k, v in prof["span"].items():
        print(f"时间：{k} 覆盖 {v['min']}—{v['max']}（{v['years']} 个年度）")
    for k, v in prof["entities"].items():
        print(f"实体：{k} 共 {v} 个")
    if prof.get("report_type"):
        for k, v in sorted(prof["report_type"].items()):
            print(f"报表类型：{k}（{tables._REPORT_ZH.get(k, '未识别')}）{v} 行")
    if prof["dup"]:
        print(f"重复：{prof['dup']} 行完全重复")
    if prof["thin"]:
        print("高缺失（≥30%）：" + "、".join(f"{k} {v}%" for k, v in prof["thin"].items()))
    print("\n字段对照（前 20）：")
    for c, z in tables.field_map(data["head"])[:20]:
        print(f"  {c:<16}{z}")
    print("\n" + "-" * 70 + "\n「数据来源与样本」骨架（占位符需你回填）：\n")
    print(tables.describe(prof))
    return 0


def _pos_int(text: str) -> int:
    """--limit 只接受正整数：0 或负数会让"取几条"退化成"一条不取"。"""
    try:
        v = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"要整数，收到 {text!r}")
    if v < 1:
        raise argparse.ArgumentTypeError(f"要 ≥1 的整数，收到 {v}")
    return v


def main() -> int:
    net = argparse.ArgumentParser(add_help=False)
    net.add_argument("--direct", action="store_true", help="绕过环境代理直连")
    net.add_argument("--skip-verify", action="store_true", help="跳过证书校验（仅调试）")
    net.add_argument("--json", action="store_true", help="输出 JSON")

    p = argparse.ArgumentParser(description="人文社科论文写作助手 · 工程版")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("check", parents=[net], help="文本检测")
    s.add_argument("file")
    s.set_defaults(func=cmd_check)

    s = sub.add_parser("sources", parents=[net], help="数据源清单")
    s.set_defaults(func=cmd_sources)

    s = sub.add_parser("find", parents=[net], help="文献检索（宏观指标用 macro）")
    s.add_argument("--source", default="openalex",
                   choices=["openalex", "crossref", "arxiv", "worldbank"],
                   help="worldbank 时 --q 传指标代码，如 NY.GDP.MKTP.CD")
    s.add_argument("--q", required=True)
    s.add_argument("--limit", type=_pos_int, default=10)
    s.add_argument("--cat", default="", help="arXiv 分类，如 econ.GN / q-fin.PM")
    s.add_argument("--mail", default="", help="OpenAlex polite pool 邮箱")
    s.add_argument("--country", default="CN")
    s.set_defaults(func=cmd_find)

    s = sub.add_parser("quote", parents=[net], help="单只行情")
    s.add_argument("--code", required=True)
    s.add_argument("--source", default="sina", choices=["sina", "eastmoney"])
    s.add_argument("--allow-unofficial", action="store_true")
    s.set_defaults(func=cmd_quote)

    s = sub.add_parser("macro", parents=[net], help="宏观指标")
    s.add_argument("--indicator", required=True)
    s.add_argument("--country", default="CN")
    s.add_argument("--limit", type=_pos_int, default=10)
    s.set_defaults(func=cmd_macro)

    s = sub.add_parser("cites", parents=[net], help="解析题录导出")
    s.add_argument("file")
    s.add_argument("--format", default="auto",
                   choices=["auto", "endnote", "refworks", "gbt"])
    s.add_argument("--source", default="cnki")
    s.set_defaults(func=cmd_cites)

    s = sub.add_parser("verify", parents=[net], help="核验参考文献真实性（按 DOI 查 Crossref）")
    s.add_argument("file")
    s.add_argument("--format", default="auto",
                   choices=["auto", "endnote", "refworks", "gbt"])
    s.add_argument("--source", default="cnki")
    s.add_argument("--limit", type=_pos_int, default=50,
                   help="最多核验多少条，避免打爆接口")
    s.set_defaults(func=cmd_verify)

    s = sub.add_parser("panel", parents=[net], help="CSMAR 数据画像")
    s.add_argument("file")
    s.set_defaults(func=cmd_panel)

    a = p.parse_args()
    from zuel import net as _net
    _net.DEFAULTS["direct"] = getattr(a, "direct", False)
    _net.DEFAULTS["skip_verify"] = getattr(a, "skip_verify", False)
    try:
        return a.func(a)
    except FileNotFoundError as exc:
        print(f"[出错] 文件不存在：{exc.filename}", file=sys.stderr)
        return 2
    except PermissionError as exc:
        print(f"[出错] 没有读取权限：{exc.filename}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"[出错] 读不了文件：{exc}", file=sys.stderr)
        return 2
    except (NetError, ValueError, KeyError) as exc:
        print(f"[出错] {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
