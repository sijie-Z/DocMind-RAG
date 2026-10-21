"""索引侧条款字段抽取 —— 复用 `evals/clauses.py` 的定义位口径（单一来源）。

#188 的设计约束：条号解析全仓**不得有第三套**。现状是两套已对齐的实现
（查询侧 `retriever._parse_clause_number` 与评估侧 `evals.clauses.parse_cn_number`，
由判别测试互相锚定）；索引侧新增的 `clause_no` / `section_no` 抽取直接复用
`evals.clauses.clause_definitions`（golden set 的 ClauseHit 判定就是同一口径），
本模块只做字段整形。

依赖方向说明：`evals` 包自述「纯标准库、不 import app.*」（见 `evals/__init__.py`）；
反向依赖（app → evals）仅此一处，且 evals 随 backend 源码一起分发
（Dockerfile `COPY . .`，无第三方依赖），不会引入新的运行期依赖。
**新增/修改条号口径一律先改 `evals/clauses.py`**，再同步这里的整形逻辑。
"""

from __future__ import annotations

from evals.clauses import clause_definitions


def extract_clause_fields(text: str) -> dict[str, list]:
    """从一段 chunk 正文抽取定义位的结构化条款字段。

    返回：
    - ``clause_no``：``第N条`` 定义位的条号整数列表（中文/阿拉伯数形已归一化，
      按标签排序、去重）——chunk 定义多条时是数组；
    - ``section_no``：``X.Y`` 章节编号定义位的字符串列表（如 ``["2.3"]``）。

    只认**行首定义位**（允许缩进与 markdown ``#`` 前缀）：正文中间的引用
    （「依据第7条」「见 §2.3」）不计入——与 golden set 的命中判定同口径，
    避免「引用某条号的 chunk」被精确腿误当成「定义了该条」。
    """
    clause_nos: list[int] = []
    section_nos: list[str] = []
    for label in clause_definitions(text or ""):
        # clause_definitions 的标签已归一化为「第N条」（N 为阿拉伯数字）或「§X.Y」
        if label.startswith("第") and label.endswith("条"):
            try:
                n = int(label[1:-1])
            except ValueError:  # pragma: no cover - 归一化保证是阿拉伯数字
                continue
            if n not in clause_nos:
                clause_nos.append(n)
        elif label.startswith("§"):
            section = label[1:]
            if section and section not in section_nos:
                section_nos.append(section)
    return {"clause_no": sorted(clause_nos), "section_no": sorted(section_nos)}
