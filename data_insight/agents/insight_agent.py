"""
InsightAgent - 主编排 Agent

基于 ReActAgent，协调 UnderstandAgent、AnalysisAgent、ExpressionAgent
完成从自然语言到数据洞察的全流程。
"""

import logging
from typing import Optional
from hello_agents import ReActAgent, HelloAgentsLLM, ToolRegistry, Config
from hello_agents.tools.builtin import ReadTool, WriteTool

logger = logging.getLogger(__name__)

from ..tools.db_connector_tool import DBConnectorTool
from ..tools.sql_generator_tool import SQLGeneratorTool
from ..tools.data_analyzer_tool import DataAnalyzerTool
from ..tools.chart_generator_tool import ChartGeneratorTool
from ..tools.report_generator_tool import ReportGeneratorTool
from ..tools.memory_tool import MemoryTool
from ..rag.rag_tool import RAGTool


# 主 Agent 系统提示词
INSIGHT_SYSTEM_PROMPT = """你是 RetailSage，一个专业的零售/电商数据分析智能体。

## 你的能力
1. **数据查询**：通过自然语言查询数据库，获取销售、订单、客户等数据
2. **归因分析**：当数据出现异常波动时，分析原因并给出建议
3. **知识检索**：查询零售/电商行业知识（指标定义、业务规则等）
4. **可视化**：生成数据图表（柱状图、折线图、饼图等）
5. **报告生成**：将分析结果整理为 PDF/PPT 报告
6. **记忆管理**：记住用户的关注点和偏好

## 数据库信息
- 当前使用 **SQLite** 数据库
- SQLite 语法与 MySQL 不同：
  - 列出表: SELECT name FROM sqlite_master WHERE type='table'
  - 查看表结构: PRAGMA table_info(表名)
  - 不支持 SHOW TABLES、DESCRIBE 等 MySQL 语法

## 数据库表结构（已知，无需再次查询）
- orders: order_id, order_date, region, city, store_id, store_name, channel, category, product_name, quantity, order_amount, cost_amount, customer_id, is_member
- customers: customer_id, register_date, region, city, gender, age_group, total_orders, total_amount
- stores: store_id, store_name, region, city, store_type, open_date

## 工作流程
1. 使用 **Thought** 工具分析用户需求
2. 如果涉及业务术语，先用 **rag_search** 查询指标定义
3. 使用 **db_connector** 直接编写 SQL 查询数据（注意使用 SQLite 语法）
4. 使用 **data_analyzer** 进行统计分析或归因分析（metric 参数必须使用英文字段名）
5. 使用 **chart_generator** 生成可视化图表
6. 使用 **report_generator** 生成报告（如果用户需要）
7. 使用 **memory** 工具记录用户偏好
8. 使用 **Finish** 工具返回最终答案

## data_analyzer 使用规范
- analysis_type: statistics/trend/anomaly/drill_down/attribution
- data: JSON 格式数据
- metric: 必须使用英文字段名（如 order_count, order_amount），不能用中文
- time_column: 时间列名（用于趋势分析）
- dimensions: 维度列名（用于下钻/归因分析）

## 重要提醒
- 分析结果必须基于真实数据，不要编造数据
- 归因分析需要给出具体的原因和建议
- 使用中文回答
- 展示分析过程（白盒分析）
- 优先使用 db_connector 直接查询，效率更高
- **完成所有工作后，必须调用 Finish 工具返回最终答案，不要用 TodoWrite 结束**
- Finish 工具的 answer 参数应包含完整的分析结论或报告摘要

## 效率规范（严格执行）
- **不要查询表结构**：表结构已在上方给出，直接编写 SQL 即可
- **减少 TodoWrite 调用**：TodoWrite 每次调用消耗一个步骤，仅在关键节点使用（最多2-3次），不要每完成一个小任务就调用
- **合并查询**：能用一条 SQL 完成的分析不要拆成多条
- **优先级**：先完成核心分析，再做可视化和报告"""


class InsightAgent:
    """Insight Agent 工厂 - 创建完整的数据分析 Agent"""

    @staticmethod
    def create(
        llm: Optional[HelloAgentsLLM] = None,
        db_config: dict = None,
        db_type: str = "mysql",
        table_schemas: dict = None,
        knowledge_dir: str = None,
        output_dir: str = None,
        memory_dir: str = None,
        max_steps: int = 10,
        vector_store_dir: str = None,
        trace_enabled: bool = True
    ) -> ReActAgent:
        """
        创建完整的 InsightAgent

        Args:
            llm: LLM 实例（可选，默认从环境变量创建）
            db_config: 数据库配置
            db_type: 数据库类型
            table_schemas: 表结构信息
            knowledge_dir: 知识库目录
            output_dir: 输出目录
            memory_dir: 记忆存储目录
            max_steps: 最大执行步数
            vector_store_dir: 向量库目录。多用户场景应指向**共享**目录 ——
                知识库是全站共用的资料，每个用户各建一份既浪费又要各加载一次向量模型
            trace_enabled: 是否生成 trace 文件（每轮一个 HTML，多用户下没必要）

        Returns:
            配置好的 ReActAgent
        """
        import os
        from pathlib import Path

        # 默认路径
        project_dir = Path(__file__).parent.parent
        if knowledge_dir is None:
            knowledge_dir = str(project_dir / "knowledge")
        if output_dir is None:
            output_dir = str(project_dir / "output")
        if memory_dir is None:
            memory_dir = str(project_dir / "memory")

        logger.info(
            "创建 InsightAgent: db_type=%s, knowledge_dir=%s, output_dir=%s, memory_dir=%s",
            db_type, knowledge_dir, output_dir, memory_dir
        )

        # LLM
        if llm is None:
            llm = HelloAgentsLLM()

        # 配置
        config = Config(
            skills_enabled=True,
            skills_dir=str(project_dir / "skills"),
            skills_auto_register=True,
            session_enabled=True,
            session_dir=os.path.join(memory_dir, "sessions"),
            trace_enabled=trace_enabled,
            trace_dir=os.path.join(memory_dir, "traces"),
            subagent_enabled=True,
        )

        # 工具注册表
        registry = ToolRegistry()

        # 注册内置工具
        registry.register_tool(ReadTool(project_root=str(project_dir)))
        registry.register_tool(WriteTool(project_root=str(project_dir)))
        logger.debug("已注册内置工具 ReadTool/WriteTool")

        # 注册数据库工具
        if db_config:
            db_tool = DBConnectorTool(db_config=db_config, db_type=db_type)
            registry.register_tool(db_tool)
            logger.debug("已注册数据库工具: db_type=%s", db_type)

            # 注册 NL2SQL 工具
            sql_tool = SQLGeneratorTool(
                llm=llm,
                db_connector_tool=db_tool,
                table_schemas=table_schemas or {}
            )
            registry.register_tool(sql_tool)

        # 注册数据分析工具
        analyzer_tool = DataAnalyzerTool(llm=llm)
        registry.register_tool(analyzer_tool)

        # 注册图表工具
        chart_tool = ChartGeneratorTool(output_dir=os.path.join(output_dir, "charts"))
        registry.register_tool(chart_tool)

        # 注册报告工具
        report_tool = ReportGeneratorTool(output_dir=os.path.join(output_dir, "reports"))
        registry.register_tool(report_tool)

        # 注册记忆工具
        memory_tool = MemoryTool(
            user_profiles_dir=os.path.join(memory_dir, "user_profiles"),
            conversation_dir=os.path.join(memory_dir, "conversation_history")
        )
        registry.register_tool(memory_tool)

        # 注册 RAG 工具
        from ..config import EMBEDDING_MODEL
        rag_tool = RAGTool(
            knowledge_dir=knowledge_dir,
            vector_store_dir=vector_store_dir or os.path.join(memory_dir, "vector_store"),
            embedding_model=EMBEDDING_MODEL,
            llm=llm
        )
        registry.register_tool(rag_tool)
        logger.debug("已注册 RAG 工具")

        # 创建 Agent
        agent = ReActAgent(
            name="RetailSage",
            llm=llm,
            tool_registry=registry,
            system_prompt=INSIGHT_SYSTEM_PROMPT,
            config=config,
            max_steps=max_steps
        )
        logger.info(
            "ReActAgent 创建完成: name=%s, tools=%d, max_steps=%d",
            agent.name, len(registry.get_all_tools()), max_steps
        )

        return agent
