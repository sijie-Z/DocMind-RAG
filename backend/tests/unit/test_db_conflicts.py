"""MySQL 事务冲突判定（Batch 11：从 review_case_service 提取为共享工具）。

1213 死锁与 1205 锁等待超时都是可安全重试的冲突；其它错误码必须原样走
原有异常路径（不能被误吞成 409）。
"""

from sqlalchemy.exc import OperationalError

from app.core.db_conflicts import RETRYABLE_MYSQL_ERRS, is_retryable_mysql_conflict


def _op_error(code: int) -> OperationalError:
    # 真实 asyncmy/aiomysql 驱动异常的 args 是 (errno, message) 两元组；
    # 用单参数形态会放过 args[0]→args[-1] 的变异（评审实测存活）。
    return OperationalError("SELECT 1", {}, Exception(code, "driver message"))


def test_retryable_error_codes():
    assert set(RETRYABLE_MYSQL_ERRS) == {1205, 1213}
    assert is_retryable_mysql_conflict(_op_error(1213)) is True
    assert is_retryable_mysql_conflict(_op_error(1205)) is True


def test_non_retryable_codes_and_missing_orig():
    assert is_retryable_mysql_conflict(_op_error(1317)) is False
    assert is_retryable_mysql_conflict(_op_error(1064)) is False
    # 无驱动错误码 / 空参数：不能把「不知道是什么错」当成可重试
    assert is_retryable_mysql_conflict(OperationalError("SELECT 1", {}, Exception())) is False
    assert is_retryable_mysql_conflict(OperationalError("SELECT 1", {}, None)) is False
    # 非 MySQL 驱动的 orig.args[0] 可能是字符串（如 SQLite），不得误判
    assert (
        is_retryable_mysql_conflict(OperationalError("SELECT 1", {}, Exception("syntax error")))
        is False
    )
    # 字符串形式的错误码不得与整数码混判（严格类型）
    assert (
        is_retryable_mysql_conflict(OperationalError("SELECT 1", {}, Exception("1213", "x")))
        is False
    )
