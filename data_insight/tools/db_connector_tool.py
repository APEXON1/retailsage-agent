"""
数据库连接工具

支持 MySQL / PostgreSQL / ClickHouse / SQLite 的连接和查询执行。
所有查询结果通过 ToolResponse 返回给 Agent。
支持查询结果缓存和友好的错误提示。
"""

import json
import logging
import time
from typing import Optional, Dict, Any, List
from hello_agents.tools.base import Tool, ToolParameter
from hello_agents.tools.response import ToolResponse
from hello_agents.tools.errors import ToolErrorCode

logger = logging.getLogger(__name__)


class DBConnectorTool(Tool):
    """数据库连接工具 - 支持多种数据库的查询执行"""

    def __init__(self, db_config: Dict[str, Any], db_type: str = "mysql", enable_cache: bool = True):
        """
        初始化数据库连接工具

        Args:
            db_config: 数据库连接配置
            db_type: 数据库类型 (mysql/postgresql/clickhouse/sqlite)
            enable_cache: 是否启用查询缓存
        """
        super().__init__(
            name="db_connector",
            description="执行SQL查询并返回结果。支持MySQL、PostgreSQL、ClickHouse、SQLite。"
                        "输入SQL语句，返回查询结果（JSON格式）。"
        )
        self.db_config = db_config
        self.db_type = db_type
        self._engine = None
        self.enable_cache = enable_cache

        # 初始化缓存
        self._cache = None
        if enable_cache:
            try:
                from .cache_tool import get_cache
                self._cache = get_cache(max_age_seconds=600)  # 10分钟缓存
            except ImportError:
                pass

    def _get_engine(self):
        """获取数据库连接（懒加载）"""
        if self._engine is None:
            from sqlalchemy import create_engine

            if self.db_type == "sqlite":
                # SQLite: db_config 中需要有 database 字段（文件路径）
                db_path = self.db_config.get("database", ":memory:")
                url = f"sqlite:///{db_path}"
            elif self.db_type == "mysql":
                url = (
                    f"mysql+pymysql://{self.db_config['user']}:{self.db_config['password']}"
                    f"@{self.db_config['host']}:{self.db_config['port']}/{self.db_config['database']}"
                )
            elif self.db_type == "postgresql":
                url = (
                    f"postgresql://{self.db_config['user']}:{self.db_config['password']}"
                    f"@{self.db_config['host']}:{self.db_config['port']}/{self.db_config['database']}"
                )
            elif self.db_type == "clickhouse":
                url = (
                    f"clickhouse://{self.db_config['user']}:{self.db_config['password']}"
                    f"@{self.db_config['host']}:{self.db_config['port']}/{self.db_config['database']}"
                )
            else:
                raise ValueError(f"不支持的数据库类型: {self.db_type}")

            self._engine = create_engine(url, pool_pre_ping=True)
        return self._engine

    def get_parameters(self) -> List[ToolParameter]:
        return [
            ToolParameter(
                name="sql",
                type="string",
                description="要执行的SQL查询语句",
                required=True
            ),
            ToolParameter(
                name="limit",
                type="integer",
                description="返回结果的最大行数，默认100",
                required=False
            )
        ]

    def run(self, parameters: dict) -> ToolResponse:
        """
        执行SQL查询

        Args:
            parameters: {"sql": "SELECT ...", "limit": 100}

        Returns:
            ToolResponse 包含查询结果
        """
        sql = parameters.get("sql", "").strip()
        limit = parameters.get("limit", 100)

        logger.debug("执行 SQL 查询: %s (limit=%d)", sql[:200], limit)

        if not sql:
            return ToolResponse.error(
                code=ToolErrorCode.INVALID_PARAM,
                message="SQL语句不能为空"
            )

        # 安全检查：禁止危险操作
        dangerous_keywords = ["DROP", "DELETE", "TRUNCATE", "ALTER", "INSERT", "UPDATE"]
        sql_upper = sql.upper()
        for keyword in dangerous_keywords:
            if keyword in sql_upper:
                return ToolResponse.error(
                    code=ToolErrorCode.INVALID_PARAM,
                    message=f"安全限制：不允许执行 {keyword} 操作，仅支持 SELECT 查询"
                )

        # 添加 LIMIT 保护（PRAGMA 语句不加 LIMIT）
        if "LIMIT" not in sql_upper and not sql_upper.startswith("PRAGMA"):
            sql = f"{sql.rstrip(';')} LIMIT {limit}"

        # 检查缓存（只缓存 SELECT 查询）
        if self._cache and sql_upper.startswith("SELECT"):
            cache_key = {"sql": sql}
            cached_result = self._cache.get("sql", cache_key)
            if cached_result is not None:
                logger.debug("SQL 缓存命中: %s", sql[:200])
                return ToolResponse.success(
                    text=cached_result["text"],
                    data=cached_result["data"]
                )
            logger.debug("SQL 缓存未命中: %s", sql[:200])

        try:
            import pandas as pd
            engine = self._get_engine()

            # 执行查询
            start_time = time.time()
            df = pd.read_sql(sql, engine)
            elapsed_ms = (time.time() - start_time) * 1000
            logger.info("SQL 执行成功: 耗时=%.2fms, 行数=%d", elapsed_ms, len(df))

            # 转换为结果
            row_count = len(df)
            columns = list(df.columns)

            if row_count == 0:
                return ToolResponse.success(
                    text="查询结果为空，没有匹配的数据。",
                    data={"columns": columns, "rows": [], "row_count": 0}
                )

            # 限制返回数据量（避免 token 过大）
            if row_count > 50:
                preview_df = df.head(50)
                truncated = True
            else:
                preview_df = df
                truncated = False

            # 转换为 JSON 可序列化格式
            rows = preview_df.to_dict(orient="records")
            # 处理特殊类型（Timestamp, Decimal 等）
            rows = json.loads(json.dumps(rows, default=str))

            # 构建文本结果
            text_parts = [f"查询成功，共 {row_count} 行结果。"]
            if truncated:
                text_parts.append(f"（显示前 50 行，共 {row_count} 行）")

            text_parts.append(f"\n列名: {', '.join(columns)}")
            text_parts.append(f"\n数据预览:")
            for i, row in enumerate(rows[:10]):  # 文本中只显示前10行
                text_parts.append(f"  {i+1}. {row}")

            if row_count > 10:
                text_parts.append(f"  ... 还有 {row_count - 10} 行")

            result_text = "\n".join(text_parts)
            logger.debug("SQL 查询结果: 行数=%d, 列数=%d", row_count, len(columns))
            result_data = {
                "columns": columns,
                "rows": rows,
                "row_count": row_count,
                "truncated": truncated,
                "sql_executed": sql
            }

            # 保存到缓存（只缓存 SELECT 查询）
            if self._cache and sql_upper.startswith("SELECT"):
                self._cache.set("sql", cache_key, {
                    "text": result_text,
                    "data": result_data
                })

            return ToolResponse.success(
                text=result_text,
                data=result_data
            )

        except Exception as e:
            logger.exception("SQL 执行失败: %s", sql[:500])
            # 提供友好的错误提示
            error_msg = str(e)
            if "no such table" in error_msg.lower():
                friendly_msg = f"表不存在: {error_msg}. 请使用 list_tables 查看可用的表"
            elif "no such column" in error_msg.lower():
                friendly_msg = f"字段不存在: {error_msg}. 请使用 get_table_schema 查看表结构"
            elif "syntax error" in error_msg.lower():
                friendly_msg = f"SQL语法错误: {error_msg}"
            elif "connection" in error_msg.lower():
                friendly_msg = f"数据库连接失败: {error_msg}. 请检查数据库配置"
            else:
                friendly_msg = f"SQL执行失败: {error_msg}"

            return ToolResponse.error(
                code=ToolErrorCode.EXECUTION_ERROR,
                message=friendly_msg
            )

    def get_table_schema(self, table_name: str) -> ToolResponse:
        """
        获取表结构信息（用于 NL2SQL）

        Args:
            table_name: 表名

        Returns:
            ToolResponse 包含表结构
        """
        try:
            import pandas as pd
            engine = self._get_engine()

            if self.db_type == "sqlite":
                sql = f"PRAGMA table_info({table_name})"
            elif self.db_type == "mysql":
                sql = f"DESCRIBE {table_name}"
            elif self.db_type == "postgresql":
                sql = f"""
                SELECT column_name, data_type, is_nullable
                FROM information_schema.columns
                WHERE table_name = '{table_name}'
                ORDER BY ordinal_position
                """
            elif self.db_type == "clickhouse":
                sql = f"DESCRIBE TABLE {table_name}"
            else:
                return ToolResponse.error(
                    code=ToolErrorCode.INVALID_PARAM,
                    message=f"不支持的数据库类型: {self.db_type}"
                )

            df = pd.read_sql(sql, engine)
            schema_info = df.to_dict(orient="records")

            return ToolResponse.success(
                text=f"表 {table_name} 的结构:\n{df.to_string(index=False)}",
                data={"table": table_name, "columns": schema_info}
            )

        except Exception as e:
            logger.exception("获取表结构失败: %s", table_name)
            error_msg = str(e)
            if "no such table" in error_msg.lower():
                friendly_msg = f"表 '{table_name}' 不存在，请使用 list_tables 查看可用的表"
            else:
                friendly_msg = f"获取表结构失败: {error_msg}"

            return ToolResponse.error(
                code=ToolErrorCode.EXECUTION_ERROR,
                message=friendly_msg
            )

    def list_tables(self) -> ToolResponse:
        """列出数据库中的所有表"""
        try:
            import pandas as pd
            engine = self._get_engine()

            if self.db_type == "sqlite":
                sql = "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            elif self.db_type == "mysql":
                sql = "SHOW TABLES"
            elif self.db_type == "postgresql":
                sql = """
                SELECT table_name FROM information_schema.tables
                WHERE table_schema = 'public'
                """
            elif self.db_type == "clickhouse":
                sql = "SHOW TABLES"
            else:
                return ToolResponse.error(
                    code=ToolErrorCode.INVALID_PARAM,
                    message=f"不支持的数据库类型: {self.db_type}"
                )

            df = pd.read_sql(sql, engine)
            tables = df.iloc[:, 0].tolist()

            return ToolResponse.success(
                text=f"数据库中共有 {len(tables)} 张表:\n" + "\n".join(f"  - {t}" for t in tables),
                data={"tables": tables, "count": len(tables)}
            )

        except Exception as e:
            logger.exception("获取表列表失败")
            error_msg = str(e)
            if "connection" in error_msg.lower():
                friendly_msg = f"数据库连接失败: {error_msg}. 请检查数据库配置"
            else:
                friendly_msg = f"获取表列表失败: {error_msg}"

            return ToolResponse.error(
                code=ToolErrorCode.EXECUTION_ERROR,
                message=friendly_msg
            )
