"""
RetailSage Agent - Web GUI 界面（DeepSeek 风格）

基于 Gradio 的 Web 对话界面，模仿 DeepSeek 简洁设计风格。

两种运行方式：
1. 单机直跑（无认证）：`python app.py`，监听 7860，行为与改造前一致；
2. 门户托管：由 run_insight_web.py 通过 `build_demo()` 挂到 FastAPI 下，
   由门户下发的 rs_session Cookie 判定身份，每个账号独立一份 Agent 实例
   与记忆 / 图表 / 报告目录（内置知识库与业务库仍全站共享）。
"""

import logging
import os
import re
import sys
import threading
import traceback
from collections import OrderedDict
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


# ==================== 数据库表结构说明 ====================
TABLE_SCHEMAS = {
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


# ==================== 按账号隔离的 Agent 实例池 ====================
# 为什么要 per-user：ReActAgent 的会话历史是实例级状态，共享实例会让 A 的对话
# 进入 B 的上下文；而且 hello_agents 内部有并发守卫，同一实例并发 run 会直接抛错。
# 为什么缓存而不是每次重建：重建会重新加载向量模型并重建知识索引，单次 5-10 秒。
LOCAL_USER = "local"          # 单机直跑（无认证）时使用的身份
MAX_CACHED_AGENTS = 5         # 常驻上限（每个 Agent 自带一个向量模型，别无限涨）

_AGENTS: "OrderedDict[str, object]" = OrderedDict()
_LOCKS: dict[str, threading.Lock] = {}
_GUARD = threading.Lock()


def _slug(user_id: str) -> str:
    """邮箱里的 @ . 等字符不能直接进目录名。"""
    return re.sub(r"[^A-Za-z0-9._-]", "_", user_id or LOCAL_USER) or LOCAL_USER


def _lock_for(user_id: str) -> threading.Lock:
    with _GUARD:
        return _LOCKS.setdefault(user_id, threading.Lock())


def _evict_locked() -> None:
    """超出上限时淘汰最久未用的实例（跳过正在跑的）。调用方需持有 _GUARD。"""
    while len(_AGENTS) > MAX_CACHED_AGENTS:
        for uid in list(_AGENTS.keys()):
            lock = _LOCKS.get(uid)
            if lock is not None and lock.locked():
                continue
            _AGENTS.pop(uid, None)
            logger.info("淘汰空闲 Agent 实例: user=%s", uid)
            break
        else:
            break  # 全都在跑，先不淘汰


def get_agent(user_id: str):
    """取该账号的 Agent 实例，没有就创建。"""
    uid = user_id or LOCAL_USER
    with _GUARD:
        agent = _AGENTS.get(uid)
        if agent is not None:
            _AGENTS.move_to_end(uid)
            return agent

        slug = _slug(uid)
        user_memory = Path(MEMORY_DIR) / "users" / slug
        user_output = Path(OUTPUT_DIR) / "users" / slug
        user_memory.mkdir(parents=True, exist_ok=True)
        user_output.mkdir(parents=True, exist_ok=True)

        logger.info("开始为用户 %s 初始化 Agent，数据库类型=%s", uid, DEFAULT_DB_TYPE)
        db_config = DB_CONFIG.get(DEFAULT_DB_TYPE, {})

        agent = InsightAgent.create(
            db_config=db_config,
            db_type=DEFAULT_DB_TYPE,
            table_schemas=TABLE_SCHEMAS,
            knowledge_dir=str(KNOWLEDGE_DIR),
            output_dir=str(user_output),
            memory_dir=str(user_memory),
            max_steps=MAX_STEPS,
            # 知识库与业务库全站共享，不要按用户各建一份
            vector_store_dir=str(Path(MEMORY_DIR) / "vector_store"),
            trace_enabled=False,
        )
        _AGENTS[uid] = agent
        _evict_locked()
        logger.info("用户 %s 的 Agent 初始化完成", uid)
        return agent


def reset_agent(user_id: str) -> None:
    """丢弃某个账号的会话历史（新建对话）。"""
    uid = user_id or LOCAL_USER
    with _GUARD:
        agent = _AGENTS.get(uid)
    if agent is None:
        return
    try:
        agent.clear_history()
    except Exception as e:  # 清历史失败不应影响用户继续提问
        logger.warning("清理会话历史失败: user=%s, err=%s", uid, e)


# ==================== 事件处理 ====================
def _uid_of(request: "gr.Request | None") -> str:
    """门户托管时由 auth_dependency 注入到 request.username；单机直跑时为 None。"""
    try:
        return (request.username if request is not None else None) or LOCAL_USER
    except Exception:
        return LOCAL_USER


def chat_fn(message, history, request: "gr.Request"):
    uid = _uid_of(request)
    logger.info("收到 Web 用户消息: user=%s, 长度=%d 字符", uid, len(message or ""))

    # 未配置 Key 时给出明确指引，而不是抛一整屏堆栈
    if not LLM_API_KEY:
        return (
            "⚠️ 还没有配置 LLM API Key，暂时无法对话。\n\n"
            "请打开项目根目录的 `.env` 文件，填写这一行后重启服务：\n\n"
            "```\nLLM_API_KEY=你的key\n```\n\n"
            "默认接的是 DeepSeek（`LLM_BASE_URL=https://api.deepseek.com`）。"
            "如果你想用其他 OpenAI 兼容服务，把 `LLM_BASE_URL` 和 `LLM_MODEL_ID` 一起改掉即可。"
        )

    try:
        # 按账号加锁：同一账号串行（Agent 不支持并发 run），不同账号可并行
        with _lock_for(uid):
            response = get_agent(uid).run(message)
        logger.info("Agent 返回结果长度=%d 字符", len(str(response)))
        return str(response)
    except Exception as e:
        logger.exception("Web 对话处理失败: user=%s, err=%s", uid, e)
        return f"❌ 发生错误: {e}"


def clear_history(request: "gr.Request"):
    reset_agent(_uid_of(request))
    return []


def whoami(request: "gr.Request"):
    uid = _uid_of(request)
    if uid == LOCAL_USER:
        return "👤 单机模式（未启用账号认证）"
    return f"👤 当前账号：`{uid}`"


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
    min-height: 60vh !important;
}
.example-card {
    cursor: pointer !important;
    border: 1px solid var(--border-color-primary) !important;
    border-radius: 12px !important;
    padding: 14px 18px !important;
    transition: all 0.2s ease !important;
    background: var(--background-fill-secondary) !important;
    color: var(--body-text-color) !important;
    font-size: 14px !important;
    line-height: 1.5 !important;
}
.example-card:hover {
    border-color: #4f46e5 !important;
    background: var(--background-fill-primary) !important;
}
.welcome-title {
    text-align: center !important;
    margin-bottom: 0 !important;
}
.welcome-subtitle {
    text-align: center !important;
    color: var(--body-text-color-subdued) !important;
    margin-top: 8px !important;
}
.sidebar-btn {
    border: 1px solid var(--border-color-primary) !important;
    border-radius: 8px !important;
    background: var(--button-secondary-background-fill) !important;
    color: var(--button-secondary-text-color) !important;
    font-weight: 500 !important;
    height: 42px !important;
    max-height: 42px !important;
    transition: all 0.15s ease !important;
}
.sidebar-btn:hover {
    background: var(--button-secondary-background-fill-hover) !important;
    border-color: var(--border-color-accent) !important;
}
.footer-info {
    text-align: center !important;
    color: var(--body-text-color-subdued) !important;
    font-size: 12px !important;
    margin-top: 8px !important;
}
.sidebar-col {
    justify-content: flex-start !important;
    padding-top: 0 !important;
}
.main-row {
    align-items: flex-start !important;
}
.user-tag {
    font-size: 12px !important;
    color: var(--body-text-color-subdued) !important;
    word-break: break-all !important;
    margin-bottom: 4px !important;
}
.portal-link {
    display: block !important;
    text-align: center !important;
    font-size: 12px !important;
    margin-top: 6px !important;
}
"""


def build_demo() -> "gr.Blocks":
    """构建 UI。门户托管与单机直跑共用这一份，避免两套界面走样。"""
    with gr.Blocks(
        title="RetailSage Agent",
        fill_height=True,
    ) as demo:

        with gr.Row(equal_height=True, elem_classes=["main-row"]):
            with gr.Column(scale=1, min_width=200, elem_classes=["sidebar-col"]):
                user_tag = gr.Markdown("", elem_classes=["user-tag"])
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
                gr.HTML(
                    '<a class="portal-link" href="http://127.0.0.1:8000/" target="_self">'
                    '← 返回门户</a>',
                )

            with gr.Column(scale=4):
                gr.Markdown(
                    """
                    <div class="welcome-title">
                        <h1 style="font-size: 28px; font-weight: 600; color: var(--body-text-color);">📊 RetailSage Agent</h1>
                    </div>
                    <div class="welcome-subtitle">
                        <p style="font-size: 15px;">零售/电商数据分析智能体 — 用对话的方式从数据中获得洞察</p>
                    </div>
                    """,
                )

                chatbot = gr.ChatInterface(
                    fn=chat_fn,
                    # 默认 concurrency_limit="default" → queue 全局只放 1 个，
                    # 多账号会互相排队，这里显式放宽
                    concurrency_limit=8,
                    chatbot=gr.Chatbot(show_label=False, elem_classes=["chatbot"]),
                )

                gr.Markdown(
                    '<p class="footer-info">RetailSage Agent v0.1 · 基于 DeepSeek LLM · 数据仅供分析使用</p>',
                )

        new_chat_btn.click(clear_history, outputs=chatbot.chatbot)
        demo.load(whoami, outputs=user_tag)
        demo.queue(default_concurrency_limit=8)

    return demo


if __name__ == "__main__":
    os.environ["no_proxy"] = "localhost,127.0.0.1,0.0.0.0"
    os.environ["NO_PROXY"] = "localhost,127.0.0.1,0.0.0.0"
    logger.info("启动 RetailSage Agent Web 界面（单机模式）")
    print("✅ 启动 Web 界面...")
    print("⏳ Agent 将在第一次对话时自动初始化")
    build_demo().launch(
        server_name="127.0.0.1",
        server_port=7860,
        share=False,
        inbrowser=True,
        css=CUSTOM_CSS,
    )
