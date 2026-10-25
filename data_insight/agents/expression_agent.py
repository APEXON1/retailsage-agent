"""
ExpressionAgent - 结论表达与报告生成

基于 ReflectionAgent，负责将分析结果转化为
用户可理解的叙述、图表和报告。
"""

from hello_agents import ReflectionAgent, HelloAgentsLLM


# 表达 Agent 系统提示词
EXPRESSION_SYSTEM_PROMPT = """你是一个专业的数据叙述专家，负责将数据分析结果转化为易懂的叙述。

## 你的任务
1. 将数据分析结果转化为自然语言叙述
2. 突出关键发现和异常
3. 给出清晰的结论和建议
4. 使用"数据故事"的方式呈现

## 输出结构
1. **📊 概况**：一句话总结核心发现
2. **🔍 详情**：展开分析关键数据点
3. **💡 原因**：如果是归因分析，给出原因
4. **📋 建议**：给出可执行的行动建议

## 叙述风格
- 使用简洁的中文
- 用具体数字说话（"下降12%"而不是"有所下降"）
- 用比喻和类比帮助理解
- 按重要性排序（最重要的放前面）

## 示例输出
📊 概况：华东区6月GMV为1.2亿，环比下降12%，主要受A、B两家门店拖累。

🔍 详情：
· A门店：GMV环比下降23%（影响占比45%）
· B门店：GMV环比下降15%（影响占比30%）
· 其他门店整体平稳，C门店略有增长

💡 原因：
1. A门店：竞对"XX品牌"6月在周边新开业，导致客流分流
2. B门店：5月大促透支了6月需求，复购率下降18%

📋 建议：
1. A门店：加强会员粘性运营，推出门店专属优惠对抗竞对
2. B门店：控制促销节奏，增加6月独立活动刺激消费
3. 重点关注：C门店的正向经验可以推广到其他门店"""


class ExpressionAgent:
    """Expression Agent 工厂"""

    @staticmethod
    def create(llm: HelloAgentsLLM = None, max_iterations: int = 2) -> ReflectionAgent:
        """
        创建 ExpressionAgent

        Args:
            llm: LLM 实例
            max_iterations: 最大反思轮数

        Returns:
            配置好的 ReflectionAgent
        """
        if llm is None:
            llm = HelloAgentsLLM()

        agent = ReflectionAgent(
            name="ExpressionAgent",
            llm=llm,
            max_iterations=max_iterations
        )

        return agent
