"""
NL2SQL 工具 - 自然语言转 SQL

基于 LLM 的 Function Calling 能力，将用户的自然语言查询
转换为可执行的 SQL 语句。支持多轮修正机制提升准确率。
"""

import json
from typing import Optional, Dict, Any, List
from hello_agents.tools.base import Tool, ToolParameter
from hello_agents.tools.response import ToolResponse
from hello_agents.tools.errors import ToolErrorCode


class SQLGeneratorTool(Tool):
    """NL2SQL 工具 - 自然语言转 SQL 查询"""

    def __init__(self, llm, db_connector_tool, table_schemas: Dict[str, Any] = None):
        """
        初始化 NL2SQL 工具

        Args:
            llm: HelloAgentsLLM 实例
            db_connector_tool: DBConnectorTool 实例（用于获取表结构和执行SQL）
            table_schemas: 预定义的表结构信息（可选，避免重复查询）
        """
        super().__init__(
            name="sql_generator",
            description="将自然语言查询转换为SQL语句。"
                        "输入用户的自然语言问题，输出可执行的SQL。"
                        "支持多轮修正机制确保SQL正确性。"
        )
        self.llm = llm
        self.db_connector = db_connector_tool
        self.table_schemas = table_schemas or {}
        self._max_retries = 3

    def get_parameters(self) -> List[ToolParameter]:
        return [
            ToolParameter(
                name="question",
                type="string",
                description="用户的自然语言查询问题",
                required=True
            ),
            ToolParameter(
                name="context",
                type="string",
                description="额外上下文信息（如之前的对话、业务规则等）",
                required=False
            )
        ]

    def _build_schema_prompt(self) -> str:
        """构建表结构描述（供 LLM 理解数据库结构）"""
        if not self.table_schemas:
            # 尝试从数据库获取
            return "请先提供数据库表结构信息。"

        parts = ["## 数据库表结构\n"]
        for table_name, schema in self.table_schemas.items():
            parts.append(f"### 表: {table_name}")
            if isinstance(schema, dict):
                if "description" in schema:
                    parts.append(f"说明: {schema['description']}")
                if "columns" in schema:
                    parts.append("字段:")
                    for col in schema["columns"]:
                        col_name = col.get("name", col.get("Field", ""))
                        col_type = col.get("type", col.get("Type", ""))
                        col_desc = col.get("description", "")
                        parts.append(f"  - {col_name} ({col_type}): {col_desc}")
            parts.append("")

        return "\n".join(parts)

    def _generate_sql(self, question: str, context: str = "") -> str:
        """
        调用 LLM 生成 SQL

        Args:
            question: 用户问题
            context: 额外上下文

        Returns:
            生成的 SQL 语句
        """
        schema_prompt = self._build_schema_prompt()

        system_prompt = f"""你是一个专业的 SQL 生成助手。根据用户的自然语言问题，生成可执行的 SQL 查询语句。

{schema_prompt}

## 规则
1. 只生成 SELECT 查询，不允许 INSERT/UPDATE/DELETE/DROP 等操作
2. 使用标准 SQL 语法，兼容 MySQL
3. 字段名和表名必须与数据库结构一致
4. 对于时间范围查询，使用合适的日期函数
5. 只返回 SQL 语句本身，不要包含解释

## 输出格式
直接返回 SQL 语句，不要包含 markdown 代码块标记。"""

        user_content = f"问题: {question}"
        if context:
            user_content += f"\n\n上下文: {context}"

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content}
        ]

        response = self.llm.invoke(messages)
        sql = response.content.strip()

        # 清理 SQL（移除可能的 markdown 标记）
        if sql.startswith("```"):
            sql = sql.split("\n", 1)[-1]
        if sql.endswith("```"):
            sql = sql.rsplit("```", 1)[0]
        sql = sql.strip().rstrip(";")

        return sql

    def _validate_and_fix_sql(self, sql: str, question: str, error_msg: str = "") -> str:
        """
        验证并修复 SQL（多轮修正机制）

        Args:
            sql: 原始 SQL
            question: 用户问题
            error_msg: 执行错误信息（如果有）

        Returns:
            修复后的 SQL
        """
        if not error_msg:
            return sql

        system_prompt = """你是一个 SQL 修复专家。根据错误信息修复 SQL 语句。

## 规则
1. 只修复语法错误和逻辑错误
2. 保持原始查询意图不变
3. 只返回修复后的 SQL 语句
4. 不要包含解释"""

        user_content = f"""原始问题: {question}

SQL 语句:
{sql}

错误信息:
{error_msg}

请修复上述 SQL 语句。"""

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content}
        ]

        response = self.llm.invoke(messages)
        fixed_sql = response.content.strip()

        # 清理
        if fixed_sql.startswith("```"):
            fixed_sql = fixed_sql.split("\n", 1)[-1]
        if fixed_sql.endswith("```"):
            fixed_sql = fixed_sql.rsplit("```", 1)[0]

        return fixed_sql.strip().rstrip(";")

    def run(self, parameters: dict) -> ToolResponse:
        """
        执行 NL2SQL 转换

        Args:
            parameters: {"question": "上个月华东区销售额", "context": "..."}

        Returns:
            ToolResponse 包含生成的 SQL 和执行结果
        """
        question = parameters.get("question", "").strip()
        context = parameters.get("context", "")

        if not question:
            return ToolResponse.error(
                code=ToolErrorCode.INVALID_PARAM,
                message="查询问题不能为空"
            )

        try:
            # Step 1: 生成 SQL
            sql = self._generate_sql(question, context)

            # Step 2: 执行 SQL（带多轮修正）
            last_error = ""
            for attempt in range(self._max_retries):
                exec_result = self.db_connector.run({"sql": sql})

                if exec_result.status.value == "success":
                    # 成功：返回 SQL 和结果
                    return ToolResponse.success(
                        text=f"生成的SQL:\n{sql}\n\n{exec_result.text}",
                        data={
                            "sql": sql,
                            "query_result": exec_result.data,
                            "attempts": attempt + 1
                        }
                    )
                elif exec_result.status.value == "error":
                    # 失败：尝试修复
                    last_error = exec_result.error_info.get("message", "") if exec_result.error_info else ""
                    sql = self._validate_and_fix_sql(sql, question, last_error)
                else:
                    # 部分成功：也算成功
                    return ToolResponse.partial(
                        text=f"生成的SQL:\n{sql}\n\n{exec_result.text}",
                        data={
                            "sql": sql,
                            "query_result": exec_result.data,
                            "attempts": attempt + 1
                        }
                    )

            # 所有重试都失败
            return ToolResponse.error(
                code=ToolErrorCode.EXECUTION_ERROR,
                message=f"SQL生成失败（{self._max_retries}次尝试）: {last_error}\n最后生成的SQL: {sql}"
            )

        except Exception as e:
            return ToolResponse.error(
                code=ToolErrorCode.EXECUTION_ERROR,
                message=f"NL2SQL处理异常: {str(e)}"
            )

    def set_table_schemas(self, schemas: Dict[str, Any]):
        """设置表结构信息"""
        self.table_schemas = schemas
