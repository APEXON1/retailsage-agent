"""
错误处理工具 - 统一错误处理和重试机制

提供友好的错误提示和自动重试功能。
"""

import logging
import time
import traceback
from typing import Callable, Any, Optional, Dict
from functools import wraps
from hello_agents.tools.response import ToolResponse
from hello_agents.tools.errors import ToolErrorCode

logger = logging.getLogger(__name__)


# 错误类型映射到用户友好提示
ERROR_MESSAGES = {
    # 数据库错误
    "CONNECTION_ERROR": "数据库连接失败，请检查数据库配置和网络连接",
    "QUERY_ERROR": "SQL查询执行失败，请检查SQL语法",
    "TIMEOUT_ERROR": "查询超时，请尝试简化查询条件或添加LIMIT限制",
    "PERMISSION_ERROR": "权限不足，请检查数据库用户权限",

    # RAG 错误
    "MODEL_LOAD_ERROR": "模型加载失败，请检查网络连接或模型路径",
    "INDEX_ERROR": "索引构建失败，请检查知识库文件",
    "SEARCH_ERROR": "检索失败，请重试或检查查询内容",

    # LLM 错误
    "API_ERROR": "LLM API调用失败，请检查API Key和网络连接",
    "RATE_LIMIT_ERROR": "API调用频率过高，请稍后重试",
    "TOKEN_LIMIT_ERROR": "输入内容过长，请简化查询或分批处理",

    # 通用错误
    "INVALID_PARAM": "参数错误，请检查输入格式",
    "NOT_FOUND": "未找到相关数据",
    "UNKNOWN_ERROR": "发生未知错误，请重试或联系管理员",
}


def get_user_friendly_message(error: Exception, context: str = "") -> str:
    """
    获取用户友好的错误提示

    Args:
        error: 异常对象
        context: 错误上下文

    Returns:
        用户友好的错误消息
    """
    error_type = type(error).__name__
    error_msg = str(error)

    # 数据库连接错误
    if "Connection" in error_type or "connect" in error_msg.lower():
        return ERROR_MESSAGES["CONNECTION_ERROR"]

    # SQL语法错误
    if "SyntaxError" in error_type or "syntax" in error_msg.lower():
        return f"SQL语法错误: {error_msg[:100]}"

    # 超时错误
    if "Timeout" in error_type or "timeout" in error_msg.lower():
        return ERROR_MESSAGES["TIMEOUT_ERROR"]

    # 权限错误
    if "Permission" in error_type or "permission" in error_msg.lower():
        return ERROR_MESSAGES["PERMISSION_ERROR"]

    # API错误
    if "APIError" in error_type or "api" in error_msg.lower():
        if "rate" in error_msg.lower():
            return ERROR_MESSAGES["RATE_LIMIT_ERROR"]
        return ERROR_MESSAGES["API_ERROR"]

    # 默认消息
    if context:
        return f"{context}: {error_msg[:200]}"
    return f"操作失败: {error_msg[:200]}"


def retry_on_error(max_retries: int = 3, delay: float = 1.0, backoff: float = 2.0):
    """
    重试装饰器

    Args:
        max_retries: 最大重试次数
        delay: 初始延迟时间（秒）
        backoff: 延迟倍增因子
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            last_error = None
            current_delay = delay

            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    last_error = e
                    if attempt < max_retries:
                        logger.warning(
                            "[%s] 第 %d 次重试失败，%.1f 秒后重试: %s",
                            func.__name__, attempt + 1, current_delay, e
                        )
                        time.sleep(current_delay)
                        current_delay *= backoff
                    else:
                        logger.error(
                            "[%s] 已达到最大重试次数 (%d): %s",
                            func.__name__, max_retries, last_error
                        )

            # 所有重试都失败
            raise last_error

        return wrapper
    return decorator


def safe_execute(func: Callable, *args, default=None, error_context: str = "", **kwargs) -> Any:
    """
    安全执行函数，捕获异常并返回默认值

    Args:
        func: 要执行的函数
        *args: 函数参数
        default: 异常时返回的默认值
        error_context: 错误上下文描述
        **kwargs: 函数关键字参数

    Returns:
        函数执行结果或默认值
    """
    try:
        return func(*args, **kwargs)
    except Exception as e:
        logger.exception("[%s] %s: %s", func.__name__, error_context, e)
        return default


def create_error_response(error: Exception, context: str = "") -> ToolResponse:
    """
    创建错误响应

    Args:
        error: 异常对象
        context: 错误上下文

    Returns:
        ToolResponse 错误响应
    """
    friendly_message = get_user_friendly_message(error, context)

    # 确定错误代码
    error_type = type(error).__name__
    if "Connection" in error_type:
        code = ToolErrorCode.EXECUTION_ERROR
    elif "Timeout" in error_type:
        code = ToolErrorCode.EXECUTION_ERROR
    elif "Value" in error_type or "Type" in error_type:
        code = ToolErrorCode.INVALID_PARAM
    else:
        code = ToolErrorCode.EXECUTION_ERROR

    return ToolResponse.error(code=code, message=friendly_message)


class ErrorContext:
    """错误上下文管理器"""

    def __init__(self, operation: str, suppress: bool = False):
        """
        初始化错误上下文

        Args:
            operation: 操作描述
            suppress: 是否抑制异常（不抛出）
        """
        self.operation = operation
        self.suppress = suppress
        self.error = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_val is not None:
            self.error = exc_val
            friendly_msg = get_user_friendly_message(exc_val, self.operation)
            logger.exception("[%s] %s", self.operation, exc_val)

            if not self.suppress:
                return False  # 不抑制异常
            return True  # 抑制异常
        return False


def format_error_for_llm(error: Exception, context: str = "") -> str:
    """
    格式化错误信息供LLM理解

    Args:
        error: 异常对象
        context: 错误上下文

    Returns:
        LLM可理解的错误描述
    """
    friendly_msg = get_user_friendly_message(error, context)

    return f"""错误信息: {friendly_msg}

请根据错误信息调整你的操作：
1. 如果是SQL语法错误，请检查SQL语句并修正
2. 如果是连接错误，请检查数据库配置
3. 如果是超时错误，请简化查询或添加更多限制条件
4. 如果是权限错误，请联系管理员

你可以尝试：
- 使用不同的查询方式
- 检查表名和字段名是否正确
- 添加LIMIT限制返回行数"""
