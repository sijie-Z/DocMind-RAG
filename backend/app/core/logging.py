# -*- coding: utf-8 -*-
"""结构化日志配置模块"""
import logging
import logging.handlers
import os
import json
import time
import contextvars

from app.core.config import settings

# 请求级上下文变量 — 中间件注入，日志格式化器读取
request_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("request_id", default=None)
trace_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("trace_id", default=None)
user_id_var: contextvars.ContextVar[str | int | None] = contextvars.ContextVar("user_id", default=None)


class JsonFormatter(logging.Formatter):
    """JSON 结构化日志格式化器。

    自动注入 request_id / trace_id / user_id（如果中间件已设置）。
    """

    def __init__(self, service_name: str = "docmind"):
        super().__init__()
        self.service_name = service_name

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(record.created)),
            "level": record.levelname,
            "service": self.service_name,
            "module": record.name,
            "message": record.getMessage(),
        }

        request_id = request_id_var.get()
        if request_id:
            payload["request_id"] = request_id

        trace_id = trace_id_var.get()
        if trace_id:
            payload["trace_id"] = trace_id

        user_id = user_id_var.get()
        if user_id is not None:
            payload["user_id"] = user_id

        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        if record.exc_text:
            payload["exc_text"] = record.exc_text

        # Include extra fields passed via extra={} in log calls
        for key, value in record.__dict__.items():
            if key not in (
                "args", "asctime", "created", "exc_info", "exc_text",
                "filename", "funcName", "levelname", "levelno", "lineno",
                "message", "module", "msecs", "msg", "name", "pathname",
                "process", "processName", "relativeCreated", "stack_info",
                "thread", "threadName", "service", "request_id", "trace_id",
                "user_id",
            ):
                if isinstance(value, (str, int, float, bool, list, dict)) or value is None:
                    payload[key] = value

        return json.dumps(payload, ensure_ascii=False)


def setup_logging():
    """设置日志配置"""
    log_dir = os.path.dirname(settings.LOG_FILE)
    if log_dir and not os.path.exists(log_dir):
        os.makedirs(log_dir)

    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, settings.LOG_LEVEL.upper()))

    while root_logger.handlers:
        root_logger.handlers.pop()

    console_handler = logging.StreamHandler()
    file_handler = logging.handlers.TimedRotatingFileHandler(
        settings.LOG_FILE,
        when="midnight",
        interval=1,
        backupCount=30,
        encoding="utf-8",
    )

    if getattr(settings, "LOG_JSON", True):
        formatter: logging.Formatter = JsonFormatter(service_name=settings.APP_NAME)
    else:
        formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")

    console_handler.setFormatter(formatter)
    file_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)

    # 第三方库静默
    logging.getLogger("uvicorn").setLevel(logging.INFO)
    logging.getLogger("sqlalchemy").setLevel(logging.WARNING)
    logging.getLogger("elasticsearch").setLevel(logging.WARNING)
    logging.getLogger("redis").setLevel(logging.WARNING)

    logging.getLogger("app.services.permission_service").setLevel(logging.DEBUG)

    logger = logging.getLogger("docmind")
    logger.setLevel(getattr(logging, settings.LOG_LEVEL.upper()))
    logger.propagate = True
    logger.info("日志系统初始化完成")
