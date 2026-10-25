"""
缓存工具 - 提供查询结果缓存功能

支持缓存 RAG 检索结果、SQL 查询结果等，减少重复计算。
"""

import json
import hashlib
import time
from pathlib import Path
from typing import Optional, Any, Dict
from functools import wraps


class QueryCache:
    """查询缓存管理器"""

    def __init__(self, cache_dir: str = None, max_age_seconds: int = 3600):
        """
        初始化缓存管理器

        Args:
            cache_dir: 缓存目录路径
            max_age_seconds: 缓存过期时间（秒），默认1小时
        """
        if cache_dir is None:
            cache_dir = str(Path(__file__).parent.parent / "memory" / "cache")

        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.max_age_seconds = max_age_seconds

        # 内存缓存（热数据）
        self._memory_cache: Dict[str, Dict[str, Any]] = {}

    def _generate_key(self, prefix: str, params: dict) -> str:
        """
        生成缓存键

        Args:
            prefix: 缓存前缀（如 'rag', 'sql'）
            params: 参数字典

        Returns:
            缓存键字符串
        """
        # 将参数序列化并生成哈希
        param_str = json.dumps(params, sort_keys=True, ensure_ascii=False)
        hash_value = hashlib.md5(param_str.encode()).hexdigest()[:12]
        return f"{prefix}_{hash_value}"

    def get(self, prefix: str, params: dict) -> Optional[Any]:
        """
        获取缓存值

        Args:
            prefix: 缓存前缀
            params: 参数字典

        Returns:
            缓存的值，如果不存在或过期则返回 None
        """
        key = self._generate_key(prefix, params)

        # 先检查内存缓存
        if key in self._memory_cache:
            entry = self._memory_cache[key]
            if time.time() - entry["timestamp"] < self.max_age_seconds:
                return entry["value"]
            else:
                # 过期，删除
                del self._memory_cache[key]

        # 检查文件缓存
        cache_file = self.cache_dir / f"{key}.json"
        if cache_file.exists():
            try:
                with open(cache_file, "r", encoding="utf-8") as f:
                    entry = json.load(f)

                # 检查是否过期
                if time.time() - entry["timestamp"] < self.max_age_seconds:
                    # 加载到内存缓存
                    self._memory_cache[key] = entry
                    return entry["value"]
                else:
                    # 过期，删除文件
                    cache_file.unlink()
            except (json.JSONDecodeError, KeyError):
                # 缓存文件损坏，删除
                cache_file.unlink()

        return None

    def set(self, prefix: str, params: dict, value: Any):
        """
        设置缓存值

        Args:
            prefix: 缓存前缀
            params: 参数字典
            value: 要缓存的值
        """
        key = self._generate_key(prefix, params)

        entry = {
            "timestamp": time.time(),
            "value": value
        }

        # 保存到内存缓存
        self._memory_cache[key] = entry

        # 保存到文件缓存
        cache_file = self.cache_dir / f"{key}.json"
        try:
            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump(entry, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[Cache] 写入缓存文件失败: {e}")

    def clear(self, prefix: str = None):
        """
        清除缓存

        Args:
            prefix: 缓存前缀，如果为 None 则清除所有缓存
        """
        # 清除内存缓存
        if prefix is None:
            self._memory_cache.clear()
        else:
            keys_to_remove = [k for k in self._memory_cache if k.startswith(prefix)]
            for key in keys_to_remove:
                del self._memory_cache[key]

        # 清除文件缓存
        if prefix is None:
            for cache_file in self.cache_dir.glob("*.json"):
                cache_file.unlink()
        else:
            for cache_file in self.cache_dir.glob(f"{prefix}_*.json"):
                cache_file.unlink()

    def get_stats(self) -> Dict[str, Any]:
        """获取缓存统计信息"""
        memory_count = len(self._memory_cache)
        file_count = len(list(self.cache_dir.glob("*.json")))

        return {
            "memory_cache_count": memory_count,
            "file_cache_count": file_count,
            "cache_dir": str(self.cache_dir),
            "max_age_seconds": self.max_age_seconds
        }


# 全局缓存实例
_global_cache = None


def get_cache(cache_dir: str = None, max_age_seconds: int = 3600) -> QueryCache:
    """
    获取全局缓存实例

    Args:
        cache_dir: 缓存目录路径
        max_age_seconds: 缓存过期时间（秒）

    Returns:
        QueryCache 实例
    """
    global _global_cache
    if _global_cache is None:
        _global_cache = QueryCache(cache_dir, max_age_seconds)
    return _global_cache


def cached(prefix: str, max_age_seconds: int = None):
    """
    缓存装饰器

    Args:
        prefix: 缓存前缀
        max_age_seconds: 缓存过期时间（可选）
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            cache = get_cache()

            # 生成缓存参数（排除 self）
            cache_params = {"args": str(args[1:]), "kwargs": str(kwargs)}

            # 尝试获取缓存
            cached_result = cache.get(prefix, cache_params)
            if cached_result is not None:
                return cached_result

            # 执行函数
            result = func(*args, **kwargs)

            # 缓存结果
            cache.set(prefix, cache_params, result)

            return result
        return wrapper
    return decorator
