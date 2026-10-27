"""数据源接入：文献、行情、宏观。

原则：先判访问等级，再决定能不能自动化。
    A 公开开放   —— 直接取
    B 需免费 Key —— 配 Key 后取
    C 机构订阅   —— 不抓，只解析用户导出的文件（见 tables.py）
    D 无官方接口 —— 只做单次查询，不开循环
"""
from __future__ import annotations

import json
import re
import urllib.parse
import xml.etree.ElementTree as ET

from .model import Item
from .net import NetError, fetch

# 登记表：name / field / level / 覆盖 / 取法 / 实测
SOURCES = [
    # ── 学术文献 ──────────────────────────────────────────
    dict(name="openalex", field="literature", level="A", auth=False, adapter=True,
         covers="全学科文献题录 2.5 亿+，含引文网络",
         how="JSON REST", tested="直连成功（无 Key 时与他人共享日预算）"),
    dict(name="crossref", field="literature", level="A", auth=False, adapter=True,
         covers="DOI 注册元数据：期刊、图书、会议",
         how="JSON REST", tested="直连成功"),
    dict(name="semantic_scholar", field="literature", level="A", auth=False, adapter=False,
         covers="全学科，含引用上下文与 AI 摘要",
         how="JSON REST", tested="无 Key 时 429 限流，需配免费 Key"),
    dict(name="core", field="literature", level="B", auth=True, adapter=False,
         covers="开放获取全文 3 亿+",
         how="JSON + PDF", tested="需免费 Key；未实现"),
    dict(name="arxiv", field="literature", level="A", auth=False, adapter=True,
         covers="预印本：数理、经济、金融、计算机",
         how="Atom XML", tested="直连成功（官方建议间隔 3 秒）"),
    dict(name="doaj", field="literature", level="A", auth=False, adapter=False,
         covers="开放获取期刊目录",
         how="JSON REST", tested="当前网络不可达"),
    dict(name="ncpssd", field="literature", level="M", auth=True, adapter=False,
         covers="中文社科期刊与论文",
         how="网页检索 / 手工复制", tested="免费注册可用，无公开 API"),
    dict(name="cnki", field="literature", level="C", auth=True, adapter=False,
         covers="中文学术全文库：期刊、学位论文、会议、报纸、年鉴",
         how="EndNote / RefWorks / GB/T 7714 导出",
         tested="需机构订阅；不抓，见 tables.read_export()"),
    dict(name="wanfang", field="literature", level="C", auth=True, adapter=False,
         covers="中文期刊、学位论文、会议、专利、标准",
         how="EndNote / RefWorks 导出", tested="需机构订阅；不抓"),
    dict(name="vip", field="literature", level="C", auth=True, adapter=False,
         covers="中文期刊为主", how="导出文本", tested="需机构订阅；不抓"),
    dict(name="duxiu", field="literature", level="C", auth=True, adapter=False,
         covers="图书与图书章节", how="网页 / 文献传递", tested="需机构登录"),

    # ── 财经行情与公司 ────────────────────────────────────
    dict(name="eastmoney", field="market", level="D", auth=False, adapter=True,
         covers="A 股行情快照、财务字段",
         how="JSON（网页接口）", tested="直连成功过；复测被拒（RemoteDisconnected），非官方接口不稳定"),
    dict(name="sina", field="market", level="D", auth=False, adapter=True,
         covers="A 股实时报价与当日快照",
         how="纯文本（GBK，需 Referer）", tested="直连成功"),
    dict(name="tencent_finance", field="market", level="D", auth=False, adapter=False,
         covers="A 股/港股实时报价", how="JSON", tested="未测"),
    dict(name="akshare", field="market", level="A", auth=False, adapter=False,
         covers="聚合器：把东财、新浪、同花顺等公开页封成函数",
         how="pandas DataFrame", tested="开源库，未内置"),
    dict(name="tushare", field="market", level="B", auth=True, adapter=False,
         covers="A 股行情、财务三表、宏观、基金",
         how="JSON / DataFrame", tested="需注册积分"),
    dict(name="baostock", field="market", level="A", auth=False, adapter=False,
         covers="A 股历史日线与分钟线", how="DataFrame", tested="免注册，未内置"),
    dict(name="csmar", field="market", level="C", auth=True, adapter=False,
         covers="学术研究级公司财务、治理、ESG、审计",
         how="CSV / XLSX 导出", tested="需校园网；不抓，见 tables.read_panel()"),
    dict(name="wind", field="market", level="C", auth=True, adapter=False,
         covers="全品类金融终端", how="Excel 插件 / 付费 API", tested="需终端账号"),
    dict(name="ifind", field="market", level="C", auth=True, adapter=False,
         covers="全品类金融终端", how="Excel 插件 / 付费 API", tested="需终端账号"),

    # ── 宏观统计 ──────────────────────────────────────────
    dict(name="stats_gov", field="macro", level="A", auth=False, adapter=False,
         covers="中国月度/季度/年度宏观：GDP、CPI、PPI、投资、消费、人口",
         how="网页导出 Excel", tested="脚本请求被拒（403），改网页导出"),
    dict(name="pboc", field="macro", level="A", auth=False, adapter=False,
         covers="社融、M0/M1/M2、利率、汇率、信贷收支",
         how="网页表格 / Excel", tested="未测；公开可查"),
    dict(name="worldbank", field="macro", level="A", auth=False, adapter=True,
         covers="全球 200+ 国家年度发展指标",
         how="JSON REST", tested="直连成功"),
    dict(name="fred", field="macro", level="B", auth=True, adapter=False,
         covers="美国及国际宏观 80 万+ 时间序列",
         how="JSON / XML", tested="需免费 Key"),
    dict(name="imf", field="macro", level="A", auth=False, adapter=False,
         covers="国际金融统计、世界经济展望、跨境资本",
         how="SDMX / JSON", tested="403（datamapper 接口）"),
    dict(name="bis", field="macro", level="A", auth=False, adapter=False,
         covers="国际银行业、跨境信贷、有效汇率",
         how="CSV / JSON", tested="406（需 Accept 头）"),
    dict(name="oecd", field="macro", level="A", auth=False, adapter=False,
         covers="发达国家社会经济指标", how="SDMX", tested="未测"),

    # ── 法律与案例 ────────────────────────────────────────
    dict(name="flk_npc", field="law", level="A", auth=False, adapter=False,
         covers="宪法、法律、行政法规、地方性法规、司法解释的现行文本",
         how="网页（前端为 SPA）", tested="无公开接口，人工检索核对"),
    dict(name="wenshu", field="law", level="D", auth=False, adapter=False,
         covers="各级法院裁判文书（公开部分）",
         how="网页", tested="反爬严格，无公开接口"),
    dict(name="court_gov", field="law", level="D", auth=False, adapter=False,
         covers="庭审直播与回看", how="视频 / 网页", tested="未测；仅部分案件"),
    dict(name="spp_gov", field="law", level="A", auth=False, adapter=False,
         covers="公报案例、指导性案例全文", how="网页 / PDF", tested="未测；公开可查"),
    dict(name="pkulaw", field="law", level="C", auth=True, adapter=False,
         covers="法规、案例、法学期刊、实务",
         how="网页 / 导出", tested="订阅制，学校多有采购"),

    # ── 政策文本 ──────────────────────────────────────────
    dict(name="gov_policy", field="policy", level="A", auth=False, adapter=False,
         covers="国务院及办公厅发布的政策文件",
         how="网页 / PDF", tested="未测；公开可查"),
    dict(name="ministry_site", field="policy", level="A", auth=False, adapter=False,
         covers="部门规章、地方规范性文件",
         how="网页 / PDF", tested="未测；注意版本时效"),
]


def find(name: str) -> dict:
    for s in SOURCES:
        if s["name"] == name:
            return s
    raise KeyError(f"没有登记这个源：{name}")


def _lit(src: str, title: str, people: list[str], when: str,
         ident: str, note: str, link: str, raw: dict) -> Item:
    return Item(src=src, field="literature", title=title, people=people,
                when=when, ident=ident, note=note, link=link, raw=raw)


# ── A 级：文献 ──────────────────────────────────────────────

def _openalex_abstract(w: dict) -> str:
    """把 OpenAlex 的倒排摘要（词 → 出现位置列表）拼回一段文字。

    OpenAlex 不返回整段 abstract 字段，只返回 abstract_inverted_index；
    旧代码直接读 w["abstract"]，拿到的永远是空串。
    """
    inv = w.get("abstract_inverted_index")
    if not isinstance(inv, dict):
        return ""
    pos: dict[int, str] = {}
    for word, idxs in inv.items():
        for i in idxs or []:
            pos[i] = str(word)
    return " ".join(pos[i] for i in sorted(pos))[:300]


def openalex(q: str, limit: int = 10, mail: str = "") -> list[Item]:
    params = {"search": q, "per-page": min(limit, 50)}
    if mail:
        params["mailto"] = mail
    data = json.loads(fetch("https://api.openalex.org/works?" + urllib.parse.urlencode(params)))
    out = []
    for w in data.get("results", []):
        people = [a.get("author", {}).get("display_name", "")
                  for a in w.get("authorships", [])[:5]]
        ident = w.get("doi") or w.get("id", "")
        out.append(_lit("openalex",
                        re.sub(r"\s+", " ", w.get("title") or ""),
                        [p for p in people if p],
                        str(w.get("publication_year") or ""),
                        ident,
                        _openalex_abstract(w),
                        ident if str(ident).startswith("http") else "",
                        {"cited": w.get("cited_by_count", 0)}))
    return out


def crossref(q: str, limit: int = 10) -> list[Item]:
    url = ("https://api.crossref.org/works?"
           + urllib.parse.urlencode({"query": q, "rows": min(limit, 50)}))
    data = json.loads(fetch(url))
    out = []
    for w in data.get("message", {}).get("items", []):
        people = []
        for a in w.get("author", [])[:5]:
            name = " ".join(x for x in [a.get("given"), a.get("family")] if x)
            if name:
                people.append(name)
        year = ""
        parts = (w.get("issued", {}).get("date-parts") or [[None]])[0]
        if parts and parts[0]:
            year = str(parts[0])
        out.append(_lit("crossref",
                        re.sub(r"\s+", " ", (w.get("title") or [""])[0]),
                        people, year,
                        w.get("DOI", ""),
                        re.sub(r"\s+", " ", (w.get("abstract") or ""))[:300],
                        f"https://doi.org/{w.get('DOI')}" if w.get("DOI") else "",
                        {"venue": (w.get("container-title") or [""])[0]}))
    return out


_ATOM = {"a": "http://www.w3.org/2005/Atom"}


def arxiv(q: str, limit: int = 10, cat: str = "") -> list[Item]:
    query = f"all:{q}" + (f" AND cat:{cat}" if cat else "")
    url = ("https://export.arxiv.org/api/query?"
           + urllib.parse.urlencode({"search_query": query, "start": 0,
                                     "max_results": min(limit, 50)}))
    root = ET.fromstring(fetch(url, interval=3.0).encode("utf-8"))
    out = []
    for e in root.findall("a:entry", _ATOM):
        title = " ".join((e.findtext("a:title", default="", namespaces=_ATOM) or "").split())
        if not title:
            continue
        people = [(x.findtext("a:name", default="", namespaces=_ATOM) or "").strip()
                  for x in e.findall("a:author", _ATOM)]
        link = (e.findtext("a:id", default="", namespaces=_ATOM) or "")
        out.append(_lit("arxiv", title, [p for p in people if p],
                        (e.findtext("a:published", default="", namespaces=_ATOM) or "")[:10],
                        link,
                        " ".join((e.findtext("a:summary", default="", namespaces=_ATOM) or "").split())[:300],
                        link, {"cat": cat}))
    return out


# ── A 级：宏观 ──────────────────────────────────────────────

def worldbank(indicator: str, country: str = "CN", limit: int = 10) -> list[Item]:
    # 国家代码与指标代码都要转义：用户可能传 "CN;all" 这类多值写法
    cty_code = urllib.parse.quote(str(country).strip(), safe=";,")
    ind_code = urllib.parse.quote(str(indicator).strip(), safe=".")
    url = (f"https://api.worldbank.org/v2/country/{cty_code}/indicator/{ind_code}?"
           + urllib.parse.urlencode({"format": "json", "per_page": min(limit, 100)}))
    data = json.loads(fetch(url))
    if not isinstance(data, list) or len(data) < 2 or not data[1]:
        raise NetError(f"没取到数据，指标代码可能不对：{indicator}"
                       f"（国家代码用 ISO2/ISO3，如 CN、CHN；多国用 CN;US）")
    out = []
    for row in data[1]:
        if row.get("value") is None:
            continue
        ind = row.get("indicator") or {}
        cty = row.get("country") or {}
        out.append(Item(src="worldbank", field="macro",
                        title=f"{cty.get('value', '')} {ind.get('value', '')}（{row.get('date', '')}）",
                        when=str(row.get("date", "")),
                        ident=str(ind.get("id", indicator)),
                        link=f"https://data.worldbank.org/indicator/{ind.get('id', indicator)}",
                        raw={"value": row.get("value"), "country": cty.get("id")}))
    return out


def worldbank_meta(indicator: str) -> dict:
    """取指标定义，写论文时交代口径要用。"""
    data = json.loads(fetch(f"https://api.worldbank.org/v2/indicator/{indicator}?format=json"))
    if isinstance(data, list) and len(data) > 1 and data[1]:
        r = data[1][0]
        return {"id": r.get("id"), "name": r.get("name"),
                "def": r.get("sourceNote"), "unit": r.get("unit")}
    return {}


# ── D 级：行情（只做单次查询） ──────────────────────────────

_SINA_FIELDS = ["名称", "今开", "昨收", "现价", "最高", "最低",
                "买一价", "卖一价", "成交量_股", "成交额_元"]
# 10–29 是五档买卖量价，30=日期、31=时间。旧表只收前 10 位，
# when 取 raw["日期"] 永远是空串。
_SINA_TAIL = {"日期": 30, "时间": 31}


def normalize_code(code: str) -> str:
    """统一股票代码写法，返回「交易所前缀 + 6 位」，如 600519 → sh600519。

    用户手上有四种常见写法，都收：600519、sh600519、SH600519、600519.SH。
    6 位数字按号段判交易所——6/9 上交所，0/3 深交所，4/8 北交所。
    判不出来就报错，不猜（猜错等于把茅台的价贴到别的票上）。
    """
    c = (code or "").strip().lower().replace(" ", "")
    m = re.fullmatch(r"(sh|sz|bj)(\d{6})", c)
    if m:
        return m.group(1) + m.group(2)
    m = re.fullmatch(r"(\d{6})\.(sh|sz|bj)", c)
    if m:
        return m.group(2) + m.group(1)
    if re.fullmatch(r"\d{6}", c):
        head = c[0]
        if head in "69":
            return "sh" + c
        if head in "03":
            return "sz" + c
        if head in "48":
            return "bj" + c
    raise NetError(f"代码格式应为 600519 或 sh600519，收到：{code}")


def sina(codes: list[str]) -> list[Item]:
    """codes 形如 sh600519 / sz000001，也接受 600519；一次最多 20 个。"""
    if not codes:
        raise NetError("至少给一个代码")
    if len(codes) > 20:
        raise NetError("一次最多 20 个代码")
    codes = [normalize_code(c) for c in codes]
    text = fetch("https://hq.sinajs.cn/list=" + ",".join(codes), interval=1.0,
                 head={"Referer": "https://finance.sina.com.cn"}, codec="gbk")
    out = []
    for m in re.finditer(r'var hq_str_(\w+)="([^"]*)"', text):
        code, body = m.group(1), m.group(2)
        if not body:
            continue
        parts = body.split(",")
        if len(parts) < 10:
            continue
        raw = {k: parts[i] for i, k in enumerate(_SINA_FIELDS)}
        for k, i in _SINA_TAIL.items():
            if i < len(parts):
                raw[k] = parts[i]
        out.append(Item(src="sina", field="market", title=parts[0],
                        ident=code, when=raw.get("日期", ""),
                        link=f"https://finance.sina.com.cn/realstock/company/{code}/nc.shtml",
                        raw=raw))
    if not out:
        raise NetError("没解析到行情，接口格式可能变了")
    return out


def eastmoney(code: str) -> Item:
    """单只快照。默认不开放，需调用方显式确认可用非官方接口。"""
    full = normalize_code(code)
    if full.startswith("bj"):
        raise NetError("北交所代码请用 CSMAR 导出，东方财富通道不覆盖")
    code = full[2:]
    if not code.startswith(("0", "3", "6")):
        raise NetError(f"{full} 不是沪深主板/创业板常见代码段，北交所等请用 CSMAR 导出")
    secid = ("1." if code.startswith("6") else "0.") + code
    url = ("https://push2.eastmoney.com/api/qt/stock/get?"
           + urllib.parse.urlencode({"secid": secid,
                                     "fields": "f43,f57,f58,f169,f170,f60"}))
    data = json.loads(fetch(url, interval=1.0))
    d = data.get("data") or {}
    if not d:
        raise NetError(f"没取到数据，代码可能不存在：{code}")
    return Item(src="eastmoney", field="market", title=str(d.get("f58") or ""),
                ident=str(d.get("f57") or code),
                link=f"https://quote.eastmoney.com/{code}.html",
                raw={"note": "原始字段，单位未换算，写进论文前必须人工核对", "data": d})


# ── 统一分发 ────────────────────────────────────────────────

def fetch_items(source: str, query: str, limit: int = 10, **kw) -> list[Item]:
    if source == "openalex":
        return openalex(query, limit, kw.get("mail", ""))
    if source == "crossref":
        return crossref(query, limit)
    if source == "arxiv":
        return arxiv(query, limit, kw.get("cat", ""))
    if source == "worldbank":
        return worldbank(query, kw.get("country", "CN"), limit)
    if source == "sina":
        return sina([c.strip() for c in re.split(r"[,，\s]+", query) if c.strip()])
    if source == "eastmoney":
        return [eastmoney(query.strip())]
    meta = find(source)
    raise NetError(f"{meta['name']} 是 {meta['level']} 级源，不能自动取："
                   f"{meta['tested']}。请用 tables 解析导出文件。")


def summary() -> dict:
    """供命令行展示：总数、可直连、已实现适配器。"""
    by_field: dict[str, int] = {}
    for x in SOURCES:
        by_field[x["field"]] = by_field.get(x["field"], 0) + 1
    return {
        "total": len(SOURCES),
        "by_field": by_field,
        "adapters": sum(1 for x in SOURCES if x.get("adapter")),
        "direct": sum(1 for x in SOURCES if x["tested"].startswith("直连成功")),
    }
