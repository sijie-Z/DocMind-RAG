"""
日志配置模块
"""
import logging
import logging.handlers
import os
import json
import time
import contextvars
from app.core.config import settings

request_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("request_id", default=None)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": int(time.time()),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        request_id = request_id_var.get()
        if request_id:
            payload["request_id"] = request_id

        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)

        return json.dumps(payload, ensure_ascii=False)


def setup_logging():
    """设置日志配置"""
    # 创建日志目录
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
        when='midnight',
        interval=1,
        backupCount=30,
        encoding='utf-8'
    )

    if getattr(settings, "LOG_JSON", False):
        formatter: logging.Formatter = JsonFormatter()
    else:
        formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')

    console_handler.setFormatter(formatter)
    file_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)
    
    # 设置第三方库的日志级别
    logging.getLogger('uvicorn').setLevel(logging.INFO)
    logging.getLogger('sqlalchemy').setLevel(logging.WARNING)
    logging.getLogger('elasticsearch').setLevel(logging.WARNING)
    logging.getLogger('redis').setLevel(logging.WARNING)

    # 为 permission_service 模块单独设置日志级别，以便调试RBAC初始化过程
    logging.getLogger('app.services.permission_service').setLevel(logging.DEBUG)
    
    # 创建特定的日志器
    logger = logging.getLogger('paicongming')
    logger.setLevel(getattr(logging, settings.LOG_LEVEL.upper()))
    logger.propagate = True # 确保日志事件传播到父日志器

    # 为 permission_service 模块单独设置日志级别，以便调试RBAC初始化过程
    logging.getLogger('app.services.permission_service').setLevel(logging.DEBUG)

    logger.info("日志系统初始化完成")
