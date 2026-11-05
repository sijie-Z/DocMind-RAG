"""核验 golden set（D5）：70 组人工编写的中文采购合同 V1/V2 与期望值。

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
    {
        "id": "payment_clause_reference_definition",
        "note": "付款条款显式引用第四条，第四条只有期限无付款词：付款项须识别 30 天（已知问题 #1）",
        "base": _doc(
            _A1,
            _A2,
            _pay("三", "甲方以银行转账方式支付，具体账期按第四条执行，收款账户以附件三为准。"),
            "第四条 期限：验收合格后30天内，双方应完成内部审批与用印手续，"
            "相关安排以本条约定为准，任何变更须经双方书面确认后方可执行。",
            _A5,
            _A6,
            _A7,
            _A8,
        ),
        "revised": _doc(
            _A1,
            _A2,
            _pay("三", "甲方以银行转账方式支付，具体账期按第四条执行，收款账户以附件三为准。"),
            "第四条 期限：验收合格后30天内，双方应完成内部审批与用印手续，"
            "相关安排以本条约定为准，任何变更须经双方书面确认后方可执行。",
            _A5,
            _A6,
            _A7,
            _A8,
        ),
        "items": {
            "payment_term": _item("unchanged", _PAY_30, ("revised", 4)),
            "start_condition": _item("unidentified", _ABSENT),
            "acceptance_invoice": _item("unchanged", _ACCEPT_OK, ("revised", 4)),
            "exception": _item("unidentified", _ABSENT),
        },
    },
    {
        "id": "payment_parallel_reference_targets",
        "note": "并列引用第九条、第十条：首期/尾款两个期限都可见；尾款 60→90 必须 modified",
        "base": _doc(
            _A1,
            _A2,
            _pay("三", "付款期限分别按第九条、第十条执行。"),
            "第九条 期限：首期款于验收合格后30天内结清，具体以财务确认时间为准，"
            "双方应配合完成对账。",
            "第十条 期限：尾款于交货后60天内结清，相关单据应齐全并经双方书面确认。",
            _A5,
            _A6,
            _A7,
            _A8,
        ),
        "revised": _doc(
            _A1,
            _A2,
            _pay("三", "付款期限分别按第九条、第十条执行。"),
            "第九条 期限：首期款于验收合格后30天内结清，具体以财务确认时间为准，"
            "双方应配合完成对账。",
            "第十条 期限：尾款于交货后90天内结清，相关单据应齐全并经双方书面确认。",
            _A5,
            _A6,
            _A7,
            _A8,
        ),
        "items": {
            "payment_term": _item("modified", {"status": "ambiguous"}, ("revised", 10)),
            "start_condition": _item("unidentified", _ABSENT),
            "acceptance_invoice": _item("unchanged", _ACCEPT_OK, ("revised", 9)),
            "exception": _item("unidentified", _ABSENT),
        },
    },
    {
        "id": "payment_reference_excludes_invoicing_term",
        "note": "账期引用开票条款，但 10 个工作日是开票期限：付款项必须保持未识别（评审 B R2）",
        "base": _doc(
            _A1,
            _A2,
            _pay("三", "甲方应在收到发票后付款，具体账期详见第四条。"),
            "第四条 开票：乙方应在交付后10个工作日内开具发票，发票内容应与合同"
            "约定一致，双方应妥善保管相关凭证并配合完成核验。",
            _A5,
            _A6,
            _A7,
            _A8,
        ),
        "revised": _doc(
            _A1,
            _A2,
            _pay("三", "甲方应在收到发票后付款，具体账期详见第四条。"),
            "第四条 开票：乙方应在交付后10个工作日内开具发票，发票内容应与合同"
            "约定一致，双方应妥善保管相关凭证并配合完成核验。",
            _A5,
            _A6,
            _A7,
            _A8,
        ),
        "items": {
            "payment_term": _item("unidentified", _ABSENT),
            "start_condition": _item(
                "unchanged", {"status": "identified", "events": ["收到发票"]}, ("revised", 3)
            ),
            "acceptance_invoice": _item("unchanged", _ACCEPT_OK, ("revised", 3)),
            "exception": _item("unidentified", _ABSENT),
        },
    },
    {
        "id": "payment_prepay_and_balance_public_template",
        "note": "公开示范文本结构：合同签订生效并具备实施条件预付30%，验收合格后支付70%",
        "base": _doc(
            _A1,
            _A2,
            "第三条 付款方式：合同签订生效并具备实施条件后5个工作日内，甲方向乙方预付"
            "合同总价的30%；验收合格之日起5个工作日内，甲方支付合同总价的70%。",
            "第四条 验收：甲方应在收到验收建议之日起7个工作日内组织实质性验收。",
            _A5,
            _A6,
            _A7,
            _A8,
        ),
        "revised": _doc(
            _A1,
            _A2,
            "第三条 付款方式：合同签订生效并具备实施条件后5个工作日内，甲方向乙方预付"
            "合同总价的30%；验收合格之日起5个工作日内，甲方支付合同总价的70%。",
            "第四条 验收：甲方应在收到验收建议之日起7个工作日内组织实质性验收。",
            _A5,
            _A6,
            _A7,
            _A8,
        ),
        "items": {
            "payment_term": _item(
                "unchanged", {"status": "identified", "value": 5, "unit": "workday"}, ("revised", 3)
            ),
            "start_condition": _item(
                "unchanged",
                {
                    "status": "identified",
                    "events": ["验收合格", "合同签订生效", "具备实施条件"],
                },
                ("revised", 3),
            ),
            "acceptance_invoice": _item("unchanged", _ACCEPT_OK, ("revised", 4)),
            "exception": _item("unidentified", _ABSENT),
        },
    },
    {
        "id": "payment_reference_start_event_word_change",
        "note": "被引用条款内 合同生效→合同签订生效：付款项必须 modified（评审 A B1）",
        "base": _doc(
            _A1,
            _A2,
            "第三条 付款方式：验收合格后，具体账期按第四条执行。",
            "第四条 履行期限：合同生效后10个工作日内付清，逾期未付的按合同约定承担违约责任。",
            _A5,
            _A6,
            _A7,
            _A8,
        ),
        "revised": _doc(
            _A1,
            _A2,
            "第三条 付款方式：验收合格后，具体账期按第四条执行。",
            "第四条 履行期限：合同签订生效后10个工作日内付清，逾期未付的按合同约定承担违约责任。",
            _A5,
            _A6,
            _A7,
            _A8,
        ),
        "items": {
            "payment_term": _item(
                "modified", {"status": "identified", "value": 10, "unit": "workday"}, ("revised", 4)
            ),
            "start_condition": _item(
                "unchanged", {"status": "identified", "events": ["验收合格"]}, ("revised", 3)
            ),
            "acceptance_invoice": _item("unchanged", _ACCEPT_OK, ("revised", 3)),
            "exception": _item("unidentified", _ABSENT),
        },
    },
    {
        "id": "start_bare_contract_effective",
        "note": "裸「合同生效」起算：付款 10 个工作日，start events 必须含合同生效",
        "base": _doc(
            _A1,
            _A2,
            _pay("三", "甲方应在合同生效后10个工作日内支付首期款。"),
            _acc("四", "乙方交付后10个工作日内完成验收，验收标准以本合同附件二为准。"),
            _A5,
            _A6,
            _A7,
            _A8,
        ),
        "revised": _doc(
            _A1,
            _A2,
            _pay("三", "甲方应在合同生效后10个工作日内支付首期款。"),
            _acc("四", "乙方交付后10个工作日内完成验收，验收标准以本合同附件二为准。"),
            _A5,
            _A6,
            _A7,
            _A8,
        ),
        "items": {
            "payment_term": _item(
                "unchanged",
                {"status": "identified", "value": 10, "unit": "workday"},
                ("revised", 3),
            ),
            "start_condition": _item(
                "unchanged", {"status": "identified", "events": ["合同生效"]}, ("revised", 3)
            ),
            "acceptance_invoice": _item("unchanged", _ACCEPT_OK, ("revised", 4)),
            "exception": _item("unidentified", _ABSENT),
        },
    },
    {
        "id": "start_event_overlap_dedupe",
        "note": "合同生效之日起：只算一个起算事件，不得同时计 合同生效 + 生效之日",
        "base": _doc(
            _A1,
            _A2,
            _pay("三", "甲方应在合同生效之日起10个工作日内支付货款。"),
            _acc("四", "乙方交付后10个工作日内完成验收，验收标准以本合同附件二为准。"),
            _A5,
            _A6,
            _A7,
            _A8,
        ),
        "revised": _doc(
            _A1,
            _A2,
            _pay("三", "甲方应在合同生效之日起10个工作日内支付货款。"),
            _acc("四", "乙方交付后10个工作日内完成验收，验收标准以本合同附件二为准。"),
            _A5,
            _A6,
            _A7,
            _A8,
        ),
        "items": {
            "payment_term": _item(
                "unchanged",
                {"status": "identified", "value": 10, "unit": "workday"},
                ("revised", 3),
            ),
            "start_condition": _item(
                "unchanged", {"status": "identified", "events": ["合同生效"]}, ("revised", 3)
            ),
            "acceptance_invoice": _item("unchanged", _ACCEPT_OK, ("revised", 4)),
            "exception": _item("unidentified", _ABSENT),
        },
    },
    {
        "id": "payment_reference_skips_exception",
        "note": "账期引用独立例外条款：目标期限不得吸成付款期限",
        "base": _doc(
            _A1,
            _A2,
            _pay("三", "具体账期按第九条执行。"),
            _acc("四", "乙方交付后10个工作日内完成验收，验收标准以本合同附件二为准。"),
            _A5,
            _A6,
            _A7,
            _A8,
            "第九条 例外：因不可抗力导致付款延迟的，期限顺延30天。",
        ),
        "revised": _doc(
            _A1,
            _A2,
            _pay("三", "具体账期按第九条执行。"),
            _acc("四", "乙方交付后10个工作日内完成验收，验收标准以本合同附件二为准。"),
            _A5,
            _A6,
            _A7,
            _A8,
            "第九条 例外：因不可抗力导致付款延迟的，期限顺延30天。",
        ),
        "items": {
            "payment_term": _item("unidentified", _ABSENT),
            "start_condition": _item("unidentified", _ABSENT),
            "acceptance_invoice": _item("unchanged", _ACCEPT_OK, ("revised", 4)),
            "exception": _item("unchanged", _ACCEPT_OK, ("revised", 9)),
        },
    },
    {
        "id": "payment_reference_cross_comma_negative",
        "note": "账期与引用词跨逗号：不得把第九条 12 个月吸成付款期限",
        "base": _doc(
            _A1,
            _A2,
            _pay("三", "账期以其他方式约定，详见第九条。"),
            _acc("四", "乙方交付后10个工作日内完成验收，验收标准以本合同附件二为准。"),
            _A5,
            _A6,
            _A7,
            _A8,
            "第九条 期限：自签署之日起12个月。",
        ),
        "revised": _doc(
            _A1,
            _A2,
            _pay("三", "账期以其他方式约定，详见第九条。"),
            _acc("四", "乙方交付后10个工作日内完成验收，验收标准以本合同附件二为准。"),
            _A5,
            _A6,
            _A7,
            _A8,
            "第九条 期限：自签署之日起12个月。",
        ),
        "items": {
            "payment_term": _item("unidentified", _ABSENT),
            "start_condition": _item("unidentified", _ABSENT),
            "acceptance_invoice": _item("unchanged", _ACCEPT_OK, ("revised", 4)),
            "exception": _item("unidentified", _ABSENT),
        },
    },
    {
        "id": "start_ignores_liability_clause",
        "note": "违约责任条款里的合同生效不是付款起算；违约金 5%→10% 不得让 start modified（N1）",
        "base": _doc(
            _A1,
            _A2,
            _pay("三", "甲方应在验收合格后30天内支付货款。"),
            _acc("四", "乙方交付后10个工作日内完成验收，验收标准以本合同附件二为准。"),
            _A5,
            "第六条 违约责任：合同生效后，违约方应向守约方支付违约金，违约金为合同总价的5%，"
            "并赔偿由此造成的全部损失。",
            _A7,
            _A8,
        ),
        "revised": _doc(
            _A1,
            _A2,
            _pay("三", "甲方应在验收合格后30天内支付货款。"),
            _acc("四", "乙方交付后10个工作日内完成验收，验收标准以本合同附件二为准。"),
            _A5,
            "第六条 违约责任：合同生效后，违约方应向守约方支付违约金，违约金为合同总价的10%，"
            "并赔偿由此造成的全部损失。",
            _A7,
            _A8,
        ),
        "items": {
            "payment_term": _item(
                "unchanged", {"status": "identified", "value": 30, "unit": "day"}, ("revised", 3)
            ),
            "start_condition": _item(
                "unchanged", {"status": "identified", "events": ["验收合格"]}, ("revised", 3)
            ),
            "acceptance_invoice": _item("unchanged", _ACCEPT_OK, ("revised", 4)),
            "exception": _item("unidentified", _ABSENT),
        },
    },
    {
        "id": "start_liability_heading_mixed",
        "note": "标题从付款期限改为付款期限及违约责任：混合标题不得整条踢出起算（评审 B B1）",
        "base": _doc(
            _A1,
            _A2,
            _pay("三", "甲方应在验收合格后30天内支付货款。"),
            _acc("四", "乙方交付后10个工作日内完成验收，验收标准以本合同附件二为准。"),
            _A5,
            _A6,
            _A7,
            _A8,
        ),
        "revised": _doc(
            _A1,
            _A2,
            "第三条 付款期限及违约责任：甲方应在验收合格后30天内支付货款。",
            _acc("四", "乙方交付后10个工作日内完成验收，验收标准以本合同附件二为准。"),
            _A5,
            _A6,
            _A7,
            _A8,
        ),
        "items": {
            "payment_term": _item(
                "modified", {"status": "identified", "value": 30, "unit": "day"}, ("revised", 3)
            ),
            "start_condition": _item(
                "modified", {"status": "identified", "events": ["验收合格"]}, ("revised", 3)
            ),
            "acceptance_invoice": _item("modified", _ACCEPT_OK, ("revised", 3)),
            "exception": _item("unidentified", _ABSENT),
        },
    },
    {
        "id": "start_mixed_liability_body",
        "note": "混合条款（付款期限及违约责任）内违约金 5%→10%：四项不得误报（评审 A 阻断 1）",
        "base": _doc(
            _A1,
            _A2,
            "第三条 付款期限及违约责任：甲方应在验收合格后30天内支付货款。"
            "合同生效后，违约方应向守约方支付违约金，违约金为合同总价的5%。",
            _acc("四", "乙方交付后10个工作日内完成验收，验收标准以本合同附件二为准。"),
            _A5,
            _A6,
            _A7,
            _A8,
        ),
        "revised": _doc(
            _A1,
            _A2,
            "第三条 付款期限及违约责任：甲方应在验收合格后30天内支付货款。"
            "合同生效后，违约方应向守约方支付违约金，违约金为合同总价的10%。",
            _acc("四", "乙方交付后10个工作日内完成验收，验收标准以本合同附件二为准。"),
            _A5,
            _A6,
            _A7,
            _A8,
        ),
        "items": {
            "payment_term": _item(
                "unchanged", {"status": "identified", "value": 30, "unit": "day"}, ("revised", 3)
            ),
            "start_condition": _item(
                "unchanged", {"status": "identified", "events": ["验收合格"]}, ("revised", 3)
            ),
            "acceptance_invoice": _item("unchanged", _ACCEPT_OK, ("revised", 4)),
            "exception": _item("unidentified", _ABSENT),
        },
    },
    {
        "id": "start_ignores_liability_clause_no_penalty",
        "note": "责任条款含付款词但无违约金类词：仍不得贡献起算事件",
        "base": _doc(
            _A1,
            _A2,
            _pay("三", "甲方应在验收合格后30天内支付货款。"),
            _acc("四", "乙方交付后10个工作日内完成验收，验收标准以本合同附件二为准。"),
            _A5,
            "第六条 违约责任：合同生效后，违约方应向守约方支付合同总价的5%作为赔偿，"
            "并继续履行本合同约定的其他义务；协商不成的，可向有管辖权的人民法院起诉。",
            _A7,
            _A8,
        ),
        "revised": _doc(
            _A1,
            _A2,
            _pay("三", "甲方应在验收合格后30天内支付货款。"),
            _acc("四", "乙方交付后10个工作日内完成验收，验收标准以本合同附件二为准。"),
            _A5,
            "第六条 违约责任：合同生效后，违约方应向守约方支付合同总价的5%作为赔偿，"
            "并继续履行本合同约定的其他义务；协商不成的，可向有管辖权的人民法院起诉。",
            _A7,
            _A8,
        ),
        "items": {
            "payment_term": _item(
                "unchanged", {"status": "identified", "value": 30, "unit": "day"}, ("revised", 3)
            ),
            "start_condition": _item(
                "unchanged", {"status": "identified", "events": ["验收合格"]}, ("revised", 3)
            ),
            "acceptance_invoice": _item("unchanged", _ACCEPT_OK, ("revised", 4)),
            "exception": _item("unidentified", _ABSENT),
        },
    },
    {
        "id": "start_comma_penalty_sentence",
        "note": "同句逗号连接付款+违约金：验收合格→合同生效必须 modified（评审 A/B 阻断）",
        "base": _doc(
            _A1,
            _A2,
            "第三条 付款期限：甲方应在验收合格后30天内向乙方支付货款，"
            "逾期付款的按应付款项每日万分之五支付违约金。",
            _acc("四", "乙方交付后10个工作日内完成验收，验收标准以本合同附件二为准。"),
            _A5,
            _A6,
            _A7,
            _A8,
        ),
        "revised": _doc(
            _A1,
            _A2,
            "第三条 付款期限：甲方应在合同生效后30天内向乙方支付货款，"
            "逾期付款的按应付款项每日万分之五支付违约金。",
            _acc("四", "乙方交付后10个工作日内完成验收，验收标准以本合同附件二为准。"),
            _A5,
            _A6,
            _A7,
            _A8,
        ),
        "items": {
            "payment_term": _item(
                "unchanged", {"status": "identified", "value": 30, "unit": "day"}, ("revised", 3)
            ),
            "start_condition": _item(
                "modified", {"status": "identified", "events": ["合同生效"]}, ("revised", 3)
            ),
            "acceptance_invoice": _item("modified", _ACCEPT_OK),
            "exception": _item("unidentified", _ABSENT),
        },
    },
    {
        "id": "start_unpunctuated_penalty_sentence",
        "note": "同片段无标点连词：付款货款并按日支付违约金，起算必须保留（D13）",
        "base": _doc(
            _A1,
            _A2,
            "第三条 付款期限：甲方应在验收合格后30天内向乙方支付货款并按日万分之五支付违约金。",
            _acc("四", "乙方交付后10个工作日内完成验收，验收标准以本合同附件二为准。"),
            _A5,
            _A6,
            _A7,
            _A8,
        ),
        "revised": _doc(
            _A1,
            _A2,
            "第三条 付款期限：甲方应在验收合格后30天内向乙方支付货款并按日万分之五支付违约金。",
            _acc("四", "乙方交付后10个工作日内完成验收，验收标准以本合同附件二为准。"),
            _A5,
            _A6,
            _A7,
            _A8,
        ),
        "items": {
            "payment_term": _item(
                "unchanged", {"status": "identified", "value": 30, "unit": "day"}, ("revised", 3)
            ),
            "start_condition": _item(
                "unchanged", {"status": "identified", "events": ["验收合格"]}, ("revised", 3)
            ),
            "acceptance_invoice": _item("unchanged", _ACCEPT_OK, ("revised", 4)),
            "exception": _item("unidentified", _ABSENT),
        },
    },
    {
        "id": "start_no_colon_mixed_heading_body_colon",
        "note": "无冒号混合标题 + 正文冒号：违约金比例变化不得让 start modified（D13）",
        "base": _doc(
            _A1,
            _A2,
            "第六条 付款期限及违约责任\n甲方应在验收合格后30天内支付货款。"
            "合同生效后，违约方应支付违约金：按日万分之五。",
            _acc("四", "乙方交付后10个工作日内完成验收，验收标准以本合同附件二为准。"),
            _A5,
            _A7,
            _A8,
            _A9_EXTRA,
        ),
        "revised": _doc(
            _A1,
            _A2,
            "第六条 付款期限及违约责任\n甲方应在验收合格后30天内支付货款。"
            "合同生效后，违约方应支付违约金：按日万分之十。",
            _acc("四", "乙方交付后10个工作日内完成验收，验收标准以本合同附件二为准。"),
            _A5,
            _A7,
            _A8,
            _A9_EXTRA,
        ),
        "items": {
            "payment_term": _item(
                "unchanged", {"status": "identified", "value": 30, "unit": "day"}, ("revised", 6)
            ),
            "start_condition": _item(
                "unchanged", {"status": "identified", "events": ["验收合格"]}, ("revised", 6)
            ),
            "acceptance_invoice": _item("unchanged", _ACCEPT_OK, ("revised", 4)),
            "exception": _item("unidentified", _ABSENT),
        },
    },
    {
        "id": "start_conjunction_penalty_amount_change",
        "note": "连词前含违约金类词的责任句：比例 万分之五→万分之十 不得污染四项（D13 R1）",
        "base": _doc(
            _A1,
            _A2,
            "第三条 付款期限：甲方应在验收合格后30天内支付货款。"
            "逾期付款的，每逾期一日，应按未付款项的万分之五支付违约金及赔偿损失。",
            _acc("四", "乙方交付后10个工作日内完成验收，验收标准以本合同附件二为准。"),
            _A5,
            _A6,
            _A7,
            _A8,
        ),
        "revised": _doc(
            _A1,
            _A2,
            "第三条 付款期限：甲方应在验收合格后30天内支付货款。"
            "逾期付款的，每逾期一日，应按未付款项的万分之十支付违约金及赔偿损失。",
            _acc("四", "乙方交付后10个工作日内完成验收，验收标准以本合同附件二为准。"),
            _A5,
            _A6,
            _A7,
            _A8,
        ),
        "items": {
            "payment_term": _item(
                "unchanged", {"status": "identified", "value": 30, "unit": "day"}, ("revised", 3)
            ),
            "start_condition": _item(
                "unchanged", {"status": "identified", "events": ["验收合格"]}, ("revised", 3)
            ),
            "acceptance_invoice": _item("unchanged", _ACCEPT_OK, ("revised", 4)),
            "exception": _item("unidentified", _ABSENT),
        },
    },
    {
        "id": "start_long_title_heading",
        "note": "超长/多主题标题（>40 字、含顿号）仍须剥离，起算与付款不得丢失（D13 R2）",
        "base": _doc(
            _A1,
            _A2,
            _acc("四", "乙方交付后10个工作日内完成验收，验收标准以本合同附件二为准。"),
            _A5,
            "第六条 付款期限、验收标准、发票开具要求、争议解决方式、"
            "合同解除及逾期付款违约金的计算方式：甲方应在验收合格后30天内支付货款。",
            _A7,
            _A8,
            _A9_EXTRA,
        ),
        "revised": _doc(
            _A1,
            _A2,
            _acc("四", "乙方交付后10个工作日内完成验收，验收标准以本合同附件二为准。"),
            _A5,
            "第六条 付款期限、验收标准、发票开具要求、争议解决方式、"
            "合同解除及逾期付款违约金的计算方式：甲方应在验收合格后30天内支付货款。",
            _A7,
            _A8,
            _A9_EXTRA,
        ),
        "items": {
            "payment_term": _item(
                "unchanged", {"status": "identified", "value": 30, "unit": "day"}, ("revised", 6)
            ),
            "start_condition": _item(
                "unchanged", {"status": "identified", "events": ["验收合格"]}, ("revised", 6)
            ),
            "acceptance_invoice": _item("unchanged", _ACCEPT_OK, ("revised", 4)),
            "exception": _item("unidentified", _ABSENT),
        },
    },
    {
        "id": "acceptance_penalty_title_duration_change",
        "note": "标题含逾期30日付款违约金：验收 10→15 工作日必须 modified（D13 R3）",
        "base": _doc(
            _A1,
            _A2,
            _acc("四", "乙方交付后10个工作日内完成验收，验收标准以本合同附件二为准。"),
            _A5,
            _A6,
            _A7,
            _A8,
            "第十条 逾期30日付款违约金：乙方交付后10个工作日内完成验收，"
            "验收后30天内向甲方支付货款。",
        ),
        "revised": _doc(
            _A1,
            _A2,
            _acc("四", "乙方交付后10个工作日内完成验收，验收标准以本合同附件二为准。"),
            _A5,
            _A6,
            _A7,
            _A8,
            "第十条 逾期30日付款违约金：乙方交付后15个工作日内完成验收，"
            "验收后30天内向甲方支付货款。",
        ),
        "items": {
            "payment_term": _item(
                "modified", {"status": "identified", "value": 30, "unit": "day"}, ("revised", 10)
            ),
            "start_condition": _item("unidentified", _ABSENT),
            "acceptance_invoice": _item("modified", _ACCEPT_OK, ("revised", 10)),
            "exception": _item("unidentified", _ABSENT),
        },
    },
    {
        "id": "start_reverse_penalty_payment_order",
        "note": "反向语序：支付违约金并在验收合格后30天内付款，起算与付款必须保留（D14）",
        "base": _doc(
            _A1,
            _A2,
            "第三条 付款期限：甲方应支付违约金并在验收合格后30天内向乙方支付货款。",
            _acc("四", "乙方交付后10个工作日内完成验收，验收标准以本合同附件二为准。"),
            _A5,
            _A6,
            _A7,
            _A8,
            _A9_EXTRA,
        ),
        "revised": _doc(
            _A1,
            _A2,
            "第三条 付款期限：甲方应支付违约金并在验收合格后30天内向乙方支付货款。",
            _acc("四", "乙方交付后10个工作日内完成验收，验收标准以本合同附件二为准。"),
            _A5,
            _A6,
            _A7,
            _A8,
            _A9_EXTRA,
        ),
        "items": {
            "payment_term": _item(
                "unchanged", {"status": "identified", "value": 30, "unit": "day"}, ("revised", 3)
            ),
            "start_condition": _item(
                "unchanged", {"status": "identified", "events": ["验收合格"]}, ("revised", 3)
            ),
            "acceptance_invoice": _item("unchanged", _ACCEPT_OK, ("revised", 4)),
            "exception": _item("unidentified", _ABSENT),
        },
    },
    {
        "id": "start_forward_conjunction_penalty_suffix",
        "note": "正向连词+责任后缀（支付货款并赔偿损失）：起算与付款保留（D14 R1）",
        "base": _doc(
            _A1,
            _A2,
            "第三条 付款期限：甲方应在验收合格后30天内支付货款并赔偿损失。",
            _acc("四", "乙方交付后10个工作日内完成验收，验收标准以本合同附件二为准。"),
            _A5,
            _A6,
            _A7,
            _A8,
            _A9_EXTRA,
        ),
        "revised": _doc(
            _A1,
            _A2,
            "第三条 付款期限：甲方应在验收合格后30天内支付货款并赔偿损失。",
            _acc("四", "乙方交付后10个工作日内完成验收，验收标准以本合同附件二为准。"),
            _A5,
            _A6,
            _A7,
            _A8,
            _A9_EXTRA,
        ),
        "items": {
            "payment_term": _item(
                "unchanged", {"status": "identified", "value": 30, "unit": "day"}, ("revised", 3)
            ),
            "start_condition": _item(
                "unchanged", {"status": "identified", "events": ["验收合格"]}, ("revised", 3)
            ),
            "acceptance_invoice": _item("unchanged", _ACCEPT_OK, ("revised", 4)),
            "exception": _item("unidentified", _ABSENT),
        },
    },
    {
        "id": "start_short_comma_title",
        "note": "短混合标题（违约责任，付款期限）：仍按标题处理，起算必须保留（D14 R2）",
        "base": _doc(
            _A1,
            _A2,
            "第六条 违约责任，付款期限：甲方应在验收合格后30天内支付货款。",
            _acc("四", "乙方交付后10个工作日内完成验收，验收标准以本合同附件二为准。"),
            _A5,
            _A7,
            _A8,
            _A9_EXTRA,
        ),
        "revised": _doc(
            _A1,
            _A2,
            "第六条 违约责任，付款期限：甲方应在验收合格后30天内支付货款。",
            _acc("四", "乙方交付后10个工作日内完成验收，验收标准以本合同附件二为准。"),
            _A5,
            _A7,
            _A8,
            _A9_EXTRA,
        ),
        "items": {
            "payment_term": _item(
                "unchanged", {"status": "identified", "value": 30, "unit": "day"}, ("revised", 6)
            ),
            "start_condition": _item(
                "unchanged", {"status": "identified", "events": ["验收合格"]}, ("revised", 6)
            ),
            "acceptance_invoice": _item("unchanged", _ACCEPT_OK, ("revised", 4)),
            "exception": _item("unidentified", _ABSENT),
        },
    },
    {
        "id": "start_liability_body_colon_suffix",
        "note": "责任前缀+正文冒号（违约金：付款期限为验收合格后30天）：起算与付款保留（D14 R3）",
        "base": _doc(
            _A1,
            _A2,
            "第三条 甲方逾期付款的，应支付违约金：付款期限为验收合格后30天，甲方应按期支付货款。",
            _acc("四", "乙方交付后10个工作日内完成验收，验收标准以本合同附件二为准。"),
            _A5,
            _A6,
            _A7,
            _A8,
            _A9_EXTRA,
        ),
        "revised": _doc(
            _A1,
            _A2,
            "第三条 甲方逾期付款的，应支付违约金：付款期限为验收合格后30天，甲方应按期支付货款。",
            _acc("四", "乙方交付后10个工作日内完成验收，验收标准以本合同附件二为准。"),
            _A5,
            _A6,
            _A7,
            _A8,
            _A9_EXTRA,
        ),
        "items": {
            "payment_term": _item(
                "unchanged", {"status": "identified", "value": 30, "unit": "day"}, ("revised", 3)
            ),
            "start_condition": _item(
                "unchanged", {"status": "identified", "events": ["验收合格"]}, ("revised", 3)
            ),
            "acceptance_invoice": _item("unchanged", _ACCEPT_OK, ("revised", 4)),
            "exception": _item("unidentified", _ABSENT),
        },
    },
    {
        "id": "acceptance_penalty_title_standard_change",
        "note": "标题含逾期30日：验收标准附件二→附件三必须 modified（D14 R3 hint 回退）",
        "base": _doc(
            _A1,
            _A2,
            _acc("四", "乙方交付后10个工作日内完成验收，验收标准以本合同附件二为准。"),
            _A5,
            _A6,
            _A7,
            _A8,
            "第十条 逾期30日付款违约金：验收标准为本合同附件二。",
            _A9_EXTRA,
        ),
        "revised": _doc(
            _A1,
            _A2,
            _acc("四", "乙方交付后10个工作日内完成验收，验收标准以本合同附件二为准。"),
            _A5,
            _A6,
            _A7,
            _A8,
            "第十条 逾期30日付款违约金：验收标准为本合同附件三。",
            _A9_EXTRA,
        ),
        "items": {
            "payment_term": _item("unidentified", _ABSENT),
            "start_condition": _item("unidentified", _ABSENT),
            "acceptance_invoice": _item("modified", _ACCEPT_OK, ("revised", 10)),
            "exception": _item("unidentified", _ABSENT),
        },
    },
    {
        "id": "start_liability_colon_nonpayment_suffix",
        "note": "正文冒号后缀是罚则计算起算：合同生效→合同签订生效 不得污染四项（D14 R4）",
        "base": _doc(
            _A1,
            _A2,
            "第三条 付款期限：甲方应在验收合格后30天内支付货款。"
            "逾期付款的，应支付违约金：合同生效之日起按日万分之五计算。",
            _acc("四", "乙方交付后10个工作日内完成验收，验收标准以本合同附件二为准。"),
            _A5,
            _A6,
            _A7,
            _A8,
            _A9_EXTRA,
        ),
        "revised": _doc(
            _A1,
            _A2,
            "第三条 付款期限：甲方应在验收合格后30天内支付货款。"
            "逾期付款的，应支付违约金：合同签订生效之日起按日万分之五计算。",
            _acc("四", "乙方交付后10个工作日内完成验收，验收标准以本合同附件二为准。"),
            _A5,
            _A6,
            _A7,
            _A8,
            _A9_EXTRA,
        ),
        "items": {
            "payment_term": _item(
                "unchanged", {"status": "identified", "value": 30, "unit": "day"}, ("revised", 3)
            ),
            "start_condition": _item(
                "unchanged", {"status": "identified", "events": ["验收合格"]}, ("revised", 3)
            ),
            "acceptance_invoice": _item("unchanged", _ACCEPT_OK, ("revised", 4)),
            "exception": _item("unidentified", _ABSENT),
        },
    },
    {
        "id": "start_reverse_settle_balance",
        "note": "反向语序+结清尾款：起算与 30 天付款必须保留（D15）",
        "base": _doc(
            _A1,
            _A2,
            "第三条 付款期限：甲方应支付违约金并在验收合格后30天内结清尾款。",
            _acc("四", "乙方交付后10个工作日内完成验收，验收标准以本合同附件二为准。"),
            _A5,
            _A6,
            _A7,
            _A8,
            _A9_EXTRA,
        ),
        "revised": _doc(
            _A1,
            _A2,
            "第三条 付款期限：甲方应支付违约金并在验收合格后30天内结清尾款。",
            _acc("四", "乙方交付后10个工作日内完成验收，验收标准以本合同附件二为准。"),
            _A5,
            _A6,
            _A7,
            _A8,
            _A9_EXTRA,
        ),
        "items": {
            "payment_term": _item(
                "unchanged", {"status": "identified", "value": 30, "unit": "day"}, ("revised", 3)
            ),
            "start_condition": _item(
                "unchanged", {"status": "identified", "events": ["验收合格"]}, ("revised", 3)
            ),
            "acceptance_invoice": _item("unchanged", _ACCEPT_OK, ("revised", 4)),
            "exception": _item("unidentified", _ABSENT),
        },
    },
    {
        "id": "start_reverse_contract_total_payment",
        "note": "反向语序+合同总价70%：起算与 30 天付款必须保留（D15）",
        "base": _doc(
            _A1,
            _A2,
            "第三条 付款期限：甲方应支付违约金并在验收合格后30天内支付合同总价的70%。",
            _acc("四", "乙方交付后10个工作日内完成验收，验收标准以本合同附件二为准。"),
            _A5,
            _A6,
            _A7,
            _A8,
            _A9_EXTRA,
        ),
        "revised": _doc(
            _A1,
            _A2,
            "第三条 付款期限：甲方应支付违约金并在验收合格后30天内支付合同总价的70%。",
            _acc("四", "乙方交付后10个工作日内完成验收，验收标准以本合同附件二为准。"),
            _A5,
            _A6,
            _A7,
            _A8,
            _A9_EXTRA,
        ),
        "items": {
            "payment_term": _item(
                "unchanged", {"status": "identified", "value": 30, "unit": "day"}, ("revised", 3)
            ),
            "start_condition": _item(
                "unchanged", {"status": "identified", "events": ["验收合格"]}, ("revised", 3)
            ),
            "acceptance_invoice": _item("unchanged", _ACCEPT_OK, ("revised", 4)),
            "exception": _item("unidentified", _ABSENT),
        },
    },
    {
        "id": "payment_excludes_tax_social_insurance",
        "note": "税费/社保缴纳期限不得与付款期限并列成 ambiguous（D15 R1）",
        "base": _doc(
            _A1,
            _A2,
            "第三条 付款期限：甲方应在验收合格后10天内支付货款。",
            _acc("四", "乙方交付后10个工作日内完成验收，验收标准以本合同附件二为准。"),
            "第五条 付款与税费：甲方应在验收合格后10天内支付货款。"
            "乙方应在验收合格后30天内缴纳增值税及社会保险费。",
            _A6,
            _A7,
            _A8,
            _A9_EXTRA,
        ),
        "revised": _doc(
            _A1,
            _A2,
            "第三条 付款期限：甲方应在验收合格后10天内支付货款。",
            _acc("四", "乙方交付后10个工作日内完成验收，验收标准以本合同附件二为准。"),
            "第五条 付款与税费：甲方应在验收合格后10天内支付货款。"
            "乙方应在验收合格后30天内缴纳增值税及社会保险费。",
            _A6,
            _A7,
            _A8,
            _A9_EXTRA,
        ),
        "items": {
            "payment_term": _item(
                "unchanged", {"status": "identified", "value": 10, "unit": "day"}, ("revised", 3)
            ),
            "start_condition": _item(
                "unchanged", {"status": "identified", "events": ["验收合格"]}, ("revised", 3)
            ),
            "acceptance_invoice": _item("unchanged", _ACCEPT_OK, ("revised", 4)),
            "exception": _item("unidentified", _ABSENT),
        },
    },
    {
        "id": "payment_excludes_liability_anchor_title",
        "note": "违约金的缴纳/承担验收费用：付款与起算均不得识别（D15 R1）",
        "base": _doc(
            _A1,
            _A2,
            "第四条 付款期限：甲方应支付违约金并在验收合格后30天内承担验收费用。",
            _A5,
            _A6,
            _A7,
            _A8,
            "第九条 违约金的缴纳：乙方应在验收合格后10天内缴纳；逾期每日按0.05%支付违约金。",
        ),
        "revised": _doc(
            _A1,
            _A2,
            "第四条 付款期限：甲方应支付违约金并在验收合格后30天内承担验收费用。",
            _A5,
            _A6,
            _A7,
            _A8,
            "第九条 违约金的缴纳：乙方应在验收合格后10天内缴纳；逾期每日按0.05%支付违约金。",
        ),
        "items": {
            "payment_term": _item("unidentified", _ABSENT),
            "start_condition": _item("unidentified", _ABSENT),
            "acceptance_invoice": _item("unchanged", _ACCEPT_OK, ("revised", 4)),
            "exception": _item("unidentified", _ABSENT),
        },
    },
    {
        "id": "start_exception_new_event",
        "note": "新增例外条款提到验收合格+付款顺延：起算条件不得因此 modified（已知问题 #3）",
        "base": _doc(
            _A1,
            _A2,
            _pay("三", "甲方应在收到发票后30天内支付货款。"),
            _acc("四", "乙方交付后10个工作日内完成验收，验收标准以本合同附件二为准。"),
            _A5,
            _A6,
            _A7,
            _A8,
        ),
        "revised": _doc(
            _A1,
            _A2,
            _pay("三", "甲方应在收到发票后30天内支付货款。"),
            _acc("四", "乙方交付后10个工作日内完成验收，验收标准以本合同附件二为准。"),
            _A5,
            _A6,
            _A7,
            _A8,
            "第九条 例外：因不可抗力导致验收合格延迟的，付款相应顺延。",
        ),
        "items": {
            "payment_term": _item("unchanged", _PAY_30, ("revised", 3)),
            "start_condition": _item(
                "unchanged", {"status": "identified", "events": ["收到发票"]}, ("revised", 3)
            ),
            "acceptance_invoice": _item("unchanged", _ACCEPT_OK, ("revised", 4)),
            "exception": _item("added", _ACCEPT_OK, ("revised", 9)),
        },
    },
    {
        "id": "payment_tax_invoice_modifier",
        "note": "增值税专用发票是付款宾语的定语，不得否决付款期限（评审 B N1）",
        "base": _doc(
            _A1,
            _A2,
            _pay("三", "甲方应在验收合格后30天内凭增值税专用发票支付货款。"),
            _acc("四", "乙方交付后10个工作日内完成验收，验收标准以本合同附件二为准。"),
            _A5,
            _A6,
            _A7,
            _A8,
        ),
        "revised": _doc(
            _A1,
            _A2,
            _pay("三", "甲方应在验收合格后60天内凭增值税专用发票支付货款。"),
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
        "id": "payment_tax_inclusive_price",
        "note": "含增值税的合同价款：税费只是付款宾语定语，30→60 必须 modified（评审 B N1）",
        "base": _doc(
            _A1,
            _A2,
            _pay("三", "甲方应在验收合格后30天内支付含增值税的合同价款。"),
            _acc("四", "乙方交付后10个工作日内完成验收，验收标准以本合同附件二为准。"),
            _A5,
            _A6,
            _A7,
            _A8,
        ),
        "revised": _doc(
            _A1,
            _A2,
            _pay("三", "甲方应在验收合格后60天内支付含增值税的合同价款。"),
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
        "id": "payment_settle_then_pay",
        "note": "结清往来账目并支付货款：并列付款义务存在，期限仍归付款（评审 B N1）",
        "base": _doc(
            _A1,
            _A2,
            _pay("三", "甲方应在验收合格后30天内结清往来账目并支付货款。"),
            _acc("四", "乙方交付后10个工作日内完成验收，验收标准以本合同附件二为准。"),
            _A5,
            _A6,
            _A7,
            _A8,
        ),
        "revised": _doc(
            _A1,
            _A2,
            _pay("三", "甲方应在验收合格后60天内结清往来账目并支付货款。"),
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
        "id": "payment_excludes_tax_wording_variants",
        "note": "相关税费/结清关系/印花税等改写不得与付款期限并列（评审 A N1/N3）",
        "base": _doc(
            _A1,
            _A2,
            _pay("三", "甲方应在验收合格后10天内支付货款。"),
            _acc("四", "乙方交付后10个工作日内完成验收，验收标准以本合同附件二为准。"),
            "第五条 付款与税费：乙方应在验收合格后30天内缴纳相关税费。"
            "乙方应在验收合格后35天内支付相关税费。"
            "乙方应在合同解除后40天内结清关系。乙方应在验收合格后45天内支付印花税。",
            _A6,
            _A7,
            _A8,
        ),
        "revised": _doc(
            _A1,
            _A2,
            _pay("三", "甲方应在验收合格后10天内支付货款。"),
            _acc("四", "乙方交付后10个工作日内完成验收，验收标准以本合同附件二为准。"),
            "第五条 付款与税费：乙方应在验收合格后30天内缴纳相关税费。"
            "乙方应在验收合格后35天内支付相关税费。"
            "乙方应在合同解除后40天内结清关系。乙方应在验收合格后45天内支付印花税。",
            _A6,
            _A7,
            _A8,
        ),
        "items": {
            "payment_term": _item(
                "unchanged", {"status": "identified", "value": 10, "unit": "day"}, ("revised", 3)
            ),
            "start_condition": _item("unchanged", _START_ACCEPTED, ("revised", 3)),
            "acceptance_invoice": _item("unchanged", _ACCEPT_OK, ("revised", 4)),
            "exception": _item("unidentified", _ABSENT),
        },
    },
    {
        "id": "payment_penalty_colon_fee_not_payment",
        "note": "违约金：相关费用应在…缴纳 的期限不归付款（评审 A N2）",
        "base": _doc(
            _A1,
            _A2,
            "第三条 付款期限：违约金：相关费用应在合同生效后10日内缴纳。",
            _acc("四", "乙方交付后10个工作日内完成验收，验收标准以本合同附件二为准。"),
            _A5,
            _A7,
            _A8,
            _A9_EXTRA,
        ),
        "revised": _doc(
            _A1,
            _A2,
            "第三条 付款期限：违约金：相关费用应在合同生效后20日内缴纳。",
            _acc("四", "乙方交付后10个工作日内完成验收，验收标准以本合同附件二为准。"),
            _A5,
            _A7,
            _A8,
            _A9_EXTRA,
        ),
        "items": {
            "payment_term": _item("unidentified", _ABSENT),
            "start_condition": _item("unidentified", _ABSENT),
            "acceptance_invoice": _item("unchanged", _ACCEPT_OK, ("revised", 4)),
            "exception": _item("unidentified", _ABSENT),
        },
    },
    {
        "id": "start_reverse_subject_changed",
        "note": "反向语序付款主体 甲方→乙方 必须报 payment modified（评审 A S1）",
        "base": _doc(
            _A1,
            _A2,
            "第三条 付款期限：甲方应支付违约金并在验收合格后30天内结清尾款。",
            _acc("四", "乙方交付后10个工作日内完成验收，验收标准以本合同附件二为准。"),
            _A5,
            _A6,
            _A7,
            _A8,
        ),
        "revised": _doc(
            _A1,
            _A2,
            "第三条 付款期限：乙方应支付违约金并在验收合格后30天内结清尾款。",
            _acc("四", "乙方交付后10个工作日内完成验收，验收标准以本合同附件二为准。"),
            _A5,
            _A6,
            _A7,
            _A8,
        ),
        "items": {
            "payment_term": _item(
                "modified", {"status": "identified", "value": 30, "unit": "day"}, ("revised", 3)
            ),
            "start_condition": _item("unchanged", _START_ACCEPTED, ("revised", 3)),
            "acceptance_invoice": _item("unchanged", _ACCEPT_OK, ("revised", 4)),
            "exception": _item("unidentified", _ABSENT),
        },
    },
    {
        "id": "start_reverse_tax_inclusive_price",
        "note": "反向语序+含增值税价款：税费只是付款宾语定语，30→60 必须 modified（评审 B N4）",
        "base": _doc(
            _A1,
            _A2,
            "第三条 付款期限：甲方应支付违约金并在验收合格后30天内支付含增值税的合同价款。",
            _acc("四", "乙方交付后10个工作日内完成验收，验收标准以本合同附件二为准。"),
            _A5,
            _A6,
            _A7,
            _A8,
        ),
        "revised": _doc(
            _A1,
            _A2,
            "第三条 付款期限：甲方应支付违约金并在验收合格后60天内支付含增值税的合同价款。",
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
        "id": "payment_conjunct_tax_obligation",
        "note": "付款与税费并列义务：期限仍归付款，30→60 必须 modified（评审 B N4 非阻断 4）",
        "base": _doc(
            _A1,
            _A2,
            _pay("三", "甲方应在验收合格后30天内支付货款并承担税费。"),
            _acc("四", "乙方交付后10个工作日内完成验收，验收标准以本合同附件二为准。"),
            _A5,
            _A6,
            _A7,
            _A8,
        ),
        "revised": _doc(
            _A1,
            _A2,
            _pay("三", "甲方应在验收合格后60天内支付货款并承担税费。"),
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
        "id": "payment_liability_article_body_excluded",
        "note": "违约责任标题下的付款期限整体不抽取：有意取舍，正名用例（评审 A S3/B N3）",
        "base": _doc(
            _A1,
            _A2,
            _acc("三", "乙方交付后10个工作日内完成验收，验收标准以本合同附件二为准。"),
            _A5,
            "第六条 违约责任：甲方应在验收合格后30天内支付货款，逾期每日按0.05%支付违约金。",
            _A7,
            _A8,
            _A9_EXTRA,
        ),
        "revised": _doc(
            _A1,
            _A2,
            _acc("三", "乙方交付后10个工作日内完成验收，验收标准以本合同附件二为准。"),
            _A5,
            "第六条 违约责任：甲方应在验收合格后30天内支付货款，逾期每日按0.05%支付违约金。",
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
        "id": "start_excludes_liability_social_suffix",
        "note": "责任句后缀缴纳社保费用：付款/起算均不得识别（评审 B N2）",
        "base": _doc(
            _A1,
            _A2,
            _acc("三", "乙方交付后10个工作日内完成验收，验收标准以本合同附件二为准。"),
            "第四条 付款期限：甲方应支付违约金并在验收合格后30天内缴纳社保费用。",
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
            "第四条 付款期限：甲方应支付违约金并在验收合格后30天内缴纳社保费用。",
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
        "id": "payment_weak_verb_conjunct_tax",
        "note": "弱动词并列税费（结清尾款并缴纳增值税）30→60 必须 modified（评审 A B1）",
        "base": _doc(
            _A1,
            _A2,
            _pay("三", "甲方应在验收合格后30天内结清尾款并缴纳增值税。"),
            _acc("四", "乙方交付后10个工作日内完成验收，验收标准以本合同附件二为准。"),
            _A5,
            _A6,
            _A7,
            _A8,
        ),
        "revised": _doc(
            _A1,
            _A2,
            _pay("三", "甲方应在验收合格后60天内结清尾款并缴纳增值税。"),
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
        "id": "start_reverse_conjunct_tax_event_changed",
        "note": "反向责任句+税费并列：起算事件变化必须 modified（评审 A B2）",
        "base": _doc(
            _A1,
            _A2,
            "第三条 付款期限：甲方应支付违约金并在验收合格后30天内支付货款并承担税费。",
            _acc("四", "乙方交付后10个工作日内完成验收，验收标准以本合同附件二为准。"),
            _A5,
            _A6,
            _A7,
            _A8,
        ),
        "revised": _doc(
            _A1,
            _A2,
            "第三条 付款期限：甲方应支付违约金并在收到发票后30天内支付货款并承担税费。",
            _acc("四", "乙方交付后10个工作日内完成验收，验收标准以本合同附件二为准。"),
            _A5,
            _A6,
            _A7,
            _A8,
        ),
        "items": {
            "payment_term": _item(
                "unchanged", {"status": "identified", "value": 30, "unit": "day"}, ("revised", 3)
            ),
            "start_condition": _item(
                "modified", {"status": "identified", "events": ["收到发票"]}, ("revised", 3)
            ),
            "acceptance_invoice": _item("modified", _ACCEPT_OK, ("revised", 3)),
            "exception": _item("unidentified", _ABSENT),
        },
    },
    {
        "id": "payment_reverse_object_changed",
        "note": "反向责任句付款宾语 货款→价款 必须 modified（评审 A B2）",
        "base": _doc(
            _A1,
            _A2,
            "第三条 付款期限：甲方应支付违约金并在验收合格后30天内支付货款并承担税费。",
            _acc("四", "乙方交付后10个工作日内完成验收，验收标准以本合同附件二为准。"),
            _A5,
            _A6,
            _A7,
            _A8,
        ),
        "revised": _doc(
            _A1,
            _A2,
            "第三条 付款期限：甲方应支付违约金并在验收合格后30天内支付价款并承担税费。",
            _acc("四", "乙方交付后10个工作日内完成验收，验收标准以本合同附件二为准。"),
            _A5,
            _A6,
            _A7,
            _A8,
        ),
        "items": {
            "payment_term": _item(
                "modified", {"status": "identified", "value": 30, "unit": "day"}, ("revised", 3)
            ),
            "start_condition": _item("modified", _START_ACCEPTED, ("revised", 3)),
            "acceptance_invoice": _item("modified", _ACCEPT_OK, ("revised", 3)),
            "exception": _item("unidentified", _ABSENT),
        },
    },
    {
        "id": "start_nested_payment_penalty_label",
        "note": "嵌套标签「付款期限：逾期付款违约金：…」的起算不得丢失（评审 A B3）",
        "base": _doc(
            _A1,
            _A2,
            "第三条 付款期限：逾期付款违约金：甲方应在验收合格后30天内支付货款。",
            _acc("四", "乙方交付后10个工作日内完成验收，验收标准以本合同附件二为准。"),
            _A5,
            _A6,
            _A7,
            _A8,
        ),
        "revised": _doc(
            _A1,
            _A2,
            "第三条 付款期限：逾期付款违约金：甲方应在验收合格后60天内支付货款。",
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
        "id": "payment_penalty_colon_mixed_label_fee",
        "note": "混合标签+费用缴纳：相关费用/社保费用不得成为付款或起算（评审 B R4）",
        "base": _doc(
            _A1,
            _A2,
            _acc("三", "乙方交付后10个工作日内完成验收，验收标准以本合同附件二为准。"),
            "第四条 付款期限：逾期付款违约金：相关费用应在合同生效后10天内缴纳。",
            "第五条 付款期限：逾期付款违约金：甲方应在验收合格后30天内缴纳社保费用。",
            _A6,
            _A7,
            _A8,
            _A9_EXTRA,
        ),
        "revised": _doc(
            _A1,
            _A2,
            _acc("三", "乙方交付后10个工作日内完成验收，验收标准以本合同附件二为准。"),
            "第四条 付款期限：逾期付款违约金：相关费用应在合同生效后10天内缴纳。",
            "第五条 付款期限：逾期付款违约金：甲方应在验收合格后30天内缴纳社保费用。",
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
        "id": "payment_repeated_verb_conjunct_tax",
        "note": "同动词重复并列（支付货款并支付相关税费）30→60 必须 modified（评审 A R4）",
        "base": _doc(
            _A1,
            _A2,
            _pay("三", "甲方应在验收合格后30天内支付货款并支付相关税费。"),
            _acc("四", "乙方交付后10个工作日内完成验收，验收标准以本合同附件二为准。"),
            _A5,
            _A6,
            _A7,
            _A8,
        ),
        "revised": _doc(
            _A1,
            _A2,
            _pay("三", "甲方应在验收合格后60天内支付货款并支付相关税费。"),
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
        "id": "payment_penalty_colon_service_fee",
        "note": "混合标签下服务费是强付款宾语：30→60 必须 modified（评审 B R4）",
        "base": _doc(
            _A1,
            _A2,
            "第三条 付款期限：逾期付款违约金：甲方应在验收合格后30天内支付服务费。",
            _acc("四", "乙方交付后10个工作日内完成验收，验收标准以本合同附件二为准。"),
            _A5,
            _A6,
            _A7,
            _A8,
        ),
        "revised": _doc(
            _A1,
            _A2,
            "第三条 付款期限：逾期付款违约金：甲方应在验收合格后60天内支付服务费。",
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
]
