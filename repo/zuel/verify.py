"""文献核验：拿条目去 Crossref 比对，看它是不是真的那篇。

只核有 DOI 的条目。中文期刊大多没有 DOI，或 DOI 不在 Crossref 注册，
这部分退回人工核对，本模块不猜。

四类问题，按严重度从高到低：
    doi_missing    DOI 查不到（错号、或该刊未在 Crossref 注册）
    doi_mismatch   DOI 有效，但指向另一篇论文（张冠李戴）
    field_diff     标题能对上，但作者／年／卷期页与注册记录不符
    order_diff     作者集合一致、顺序不同（常见于中文期刊的英译名倒序）

没有 DOI 的条目走 `completeness()`：只查题录自身缺了什么，不判断真假。
"""
from __future__ import annotations

import json
import re
import urllib.parse
from datetime import date

from . import net
from .model import Item

_API = "https://api.crossref.org/works/"
_BLOCK = 0.55          # 标题相似度低于此值，判定指向了另一篇论文
_CLOSE = 0.92          # 高于此值视为同一标题，不再报差异

_PUNCT = re.compile(r"[^\w\u4e00-\u9fff]+")
_CJK = re.compile(r"[\u4e00-\u9fff]")


def _norm(text: str) -> str:
    """标题归一化：去标点、压空白、转小写。中英文都适用。"""
    return _PUNCT.sub(" ", (text or "").lower()).strip()


def _tokens(text: str) -> set[str]:
    return {t for t in _norm(text).split() if t}


def _bigrams(text: str) -> set[str]:
    """字级 2-gram。中文没有空格分词，整句会只剩一个 token，Jaccard 无意义。"""
    compact = re.sub(r"\s+", "", text or "")
    return {compact[i:i + 2] for i in range(len(compact) - 1)} if len(compact) >= 2 else {compact}


def overlap(a: str, b: str) -> float:
    """两段标题的相似度，0–1。空标题返回 0。

    三条路径叠加：完全相同 → 1；短标题是长标题的前缀（副标题、破折号补充说明）
    → 1；否则算 token 并集的重叠率。中文额外用 2-gram 取较大值——只比空格分词
    会把「数字化转型与企业创新」和它的副标题版当成两篇不同的文章。
    """
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    short, long_ = (na, nb) if len(na) <= len(nb) else (nb, na)
    if len(short) >= 6 and long_.startswith(short):
        return 1.0
    ta, tb = _tokens(a), _tokens(b)
    jac = len(ta & tb) / len(ta | tb)
    if _CJK.search(na) or _CJK.search(nb):
        ca, cb = _bigrams(na), _bigrams(nb)
        if ca and cb:
            return max(jac, len(ca & cb) / len(ca | cb))
    return jac


def lookup(doi: str, timeout: float = 12.0) -> dict | None:
    """按 DOI 取 Crossref 记录。404 返回 None；网络错误上抛。"""
    doi = (doi or "").strip()
    if not doi:
        return None
    url = _API + urllib.parse.quote(doi, safe="/:;().-_")
    try:
        raw = net.fetch(url, head={"Accept": "application/json"}, timeout=timeout,
                        interval=1.0)
    except net.NetError as exc:
        msg = str(exc)
        # 只认以「HTTP 404：」开头的错误。用子串匹配会把 URL 或 DOI 里
        # 恰好含 "404" 的 429/超时误判成「查无此 DOI」，凭空报必改。
        if re.match(r"^HTTP 404[：:]", msg) or "404 Not Found" in msg:
            return None
        raise
    try:
        return json.loads(raw).get("message") or {}
    except json.JSONDecodeError:
        return None


_DOI_SHAPE = re.compile(r"^10\.\d{4,9}/\S+$")


def _normalize_doi(value: str) -> str:
    """去掉 URL 前缀与尾部标点；不像 DOI 的一律返回空串。"""
    v = (value or "").strip()
    v = re.sub(r"^https?://(dx\.)?doi\.org/", "", v, flags=re.I)
    v = re.sub(r"^doi:\s*", "", v, flags=re.I)
    v = v.strip().rstrip(".")
    return v if _DOI_SHAPE.match(v) else ""


def compare(item: Item, record: dict) -> list[dict]:
    """把一条题录与 Crossref 记录逐字段比对，返回问题列表。"""
    issues: list[dict] = []
    ct = (record.get("title") or [""])[0]
    score = overlap(item.title, ct)

    if score < _BLOCK:
        issues.append({
            "kind": "doi_mismatch", "level": "block",
            "what": "DOI 指向另一篇论文",
            "detail": f"条目题名「{item.title[:40]}」／注册题名「{ct[:40]}」",
            "how": "核对 DOI 是否抄错；确认后改 DOI，或换成该文真实 DOI",
        })
        return issues          # 题名都对不上，后面的字段比较没有意义

    # 作者
    reg_family = [
        _norm(a.get("family", "") or a.get("given", ""))
        for a in (record.get("author") or [])
        if a.get("family") or a.get("given")
    ]
    mine = [p for p in item.people if p]
    if reg_family and mine:
        # 题录里常是「名 姓」全写，注册记录只存姓，所以按“我的写法里是否含该姓”配对，
        # 再比较配出来的顺序，而不是直接比字符串。
        matched: list[str] = []
        for who in mine:
            norm = _norm(who)
            hit = next((f for f in reg_family if f and f in norm), "")
            matched.append(hit)
        paired = len([m for m in matched if m]) == len(reg_family) == len(mine)
        if not paired:
            issues.append({
                "kind": "field_diff", "level": "warn",
                "what": "作者人数或姓名与注册记录对不上",
                "detail": f"条目：{'、'.join(mine[:3])}／注册：{'、'.join(reg_family[:3])}",
                "how": "核原本封面页；中文作者注意拼音写法是否被自动转换",
            })
        elif [m for m in matched if m] != reg_family:
            issues.append({
                "kind": "order_diff", "level": "warn",
                "what": "作者顺序与注册记录不一致",
                "detail": f"条目：{'、'.join(mine[:4])}／注册：{'、'.join(reg_family[:4])}",
                "how": "按注册记录的顺序改；确属不同版本的，在脚注说明",
            })

    # 年
    parts = (record.get("issued", {}).get("date-parts") or [[None]])[0]
    reg_year = str(parts[0]) if parts and parts[0] else ""
    if reg_year and item.when and reg_year != str(item.when)[:4]:
        issues.append({
            "kind": "field_diff", "level": "warn",
            "what": "年份不一致",
            "detail": f"条目 {item.when}／注册 {reg_year}（网络首发与正式出版年可能不同）",
            "how": "以正式出版年为准；引用网络首发版要注明",
        })

    # 卷期页
    reg_vol = str(record.get("volume") or "")
    reg_issue = str(record.get("issue") or "")
    reg_page = str(record.get("page") or "")
    raw = item.raw or {}
    pairs = [
        ("卷", str(raw.get("volume") or ""), reg_vol),
        ("期", str(raw.get("issue") or ""), reg_issue),
        ("页码", str(raw.get("pages") or ""), reg_page),
    ]
    for label, have, want in pairs:
        if not have or not want:
            continue
        if _norm(have) != _norm(want):
            issues.append({
                "kind": "field_diff", "level": "warn",
                "what": f"{label}与注册记录不一致",
                "detail": f"条目 {have}／注册 {want}",
                "how": "以注册记录为准；Early Access 转正式出版常出现这类漂移",
            })
    return issues


def completeness(items: list[Item], *, today: date | None = None) -> list[dict]:
    """对核不了 DOI 的条目做「看得出缺什么」的自查。

    中文期刊大多不注册 DOI，拿不到 Crossref 记录不等于没事——但至少能查出
    缺年份、缺页码、年份在未来、缺作者、题名重复这五类硬伤。这一段不做
    真假判断，只查题录自身的完整性，错误文案里写清楚这一点，不冒充核实。
    """
    now_year = (today or date.today()).year
    seen: dict[str, int] = {}
    rows: list[dict] = []
    for it in items:
        issues: list[dict] = []
        title = (it.title or "").strip()
        year = (it.when or "").strip()
        raw = it.raw or {}
        pages = str(raw.get("pages") or "").strip()
        kind = str(raw.get("kind_zh") or "")

        if not title:
            issues.append({"kind": "no_title", "level": "block",
                           "what": "缺题名", "detail": "解析后没有题名",
                           "how": "回导出文件核对，或手工补上题名后再引"})
        m = re.search(r"(?:19|20)\d{2}", year)
        if not m:
            issues.append({"kind": "no_year", "level": "warn",
                           "what": "缺出版年", "detail": "题录里没有四位年份",
                           "how": "补年份；GB/T 7714 条目缺年份不能提交"})
        elif int(m.group(0)) > now_year + 1:
            issues.append({"kind": "future_year", "level": "warn",
                           "what": "年份在未来",
                           "detail": f"题录写 {m.group(0)}，当前 {now_year} 年",
                           "how": "网络首发或录稿待刊要标[网络首发]；否则是抄错"})
        if not it.people:
            issues.append({"kind": "no_author", "level": "warn",
                           "what": "缺作者", "detail": "没有解析到作者",
                           "how": "补作者；团体作者写全称，不留空"})
        # 解析器产出的类型是「期刊论文」（tables.KIND_ZH/_EN_KIND），
        # 旧条件只比「期刊」，两类导出的期刊条目永远查不到缺页。
        if kind.startswith(("期刊", "报纸")) and not pages:
            issues.append({"kind": "no_pages", "level": "note",
                           "what": "缺页码", "detail": f"{kind}条目没有起止页",
                           "how": "补起止页；只有篇名的网络版补 DOI 或 URL"})
        key = _norm(title)
        if key:
            seen[key] = seen.get(key, 0) + 1
            if seen[key] == 2:
                issues.append({"kind": "duplicate", "level": "note",
                               "what": "题名重复", "detail": "同一篇被收了两次",
                               "how": "合并成一条，检查正文引用编号是否错位"})
        if issues:
            rows.append({"title": title or "（无题名）", "doi": "",
                         "kind": issues[0]["kind"], "level": issues[0]["level"],
                         "issues": issues})
    return rows


def verify_items(items: list[Item], *, limit: int = 50,
                 timeout: float = 12.0) -> dict:
    """批量核验。返回统计与问题清单，不抛异常（单条失败只记账）。"""
    checked, skipped, not_doi, failed = 0, 0, 0, []
    rows: list[dict] = []
    doi_total = sum(1 for it in items if _normalize_doi(it.ident))
    for it in items:
        doi = _normalize_doi(it.ident)
        if not doi:
            if (it.ident or "").strip():
                not_doi += 1        # 有标识符，但不是 DOI（ISBN、报告号等）
            else:
                skipped += 1
            continue
        if checked >= limit:
            break
        checked += 1
        try:
            rec = lookup(doi, timeout=timeout)
        except Exception as exc:              # 网络问题不该中断整批
            failed.append({"title": it.title[:40], "doi": doi, "reason": str(exc)[:60]})
            continue
        if rec is None:
            rows.append({
                "title": it.title, "doi": doi, "kind": "doi_missing", "level": "block",
                "issues": [{
                    "kind": "doi_missing", "level": "block",
                    "what": "DOI 查不到",
                    "detail": f"{doi} 在 Crossref 无记录",
                    "how": "DOI 可能抄错，或该刊未注册 DOI；改人工核对封面页",
                }],
            })
            continue
        issues = compare(it, rec)
        if issues:
            rows.append({"title": it.title, "doi": doi,
                         "kind": issues[0]["kind"], "level": issues[0]["level"],
                         "issues": issues})
    return {
        "total": len(items),
        "checked": checked,
        "skipped": skipped,
        "not_doi": not_doi,
        "failed": failed,
        "truncated": max(0, doi_total - checked),
        "problems": rows,
        "selfcheck": completeness([
            it for it in items if not _normalize_doi(it.ident)
        ]),
        "ok": max(0, checked - len(rows) - len(failed)),
    }
