"""
RetailSage Agent 日志模块

提供统一的日志配置与获取接口：
- 控制台输出（带颜色级别）
- 文件输出（按大小轮转，默认单文件 10MB，保留 5 个备份）
- 可通过环境变量 LOG_LEVEL / LOG_DIR 调整
"""

import logging
import logging.handlers
import sys
from pathlib import Path
from typing import Optional

from data_insight.config import LOG_DIR, LOG_LEVEL


DEFAULT_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
DEFAULT_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
DEFAULT_MAX_BYTES = 10 * 1024 * 1024  # 10 MB
DEFAULT_BACKUP_COUNT = 5
DEFAULT_LOG_FILENAME = "retailsage.log"


def setup_logging(
    log_dir: Optional[Path] = None,
    log_level: Optional[str] = None,
    log_format: Optional[str] = None,
) -> logging.Logger:
    """
    初始化项目日志系统。

    幂等设计：重复调用不会重复添加 Handler，仅会刷新日志级别。

    Args:
        log_dir: 日志目录，默认使用 config.LOG_DIR
        log_level: 日志级别，默认使用 config.LOG_LEVEL
        log_format: 日志格式字符串，默认使用项目标准格式

    Returns:
        配置好的 root logger
    """
    log_dir = Path(log_dir or LOG_DIR)
    log_dir.mkdir(parents=True, exist_ok=True)

    level_name = (log_level or LOG_LEVEL or "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    root = logging.getLogger()
    root.setLevel(level)

    # 避免重复配置
    if getattr(root, "_retailsage_logging_configured", False):
        return root
    root._retailsage_logging_configured = True  # type: ignore[attr-defined]

    fmt = log_format or DEFAULT_FORMAT
    formatter = logging.Formatter(fmt, datefmt=DEFAULT_DATE_FORMAT)

    # 控制台 Handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.set_name("retailsage_console")
    console_handler.setFormatter(formatter)
    root.addHandler(console_handler)

    # 文件 Handler（按大小轮转）
    log_file = log_dir / DEFAULT_LOG_FILENAME
    file_handler = logging.handlers.RotatingFileHandler(
        log_file,
        maxBytes=DEFAULT_MAX_BYTES,
        backupCount=DEFAULT_BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.set_name("retailsage_file")
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    logging.getLogger("hello_agents").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("gradio").setLevel(logging.WARNING)

    root.info("日志系统已启动: level=%s, file=%s", level_name, log_file)
    return root


def get_logger(name: str) -> logging.Logger:
    """
    获取指定名称的 logger。

    Args:
        name: logger 名称，建议使用 __name__

    Returns:
        logging.Logger 实例
    """
    return logging.getLogger(name)
