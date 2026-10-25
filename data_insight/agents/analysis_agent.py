"""
AnalysisAgent - 数据分析与归因

基于 ReActAgent，负责执行数据查询、统计分析和归因分析。
"""

from hello_agents import ReActAgent, HelloAgentsLLM, ToolRegistry

from ..tools.db_connector_tool import DBConnectorTool
from ..tools.sql_generator_tool import SQLGeneratorTool
from ..tools.data_analyzer_tool import DataAnalyzerTool


# 分析 Agent 系统提示词
ANALYSIS_SYSTEM_PROMPT = """你是一个专业的数据分析师，负责执行数据查询和分析。

## 你的能力
1. 使用 sql_generator 将自然语言转换为 SQL 并查询数据
2. 使用 data_analyzer 进行统计分析、趋势分析、异常检测
3. 使用 data_analyzer 进行维度下钻和归因分析

## 工作流程
1. 使用 **Thought** 分析需要什么数据
2. 使用 **sql_generator** 查询数据
3. 使用 **data_analyzer** 分析数据
4. 使用 **Finish** 返回分析结果

## 归因分析要求
当用户问"为什么"时，需要：
1. 对关键维度进行下钻（门店、品类、时段等）
2. 找出偏离最大的维度值
3. 给出可能的原因和证据
4. 按影响度排序
5. 给出可执行的建议

## 重要
- 所有结论必须基于数据
- 给出具体数字，不要模糊描述
- 使用中文回答"""


class AnalysisAgent:
    """Analysis Agent 工厂"""

    @staticmethod
    def create(
        llm: HelloAgentsLLM = None,
        db_config: dict = None,
        db_type: str = "mysql",
        table_schemas: dict = None,
        max_steps: int = 5
    ) -> ReActAgent:
        """
        创建 AnalysisAgent

        Args:
            llm: LLM 实例
            db_config: 数据库配置
            db_type: 数据库类型
            table_schemas: 表结构信息
            max_steps: 最大执行步数

        Returns:
            配置好的 ReActAgent
        """
        if llm is None:
            llm = HelloAgentsLLM()

        registry = ToolRegistry()

        # 注册数据库工具
        if db_config:
            db_tool = DBConnectorTool(db_config=db_config, db_type=db_type)
            registry.register_tool(db_tool)

            sql_tool = SQLGeneratorTool(
                llm=llm,
                db_connector_tool=db_tool,
                table_schemas=table_schemas or {}
            )
            registry.register_tool(sql_tool)

        # 注册分析工具
        analyzer_tool = DataAnalyzerTool(llm=llm)
        registry.register_tool(analyzer_tool)

        agent = ReActAgent(
            name="AnalysisAgent",
            llm=llm,
            tool_registry=registry,
            system_prompt=ANALYSIS_SYSTEM_PROMPT,
            max_steps=max_steps
        )

        return agent
