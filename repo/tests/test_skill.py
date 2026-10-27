"""回归测试：覆盖检测规则的易错点与导出解析的边界。

跑法（在 repo/ 目录下）：
    python -m unittest discover -s tests -v
"""
from __future__ import annotations

import io
import os
import sys
import tempfile
import unittest
import urllib.request

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from zuel import checker, feeds, tables, verify  # noqa: E402
from zuel.model import Level  # noqa: E402


def codes(text: str) -> set[str]:
    return {p.code for p in checker.run(text)}


class StructureTest(unittest.TestCase):
    def test_review_section_is_not_reference_list(self):
        """「文献综述」不能冒充参考文献，否则真缺文献的论文会被判通过。"""
        self.assertIn("ST-01", codes("# 文献综述\n\n## 一、研究回顾\n\n本文认为……\n"))

    def test_reference_heading_accepted(self):
        text = "## 摘要\n正文\n\n## 关键词\nA\n\n## 参考文献\n[1] 张三. 某文[J]. 某刊, 2023.\n"
        self.assertNotIn("ST-01", codes(text))

    def test_content_abstract_accepted(self):
        text = "## 内容摘要\n正文\n\n## 关键词\nA\n\n## 参考文献\n[1] 张三. 某文[J]. 某刊, 2023.\n"
        self.assertNotIn("ST-01", codes(text))

    def test_reference_with_count_accepted(self):
        text = ("## 摘要\n正文\n\n## 关键词\nA\n\n"
                "## 参考文献（共 32 篇）\n[1] 张三. 某文[J]. 某刊, 2023.\n")
        missing = [p.what for p in checker.run(text) if p.code == "ST-01"]
        self.assertFalse([m for m in missing if "参考文献" in m])

    def test_missing_reference_is_warn_level(self):
        """缺参考文献必须报 warn，不能只给 note。"""
        levels = {p.level for p in checker.run("# 文献综述\n\n本文认为……\n") if p.code == "ST-01"}
        self.assertIn(Level.WARN, levels)


class ClaimTest(unittest.TestCase):
    def test_strong_assertion_detected_with_data_word(self):
        """句子里出现「数据」二字，不能把强断言豁免掉。"""
        text = "数字化转型无疑是企业发展的必然选择。毋庸置疑，这一结论极其重要。数据来源于公开数据库。"
        cs = codes(text)
        self.assertIn("R3-01", cs)
        self.assertIn("L-08", cs)

    def test_vague_citation(self):
        self.assertIn("R1-01", codes("有学者指出，数字化转型能促进创新。"))

    def test_future_year(self):
        self.assertIn("R1-03", codes("某项研究（2099）认为该效应显著。"))

    def test_normal_paragraph_has_no_claim_problem(self):
        text = ("本文以 2011—2021 年 A 股上市公司为样本，检验数字化转型对企业创新的影响。"
                "数据来自 CSMAR 数据库，剔除金融业与 ST 公司后得到 12847 个观测值。"
                "基准回归结果显示，数字化转型系数为 0.042，在 1% 水平上显著。"
                "这一结果在更换创新度量方式后依然稳健，说明结论不依赖单一变量构造。")
        cs = codes(text)
        self.assertNotIn("R3-01", cs)
        self.assertNotIn("L-08", cs)


class FormatTest(unittest.TestCase):
    def test_eb_ol_has_doc_type(self):
        line = "[1] 国家统计局. 统计公报[EB/OL]. (2024-02-29)[2024-05-01]. https://x.cn."
        self.assertNotIn("F-02", codes(line))

    def test_missing_doc_type(self):
        line = "[1] 张三. 公司金融. 北京: 中国人民大学出版社, 2020."
        self.assertIn("F-02", codes(line))

    def test_two_self_references(self):
        self.assertIn("S-02", codes("本文认为如此。笔者认为如此。"))


class ExportTest(unittest.TestCase):
    def _tmp(self, content: str, suffix: str = ".txt") -> str:
        fd, path = tempfile.mkstemp(suffix=suffix)
        os.close(fd)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return path

    def test_detect_endnote(self):
        text = "%A 张三\n%T 某文\n%D 2023\n\n%A 李四\n%T 另一篇\n%D 2022\n"
        self.assertEqual(tables.sniff_kind(text), "endnote")

    def test_parse_endnote(self):
        path = self._tmp("%A 张三\n%T 数字化转型\n%J 经济研究\n%D 2023\n")
        items, fmt = tables.read_export(path)
        self.assertEqual(fmt, "endnote")
        self.assertEqual(items[0].title, "数字化转型")
        os.unlink(path)

    def test_gbt_unparsed_kept(self):
        path = self._tmp("[1] 张三. 某文[J]. 某刊, 2023.\n[2] 这条没有类型标识\n")
        items, _ = tables.read_export(path)
        self.assertTrue(any("unparsed" in i.raw for i in items))
        os.unlink(path)

    def test_eb_ol_detected_as_gbt(self):
        text = "[1] 国务院. 某通知[EB/OL]. (2024-01-01)[2024-05-01]. https://www.gov.cn/x.htm."
        self.assertEqual(tables.sniff_kind(text), "gbt")


class PanelTest(unittest.TestCase):
    def test_header_sniff_skips_notice_rows(self):
        """CSMAR 导出前两行是说明，不能当表头。"""
        raw = ("本数据来源于CSMAR数据库\n"
               ",,\n"
               "Stkcd,Accper,TotalAssets\n"
               "000001,2020-12-31,100\n"
               "000002,2020-12-31,200\n")
        fd, path = tempfile.mkstemp(suffix=".csv")
        os.close(fd)
        with open(path, "w", encoding="utf-8") as f:
            f.write(raw)
        data = tables.read_panel(path)
        self.assertEqual(data["head"][0], "Stkcd")
        self.assertEqual(len(data["body"]), 2)
        os.unlink(path)

    def test_profile_counts(self):
        prof = tables.profile(["Stkcd", "Accper", "TotalAssets"],
                              [["000001", "2020-12-31", "100"],
                               ["000002", "2021-12-31", ""],
                               ["000001", "2020-12-31", "100"]])
        self.assertEqual(prof["rows"], 3)
        self.assertEqual(prof["dup"], 1)
        self.assertIn("TotalAssets", prof["missing"])

    def test_empty_file_rejected(self):
        fd, path = tempfile.mkstemp(suffix=".csv")
        os.close(fd)
        with self.assertRaises(ValueError):
            tables.read_panel(path)
        os.unlink(path)

    def test_all_blank_cells_rejected(self):
        fd, path = tempfile.mkstemp(suffix=".csv")
        os.close(fd)
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n,\n,\n")
        with self.assertRaises(ValueError):
            tables.read_panel(path)
        os.unlink(path)

    def test_describe_keeps_placeholders(self):
        prof = tables.profile(["Stkcd", "Accper"], [["000001", "2020-12-31"]])
        draft = tables.describe(prof)
        self.assertIn("[待填", draft)


class RegistryTest(unittest.TestCase):
    def test_sources_declared(self):
        names = {s["name"] for s in feeds.SOURCES}
        for must in ("openalex", "crossref", "arxiv", "cnki", "csmar", "sina"):
            self.assertIn(must, names)

    def test_c_level_not_fetchable(self):
        with self.assertRaises(Exception):
            feeds.fetch_items("cnki", "数字经济")

    def test_unknown_source(self):
        with self.assertRaises(KeyError):
            feeds.find("nope")


class CliTest(unittest.TestCase):
    """命令行契约：出错要给提示与退出码，不能抛栈。"""

    def _run(self, *args):
        import subprocess
        return subprocess.run([sys.executable, "skill.py", *args],
                              cwd=_HERE, capture_output=True, text=True,
                              encoding="utf-8", errors="replace")

    def test_missing_file_exits_2_with_hint(self):
        r = self._run("panel", "no_such_file_xyz.csv")
        self.assertEqual(r.returncode, 2)
        self.assertIn("文件不存在", r.stderr)
        self.assertNotIn("Traceback", r.stderr)

    def test_sources_json_is_valid(self):
        import json
        r = self._run("sources", "--json")
        self.assertEqual(r.returncode, 0)
        data = json.loads(r.stdout)
        self.assertEqual(data["summary"]["total"], 34)
        self.assertEqual(len(data["sources"]), 34)


class NewFixTest(unittest.TestCase):
    """本轮修复项：F-05 编号链、ST-02 论点句、Typrep 警告、编码兜底、西文作者。"""

    def test_f05_missing_number(self):
        t = "## 参考文献\n[1] 张三. 甲[J]. 某刊, 2020.\n[3] 李四. 乙[J]. 某刊, 2021.\n"
        self.assertIn("F-05", codes(t))

    def test_f05_duplicate_number(self):
        t = "## 参考文献\n[1] 张三. 甲[J].\n[1] 张三. 甲[J].\n[2] 李四. 乙[J].\n"
        self.assertIn("F-05", codes(t))

    def test_f05_clean_list_passes(self):
        t = "## 参考文献\n[1] 张三. 甲[J].\n[2] 李四. 乙[J].\n[3] 王五. 丙[M].\n"
        self.assertNotIn("F-05", codes(t))

    def test_thesis_regex_covers_variants(self):
        for line in ["本研究提出，融资约束是关键渠道。",
                     "文章主张以类型化方式处理。",
                     "本文的核心命题是评级具有信息含量。",
                     "笔者试图说明制度变迁的路径。"]:
            self.assertTrue(checker.has_thesis(line), line)

    def test_thesis_regex_rejects_plain_description(self):
        self.assertFalse(checker.has_thesis("本文分为五章，第一章介绍背景。"))

    def test_s02_benyan_not_flagged(self):
        t = "本文认为…\n本研究进一步检验…\n"
        self.assertNotIn("S-02", codes(t))

    def test_s02_we_is_flagged(self):
        t = "本文认为…\n我们认为…\n"
        self.assertIn("S-02", codes(t))

    def test_split_authors_keeps_western_name(self):
        self.assertEqual(tables.split_authors("Smith, J., Brown, A."),
                         ["Smith, J.", "Brown, A."])
        self.assertEqual(tables.split_authors("张三、李四"),
                         ["张三", "李四"])


class PanelReportTypeTest(unittest.TestCase):

    def _prof(self, rows):
        head = ["Stkcd", "Accper", "Typrep"]
        return tables.profile(head, rows)

    def test_report_type_warning_on_mixed(self):
        prof = self._prof([["000001", "2020-12-31", "A"],
                           ["000001", "2020-12-31", "B"]])
        self.assertIn("A", prof["report_type"])
        self.assertIn("B", prof["report_type"])
        self.assertIn("报表类型不唯一", tables.report_type_warning(prof))

    def test_report_type_silent_when_single(self):
        prof = self._prof([["000001", "2020-12-31", "A"],
                           ["000002", "2020-12-31", "A"]])
        self.assertEqual(tables.report_type_warning(prof), "")

    def test_describe_contains_typrep_warning(self):
        prof = self._prof([["000001", "2020-12-31", "A"],
                           ["000001", "2020-12-31", "B"]])
        self.assertIn("Typrep", tables.describe(prof))


class EncodingGuardTest(unittest.TestCase):

    def test_binary_file_rejected(self):
        fd, path = tempfile.mkstemp(suffix=".csv")
        os.close(fd)
        with open(path, "wb") as f:
            f.write(bytes(range(0, 256)) * 4)      # 任意二进制，latin-1 能解开但全是控制符
        try:
            with self.assertRaises(ValueError) as cm:
                tables.read_panel(path)
            self.assertIn("编码识别失败", str(cm.exception))
        finally:
            os.unlink(path)

    def test_normal_utf8_still_reads(self):
        fd, path = tempfile.mkstemp(suffix=".csv")
        os.close(fd)
        with open(path, "w", encoding="utf-8") as f:
            f.write("Stkcd,Accper,Typrep\n000001,2020-12-31,A\n")
        try:
            d = tables.read_panel(path)
            self.assertEqual(d["head"][0], "Stkcd")
        finally:
            os.unlink(path)


class SafeRedirectTest(unittest.TestCase):

    def test_cross_host_redirect_blocked(self):
        from zuel import net
        h = net._SafeRedirect()
        req = urllib.request.Request("https://a.example.com/x",
                                     headers={"Authorization": "Bearer secret"})
        with self.assertRaises(net.NetError) as cm:
            h.redirect_request(req, None, 302, "", {}, "https://b.example.com/y")
        self.assertIn("重定向被拒绝", str(cm.exception))

    def test_http_downgrade_blocked(self):
        from zuel import net
        h = net._SafeRedirect()
        req = urllib.request.Request("https://a.example.com/x")
        with self.assertRaises(net.NetError):
            h.redirect_request(req, None, 301, "", {}, "http://a.example.com/x")


class VerifyTest(unittest.TestCase):
    """文献核验：不联网，用构造的 Crossref 记录测比对逻辑。"""

    def _item(self, **kw):
        from zuel.model import Item
        base = dict(src="cnki", field="literature", title="", people=[], when="", ident="")
        base.update(kw)
        return Item(**base)

    def _rec(self, title, authors=(), year="", volume="", issue="", page=""):
        return {
            "title": [title],
            "author": [{"family": a} for a in authors],
            "issued": {"date-parts": [[int(year)]]} if year else {},
            "volume": volume, "issue": issue, "page": page,
        }

    def test_overlap_scores(self):
        self.assertGreater(verify.overlap("Digital Transformation Strategies",
                                         "Digital Transformation Strategies"), 0.99)
        self.assertLess(verify.overlap("Digital Transformation Strategies",
                                      "A Study of Soil Erosion in Loess Plateau"), 0.2)

    def test_doi_normalized(self):
        self.assertEqual(verify._normalize_doi("https://doi.org/10.1000/xyz."), "10.1000/xyz")
        self.assertEqual(verify._normalize_doi("10.1000/xyz"), "10.1000/xyz")

    def test_title_mismatch_is_block(self):
        item = self._item(title="Digital Transformation Strategies", ident="10.1/x")
        rec = self._rec("Quantum Chromodynamics at Finite Temperature")
        issues = verify.compare(item, rec)
        self.assertEqual(issues[0]["kind"], "doi_mismatch")
        self.assertEqual(issues[0]["level"], "block")

    def test_field_diff_volume(self):
        item = self._item(title="Digital Transformation Strategies",
                          people=["Matt"], when="2015",
                          raw={"volume": "99", "issue": "1", "pages": "1-10"})
        rec = self._rec("Digital Transformation Strategies", ["Matt"], "2015",
                        volume="58", issue="1", page="1-10")
        kinds = [i["kind"] for i in verify.compare(item, rec)]
        self.assertIn("field_diff", kinds)

    def test_order_diff(self):
        item = self._item(title="Digital Transformation Strategies",
                          people=["Benlian", "Matt"], when="2015")
        rec = self._rec("Digital Transformation Strategies", ["Matt", "Benlian"], "2015")
        kinds = [i["kind"] for i in verify.compare(item, rec)]
        self.assertIn("order_diff", kinds)

    def test_clean_record_no_issue(self):
        item = self._item(title="Digital Transformation Strategies",
                          people=["Christian Matt", "Thomas Hess"],
                          when="2015", raw={"volume": "58", "issue": "1", "pages": "1-10"})
        rec = self._rec("Digital Transformation Strategies",
                        ["Matt", "Hess"], "2015", volume="58", issue="1", page="1-10")
        self.assertEqual(verify.compare(item, rec), [])

    def test_non_doi_identifier_counted(self):
        item = self._item(title="某报告", ident="ISBN 978-7-300-00000-0")
        res = verify.verify_items([item])
        self.assertEqual(res["checked"], 0)
        self.assertEqual(res["not_doi"], 1)
        self.assertEqual(res["skipped"], 0)

    def test_normalize_rejects_non_doi(self):
        self.assertEqual(verify._normalize_doi("ISBN 978-7-300"), "")
        self.assertEqual(verify._normalize_doi("doi:10.1000/abc"), "10.1000/abc")

    def test_items_without_doi_are_skipped(self):
        res = verify.verify_items([self._item(title="中文文献")])
        self.assertEqual(res["checked"], 0)
        self.assertEqual(res["skipped"], 1)
        self.assertEqual(res["problems"], [])


class FalsePositiveTest(unittest.TestCase):
    """第三轮：把"报对了"与"报错了"分开——误报会让学生改坏正确写法。"""

    def test_st02_ignores_thesis_outside_intro(self):
        """引言无论点句、结论里出现「本文认为」，仍应报 ST-02（旧版会漏）。"""
        t = "\n# 1 引言\n近年来数字经济快速发展。已有研究关注其影响。\n## 2 文献综述\n张三认为转型促进创新。\n## 5 结论\n本文认为数字化转型显著提升创新。"
        self.assertIn("ST-02", codes(t))

    def test_st02_body_mention_does_not_open_section(self):
        """正文里「如引言所述」不应被当成引言标题，把后半篇吞进来。"""
        t = "\n# 1 引言\n如引言所述，本文研究转型。\n## 2 文献综述\n本文认为已有研究存在缺口。"
        self.assertIn("ST-02", codes(t))

    def test_r1_01_allows_author_year(self):
        """（张三，2020）已经点明作者，不应再判"没说是谁"。"""
        t = "有学者指出（张三，2020），数字化转型提升创新产出。"
        self.assertNotIn("R1-01", codes(t))

    def test_r1_03_ignores_policy_target_year(self):
        """（2035）年远景目标是政策年份，不是引用年份。"""
        t = "按照（2035）年远景目标，基本实现新型工业化。"
        self.assertNotIn("R1-03", codes(t))

    def test_s02_ignores_quoted_we(self):
        """引文里的「我们」是原作者的话，不构成本文自称不统一。"""
        t = "正如作者所言，「我们的发现支持这一假说。」本文进一步检验其边界条件。"
        self.assertNotIn("S-02", codes(t))

    def test_l01_skips_result_paragraph(self):
        """结果段是一串数据陈述，不是"只下判断"。"""
        t = "样本共 3200 个公司年度观测。表 3 报告基准回归结果。列（1）为不加控制变量的结果。列（2）加入企业与年度固定效应。系数在 1% 水平显著为正。"
        self.assertNotIn("L-01", codes(t))

    def test_throttle_spaces_same_host(self):
        """同一站点连续两次请求应有最小间隔（D 级 1 秒/次的实现保证）。"""
        import time as _t
        from zuel import net
        net._LAST.clear()
        start = _t.monotonic()
        net._throttle("https://example.com/a", 0.2)
        net._throttle("https://example.com/b", 0.2)
        self.assertGreaterEqual(_t.monotonic() - start, 0.15)

    def test_panel_skips_chinese_description_row(self):
        """表头下的中文字段名行不该计入观测，也不该混进报表类型分布。"""
        csv = ("Stkcd,Accper,Typrep,Asset\n"
               "证券代码,统计截止日期,报表类型,资产总计\n"
               "000001,2022-12-31,A,100\n"
               "000001,2022-12-31,B,90\n"
               "000002,2022-12-31,A,200\n")
        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False,
                                         encoding="utf-8") as f:
            f.write(csv)
            path = f.name
        try:
            d = tables.read_panel(path)
            prof = tables.profile(d["head"], d["body"])
            self.assertEqual(prof["rows"], 3, "说明行被当成数据，观测数会多 1")
            self.assertEqual(set(prof["report_type"]), {"A", "B"})
        finally:
            os.unlink(path)

    def test_panel_rejects_binary_file(self):
        """二进制文件按扩展名骗过检查时，必须报错而不是算出一份乱码画像。"""
        with tempfile.NamedTemporaryFile("wb", suffix=".csv", delete=False) as f:
            f.write(b"PK\x03\x04" + b"\x00\x01\x02" * 200)
            path = f.name
        try:
            with self.assertRaises(ValueError):
                tables.read_panel(path)
        finally:
            os.unlink(path)

    def test_norm_sample_has_no_problem(self):
        """规范样本必须 0 条问题：误报会让"按提示改"变成"改坏"。"""
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "fixtures", "norm_sample.md")
        with io.open(path, encoding="utf-8") as f:
            res = checker.run(f.read())
        self.assertEqual(res, [], [f"{{p.code}} {{p.what}}" for p in res])


class ExportDoiTest(unittest.TestCase):
    """GB/T 著录尾部的 DOI 要摘出来，否则"核验"命令永远说没 DOI。"""

    def test_gbt_picks_doi_and_cleans_rest(self):
        line = ("[1] 张三. 数字化转型与企业绩效[J]. 管理世界, 2023, 39(4): 88-105. "
                "DOI: 10.1234/abc.2023.001")
        items = tables._from_gbt(line, "cnki")
        self.assertEqual(items[0].ident, "10.1234/abc.2023.001")
        self.assertNotIn("DOI", items[0].raw["rest"], "出处里不该残留 DOI 尾巴")
        self.assertNotIn("10.1234", items[0].raw["rest"])

    def test_gbt_without_doi_stays_empty(self):
        items = tables._from_gbt("[2] 李四. 组织变革[M]. 北京: 中国人民大学出版社, 2021.", "cnki")
        self.assertEqual(items[0].ident, "")

    def test_verify_counts_doi_before_lookup(self):
        from zuel.model import Item
        items = [Item(src="cnki", field="literature", title="甲", ident="10.1234/x"),
                 Item(src="cnki", field="literature", title="乙", ident="978-7-300"),
                 Item(src="cnki", field="literature", title="丙")]
        old = verify.lookup
        verify.lookup = lambda doi, timeout=12.0: None
        try:
            res = verify.verify_items(items)
        finally:
            verify.lookup = old
        self.assertEqual((res["checked"], res["not_doi"], res["skipped"]), (1, 1, 1))
        self.assertEqual(res["problems"][0]["kind"], "doi_missing")


class PanelHeaderTest(unittest.TestCase):
    """CSMAR 也能导出中文表头，此时 A/B 报表混排必须照样报出来。"""

    def test_chinese_header_maps_typrep(self):
        head = ["证券代码", "统计截止日期", "报表类型", "资产总计"]
        body = [["000001", "2023-12-31", "A", "100"],
                ["000002", "2023-12-31", "A", "200"],
                ["000001", "2023-12-31", "B", "101"]]
        prof = tables.profile(head, body)
        self.assertEqual(prof["report_type"], {"A": 2, "B": 1})
        self.assertIn("报表类型不唯一", tables.report_type_warning(prof))

    def test_chinese_header_shows_field_code(self):
        m = dict(tables.field_map(["报表类型"]))
        self.assertTrue(m["报表类型"].startswith("Typrep"), m["报表类型"])

    def test_xlsx_panel_readable(self):
        try:
            import openpyxl
        except ImportError:                      # 没装依赖就跳过，不伪装通过
            self.skipTest("openpyxl 未安装")
        wb = openpyxl.Workbook()
        ws = wb.active
        for r in [["Stkcd", "Accper", "Typrep", "TotalAssets"],
                  ["000001", "2023-12-31", "A", 100],
                  ["000002", "2023-12-31", "B", 200]]:
            ws.append(r)
        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as f:
            path = f.name
        wb.save(path)
        try:
            d = tables.read_panel(path)
            prof = tables.profile(d["head"], d["body"])
            self.assertEqual(prof["rows"], 2)
            self.assertEqual(prof["report_type"], {"A": 1, "B": 1})
        finally:
            os.unlink(path)


class RetryTest(unittest.TestCase):
    """只重试瞬时故障：4xx 与解析错误重试只会拖慢报错。"""

    def test_transient_is_retryable(self):
        from zuel import net
        for msg in ("TimeoutError: The read operation timed out",
                    "RemoteDisconnected: Remote end closed connection",
                    "ConnectionResetError: [Errno 10054] connection reset"):
            self.assertTrue(net.retryable(msg), msg)

    def test_client_error_not_retryable(self):
        from zuel import net
        for msg in ("HTTP 404", "JSONDecodeError: Expecting value", "HTTP 429"):
            self.assertFalse(net.retryable(msg), msg)


class DocxInputTest(unittest.TestCase):
    """学生手上有的是 .docx，让它能直接进检测，而不是先手工转纯文本。"""

    def _make_docx(self, paras, table_cell=""):
        """造一个最小 .docx：只需要 word/document.xml，够测抽取逻辑。"""
        w = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
        body = "".join(f"<w:p><w:r><w:t>{p}</w:t></w:r></w:p>" for p in paras)
        if table_cell:
            body += ("<w:tbl><w:tr><w:tc><w:p><w:r><w:t>"
                     + table_cell + "</w:t></w:r></w:p></w:tc></w:tr></w:tbl>")
        xml = (f'<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w="{w}">'
               f"<w:body>{body}</w:body></w:document>")
        fd, path = tempfile.mkstemp(suffix=".docx")
        os.close(fd)
        import zipfile
        with zipfile.ZipFile(path, "w") as z:
            z.writestr("word/document.xml", xml)
        return path

    def test_paragraphs_stay_separate(self):
        """段落必须断开：合成一行会让行级规则全部失效。"""
        import skill
        path = self._make_docx(["摘要：本文研究数字经济的就业效应。", "关键词：数字经济；就业"])
        try:
            text = skill._docx_text(path)
            self.assertEqual(len(text.splitlines()), 2)
            self.assertIn("关键词：数字经济", text.splitlines()[1])
        finally:
            os.unlink(path)

    def test_table_cell_text_included(self):
        """三线表里的文字也算正文，不能漏掉。"""
        import skill
        path = self._make_docx(["正文一段。"], table_cell="数据来源：CSMAR")
        try:
            self.assertIn("数据来源：CSMAR", skill._docx_text(path))
        finally:
            os.unlink(path)

    def test_renamed_doc_rejected(self):
        """把 .doc 改名成 .docx 是最常见的坑，要给出可操作的提示。"""
        import skill
        fd, path = tempfile.mkstemp(suffix=".docx")
        os.close(fd)
        with open(path, "wb") as f:
            f.write(b"\xd0\xcf\x11\xe0old word binary")
        try:
            with self.assertRaises(SystemExit) as cm:
                skill._read_text(path)
            self.assertIn("不是有效 Word 文件", str(cm.exception))
        finally:
            os.unlink(path)

    def test_doc_still_rejected(self):
        """.doc 扩展名拦截用真实文件测；不存在的路径现在统一报「文件不存在」。"""
        import skill
        fd, path = tempfile.mkstemp(suffix=".doc")
        os.close(fd)
        try:
            with self.assertRaises(SystemExit) as cm:
                skill._read_text(path)
            self.assertIn("二进制格式", str(cm.exception))
        finally:
            os.unlink(path)

    def test_textbox_text_excluded_once(self):
        """文本框不在检测范围（README 边界 5）；且不能因嵌套而重复抽取一遍。"""
        import skill
        w = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
        body = ("<w:p><w:r><w:t>正文一段。</w:t></w:r></w:p>"
                "<w:p><w:r><w:pict><w:txbxContent><w:p><w:r><w:t>框内文字</w:t>"
                "</w:r></w:p></w:txbxContent></w:pict></w:r></w:p>")
        xml = (f'<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w="{w}">'
               f"<w:body>{body}</w:body></w:document>")
        fd, path = tempfile.mkstemp(suffix=".docx")
        os.close(fd)
        import zipfile
        with zipfile.ZipFile(path, "w") as z:
            z.writestr("word/document.xml", xml)
        try:
            text = skill._docx_text(path)
            self.assertIn("正文一段", text)
            self.assertNotIn("框内文字", text)
        finally:
            os.unlink(path)


class CodeNormalizeTest(unittest.TestCase):
    """股票代码：用户手上多是 6 位数字，不该为此报错。"""

    def test_six_digit_gets_prefix(self):
        self.assertEqual(feeds.normalize_code("600519"), "sh600519")
        self.assertEqual(feeds.normalize_code("000001"), "sz000001")
        self.assertEqual(feeds.normalize_code("300750"), "sz300750")
        self.assertEqual(feeds.normalize_code("832000"), "bj832000")

    def test_suffix_and_case_style(self):
        self.assertEqual(feeds.normalize_code("600519.SH"), "sh600519")
        self.assertEqual(feeds.normalize_code("SH600519"), "sh600519")
        self.assertEqual(feeds.normalize_code(" sz000001 "), "sz000001")

    def test_invalid_code_rejected(self):
        from zuel.net import NetError
        for bad in ("60051", "6005190", "abcdef", ""):
            with self.assertRaises(NetError, msg=bad):
                feeds.normalize_code(bad)


class CompletenessTest(unittest.TestCase):
    """无 DOI 的条目核不了真假，但能查出题录自身缺什么。"""

    def _item(self, **kw):
        from zuel.model import Item
        base = dict(src="cnki", field="literature", title="", people=[], when="", ident="")
        base.update(kw)
        return Item(**base)

    def test_missing_year_and_author_flagged(self):
        rows = verify.completeness([self._item(title="数字经济的就业效应")])
        kinds = {i["kind"] for r in rows for i in r["issues"]}
        self.assertIn("no_year", kinds)
        self.assertIn("no_author", kinds)

    def test_future_year_flagged(self):
        from datetime import date
        item = self._item(title="某文", people=["张三"], when="2099")
        kinds = {i["kind"] for r in verify.completeness([item], today=date(2026, 1, 1))
                 for i in r["issues"]}
        self.assertIn("future_year", kinds)

    def test_missing_pages_for_journal(self):
        item = self._item(title="某文", people=["张三"], when="2020",
                          raw={"kind_zh": "期刊"})
        kinds = {i["kind"] for r in verify.completeness([item]) for i in r["issues"]}
        self.assertIn("no_pages", kinds)

    def test_duplicate_reported_once(self):
        a = self._item(title="同一篇", people=["张三"], when="2020")
        b = self._item(title="同一篇", people=["张三"], when="2020")
        rows = verify.completeness([a, b])
        self.assertEqual(sum(1 for r in rows for i in r["issues"]
                             if i["kind"] == "duplicate"), 1)

    def test_clean_item_no_issue(self):
        item = self._item(title="某文", people=["张三"], when="2020",
                          raw={"kind_zh": "期刊", "pages": "12-20"})
        self.assertEqual(verify.completeness([item]), [])


class HeadingAndInputFixTest(unittest.TestCase):
    """本轮修复：BOM、中文序号标题、自家占位符、正文编号、退出码、坏文件。"""

    def test_bom_does_not_break_first_heading(self):
        """带 BOM 的 UTF-8（WPS/记事本另存常见）不能把首行「摘要」变乱码。"""
        import skill
        fd, path = tempfile.mkstemp(suffix=".md")
        os.close(fd)
        with open(path, "w", encoding="utf-8-sig") as f:
            f.write("摘要\n关键词：X\n参考文献\n[1] 张三. A[J]. 某刊, 2020.\n")
        try:
            self.assertNotIn("ST-01", codes(skill._read_text(path)))
        finally:
            os.unlink(path)

    def test_cn_numbered_heading_recognized(self):
        """Word 里手打的「六、参考文献」「【参考文献】」也要认。"""
        for head in ("六、参考文献", "【参考文献】", "## 参考文献"):
            t = f"摘要\n关键词：X\n{head}\n[1] 张三. A[J]. 某刊, 2020.\n"
            self.assertNotIn("ST-01", codes(t), head)

    def test_uppercase_abstract_keywords(self):
        t = "ABSTRACT\nbody\nKEYWORDS: X\nREFERENCES\n[1] A. B[J]. J, 2020.\n"
        self.assertNotIn("ST-01", codes(t))

    def test_r1_02_recognises_panel_placeholder(self):
        """panel 生成的骨架用「[待填：…]」，不能漏报。"""
        self.assertIn("R1-02", codes("期刊名：[待填：期刊全称]\n"))

    def test_f05_body_citation_without_entry(self):
        t = "摘要\n关键词：X\n参考文献\n[1] 张三. A[J]. 某刊, 2020.\n本文经[2]检验。\n"
        self.assertIn("F-05", codes(t))

    def test_f05_range_citation_passes(self):
        t = "摘要\n关键词：X\n参考文献\n[1] A\n[2] B\n[3] C\n方法见[1-3]。\n"
        self.assertNotIn("F-05", codes(t))

    def test_json_exit_code_matches_text(self):
        """--json 不能永远退 0，否则 CI 按退出码判会放行有必改的稿子。"""
        import subprocess
        fd, path = tempfile.mkstemp(suffix=".md")
        os.close(fd)
        with open(path, "w", encoding="utf-8") as f:
            f.write("有学者指出问题。\n")
        try:
            a = subprocess.run([sys.executable, "skill.py", "check", path],
                               cwd=_HERE, capture_output=True)
            b = subprocess.run([sys.executable, "skill.py", "check", path, "--json"],
                               cwd=_HERE, capture_output=True)
            self.assertEqual((a.returncode, b.returncode), (1, 1))
        finally:
            os.unlink(path)

    def test_missing_docx_is_file_not_found(self):
        """文件不存在要报「文件不存在」，不能被误诊成「不是有效 Word 文件」。"""
        import subprocess
        r = subprocess.run([sys.executable, "skill.py", "check", "no_such_xyz.docx"],
                           cwd=_HERE, capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        self.assertEqual(r.returncode, 2)
        self.assertIn("文件不存在", r.stderr)
        self.assertNotIn("Traceback", r.stderr)

    def test_broken_xlsx_no_traceback(self):
        """改名的旧 .xls 或半截下载文件不能把 BadZipFile 栈直接掼给用户。"""
        import subprocess
        fd, path = tempfile.mkstemp(suffix=".xlsx")
        os.close(fd)
        with open(path, "wb") as f:
            f.write(b"not a zip")
        try:
            r = subprocess.run([sys.executable, "skill.py", "panel", path],
                               cwd=_HERE, capture_output=True, text=True,
                               encoding="utf-8", errors="replace")
            self.assertEqual(r.returncode, 2)
            self.assertIn("读不了 .xlsx", r.stderr)
            self.assertNotIn("Traceback", r.stderr)
        finally:
            os.unlink(path)

    def test_unparsed_rows_not_counted_as_literature(self):
        import subprocess
        fd, path = tempfile.mkstemp(suffix=".txt")
        os.close(fd)
        with open(path, "w", encoding="utf-8") as f:
            f.write("[1] 张三. 甲[J]. 某刊, 2020.\n这行没有类型标识\n[2] 李四. 乙[J]. 某刊, 2021.\n")
        try:
            r = subprocess.run([sys.executable, "skill.py", "cites", path],
                               cwd=_HERE, capture_output=True, text=True,
                               encoding="utf-8", errors="replace")
            self.assertIn("解析出 2 条", r.stdout)
            self.assertIn("另有 1 行未识别", r.stdout)
        finally:
            os.unlink(path)


class TitleMatchTest(unittest.TestCase):
    """中文标题没有空格分词：主标题一致、副标题不同，不能判成两篇不同的论文。"""

    def test_cjk_subtitle_is_same_paper(self):
        score = verify.overlap("数字化转型与企业创新",
                               "数字化转型与企业创新——基于融资约束的视角")
        self.assertGreaterEqual(score, 0.99)

    def test_cjk_unrelated_titles_not_same(self):
        self.assertLess(verify.overlap("数字化转型与企业创新", "ESG 表现与融资成本"), 0.3)

    def test_english_subtitle_is_same_paper(self):
        score = verify.overlap("Digital Transformation Strategies",
                               "Digital Transformation Strategies: Evidence from China")
        self.assertGreaterEqual(score, 0.99)

    def test_over_limit_reported(self):
        """--limit 截断必须报出来，不能默默少核几条。"""
        from unittest import mock
        from zuel.model import Item
        items = [Item(src="x", field="literature", title=f"t{i}", ident=f"10.1000/x{i}")
                 for i in range(3)]
        with mock.patch.object(verify, "lookup", return_value={"title": ["t0"]}):
            res = verify.verify_items(items, limit=1)
        self.assertEqual(res["checked"], 1)
        self.assertEqual(res["truncated"], 2)


class RoundFiveFixTest(unittest.TestCase):
    """第四轮修复：新浪日期、OpenAlex 摘要、404 误判、缺页检查、未引条目、
    误诊顺序、docx 解压炸弹与 DTD、直跑测试入口。"""

    def _run_cli(self, *args):
        import subprocess
        return subprocess.run([sys.executable, "skill.py", *args],
                              cwd=_HERE, capture_output=True, text=True,
                              encoding="utf-8", errors="replace")

    def test_sina_raw_has_date_and_when(self):
        """新浪字段表收了 30 位日期后，when 不再恒为空串。"""
        from unittest import mock
        parts = ["贵州茅台"] + ["1"] * 29 + ["2026-10-23", "15:00:00"]
        text = 'var hq_str_sh600519="' + ",".join(parts) + '";\n'
        with mock.patch.object(feeds, "fetch", return_value=text):
            items = feeds.sina(["600519"])
        self.assertEqual(items[0].when, "2026-10-23")
        self.assertEqual(items[0].raw["日期"], "2026-10-23")
        self.assertEqual(items[0].raw["名称"], "贵州茅台")

    def test_openalex_abstract_rebuilt_from_inverted_index(self):
        """OpenAlex 只有倒排摘要，要拼回一段文字，不能永远给空串。"""
        from unittest import mock
        import json
        w = {"title": "T", "publication_year": 2023,
             "abstract_inverted_index": {"B": [1], "A": [0], "C": [2]}}
        with mock.patch.object(feeds, "fetch",
                               return_value=json.dumps({"results": [w]})):
            items = feeds.openalex("q")
        self.assertEqual(items[0].note, "A B C")

    def test_openalex_missing_abstract_stays_empty(self):
        from unittest import mock
        import json
        with mock.patch.object(feeds, "fetch",
                               return_value=json.dumps({"results": [{"title": "T"}]})):
            self.assertEqual(feeds.openalex("q")[0].note, "")

    def test_lookup_only_treats_http_404_as_missing(self):
        """429/超时里恰好含 404 子串，不能被判成「查无此 DOI」。"""
        from unittest import mock
        from zuel import net as netmod
        from zuel.net import NetError
        with mock.patch.object(netmod, "fetch",
                               side_effect=NetError("HTTP 429：https://api.crossref.org/works/10.1111/404.x")):
            with self.assertRaises(NetError):
                verify.lookup("10.1111/404.x")
        with mock.patch.object(netmod, "fetch",
                               side_effect=NetError("HTTP 404：https://api.crossref.org/works/10.1/none")):
            self.assertIsNone(verify.lookup("10.1/none"))

    def test_gbt_journal_missing_pages_flagged(self):
        """GB/T 期刊条目没有起止页，no_pages 检查必须真的报出来。"""
        items = tables._from_gbt("[1] 张三. 某文[J]. 某刊, 2023.", "cnki")
        kinds = {i["kind"] for r in verify.completeness(items) for i in r["issues"]}
        self.assertIn("no_pages", kinds)

    def test_gbt_pages_extracted(self):
        """起止页写进 raw['pages'] 后，完整条目不再误报缺页。"""
        items = tables._from_gbt(
            "[1] 张三. 某文[J]. 管理世界, 2023, 39(4): 88-105.", "cnki")
        self.assertEqual(items[0].raw["pages"], "88-105")
        self.assertEqual(verify.completeness(items), [])

    def test_endnote_journal_missing_pages_flagged(self):
        """EndNote 导出的期刊条目缺页码同样要报（类型值是「期刊论文」）。"""
        from zuel.model import Item
        item = Item(src="cnki", field="literature", title="某文", people=["张三"],
                    when="2020", raw={"kind_zh": "期刊论文"})
        kinds = {i["kind"] for r in verify.completeness([item]) for i in r["issues"]}
        self.assertIn("no_pages", kinds)

    def test_f05_uncited_entries_flagged(self):
        """文末有条目、正文从未引用——SKILL 第 8 节承诺的「表里有但正文没引 → 删」。"""
        t = ("摘要\n关键词：X\n参考文献\n[1] 张三. A[J]. 某刊, 2020.\n"
             "[2] 李四. B[J]. 某刊, 2021.\n正文引用见[1]。\n")
        hits = [p.what for p in checker.run(t) if p.code == "F-05"]
        self.assertTrue(any("正文没引过" in w for w in hits), hits)

    def test_f05_author_year_style_skips_uncited_check(self):
        """作者-年份体例正文没有 [n]，未引条目不查，否则全是误报。"""
        t = ("## 摘要\n张三（2021）认为转型促进创新。\n\n## 参考文献\n"
             "[1] 张三. 某文[J]. 经济研究, 2021, 56(8): 102-118.\n")
        self.assertNotIn("F-05", codes(t))

    def test_missing_binary_file_reports_not_found(self):
        """不存在的 .pdf 应报「文件不存在」，不能误诊成「二进制格式」。"""
        r = self._run_cli("check", "no_such_file_xyz.pdf")
        self.assertEqual(r.returncode, 2)
        self.assertIn("文件不存在", r.stderr)
        self.assertNotIn("二进制格式", r.stderr)

    def test_docx_rejects_dtd_entities(self):
        """正文 XML 带 DTD/实体定义时拒绝解析，不进 ElementTree。"""
        import skill
        import zipfile
        xml = (b'<?xml version="1.0"?><!DOCTYPE w:document ['
               b'<!ENTITY x "y">]><w:document/>')
        fd, path = tempfile.mkstemp(suffix=".docx")
        os.close(fd)
        with zipfile.ZipFile(path, "w") as z:
            z.writestr("word/document.xml", xml)
        try:
            with self.assertRaises(SystemExit) as cm:
                skill._docx_text(path)
            self.assertIn("DTD", str(cm.exception))
        finally:
            os.unlink(path)

    def test_docx_rejects_decompression_bomb(self):
        """解压后超上限的 document.xml 要中止，不能拉爆内存。"""
        import skill
        import zipfile
        xml = b"<?xml version='1.0'?><w:document/>" + b"A" * (33 * 1024 * 1024)
        fd, path = tempfile.mkstemp(suffix=".docx")
        os.close(fd)
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as z:
            z.writestr("word/document.xml", xml)
        try:
            with self.assertRaises(SystemExit) as cm:
                skill._docx_text(path)
            self.assertIn("上限", str(cm.exception))
        finally:
            os.unlink(path)

    def test_arxiv_endpoint_is_https(self):
        """arXiv 请求走 https，不因明文传输被中间人改内容。"""
        import inspect
        self.assertIn("https://export.arxiv.org", inspect.getsource(feeds.arxiv))
        self.assertNotIn("http://export", inspect.getsource(feeds.arxiv))

    def test_unittest_main_guard_after_last_class(self):
        """直跑 test_skill.py 也必须执行全部用例：入口只能放在文件末尾。

        旧版 unittest.main() 在文件中部，其后 8 个类在直跑时永远不执行
        （discover 模式恰好掩盖了这一点）。
        """
        src = io.open(os.path.abspath(__file__), encoding="utf-8").read()
        last_class = src.rfind("class ")
        guard = src.find('if __name__ == "__main__":')
        self.assertGreater(guard, last_class,
                           "unittest.main() 必须放在最后一个测试类之后")


if __name__ == "__main__":
    unittest.main()
