# 论文写作助手 · 工程版

中南财经政法大学人文社科与经管类**本科**论文写作技能的工程实现。

平台版是上层目录的 `SKILL.md`（自包含、零依赖，粘贴进 Comate 即可）；本目录是可运行、可验证的同一套判据，用于证明技能里写的检查项不是纯文字约定。

## 目录

```
repo/
├── skill.py            # 命令行入口
├── zuel/
│   ├── model.py        # 数据结构（Problem / Item / Level）
│   ├── net.py          # 取数：代理、证书、编码、重定向守卫、按站点节流、错误提示
│   ├── checker.py      # 文本检测：22 条确定性规则（+ SYS-01 崩溃提示）
│   ├── feeds.py        # 数据源：文献 / 行情 / 宏观
│   ├── tables.py       # 导出文件解析：题录 / CSMAR 面板
│   ├── verify.py       # 文献核验：DOI → Crossref，逐字段比对
│   └── __init__.py
└── tests/
    ├── test_skill.py   # 回归用例：命令行契约、编码与重定向守卫、文献核验、误报回归
    ├── test_docs.py    # 文档数字与代码实际值的一致性
    └── fixtures/
        └── norm_sample.md   # 61 行规范样本，断言「0 条问题」
```

合计 **115 个用例**。

## 安装与运行

```bash
pip install -r requirements.txt

python -m unittest discover -s tests -v     # 115 个用例
python skill.py check 论文片段.md            # 文本检测
python skill.py sources                     # 数据源清单与实测状态
```

## 八条命令

```bash
# 1. 文本检测：22 条规则，输出「必改 / 建议 / 提示」三级
python skill.py check 论文片段.md
python skill.py check 论文.docx              # 直接读 .docx，不必先转纯文本
python skill.py check 论文片段.md --json

# 2. 数据源清单：34 个源、五级访问模型、逐源实测状态
python skill.py sources

# 3. 文献检索（仅 A 级开放接口）
python skill.py find --source openalex --q "digital transformation"
python skill.py find --source arxiv --q "asset pricing" --cat q-fin.PM
python skill.py find --source crossref --q "ESG financing cost"

# 4. 单只行情（D 级，单次查询）；代码写 600519 也行，自动补交易所前缀
python skill.py quote --code 600519        # sh600519 亦可
python skill.py quote --code 600519 --source eastmoney --allow-unofficial

# 5. 宏观指标（World Bank）
python skill.py macro --indicator NY.GDP.MKTP.CD --country CN

# 6. 导出文件解析（C 级：知网题录 / CSMAR 面板）
python skill.py cites 知网导出.txt
python skill.py panel 资产负债表.csv

# 7. 文献核验：有 DOI 的查 Crossref 比对字段，没 DOI 的查题录完整性
python skill.py verify 知网导出.txt
python skill.py verify 参考文献.md --limit 20 --json
```

## 22 条检测规则

| 组 | 编号 | 查什么 |
|---|---|---|
| 真实性 | R1-01 | 模糊引用（「有学者指出」却没说是谁） |
| | R1-02 | 待补充占位符 |
| | R1-03 | 引用年份晚于当前年份 |
| | R3-01 | 过度断言（无疑、毋庸置疑、极其、必然…） |
| 术语 | T-01 | 同一概念多种写法混用 |
| 格式 | F-01 | 空脚注 |
| | F-02 | 参考文献缺文献类型标识 |
| | F-03 | 中文里用英文标点 |
| | F-04 | 标题编号两套体系混用 |
| | F-05 | 参考文献编号缺号／重号／不连续；正文标 [n] 而文末没有第 n 条；文末有条目正文从未引用（正文用作者-年份体例时不查） |
| 语体 | S-01 | 口语化表述 |
| | S-02 | 自称不统一（「本文」「笔者」与「我们」混用） |
| 论证 | L-01 | 连续多句只下判断、没有推进 |
| | L-02 | 连续多段以并列词开头 |
| | L-03 | 引文后不解释就换话题 |
| | L-04 | 章节开头铺背景、不接上一章 |
| | L-05 | 因果前提未论证 |
| | L-06 | 长句全是叙述、没有分析 |
| | L-07 | 总结句堆砌并列概念 |
| | L-08 | 强断言缺论据 |
| 结构 | ST-01 | 缺摘要／关键词／参考文献 |
| | ST-02 | 引言没有明确论点句 |

单条规则崩溃不会中断整体检查，结果里会给出 `SYS-01` 提示，避免因为一条规则出错而输出「0 个问题」。

## 数据源五级模型

| 级 | 含义 | 做法 | 本仓库 |
|:---:|---|---|---|
| A | 公开开放 | 直接取 | OpenAlex、Crossref、arXiv、World Bank |
| B | 免费 Key | 配 Key 后取 | CORE、FRED（未实现） |
| C | 机构订阅 | **不抓**，只解析导出文件 | 知网、万方、CSMAR |
| D | 无官方接口 | 单次查询，默认关闭 | 新浪、东方财富 |
| M | 手动导出 | 解析用户文件 | EndNote／RefWorks／GB/T 7714 |

**实测口径**：34 个源中当前网络可直连 6 个——OpenAlex、Crossref、arXiv、World Bank、新浪（5 个稳定），加东方财富（非官方接口，时通时断，复测被拒）。国家统计局对脚本请求返回 403，IMF 403、BIS 406、Semantic Scholar 无 Key 时 429——全部如实登记，不宣称「全部可抓」。

## 两个容易踩的细节

**题录里的 DOI 会被摘出来。** 知网/万方的 GB/T 7714 导出常把 DOI 附在末尾（`… 88-105. DOI: 10.xxxx/yyy`）。`cites`/`verify` 会把它写进「标识符」，同时从「出处」里剜掉，避免卷期页混进一串 DOI。摘不出来时 `verify` 只能一律回「没有可核验的条目」，等于核验没做——所以这一步有专门的用例守着。

**中文表头也能读。** CSMAR 允许导出中文字段名（`证券代码/报表类型/资产总计`）。`panel` 用中英文别名双向匹配，字段对照里会回填代码（`报表类型 → Typrep`），A/B 报表混排照样告警。

**没 DOI 不等于没检查。** 中文期刊大多不注册 DOI，这类条目走 `completeness()`：查缺题名、缺年份、年份在未来、缺作者、期刊条目缺页码、题名重复六类硬伤。它只判题录自身是否完整，不判断内容是否属实——输出里写明了这一点，避免把「格式齐全」误读成「文献为真」。

**Word 稿件直接进检测。** `.docx` 是一个 zip，正文在 `word/document.xml`，用标准库解包逐段落取 `w:t`。段落必须逐段断开——整篇压成一行会让所有行级规则同时失效，这条有专门的用例守着。`.doc`／`.pdf` 仍拒绝，提示另存为 `.docx`。

## 网络排查

| 现象 | 处理 |
|---|---|
| `502 Tunnel` | 加 `--direct` 绕过本机代理 |
| `CERTIFICATE_VERIFY_FAILED` | `pip install certifi`；临时调试可加 `--skip-verify` |
| `429` | 配免费 Key，或降低频率 |
| `timed out` | 工具已自动重试 1 次；仍失败多为该站此刻不可达，可加 `--direct` 绕代理后再试 |
| `403` | 站点拒绝脚本，改用网页导出 |

## 边界

1. 中文库（知网、万方）不能自动抓，只解析你导出的题录。
2. 非官方接口（东财、新浪）只做单次查询，不写循环、不绕登录。
3. 规则只能抓字面特征，判断不了论点是否成立——那部分靠 `SKILL.md` 第 7 节的论证断裂清单人工过。
4. `panel` 生成的「数据来源与样本」段落里，剔除规则与最终 N 都是占位符，没做的处理不会被写成「已完成」。
5. `.docx` 只取段落文字，脚注、批注、页眉页脚与文本框不在检测范围；引注格式类规则需要脚注原文时要手工贴入。
