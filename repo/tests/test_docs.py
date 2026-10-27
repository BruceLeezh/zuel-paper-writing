"""文档一致性：文档里写的数字必须与代码实际情况一致。

这类漂移不会让程序崩溃，但会让评审看到自相矛盾的说法（比如文档写 48 个
用例、实际跑出 56 个）。改代码时忘了同步文档，就由这组用例兜住。

工程版可能放在提交包里，也可能单独放在包外，所以这里会自己找一遍：
只看 `repo/` 的父目录与其同级目录，找到含 `知识库/` 的那一层就是交付包；
找不到就跳过本组用例。不做全盘搜索——临时目录里的旧解压副本会把校验带偏。

跑法（在 repo/ 目录下）：
    python -m unittest tests.test_docs -v
"""
from __future__ import annotations

import io
import os
import re
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))          # repo/tests
_REPO = os.path.dirname(_HERE)                              # repo


def _read(path: str) -> str:
    with io.open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


def _kb_dir(pkg):
    """知识库目录，兼容 知识库/ 与 02-知识库/ 两种叫法。"""
    for name in ("知识库", "02-知识库"):
        d = os.path.join(pkg, name)
        if os.path.isdir(d):
            return d
    return None


def _find_pkg():
    """找技能目录：含知识库的那一层。

    只认与 repo/ 相邻的两处——repo 的父目录、父目录的同级子目录——
    不再向上无限搜索：临时目录里若存着旧解压副本，会被误当成交付包，
    导致拿旧文档校验新代码。
    """
    up = os.path.dirname(_REPO)                    # 技能目录（repo 的父级）
    deliver = os.path.dirname(up)                  # 交付根
    cands = [up, deliver]
    if os.path.isdir(deliver):
        cands += [os.path.join(deliver, n) for n in sorted(os.listdir(deliver))
                  if os.path.isdir(os.path.join(deliver, n))]
    for c in cands:
        if _kb_dir(c):
            return c
    return None


def _skill_md(pkg):
    """技能正文：包内 SKILL.md 优先，兼容旧的副本命名。"""
    for rel in ("SKILL.md", "01-技能正文.md", "技能正文副本.md"):
        p = os.path.join(pkg, rel)
        if os.path.isfile(p):
            return p
    return None


def _docs(pkg):
    """收集需要校验的文档：交付层 md、技能目录 md、知识库 md、repo/README、包外审查材料。"""
    out = {}
    deliver = os.path.dirname(pkg)              # 与技能目录同级的交付根
    for base, tag in ((deliver, ""), (pkg, "")):
        if not os.path.isdir(base):
            continue
        for name in os.listdir(base):
            p = os.path.join(base, name)
            if os.path.isfile(p) and name.endswith(".md"):
                out[(tag + name)] = _read(p)
    kb = _kb_dir(pkg)
    if kb:
        for name in os.listdir(kb):
            if name.endswith(".md"):
                out["知识库/" + name] = _read(os.path.join(kb, name))
    readme = os.path.join(_REPO, "README.md")
    if os.path.isfile(readme):
        out["repo/README.md"] = _read(readme)
    ext = os.path.join(deliver, "_包外审查材料")
    if os.path.isdir(ext):
        for name in os.listdir(ext):
            if name.endswith(".md"):
                out["_包外审查材料/" + name] = _read(os.path.join(ext, name))
    return out


def _actual_test_count() -> int:
    n = 0
    for name in os.listdir(_HERE):
        if name.startswith("test_") and name.endswith(".py"):
            n += len(re.findall(r"^\s+def test_", _read(os.path.join(_HERE, name)), re.M))
    return n


class DocNumberTest(unittest.TestCase):
    """文档里的规则数／用例数／正文行数，必须与代码一致。"""

    @classmethod
    def setUpClass(cls):
        pkg = _find_pkg()
        if pkg is None:
            raise unittest.SkipTest("没找到含知识库的目录，跳过文档一致性检查")
        cls.pkg = pkg
        cls.docs = _docs(pkg)
        from zuel import checker
        cls.rules = len(checker.RULES)
        cls.tests = _actual_test_count()
        main = _skill_md(pkg)
        cls.lines = len(_read(main).splitlines()) if main else 0

    def test_docs_found(self):
        self.assertGreaterEqual(len(self.docs), 6, "没找到足够的文档，路径解析可能有问题")

    def test_rule_count_consistent(self):
        pat = re.compile(r"(\d+) 条(?:规则|检测规则|确定性规则)")
        bad = [f"{name}: 写 {m.group(1)} 条，实际 {self.rules} 条"
               for name, text in self.docs.items() for m in pat.finditer(text)
               if int(m.group(1)) != self.rules]
        self.assertEqual(bad, [], "规则数不一致：\n" + "\n".join(bad))

    def test_test_count_consistent(self):
        pat = re.compile(r"(?:Ran )?(\d+) 个?(?:回归)?(?:用例|测试)|Ran (\d+) tests")
        bad = []
        for name, text in self.docs.items():
            for m in pat.finditer(text):
                got = int(m.group(1) or m.group(2))
                if got != self.tests:
                    bad.append(f"{name}: 写 {got}，实际 {self.tests}")
        self.assertEqual(bad, [], "用例数不一致：\n" + "\n".join(bad))

    def test_main_length_consistent(self):
        pat = re.compile(r"(\d{3}) 行")
        bad = [f"{name}: 写 {m.group(1)} 行，实际 {self.lines} 行"
               for name, text in self.docs.items() for m in pat.finditer(text)
               if int(m.group(1)) != self.lines]
        self.assertEqual(bad, [], "正文行数不一致：\n" + "\n".join(bad))

    def test_knowledge_base_complete(self):
        kb = _kb_dir(self.pkg)
        self.assertIsNotNone(kb, "找不到知识库目录")
        names = sorted(n for n in os.listdir(kb) if n.endswith(".md"))
        self.assertEqual(len(names), 5, f"知识库应为 5 份，实际 {names}")
        for i in range(1, 6):
            self.assertTrue(any(n.startswith(f"K{i}-") for n in names), f"缺 K{i}")

    def test_engineering_present(self):
        """工程版是作品可信度的证据，应当留在包内。"""
        self.assertTrue(os.path.isdir(os.path.join(self.pkg, "repo")),
                        "包内找不到 repo/，判据就只剩文字声明了")


if __name__ == "__main__":
    unittest.main()
