"""弱口令残留的静态回归门禁（chore/test-credentials）。

背景：旧演示口令 `123456` 在本仓库反复"清而复生"——登录页模板、FAQ、
评测集"标准答案"、压测脚本、诊断脚本都出现过；`check_login.py` 则把
`admin/123456` 直接写死。本测试把本 chore 的验收线钉成静态断言（不依赖
网络 / 数据库 / aiohttp；CI 以 --noconftest 也能跑）：

- backend/tests/unit/eval_dataset.jsonl：标准答案不得含口令明文；
- backend/full_stress_test.py：压测账号必须读环境变量、缺省报错；
- backend/scripts/check_login.py：已按死代码审计「立即清」删除，不得复活。

变异点（任一成立 → 对应用例精确红）：数据集答案回填弱口令 / 压测脚本
回落默认值 / check_login.py 被重新提交。
"""

from pathlib import Path

_UNIT_DIR = Path(__file__).resolve().parent
_BACKEND_DIR = _UNIT_DIR.parents[1]

_OLD_WEAK_PASSWORD = "123456"


def test_eval_dataset_answer_has_no_password_plaintext():
    """评测集标准答案不得把已废弃弱口令写进去。"""
    lines = (_UNIT_DIR / "eval_dataset.jsonl").read_text(encoding="utf-8").splitlines()
    offenders = [n for n, line in enumerate(lines, 1) if _OLD_WEAK_PASSWORD in line]
    assert not offenders, (
        f"eval_dataset.jsonl 第 {offenders} 行仍含旧弱口令；"
        "演示账号密码为随机生成 / 由环境变量指定，标准答案不得写出口令明文。"
    )


def test_stress_test_reads_credentials_from_env():
    """压测脚本必须读环境变量，且源码不得残留弱口令或默认账号。"""
    source = (_BACKEND_DIR / "full_stress_test.py").read_text(encoding="utf-8")
    assert "STRESS_TEST_USERNAME" in source
    assert "STRESS_TEST_PASSWORD" in source
    assert "os.environ" in source, "账号必须来自 os.environ"
    assert "raise SystemExit" in source, "缺省（未设置环境变量）必须报错退出"
    assert _OLD_WEAK_PASSWORD not in source, "不得残留旧弱口令明文"
    assert "'guest'" not in source, "不得回落默认账号 guest"


def test_check_login_script_stays_deleted():
    """check_login.py 属死代码审计「立即清」，删除后不得复活。"""
    assert not (_BACKEND_DIR / "scripts" / "check_login.py").exists(), (
        "backend/scripts/check_login.py 已删除（死代码审计 §③「立即清」："
        "零引用 + 硬编码 admin/123456），请勿重新提交。"
    )
