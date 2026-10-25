"""
UnderstandAgent - 意图识别与语义解析

基于 SimpleAgent，负责理解用户的自然语言输入，
提取意图、实体和上下文信息。
"""

from hello_agents import SimpleAgent, HelloAgentsLLM


# 理解 Agent 系统提示词
UNDERSTAND_SYSTEM_PROMPT = """你是一个意图识别专家，负责理解用户的自然语言查询。

## 你的任务
1. 识别用户意图（查询/分析/报告/对比/异常检测）
2. 提取关键实体（时间、区域、指标、维度）
3. 解析业务术语（通过指标字典）
4. 输出结构化的查询意图

## 输出格式
请以 JSON 格式输出：
```json
{
    "intent": "query|analyze|report|compare|anomaly",
    "metrics": ["GMV", "订单量"],
    "time_range": {"start": "2026-06-01", "end": "2026-06-30"},
    "dimensions": ["区域", "门店"],
    "filters": {"区域": "华东"},
    "analysis_type": "basic|trend|attribution",
    "output_format": "text|chart|report"
}
```"""


class UnderstandAgent:
    """Understand Agent 工厂"""

    @staticmethod
    def create(llm: HelloAgentsLLM = None) -> SimpleAgent:
        """
        创建 UnderstandAgent

        Args:
            llm: LLM 实例

        Returns:
            配置好的 SimpleAgent
        """
        if llm is None:
            llm = HelloAgentsLLM()

        agent = SimpleAgent(
            name="UnderstandAgent",
            llm=llm,
            system_prompt=UNDERSTAND_SYSTEM_PROMPT,
            enable_tool_calling=False,  # 纯文本解析，不需要工具
        )

        return agent
