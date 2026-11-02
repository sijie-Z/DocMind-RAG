"""核验 golden set（D5）：10 组人工编写的中文采购合同 V1/V2 与期望值。

gold 只依据合同文本人工判定，不从引擎输出反推；change_type 用产品语义：
- 付款天数 30天→60天 = payment_term modified；
- 只改条款编号、内容不变 = renumbered；
- 裸“日”不写自然日/工作日 = 引擎按设计输出 ambiguous（不猜），gold 记 unidentified；
- 两版都缺失 = unidentified（不是 unchanged）。

抽取准确率的 gold：
- payment_term：value/unit 或 ambiguous/not_found；
- start_condition：events 集合（固定词表，可从文本人工读出）或 not_found；
- acceptance_invoice / exception：仅 status（identified/not_found）——术语集合的
  子串重叠（验收合格 vs 验收）会让“集合相等”变成引擎口径，v1 不做；
- evidence：期望证据所在侧与条款号（人工阅读）。

注意：用例文本刻意保持“每条 < 5 句、整篇 > 500 字”，使生产分块器
（semantic_chunker）走条款切分且不触发 embedding，评测离线且确定性。
"""

from __future__ import annotations

# ── 公共条款（长句用于让整篇 > 500 字，进入条款切分分支）─────────────────

_A1 = (
    "第一条 合同标的：甲方向乙方采购办公设备一批，具体型号、数量与技术参数以附件一"
    "《采购清单》为准，附件与本合同具有同等法律效力，双方均应严格遵照执行，未经双方书面确认不得擅自变更。"
)
_A2 = (
    "第二条 合同价款：本合同总价为人民币壹拾万元整，已包含运输、安装、调试、培训及"
    "税费等全部费用，乙方不得以任何理由另行收取其他费用。"
)
_A5 = (
    "第五条 交付：乙方应于合同生效后三十日内将全部设备交付至甲方指定地点，并提前"
    "三个工作日通知甲方做好接收准备，双方应就交付进度保持书面沟通。"
)
_A6 = (
    "第六条 违约：逾期交付的，每延迟一日按合同金额千分之一支付违约金；逾期超过三十"
    "日的，甲方有权解除合同并要求乙方赔偿全部损失。"
)
_A7 = (
    "第七条 保密：双方对履行本合同过程中知悉的对方商业秘密负有保密义务，未经对方书"
    "面同意不得向任何第三方披露，保密义务不因合同终止而免除，该义务在合同解除后继续有效。"
)
_A8 = (
    "第八条 争议解决：双方协商不成的，提交甲方所在地人民法院诉讼解决，诉讼期间双方"
    "仍应继续履行不受争议影响的义务，实际履行地点为甲方住所地。"
)
_A9_EXTRA = (
    "第九条 其他约定：本合同未尽事宜由双方友好协商解决，任何变更均应以书面形式确认，"
    "口头承诺不构成对本合同的变更。"
)


def _pay(no: str, body: str) -> str:
    return f"第{no}条 付款期限：{body}"


def _acc(no: str, body: str) -> str:
    return f"第{no}条 验收：{body}"


def _doc(*articles: str) -> str:
    return "采购合同（演示）\n" + "\n".join(articles)


def _item(change_type: str, facts: dict | None = None, evidence: tuple[str, int] | None = None):
    return {"change_type": change_type, "facts": facts, "evidence": evidence}


_PAY_30 = {"status": "identified", "value": 30, "unit": "day"}
_PAY_60 = {"status": "identified", "value": 60, "unit": "day"}
_PAY_AMBIG = {"status": "ambiguous"}
_ABSENT = {"status": "not_found"}
_START_ACCEPTED = {"status": "identified", "events": ["验收合格"]}
_ACCEPT_OK = {"status": "identified"}


CASES = [
    {
        "id": "payment_days_modified",
        "note": "付款 30天→60天；起算条件与验收词未变",
        "base": _doc(
            _A1,
            _A2,
            _pay("三", "甲方应在验收合格后30天支付货款。"),
            _acc("四", "乙方交付后10个工作日内完成验收，验收标准以本合同附件二为准。"),
            _A5,
            _A6,
            _A7,
            _A8,
        ),
        "revised": _doc(
            _A1,
            _A2,
            _pay("三", "甲方应在验收合格后60天支付货款。"),
            _acc("四", "乙方交付后10个工作日内完成验收，验收标准以本合同附件二为准。"),
            _A5,
            _A6,
            _A7,
            _A8,
        ),
        "items": {
            "payment_term": _item("modified", _PAY_60, ("revised", 3)),
            "start_condition": _item("unchanged", _START_ACCEPTED, ("revised", 3)),
            "acceptance_invoice": _item("unchanged", _ACCEPT_OK, ("revised", 4)),
            "exception": _item("unidentified", _ABSENT),
        },
    },
    {
        "id": "payment_bare_ri_value_changed",
        "note": "裸『日』：单位不明，但 30→60 的数值变化可确定 → modified（评审 A R1）",
        "base": _doc(
            _A1,
            _A2,
            _pay("三", "甲方应在验收合格后30日内支付货款。"),
            _acc("四", "乙方交付后10个工作日内完成验收，验收标准以本合同附件二为准。"),
            _A5,
            _A6,
            _A7,
            _A8,
        ),
        "revised": _doc(
            _A1,
            _A2,
            _pay("三", "甲方应在验收合格后60日内支付货款。"),
            _acc("四", "乙方交付后10个工作日内完成验收，验收标准以本合同附件二为准。"),
            _A5,
            _A6,
            _A7,
            _A8,
        ),
        "items": {
            "payment_term": _item("modified", _PAY_AMBIG, ("revised", 3)),
            "start_condition": _item("unchanged", _START_ACCEPTED, ("revised", 3)),
            "acceptance_invoice": _item("unchanged", _ACCEPT_OK, ("revised", 4)),
            "exception": _item("unidentified", _ABSENT),
        },
    },
    {
        "id": "payment_bare_ri_value_same",
        "note": "裸『日』且数值相同：单位不明但两版一致 → unchanged（评审 A R1）",
        "base": _doc(
            _A1,
            _A2,
            _pay("三", "甲方应在验收合格后30日内支付货款。"),
            _acc("四", "乙方交付后10个工作日内完成验收，验收标准以本合同附件二为准。"),
            _A5,
            _A6,
            _A7,
            _A8,
        ),
        "revised": _doc(
            _A1,
            _A2,
            _pay("三", "甲方应在验收合格后30日内支付货款。"),
            _acc("四", "乙方交付后10个工作日内完成验收，验收标准以本合同附件二为准。"),
            _A5,
            _A6,
            _A7,
            _A8,
        ),
        "items": {
            "payment_term": _item("unchanged", _PAY_AMBIG, ("revised", 3)),
            "start_condition": _item("unchanged", _START_ACCEPTED, ("revised", 3)),
            "acceptance_invoice": _item("unchanged", _ACCEPT_OK, ("revised", 4)),
            "exception": _item("unidentified", _ABSENT),
        },
    },
    {
        "id": "payment_absent_both",
        "note": "两版都无付款条款 → payment/start 均 unidentified，acceptance unchanged",
        "base": _doc(
            _A1,
            _A2,
            _acc("三", "乙方交付后10个工作日内完成验收，验收标准以本合同附件二为准。"),
            _A5,
            _A6,
            _A7,
            _A8,
            _A9_EXTRA,
        ),
        "revised": _doc(
            _A1,
            _A2,
            _acc("三", "乙方交付后10个工作日内完成验收，验收标准以本合同附件二为准。"),
            _A5,
            _A6,
            _A7,
            _A8,
            _A9_EXTRA,
        ),
        "items": {
            "payment_term": _item("unidentified", _ABSENT),
            "start_condition": _item("unidentified", _ABSENT),
            "acceptance_invoice": _item("unchanged", _ACCEPT_OK, ("revised", 3)),
            "exception": _item("unidentified", _ABSENT),
        },
    },
    {
        "id": "exception_added",
        "note": "V2 新增不可抗力例外条款 → exception added",
        "base": _doc(
            _A1,
            _A2,
            _pay("三", "甲方应在验收合格后30天支付货款。"),
            _acc("四", "乙方交付后10个工作日内完成验收，验收标准以本合同附件二为准。"),
            _A5,
            _A6,
            _A7,
            _A8,
        ),
        "revised": _doc(
            _A1,
            _A2,
            _pay("三", "甲方应在验收合格后30天支付货款。"),
            _acc("四", "乙方交付后10个工作日内完成验收，验收标准以本合同附件二为准。"),
            _A5,
            _A6,
            _A7,
            _A8,
            "第九条 例外：因不可抗力导致验收延迟的，付款期限相应顺延。",
        ),
        "items": {
            "payment_term": _item("unchanged", _PAY_30, ("revised", 3)),
            "start_condition": _item("unchanged", _START_ACCEPTED, ("revised", 3)),
            "acceptance_invoice": _item("unchanged", _ACCEPT_OK, ("revised", 4)),
            "exception": _item("added", {"status": "identified"}, ("revised", 9)),
        },
    },
    {
        "id": "exception_removed",
        "note": "V1 有例外条款、V2 删除 → exception removed（证据在 base 侧）",
        "base": _doc(
            _A1,
            _A2,
            _pay("三", "甲方应在验收合格后30天支付货款。"),
            _acc("四", "乙方交付后10个工作日内完成验收，验收标准以本合同附件二为准。"),
            _A5,
            _A6,
            _A7,
            _A8,
            "第九条 例外：因不可抗力导致验收延迟的，付款期限相应顺延。",
        ),
        "revised": _doc(
            _A1,
            _A2,
            _pay("三", "甲方应在验收合格后30天支付货款。"),
            _acc("四", "乙方交付后10个工作日内完成验收，验收标准以本合同附件二为准。"),
            _A5,
            _A6,
            _A7,
            _A8,
        ),
        "items": {
            "payment_term": _item("unchanged", _PAY_30, ("revised", 3)),
            "start_condition": _item("unchanged", _START_ACCEPTED, ("revised", 3)),
            "acceptance_invoice": _item("unchanged", _ACCEPT_OK, ("revised", 4)),
            "exception": _item("removed", {"status": "identified"}, ("base", 9)),
        },
    },
    {
        "id": "exception_with_duration",
        "note": "新增例外条款含「顺延30天」：payment/acceptance 不应被污染（评审 A R3）",
        "base": _doc(
            _A1,
            _A2,
            _pay("三", "甲方应在验收合格后30天支付货款。"),
            _acc("四", "乙方交付后10个工作日内完成验收，验收标准以本合同附件二为准。"),
            _A5,
            _A6,
            _A7,
            _A8,
        ),
        "revised": _doc(
            _A1,
            _A2,
            _pay("三", "甲方应在验收合格后30天支付货款。"),
            _acc("四", "乙方交付后10个工作日内完成验收，验收标准以本合同附件二为准。"),
            _A5,
            _A6,
            _A7,
            _A8,
            "第九条 例外：因不可抗力导致验收延迟的，付款期限相应顺延30天。",
        ),
        "items": {
            "payment_term": _item("unchanged", _PAY_30, ("revised", 3)),
            "start_condition": _item("unchanged", _START_ACCEPTED, ("revised", 3)),
            "acceptance_invoice": _item("unchanged", _ACCEPT_OK, ("revised", 4)),
            "exception": _item("added", {"status": "identified"}, ("revised", 9)),
        },
    },
    {
        "id": "start_condition_modified",
        "note": "起算事件 验收合格→收到发票（付款天数不变）",
        "base": _doc(
            _A1,
            _A2,
            _pay("三", "甲方应在验收合格后30天支付货款。"),
            _acc("四", "乙方交付后10个工作日内完成验收，验收标准以本合同附件二为准。"),
            _A5,
            _A6,
            _A7,
            _A8,
        ),
        "revised": _doc(
            _A1,
            _A2,
            _pay("三", "甲方应在收到发票后30天支付货款。"),
            _acc("四", "乙方交付后10个工作日内完成验收，验收标准以本合同附件二为准。"),
            _A5,
            _A6,
            _A7,
            _A8,
        ),
        "items": {
            "payment_term": _item("unchanged", _PAY_30, ("revised", 3)),
            "start_condition": _item(
                "modified", {"status": "identified", "events": ["收到发票"]}, ("revised", 3)
            ),
            "acceptance_invoice": _item("modified", {"status": "identified"}, ("revised", 3)),
            "exception": _item("unidentified", _ABSENT),
        },
    },
    {
        "id": "acceptance_terms_modified",
        "note": "付款条件新增『对账无误』→ acceptance/start 变化，付款天数不变",
        "base": _doc(
            _A1,
            _A2,
            _pay("三", "甲方应在验收合格后30天支付货款。"),
            _acc("四", "乙方交付后10个工作日内完成验收，验收标准以本合同附件二为准。"),
            _A5,
            _A6,
            _A7,
            _A8,
        ),
        "revised": _doc(
            _A1,
            _A2,
            _pay("三", "甲方应在验收合格并对账无误后30天支付货款。"),
            _acc("四", "乙方交付后10个工作日内完成验收，验收标准以本合同附件二为准。"),
            _A5,
            _A6,
            _A7,
            _A8,
        ),
        "items": {
            "payment_term": _item("unchanged", _PAY_30, ("revised", 3)),
            "start_condition": _item(
                "modified",
                {"status": "identified", "events": ["验收合格", "对账无误"]},
                ("revised", 3),
            ),
            "acceptance_invoice": _item("modified", {"status": "identified"}, ("revised", 3)),
            "exception": _item("unidentified", _ABSENT),
        },
    },
    {
        "id": "renumbered_only",
        "note": "插入定义条款并顺延编号；四个核验项内容不变 → renumbered",
        "base": _doc(
            _A1,
            _A2,
            _pay("三", "甲方应在验收合格后30天支付货款。"),
            _acc("四", "乙方交付后10个工作日内完成验收，验收标准以本合同附件二为准。"),
            _A5,
            _A6,
            _A7,
            _A8,
        ),
        "revised": _doc(
            _A1,
            _A2,
            "第三条 定义：本合同中『工作日』指法定工作日，『天』指自然日。",
            _pay("四", "甲方应在验收合格后30天支付货款。"),
            _acc("五", "乙方交付后10个工作日内完成验收，验收标准以本合同附件二为准。"),
            "第六条 " + _A5.split(" ", 1)[1],
            "第七条 " + _A6.split(" ", 1)[1],
            "第八条 " + _A7.split(" ", 1)[1],
            "第九条 " + _A8.split(" ", 1)[1],
        ),
        "items": {
            "payment_term": _item("renumbered", _PAY_30, ("revised", 4)),
            "start_condition": _item("renumbered", _START_ACCEPTED, ("revised", 4)),
            "acceptance_invoice": _item("renumbered", _ACCEPT_OK, ("revised", 5)),
            "exception": _item("unidentified", _ABSENT),
        },
    },
    {
        "id": "unchanged_all",
        "note": "两版完全一致 → 三项 unchanged，缺失项 unidentified",
        "base": _doc(
            _A1,
            _A2,
            _pay("三", "甲方应在验收合格后30天支付货款。"),
            _acc("四", "乙方交付后10个工作日内完成验收，验收标准以本合同附件二为准。"),
            _A5,
            _A6,
            _A7,
            _A8,
        ),
        "revised": _doc(
            _A1,
            _A2,
            _pay("三", "甲方应在验收合格后30天支付货款。"),
            _acc("四", "乙方交付后10个工作日内完成验收，验收标准以本合同附件二为准。"),
            _A5,
            _A6,
            _A7,
            _A8,
        ),
        "items": {
            "payment_term": _item("unchanged", _PAY_30, ("revised", 3)),
            "start_condition": _item("unchanged", _START_ACCEPTED, ("revised", 3)),
            "acceptance_invoice": _item("unchanged", _ACCEPT_OK, ("revised", 4)),
            "exception": _item("unidentified", _ABSENT),
        },
    },
    {
        "id": "acceptance_days_modified",
        "note": "验收 10个工作日→15个工作日；付款条款未动",
        "base": _doc(
            _A1,
            _A2,
            _pay("三", "甲方应在验收合格后30天支付货款。"),
            _acc("四", "乙方交付后10个工作日内完成验收，验收标准以本合同附件二为准。"),
            _A5,
            _A6,
            _A7,
            _A8,
        ),
        "revised": _doc(
            _A1,
            _A2,
            _pay("三", "甲方应在验收合格后30天支付货款。"),
            _acc("四", "乙方交付后15个工作日内完成验收，验收标准以本合同附件二为准。"),
            _A5,
            _A6,
            _A7,
            _A8,
        ),
        "items": {
            "payment_term": _item("unchanged", _PAY_30, ("revised", 3)),
            "start_condition": _item("unchanged", _START_ACCEPTED, ("revised", 3)),
            "acceptance_invoice": _item("modified", _ACCEPT_OK, ("revised", 4)),
            "exception": _item("unidentified", _ABSENT),
        },
    },
    {
        "id": "acceptance_first_payment_invoice",
        "note": "验收条款在前、付款条款含发票；验收期限 10→15 变化（评审 A 回归场景）",
        "base": _doc(
            _A1,
            _A2,
            _acc("三", "乙方交付后10个工作日内完成验收，验收标准以本合同附件二为准。"),
            _pay("四", "甲方应在收到发票后30天支付货款。"),
            _A5,
            _A6,
            _A7,
            _A8,
        ),
        "revised": _doc(
            _A1,
            _A2,
            _acc("三", "乙方交付后15个工作日内完成验收，验收标准以本合同附件二为准。"),
            _pay("四", "甲方应在收到发票后30天支付货款。"),
            _A5,
            _A6,
            _A7,
            _A8,
        ),
        "items": {
            "payment_term": _item("unchanged", _PAY_30, ("revised", 4)),
            "start_condition": _item(
                "unchanged", {"status": "identified", "events": ["收到发票"]}, ("revised", 4)
            ),
            "acceptance_invoice": _item("modified", _ACCEPT_OK, ("revised", 3)),
            "exception": _item("unidentified", _ABSENT),
        },
    },
    {
        "id": "early_exception_clause_hijack",
        "note": "前部新增含「验收」的例外条款 + 付款起算改发票；主证据不得被例外条款劫持",
        "base": _doc(
            _A1,
            _A2,
            _pay("三", "甲方应在验收合格后30天支付货款。"),
            _acc("四", "乙方交付后10个工作日内完成验收，验收标准以本合同附件二为准。"),
            _A5,
            _A6,
            _A7,
            _A8,
        ),
        "revised": _doc(
            _A1,
            _A2,
            "第三条 例外：因不可抗力导致验收延迟的，付款期限相应顺延。",
            _pay("四", "甲方应在收到发票后30天支付货款。"),
            _acc("五", "乙方交付后10个工作日内完成验收，验收标准以本合同附件二为准。"),
            "第六条 " + _A5.split(" ", 1)[1],
            "第七条 " + _A6.split(" ", 1)[1],
            "第八条 " + _A7.split(" ", 1)[1],
            "第九条 " + _A8.split(" ", 1)[1],
        ),
        "items": {
            "payment_term": _item("renumbered", _PAY_30, ("revised", 4)),
            "start_condition": _item(
                "modified", {"status": "identified", "events": ["收到发票"]}, ("revised", 4)
            ),
            "acceptance_invoice": _item("modified", _ACCEPT_OK, ("revised", 4)),
            "exception": _item("added", {"status": "identified"}, ("revised", 3)),
        },
    },
    {
        "id": "cn_numeral_workday_modified",
        "note": "中文数字期限：三十个工作日→六十个工作日",
        "base": _doc(
            _A1,
            _A2,
            _pay("三", "甲方应在验收合格后三十个工作日内支付货款。"),
            _acc("四", "乙方交付后10个工作日内完成验收，验收标准以本合同附件二为准。"),
            _A5,
            _A6,
            _A7,
            _A8,
        ),
        "revised": _doc(
            _A1,
            _A2,
            _pay("三", "甲方应在验收合格后六十个工作日内支付货款。"),
            _acc("四", "乙方交付后10个工作日内完成验收，验收标准以本合同附件二为准。"),
            _A5,
            _A6,
            _A7,
            _A8,
        ),
        "items": {
            "payment_term": _item(
                "modified",
                {"status": "identified", "value": 60, "unit": "workday"},
                ("revised", 3),
            ),
            "start_condition": _item("unchanged", _START_ACCEPTED, ("revised", 3)),
            "acceptance_invoice": _item("unchanged", _ACCEPT_OK, ("revised", 4)),
            "exception": _item("unidentified", _ABSENT),
        },
    },
    {
        "id": "cn_numeral_bare_ri_modified",
        "note": "中文数字裸『日』：三十日→六十日，数值可确定但单位不猜",
        "base": _doc(
            _A1,
            _A2,
            _pay("三", "甲方应在验收合格后三十日内支付货款。"),
            _acc("四", "乙方交付后10个工作日内完成验收，验收标准以本合同附件二为准。"),
            _A5,
            _A6,
            _A7,
            _A8,
        ),
        "revised": _doc(
            _A1,
            _A2,
            _pay("三", "甲方应在验收合格后六十日内支付货款。"),
            _acc("四", "乙方交付后10个工作日内完成验收，验收标准以本合同附件二为准。"),
            _A5,
            _A6,
            _A7,
            _A8,
        ),
        "items": {
            "payment_term": _item("modified", _PAY_AMBIG, ("revised", 3)),
            "start_condition": _item("unchanged", _START_ACCEPTED, ("revised", 3)),
            "acceptance_invoice": _item("unchanged", _ACCEPT_OK, ("revised", 4)),
            "exception": _item("unidentified", _ABSENT),
        },
    },
    {
        "id": "shared_term_acceptance_change",
        "note": "付款条款写「验收后」共享术语；验收条款自身 10→15 必须 modified（S1）",
        "base": _doc(
            _A1,
            _A2,
            _pay("三", "甲方应在验收后30天支付货款。"),
            _acc("四", "乙方交付后10个工作日内完成验收，验收标准以本合同附件二为准。"),
            _A5,
            _A6,
            _A7,
            _A8,
        ),
        "revised": _doc(
            _A1,
            _A2,
            _pay("三", "甲方应在验收后30天支付货款。"),
            _acc("四", "乙方交付后15个工作日内完成验收，验收标准以本合同附件二为准。"),
            _A5,
            _A6,
            _A7,
            _A8,
        ),
        "items": {
            "payment_term": _item("unchanged", _PAY_30, ("revised", 3)),
            "start_condition": _item("unidentified", _ABSENT),
            "acceptance_invoice": _item("modified", _ACCEPT_OK, ("revised", 4)),
            "exception": _item("unidentified", _ABSENT),
        },
    },
    {
        "id": "acceptance_second_clause_added",
        "note": "V2 新增第二条验收条款（同术语）；验收项必须 modified（S1）",
        "base": _doc(
            _A1,
            _A2,
            _pay("三", "甲方应在验收合格后30天支付货款。"),
            _acc("四", "乙方交付后10个工作日内完成验收，验收标准以本合同附件二为准。"),
            _A5,
            _A6,
            _A7,
            _A8,
        ),
        "revised": _doc(
            _A1,
            _A2,
            _pay("三", "甲方应在验收合格后30天支付货款。"),
            _acc("四", "乙方交付后10个工作日内完成验收，验收标准以本合同附件二为准。"),
            _A5,
            _A6,
            _A7,
            _A8,
            "第九条 验收：甲方书面确认后方视为验收完成。",
        ),
        "items": {
            "payment_term": _item("unchanged", _PAY_30, ("revised", 3)),
            "start_condition": _item("unchanged", _START_ACCEPTED, ("revised", 3)),
            "acceptance_invoice": _item("modified", _ACCEPT_OK, ("revised", 9)),
            "exception": _item("unidentified", _ABSENT),
        },
    },
    {
        "id": "no_colon_exception_article",
        "note": "无冒号的独立例外条款（第九条 例外 …）不得污染付款项",
        "base": _doc(
            _A1,
            _A2,
            _pay("三", "甲方应在验收合格后30天支付货款。"),
            _acc("四", "乙方交付后10个工作日内完成验收，验收标准以本合同附件二为准。"),
            _A5,
            _A6,
            _A7,
            _A8,
        ),
        "revised": _doc(
            _A1,
            _A2,
            _pay("三", "甲方应在验收合格后30天支付货款。"),
            _acc("四", "乙方交付后10个工作日内完成验收，验收标准以本合同附件二为准。"),
            _A5,
            _A6,
            _A7,
            _A8,
            "第九条 例外 因不可抗力导致付款延迟的，付款期限相应顺延30天。",
        ),
        "items": {
            "payment_term": _item("unchanged", _PAY_30, ("revised", 3)),
            "start_condition": _item("unchanged", _START_ACCEPTED, ("revised", 3)),
            "acceptance_invoice": _item("unchanged", _ACCEPT_OK, ("revised", 4)),
            "exception": _item("added", {"status": "identified"}, ("revised", 9)),
        },
    },
    {
        "id": "cn_month_modified",
        "note": "中文数字月份：三个月→六个月",
        "base": _doc(
            _A1,
            _A2,
            _pay("三", "甲方应在验收合格后三个月内支付货款。"),
            _acc("四", "乙方交付后10个工作日内完成验收，验收标准以本合同附件二为准。"),
            _A5,
            _A6,
            _A7,
            _A8,
        ),
        "revised": _doc(
            _A1,
            _A2,
            _pay("三", "甲方应在验收合格后六个月内支付货款。"),
            _acc("四", "乙方交付后10个工作日内完成验收，验收标准以本合同附件二为准。"),
            _A5,
            _A6,
            _A7,
            _A8,
        ),
        "items": {
            "payment_term": _item(
                "modified", {"status": "identified", "value": 6, "unit": "month"}, ("revised", 3)
            ),
            "start_condition": _item("unchanged", _START_ACCEPTED, ("revised", 3)),
            "acceptance_invoice": _item("unchanged", _ACCEPT_OK, ("revised", 4)),
            "exception": _item("unidentified", _ABSENT),
        },
    },
    {
        "id": "connected_predicate_payment",
        "note": "连谓结构：30天内完成对账并支付，付款天数变化不得污染验收",
        "base": _doc(
            _A1,
            _A2,
            _pay("三", "甲方应在验收合格后30天内完成对账并支付货款。"),
            _acc("四", "乙方交付后10个工作日内完成验收，验收标准以本合同附件二为准。"),
            _A5,
            _A6,
            _A7,
            _A8,
        ),
        "revised": _doc(
            _A1,
            _A2,
            _pay("三", "甲方应在验收合格后60天内完成对账并支付货款。"),
            _acc("四", "乙方交付后10个工作日内完成验收，验收标准以本合同附件二为准。"),
            _A5,
            _A6,
            _A7,
            _A8,
        ),
        "items": {
            "payment_term": _item("modified", _PAY_60, ("revised", 3)),
            "start_condition": _item("unchanged", _START_ACCEPTED, ("revised", 3)),
            "acceptance_invoice": _item("unchanged", _ACCEPT_OK, ("revised", 4)),
            "exception": _item("unidentified", _ABSENT),
        },
    },
    {
        "id": "delivery_duration_not_payment",
        "note": "交付期限 10→15 个工作日：不得被付款锚点吸走",
        "base": _doc(
            _A1,
            _A2,
            _pay("三", "甲方应在验收合格后30天支付货款。"),
            _acc("四", "乙方应在10个工作日内交付，甲方随后付款。"),
            _A5,
            _A6,
            _A7,
            _A8,
        ),
        "revised": _doc(
            _A1,
            _A2,
            _pay("三", "甲方应在验收合格后30天支付货款。"),
            _acc("四", "乙方应在15个工作日内交付，甲方随后付款。"),
            _A5,
            _A6,
            _A7,
            _A8,
        ),
        "items": {
            "payment_term": _item("unchanged", _PAY_30, ("revised", 3)),
            "start_condition": _item("unchanged", _START_ACCEPTED, ("revised", 3)),
            "acceptance_invoice": _item("modified", _ACCEPT_OK, ("revised", 4)),
            "exception": _item("unidentified", _ABSENT),
        },
    },
    {
        "id": "payment_after_delivery_event",
        "note": "交付触发付款：交付后 5→10 个工作日内支付，付款项必须 modified（A-1）",
        "base": _doc(
            _A1,
            _A2,
            _pay("三", "甲方应在交付后5个工作日内支付货款。"),
            _acc("四", "乙方交付后10个工作日内完成验收，验收标准以本合同附件二为准。"),
            _A5,
            _A6,
            _A7,
            _A8,
        ),
        "revised": _doc(
            _A1,
            _A2,
            _pay("三", "甲方应在交付后10个工作日内支付货款。"),
            _acc("四", "乙方交付后10个工作日内完成验收，验收标准以本合同附件二为准。"),
            _A5,
            _A6,
            _A7,
            _A8,
        ),
        "items": {
            "payment_term": _item(
                "modified", {"status": "identified", "value": 10, "unit": "workday"}, ("revised", 3)
            ),
            "start_condition": _item("unidentified", _ABSENT),
            "acceptance_invoice": _item("unchanged", _ACCEPT_OK, ("revised", 4)),
            "exception": _item("unidentified", _ABSENT),
        },
    },
]
