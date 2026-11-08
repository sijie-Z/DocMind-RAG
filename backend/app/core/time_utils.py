"""时间工具：数据库列（MySQL DATETIME，无时区）统一存 naive UTC。"""

from datetime import UTC, datetime


def utcnow() -> datetime:
    """返回 naive UTC 时间，与 MySQL DATETIME 列和既有的 UTC 存入口径一致。

    主机直跑（非容器）时 Python 默认时区是本地时区；直接 ``datetime.now()``
    会把本地墙钟写进按 UTC 解释的列/JSON，前端解析后整体偏移。
    """
    return datetime.now(UTC).replace(tzinfo=None)
