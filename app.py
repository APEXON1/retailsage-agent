"""
RetailSage Agent - Web GUI 界面（DeepSeek 风格）

基于 Gradio 的 Web 对话界面，模仿 DeepSeek 简洁设计风格。
"""

import logging
import os
import sys
import traceback
from pathlib import Path

project_dir = Path(__file__).parent
sys.path.insert(0, str(project_dir))

from dotenv import load_dotenv
load_dotenv()

from data_insight.utils.logger import setup_logging

# 初始化日志系统
setup_logging()
logger = logging.getLogger(__name__)

import gradio as gr
from data_insight.config import (
    DB_CONFIG, DEFAULT_DB_TYPE, LLM_MODEL_ID, LLM_API_KEY, LLM_BASE_URL,
    KNOWLEDGE_DIR, OUTPUT_DIR, MEMORY_DIR, MAX_STEPS
)
from data_insight.agents.insight_agent import InsightAgent


agent = None


def init_agent():
    global agent
    if agent is not None:
        return agent

    logger.info("开始初始化 Web Agent，数据库类型=%s", DEFAULT_DB_TYPE)
    db_config = DB_CONFIG.get(DEFAULT_DB_TYPE, {})
    db_type = DEFAULT_DB_TYPE

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

    agent = InsightAgent.create(
        db_config=db_config,
        db_type=db_type,
        table_schemas=table_schemas,
        knowledge_dir=str(KNOWLEDGE_DIR),
        output_dir=str(OUTPUT_DIR),
        memory_dir=str(MEMORY_DIR),
        max_steps=MAX_STEPS
    )
    logger.info("Web Agent 初始化完成")
    return agent


def chat_fn(message, history):
    logger.info("收到 Web 用户消息，长度=%d 字符", len(message))
    try:
        a = init_agent()
        response = a.run(message)
        logger.info("Agent 返回结果长度=%d 字符", len(str(response)))
        return str(response)
    except Exception as e:
        logger.exception("Web 对话处理失败: %s", e)
        return f"❌ 发生错误: {e}\n\n{traceback.format_exc()}"


def clear_history():
    global agent
    if agent is not None:
        agent.clear_history()
    return []


CUSTOM_CSS = """
.gradio-container {
    max-width: 900px !important;
    margin: auto !important;
}
.main-panel {
    border: none !important;
    box-shadow: none !important;
}
.chatbot {
    border: none !important;
    border-radius: 12px !important;
    min-height: 500px !important;
}
.example-card {
    cursor: pointer !important;
    border: 1px solid #e5e7eb !important;
    border-radius: 12px !important;
    padding: 14px 18px !important;
    transition: all 0.2s ease !important;
    background: #fff !important;
    font-size: 14px !important;
    line-height: 1.5 !important;
}
.example-card:hover {
    border-color: #4f46e5 !important;
    background: #f5f3ff !important;
    box-shadow: 0 2px 8px rgba(79, 70, 229, 0.1) !important;
}
.welcome-title {
    text-align: center !important;
    margin-bottom: 0 !important;
}
.welcome-subtitle {
    text-align: center !important;
    color: #6b7280 !important;
    margin-top: 8px !important;
}
.sidebar-btn {
    border: 1px solid #e5e7eb !important;
    border-radius: 8px !important;
    background: #fff !important;
    font-weight: 500 !important;
    transition: all 0.15s ease !important;
}
.sidebar-btn:hover {
    background: #f9fafb !important;
    border-color: #d1d5db !important;
}
.footer-info {
    text-align: center !important;
    color: #9ca3af !important;
    font-size: 12px !important;
    margin-top: 8px !important;
}
"""


with gr.Blocks(
    title="RetailSage Agent",
    css=CUSTOM_CSS,
    fill_height=True,
) as demo:

    with gr.Row(equal_height=True):
        with gr.Column(scale=1, min_width=200):
            gr.Markdown("")
            new_chat_btn = gr.Button(
                "✏️ 新建对话",
                size="sm",
                elem_classes=["sidebar-btn"],
            )
            gr.Markdown(
                """
                ---
                ### 💡 我能做什么
                - 📊 数据查询：自然语言查销售额、订单量
                - 🔍 归因分析：找出业绩变化的原因
                - 📈 趋势分析：发现数据中的趋势
                - 📋 报告生成：一键生成分析报告
                - 📖 知识问答：解释业务指标含义
                """,
                elem_id="sidebar-info",
            )

        with gr.Column(scale=4):
            gr.Markdown(
                """
                <div class="welcome-title">
                    <h1 style="font-size: 28px; font-weight: 600; color: #111827;">📊 RetailSage Agent</h1>
                </div>
                <div class="welcome-subtitle">
                    <p style="font-size: 15px;">零售/电商数据分析智能体 — 用对话的方式从数据中获得洞察</p>
                </div>
                """,
            )

            chatbot = gr.ChatInterface(
                fn=chat_fn,
            )

            gr.Markdown(
                '<p class="footer-info">RetailSage Agent v0.1 · 基于 DeepSeek LLM · 数据仅供分析使用</p>',
            )

    new_chat_btn.click(clear_history)


if __name__ == "__main__":
    os.environ["no_proxy"] = "localhost,127.0.0.1,0.0.0.0"
    os.environ["NO_PROXY"] = "localhost,127.0.0.1,0.0.0.0"
    logger.info("启动 RetailSage Agent Web 界面")
    print("✅ 启动 Web 界面...")
    print("⏳ Agent 将在第一次对话时自动初始化")
    demo.launch(
        server_name="127.0.0.1",
        server_port=7860,
        share=False,
        inbrowser=True,
    )

