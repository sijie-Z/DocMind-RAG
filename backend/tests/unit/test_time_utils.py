"""时间工具：数据库/JSON 统一 naive UTC，避免主机直跑时写入本地墙钟。"""

from datetime import UTC, datetime

from app.core.time_utils import utcnow


def test_utcnow_is_naive_utc():
    now = utcnow()
    assert now.tzinfo is None
    current_utc_naive = datetime.now(UTC).replace(tzinfo=None)
    assert abs((current_utc_naive - now).total_seconds()) < 2
