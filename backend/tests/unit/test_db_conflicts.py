"""MySQL 事务冲突判定（Batch 11：从 review_case_service 提取为共享工具）。

1213 死锁与 1205 锁等待超时都是可安全重试的冲突；其它错误码必须原样走
原有异常路径（不能被误吞成 409）。
"""

from sqlalchemy.exc import OperationalError

from app.core.db_conflicts import RETRYABLE_MYSQL_ERRS, is_retryable_mysql_conflict


def _op_error(code: int) -> OperationalError:
    return OperationalError("SELECT 1", {}, Exception(code))


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
