"""MySQL 事务冲突判定：死锁与锁等待超时都是可安全重试的冲突。

- ``1213`` (ER_LOCK_DEADLOCK)：InnoDB 检测到锁序环，随机回滚牺牲者；
- ``1205`` (ER_LOCK_WAIT_TIMEOUT)：等待行锁超时——``innodb_deadlock_detect=OFF``
  的部署只会报它。

两者都意味着「这次事务因并发操作被 abort」，调用方应返回可重试的业务冲突
（409）而不是未处理异常（500）。判断依据是底层驱动异常的参数首位错误码
（aiomysql/asyncmy/pymysql 都会把驱动错误的 ``args[0]`` 保留为 errno）；
其它错误码或非 MySQL 驱动一律返回 False，保持原有异常路径不变。
"""

from __future__ import annotations

from sqlalchemy.exc import OperationalError

RETRYABLE_MYSQL_ERRS = frozenset({1205, 1213})


def is_retryable_mysql_conflict(exc: OperationalError) -> bool:
    """该 OperationalError 是否为可安全重试的 MySQL 事务冲突。"""
    orig = getattr(exc, "orig", None)
    args = getattr(orig, "args", ()) or ()
    return bool(args) and args[0] in RETRYABLE_MYSQL_ERRS
