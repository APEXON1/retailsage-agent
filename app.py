"""
RetailSage Agent - Web GUI 界面（DeepSeek 风格）

基于 Gradio 的 Web 对话界面，采用 DeepSeek 风格的底部输入框 + 推荐问题设计。

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
import time
import traceback
from collections import OrderedDict
from pathlib import Path

project_dir = Path(__file__).parent
sys.path.insert(0, str(project_dir))

from dotenv import load_dotenv
load_dotenv()

# HuggingFace 镜像必须在 gradio / huggingface_hub 被导入之前设置。
# huggingface_hub.constants 在导入时就会把 HF_ENDPOINT 固化为 ENDPOINT
# 及 HUGGINGFACE_CO_URL_TEMPLATE，晚于此设置将不生效（仍连 huggingface.co 超时）。
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

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


# assistant 气泡内的"思考中"动画（实时渲染时作为占位内容）
THINKING_HTML = (
    '<div class="thinking-bubble">'
    '<span class="thinking-spinner"></span>'
    '<span class="thinking-text">模型思考中...</span>'
    '</div>'
)


def respond(message: str, history: list, request: gr.Request):
    """
    处理用户发送消息并返回 Assistant 回复（generator 实时渲染）。
    history 采用 Gradio messages 格式：[{"role": "user"/"assistant", "content": "..."}]
    每次 yield 返回 3 元组：(msg_input_value, history, time_text)。

    实时渲染流程：
    1. 立即把用户消息上屏，并在 assistant 气泡内显示"模型思考中"动画；
    2. Agent 返回后，用真实回答替换掉思考动画气泡。
    """
    uid = _uid_of(request)
    logger.info("收到 Web 用户消息: user=%s, 长度=%d 字符", uid, len(message or ""))

    # 未配置 Key 时给出明确指引
    if not LLM_API_KEY:
        history = history + [
            {"role": "user", "content": message},
            {"role": "assistant", "content": (
                "⚠️ 还没有配置 LLM API Key，暂时无法对话。\n\n"
                "请打开项目根目录的 `.env` 文件，填写这一行后重启服务：\n\n"
                "```\nLLM_API_KEY=你的key\n```\n\n"
                "默认接的是 DeepSeek（`LLM_BASE_URL=https://api.deepseek.com`）。"
                "如果你想用其他 OpenAI 兼容服务，把 `LLM_BASE_URL` 和 `LLM_MODEL_ID` 一起改掉即可。"
            )},
        ]
        yield "", history, ""
        return

    if not message or not message.strip():
        yield "", history, ""
        return

    # 第一步：立即上屏用户消息 + assistant 气泡内显示"思考中"动画
    history = history + [{"role": "user", "content": message}]
    thinking_history = history + [{"role": "assistant", "content": THINKING_HTML}]
    yield "", thinking_history, ""

    start_time = time.time()
    try:
        # 按账号加锁：同一账号串行，不同账号可并行
        with _lock_for(uid):
            response = get_agent(uid).run(message)
        response_text = str(response)
        elapsed = time.time() - start_time
        logger.info("Agent 返回结果长度=%d 字符，耗时=%.2f秒", len(response_text), elapsed)
        final_history = history + [{"role": "assistant", "content": response_text}]
        time_text = f"✅ 思考完成，用时 {elapsed:.1f} 秒"
    except Exception as e:
        elapsed = time.time() - start_time
        logger.exception("Web 对话处理失败: user=%s, err=%s", uid, e)
        final_history = history + [{"role": "assistant", "content": f"❌ 发生错误: {e}"}]
        time_text = f"⚠️ 处理失败，用时 {elapsed:.1f} 秒"

    # 第二步：用真实回答替换"思考中"气泡
    yield "", final_history, time_text


def use_suggestion(text: str):
    """点击推荐问题时，直接返回该文本用于填充输入框。"""
    return text


def clear_history(request: gr.Request):
    reset_agent(_uid_of(request))
    return []


def whoami(request: gr.Request):
    uid = _uid_of(request)
    if uid == LOCAL_USER:
        return "👤 单机模式（未启用账号认证）"
    return f"👤 当前账号：`{uid}`"


CUSTOM_CSS = """
/* 整体容器 */
.gradio-container {
    position: relative !important;
    max-width: 1000px !important;
    margin: auto !important;
    background: #f9fafb !important;
}

/* 主面板 */
.main-panel {
    border: none !important;
    box-shadow: none !important;
    background: transparent !important;
}

/* 顶部标题栏 */
.header-row {
    display: flex !important;
    flex-direction: row !important;
    align-items: flex-start !important;
    justify-content: flex-start !important;
    padding: 20px 0 8px 8px !important;
    gap: 12px !important;
}
.welcome-title h1 {
    font-size: 26px !important;
    font-weight: 600 !important;
    color: #111827 !important;
    margin: 0 !important;
    text-align: left !important;
    line-height: 1.2 !important;
}
.welcome-subtitle p {
    font-size: 14px !important;
    color: #6b7280 !important;
    margin: 4px 0 0 0 !important;
    text-align: left !important;
    line-height: 1.4 !important;
}
.user-tag {
    font-size: 12px !important;
    color: #9ca3af !important;
    word-break: break-all !important;
    margin-top: 4px !important;
}

/* 右上角新建对话按钮（与发送按钮同列，内容区右侧对齐） */
.new-chat-icon {
    position: absolute !important;
    top: 24px !important;
    right: 24px !important;
    z-index: 1000 !important;
    border-radius: 8px !important;
    width: 24px !important;
    height: 24px !important;
    min-width: 24px !important;
    max-width: 24px !important;
    min-height: 24px !important;
    max-height: 24px !important;
    padding: 0 !important;
    font-size: 14px !important;
    line-height: 1 !important;
    background: #ffffff !important;
    border: 1px solid #e5e7eb !important;
    box-shadow: 0 1px 2px rgba(0,0,0,0.05) !important;
    transition: all 0.15s ease !important;
    display: flex !important;
    align-items: center !important;
    justify-content: center !important;
}
.new-chat-icon:hover {
    background: #f5f3ff !important;
    border-color: #4f46e5 !important;
    color: #4f46e5 !important;
}
.new-chat-icon button {
    border-radius: 8px !important;
    width: 24px !important;
    height: 24px !important;
    padding: 0 !important;
    display: flex !important;
    align-items: center !important;
    justify-content: center !important;
}

/* 对话区域 */
.chatbot {
    border: none !important;
    border-radius: 16px !important;
    background: #ffffff !important;
    box-shadow: 0 1px 3px rgba(0, 0, 0, 0.05) !important;
    min-height: 420px !important;
}
.chatbot .message-row {
    padding: 8px 16px !important;
}
.chatbot .message.user {
    background: #4f46e5 !important;
    color: #ffffff !important;
    border-radius: 16px 16px 4px 16px !important;
}
.chatbot .message.assistant {
    background: #f3f4f6 !important;
    color: #111827 !important;
    border-radius: 16px 16px 16px 4px !important;
}

/* 底部输入区 */
.input-area {
    background: #ffffff !important;
    border: 1px solid #e5e7eb !important;
    border-radius: 20px !important;
    box-shadow: 0 4px 20px rgba(0, 0, 0, 0.08) !important;
    padding: 12px 16px !important;
    margin-top: 16px !important;
    transition: all 0.2s ease !important;
}
.input-area:focus-within {
    border-color: #4f46e5 !important;
    box-shadow: 0 4px 24px rgba(79, 70, 229, 0.15) !important;
}
.input-box textarea {
    border: none !important;
    outline: none !important;
    box-shadow: none !important;
    font-size: 15px !important;
    line-height: 1.5 !important;
    resize: none !important;
    background: transparent !important;
}
.input-box textarea:focus {
    border: none !important;
    outline: none !important;
    box-shadow: none !important;
}
.send-btn {
    border-radius: 12px !important;
    background: #4f46e5 !important;
    color: #ffffff !important;
    font-weight: 600 !important;
    transition: all 0.15s ease !important;
}
.send-btn:hover {
    background: #4338ca !important;
    box-shadow: 0 2px 8px rgba(79, 70, 229, 0.3) !important;
}

/* 推荐问题 */
.suggestions-row {
    display: flex !important;
    align-items: center !important;
    gap: 8px !important;
    margin-top: 10px !important;
    padding-left: 4px !important;
    flex-wrap: wrap !important;
}
.suggestion-label {
    color: #9ca3af !important;
    font-size: 13px !important;
    white-space: nowrap !important;
}
.suggestion-chip {
    background: #ffffff !important;
    border: 1px solid #e5e7eb !important;
    border-radius: 999px !important;
    color: #4b5563 !important;
    font-size: 13px !important;
    padding: 4px 12px !important;
    cursor: pointer !important;
    transition: all 0.15s ease !important;
    white-space: nowrap !important;
}
.suggestion-chip:hover {
    border-color: #4f46e5 !important;
    color: #4f46e5 !important;
    background: #f5f3ff !important;
}

/* 底部信息区 */
.bottom-info {
    margin-top: 8px !important;
}
.capabilities {
    text-align: center !important;
    color: #9ca3af !important;
    font-size: 12px !important;
    margin-top: 8px !important;
}
.portal-link {
    display: block !important;
    text-align: center !important;
    font-size: 12px !important;
    margin-top: 6px !important;
    color: #9ca3af !important;
}
.portal-link a {
    color: #6b7280 !important;
    text-decoration: none !important;
}
.portal-link a:hover {
    color: #4f46e5 !important;
}

/* 思考中动画（渲染在 assistant 气泡内） */
.thinking-bubble {
    display: flex !important;
    align-items: center !important;
    gap: 8px !important;
}
.thinking-bubble p {
    margin: 0 !important;
}
.thinking-text {
    color: #6b7280 !important;
    font-size: 14px !important;
}
.thinking-spinner {
    display: inline-block !important;
    width: 14px !important;
    height: 14px !important;
    border: 2px solid #e5e7eb !important;
    border-top-color: #4f46e5 !important;
    border-radius: 50% !important;
    animation: spin 1s linear infinite !important;
    vertical-align: middle !important;
}
@keyframes spin {
    to { transform: rotate(360deg); }
}
.time-display {
    text-align: center !important;
    color: #6b7280 !important;
    font-size: 12px !important;
    margin-top: 4px !important;
    min-height: 18px !important;
}

/* 页脚 */
.footer-info {
    text-align: center !important;
    color: #9ca3af !important;
    font-size: 12px !important;
    margin-top: 16px !important;
}
"""


# 推荐问题列表
SUGGESTIONS = [
    "分析华东区最近30天的销售趋势",
    "GMV是什么意思？",
    "各门店业绩排名如何？",
    "帮我生成一份上周业绩周报",
    "为什么最近一周业绩下降了？",
]


def build_demo() -> "gr.Blocks":
    """构建 UI。门户托管与单机直跑共用这一份，避免两套界面走样。"""
    with gr.Blocks(
        title="RetailSage Agent",
        css=CUSTOM_CSS,
        fill_height=True,
    ) as demo:

        with gr.Column(elem_classes=["main-panel"]):
            # 顶部标题栏：左侧标题，右侧新建对话
            with gr.Row(elem_classes=["header-row"]):
                with gr.Column(scale=8):
                    gr.Markdown(
                        """
                        <div class="welcome-title">
                            <h1>📊 RetailSage Agent</h1>
                        </div>
                        <div class="welcome-subtitle">
                            <p>零售/电商数据分析智能体 — 用对话的方式从数据中获得洞察</p>
                        </div>
                        """,
                    )
                    user_tag = gr.Markdown("", elem_classes=["user-tag"])
                with gr.Column(scale=1):
                    new_chat_btn = gr.Button(
                        "➕",
                        size="sm",
                        elem_classes=["new-chat-icon"],
                        variant="secondary",
                    )

            # 对话显示区（messages 格式）
            chatbot = gr.Chatbot(
                label="",
                type="messages",
                elem_classes=["chatbot"],
                height=480,
                show_copy_button=True,
            )

            # 底部输入区
            with gr.Row(elem_classes=["input-area"]):
                msg_input = gr.Textbox(
                    placeholder="输入你的数据分析问题，例如：华东区上周销售趋势如何？",
                    show_label=False,
                    container=False,
                    elem_classes=["input-box"],
                    lines=1,
                    scale=8,
                    submit_btn=False,
                )
                send_btn = gr.Button(
                    "发送",
                    scale=1,
                    variant="primary",
                    elem_classes=["send-btn"],
                )

            # 思考耗时显示
            time_display = gr.Markdown("", elem_classes=["time-display"])

            # 底部信息区：推荐问题 + 我能做什么 + 返回门户 + 页脚
            with gr.Column(elem_classes=["bottom-info"]):
                with gr.Row(elem_classes=["suggestions-row"]):
                    gr.Markdown('<span class="suggestion-label">💡 试试：</span>')
                    suggestion_btns = []
                    for text in SUGGESTIONS:
                        btn = gr.Button(
                            text,
                            size="sm",
                            elem_classes=["suggestion-chip"],
                            min_width=80,
                        )
                        suggestion_btns.append(btn)

                gr.Markdown(
                    """
                    <p class="capabilities">
                        我能做什么：📊 自然语言查数据 · 🔍 业绩归因分析 · 📈 趋势与异常发现 · 📋 一键生成报告 · 📖 业务指标解释
                    </p>
                    """,
                )

                gr.HTML(
                    '<p class="portal-link"><a href="http://127.0.0.1:8000/" target="_self">← 返回门户</a></p>',
                )

                gr.Markdown(
                    '<p class="footer-info">RetailSage Agent v0.1 · 基于 DeepSeek LLM · 数据仅供分析使用</p>',
                )

        # 事件绑定
        send_btn.click(
            fn=respond,
            inputs=[msg_input, chatbot],
            outputs=[msg_input, chatbot, time_display],
        )
        msg_input.submit(
            fn=respond,
            inputs=[msg_input, chatbot],
            outputs=[msg_input, chatbot, time_display],
        )
        for btn in suggestion_btns:
            btn.click(
                fn=use_suggestion,
                inputs=[btn],
                outputs=[msg_input],
            )
        new_chat_btn.click(
            fn=clear_history,
            inputs=[],
            outputs=[chatbot],
        )
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
    )
