"""
RetailSage Agent - CLI 入口

零售/电商数据分析智能体的命令行交互界面。
"""

import logging
import os
import sys
import traceback
from pathlib import Path


# 添加项目路径
project_dir = Path(__file__).parent.parent
sys.path.insert(0, str(project_dir))

from dotenv import load_dotenv
load_dotenv()

from data_insight.config import (
    DB_CONFIG, DEFAULT_DB_TYPE, LLM_MODEL_ID, LLM_API_KEY, LLM_BASE_URL,
    KNOWLEDGE_DIR, OUTPUT_DIR, MEMORY_DIR, MAX_STEPS
)
from data_insight.agents.insight_agent import InsightAgent
from data_insight.utils.logger import setup_logging

# 初始化日志系统
setup_logging()
logger = logging.getLogger(__name__)


def print_banner():
    """打印启动横幅"""
    banner = """
╔══════════════════════════════════════════════════════════════╗
║                                                              ║
║   📊 RetailSage Agent - 零售/电商数据分析智能体               ║
║                                                              ║
║   让每个人都能用对话的方式，从数据中获得可行动的洞察         ║
║                                                              ║
║   输入您的数据分析问题，我来帮您解答                         ║
║   输入 'quit' 或 'exit' 退出                                 ║
║   输入 'help' 查看帮助                                       ║
║                                                              ║
╚══════════════════════════════════════════════════════════════╝
    """
    print(banner)


def print_help():
    """打印帮助信息"""
    help_text = """
📖 使用帮助：

🔍 数据查询示例：
  · "上个月华东区销售额是多少？"
  · "本月订单量环比变化如何？"
  · "哪些门店的GMV下降了？"

📊 分析请求示例：
  · "帮我分析一下上月业绩下降的原因"
  · "华东区和华北区的销售对比"
  · "近半年的销售趋势如何？"

📄 报告生成示例：
  · "帮我做一份上月经营分析报告"
  · "生成一份大促复盘PPT"

🧠 知识查询示例：
  · "GMV是什么意思？"
  · "复购率怎么计算？"

⚙️ 命令：
  · help - 显示帮助
  · clear - 清空对话历史
  · quit/exit - 退出程序
    """
    print(help_text)


def create_agent():
    """创建 Agent 实例"""
    # 获取数据库配置
    db_config = DB_CONFIG.get(DEFAULT_DB_TYPE, {})
    db_type = DEFAULT_DB_TYPE

    # 表结构信息（用于 NL2SQL）
    table_schemas = {
        "orders": {
            "description": "订单表，记录所有销售订单",
            "columns": [
                {"name": "order_id", "type": "VARCHAR", "description": "订单ID，主键"},
                {"name": "order_date", "type": "DATE", "description": "订单日期"},
                {"name": "region", "type": "VARCHAR", "description": "区域（华东/华北/华南/华西）"},
                {"name": "city", "type": "VARCHAR", "description": "城市"},
                {"name": "store_id", "type": "VARCHAR", "description": "门店ID"},
                {"name": "store_name", "type": "VARCHAR", "description": "门店名称"},
                {"name": "channel", "type": "VARCHAR", "description": "渠道（online线上/offline线下）"},
                {"name": "category", "type": "VARCHAR", "description": "商品品类（电子产品/服装/食品/家居/美妆）"},
                {"name": "product_name", "type": "VARCHAR", "description": "商品名称"},
                {"name": "quantity", "type": "INT", "description": "购买数量"},
                {"name": "order_amount", "type": "DECIMAL", "description": "订单金额（元）"},
                {"name": "cost_amount", "type": "DECIMAL", "description": "成本金额（元）"},
                {"name": "customer_id", "type": "VARCHAR", "description": "客户ID"},
                {"name": "is_member", "type": "INT", "description": "是否会员（0否/1是）"},
            ]
        },
        "customers": {
            "description": "客户表，记录客户基本信息",
            "columns": [
                {"name": "customer_id", "type": "VARCHAR", "description": "客户ID，主键"},
                {"name": "register_date", "type": "DATE", "description": "注册日期"},
                {"name": "region", "type": "VARCHAR", "description": "所在区域"},
                {"name": "city", "type": "VARCHAR", "description": "所在城市"},
                {"name": "gender", "type": "VARCHAR", "description": "性别（男/女）"},
                {"name": "age_group", "type": "VARCHAR", "description": "年龄段（18-25/26-35/36-45/46-55/55+）"},
                {"name": "total_orders", "type": "INT", "description": "累计订单数"},
                {"name": "total_amount", "type": "DECIMAL", "description": "累计消费金额"},
            ]
        },
        "stores": {
            "description": "门店表，记录门店信息",
            "columns": [
                {"name": "store_id", "type": "VARCHAR", "description": "门店ID，主键"},
                {"name": "store_name", "type": "VARCHAR", "description": "门店名称"},
                {"name": "region", "type": "VARCHAR", "description": "所在区域"},
                {"name": "city", "type": "VARCHAR", "description": "所在城市"},
                {"name": "store_type", "type": "VARCHAR", "description": "门店类型（旗舰店/标准店/社区店）"},
                {"name": "open_date", "type": "DATE", "description": "开业日期"},
            ]
        }
    }

    # 创建 Agent
    agent = InsightAgent.create(
        db_config=db_config,
        db_type=db_type,
        table_schemas=table_schemas,
        knowledge_dir=str(KNOWLEDGE_DIR),
        output_dir=str(OUTPUT_DIR),
        memory_dir=str(MEMORY_DIR),
        max_steps=MAX_STEPS
    )

    return agent


def main():
    """主函数"""
    logger.info("启动 RetailSage Agent CLI")
    print_banner()

    # 检查环境变量
    if not LLM_API_KEY:
        logger.warning("未设置 LLM_API_KEY，LLM 调用可能失败")
        print("⚠️  警告：未设置 LLM_API_KEY，请在 .env 文件中配置")
        print("   当前可能无法正常调用 LLM")

    # 创建 Agent
    logger.info("开始初始化 Agent，数据库类型=%s", DEFAULT_DB_TYPE)
    print("🚀 正在初始化 Agent...")
    try:
        agent = create_agent()
        logger.info("Agent 初始化完成")
        print("✅ Agent 初始化完成\n")
    except Exception as e:
        logger.exception("Agent 初始化失败: %s", e)
        print(f"❌ Agent 初始化失败: {e}")
        print("请检查配置后重试")
        return

    # 用户ID（简单实现，实际应用中应该有认证系统）
    user_id = "default_user"

    # 主循环
    while True:
        try:
            # 获取用户输入
            user_input = input("👤 你：").strip()

            # 处理空输入
            if not user_input:
                continue

            # 处理命令
            if user_input.lower() in ["quit", "exit", "q"]:
                print("\n👋 再见！期待下次为您服务。")
                break

            if user_input.lower() == "help":
                print_help()
                continue

            if user_input.lower() == "clear":
                agent.clear_history()
                print("✅ 对话历史已清空\n")
                continue

            # 运行 Agent
            logger.info("收到用户查询: %s", user_input)
            print(f"\n🤖 RetailSage：", end="")
            result = agent.run(user_input)
            logger.info("Agent 返回结果长度: %d 字符", len(str(result)))
            print(f"\n{result}\n")

        except KeyboardInterrupt:
            logger.info("用户中断会话")
            print("\n\n⚠️ 用户中断，正在保存会话...")
            try:
                agent.save_session("session-interrupted")
                logger.info("中断会话已保存")
                print("✅ 会话已保存")
            except Exception as save_err:
                logger.warning("保存中断会话失败: %s", save_err)
            print("👋 再见！")
            break

        except Exception as e:
            logger.exception("运行 Agent 时发生错误: %s", e)
            print(f"\n❌ 发生错误: {e}")
            print("📋 完整错误堆栈:")
            traceback.print_exc()
            print("\n请重试或输入 'help' 查看帮助\n")


if __name__ == "__main__":
    main()
