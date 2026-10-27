"""
RetailSage Agent - Web GUI 界面（DeepSeek 风格）

基于 Gradio 的 Web 对话界面，采用 DeepSeek 风格的底部输入框 + 推荐问题设计。

两种运行方式：
1. 单机直跑（无认证）：`python app.py`，监听 7860，行为与改造前一致；
2. 门户托管：由 run_insight_web.py 通过 `build_demo()` 挂到 FastAPI 下，
   由门户下发的 rs_session Cookie 判定身份，每个账号独立一份 Agent 实例
   与记忆 / 图表 / 报告目录（内置知识库与业务库仍全站共享）。
"""

import asyncio
import html as _html
import logging
import os
import re
import shutil
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
    KNOWLEDGE_DIR, OUTPUT_DIR, MEMORY_DIR, MAX_STEPS, EMBEDDING_MODEL
)
from data_insight.agents.insight_agent import InsightAgent
from data_insight.tools import data_importer


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
_ASYNC_LOCKS: dict[str, "asyncio.Lock"] = {}
_GUARD = threading.Lock()


def _slug(user_id: str) -> str:
    """邮箱里的 @ . 等字符不能直接进目录名。"""
    return re.sub(r"[^A-Za-z0-9._-]", "_", user_id or LOCAL_USER) or LOCAL_USER


# ---- 按账号隔离的目录 / 数据库 ----
def _user_dir(user_id: str) -> Path:
    return Path(MEMORY_DIR) / "users" / _slug(user_id)


def _user_db_path(user_id: str) -> Path:
    """该账号独立的 SQLite 文件路径。"""
    return _user_dir(user_id) / "data.db"


def _user_kb_dir(user_id: str) -> Path:
    """该账号上传的知识文档目录。"""
    return _user_dir(user_id) / "uploads" / "kb"


def ensure_user_db(user_id: str) -> str:
    """确保该账号有独立 SQLite；首次从共享基础库复制一份（保留 orders 等基础数据）。"""
    user_db = _user_db_path(user_id)
    user_db.parent.mkdir(parents=True, exist_ok=True)
    if not user_db.exists():
        base = DB_CONFIG.get("sqlite", {}).get("database")
        if base and Path(base).exists():
            shutil.copy(base, user_db)
            logger.info("为用户 %s 复制基础数据库 -> %s", user_id, user_db)
        else:
            user_db.touch()
            logger.info("基础库缺失，为用户 %s 创建空数据库 -> %s", user_id, user_db)
    return str(user_db)


def _rebuild_user_kb(user_id: str) -> int:
    """重建该账号的知识索引（共享基础库 + 上传目录），返回索引条数。

    - 若该账号 Agent 已创建：直接重建其 rag_tool（同时刷新内存 BM25 与持久化向量库）；
    - 否则用独立 RAGTool 落盘（不依赖 LLM），Agent 之后创建时会再从源文件干净重建。
    """
    with _GUARD:
        agent = _AGENTS.get(user_id)
    rag = getattr(agent, "rag_tool", None) if agent is not None else None
    if rag is None:
        from data_insight.rag.rag_tool import RAGTool
        user_memory = _user_dir(user_id)
        rag = RAGTool(
            knowledge_dir=str(KNOWLEDGE_DIR),
            vector_store_dir=str(user_memory / "vector_store"),
            embedding_model=EMBEDDING_MODEL,
            llm=None,
            extra_knowledge_dirs=[str(_user_kb_dir(user_id))],
        )
    rag.rebuild_index()
    return rag.vector_store.get_count()


def _lock_for(user_id: str) -> threading.Lock:
    with _GUARD:
        return _LOCKS.setdefault(user_id, threading.Lock())


def _alock_for(user_id: str) -> "asyncio.Lock":
    """同账号串行的异步锁。

    流式对话在 await 处会持续让出事件循环，如果沿用 threading.Lock，
    同账号并发请求会在 loop 线程里阻塞式等待，直接把整个事件循环卡死。
    该函数只在事件循环线程内被调用，故无需 _GUARD 保护。
    """
    lock = _ASYNC_LOCKS.get(user_id)
    if lock is None:
        lock = asyncio.Lock()
        _ASYNC_LOCKS[user_id] = lock
    return lock


def _evict_locked() -> None:
    """超出上限时淘汰最久未用的实例（跳过正在跑的）。调用方需持有 _GUARD。"""
    while len(_AGENTS) > MAX_CACHED_AGENTS:
        for uid in list(_AGENTS.keys()):
            lock = _LOCKS.get(uid)
            if lock is not None and lock.locked():
                continue
            alock = _ASYNC_LOCKS.get(uid)
            if alock is not None and alock.locked():
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
        user_kb = user_memory / "uploads" / "kb"
        user_memory.mkdir(parents=True, exist_ok=True)
        user_output.mkdir(parents=True, exist_ok=True)
        user_kb.mkdir(parents=True, exist_ok=True)

        logger.info("开始为用户 %s 初始化 Agent，数据库类型=%s", uid, DEFAULT_DB_TYPE)

        # 数据库按账号隔离：SQLite 每人一份副本（首次从共享基础库复制），
        # 用户上传的表只进自己的库，互不影响；其它数据库类型暂共享。
        if DEFAULT_DB_TYPE == "sqlite":
            db_config = {"database": ensure_user_db(uid)}
        else:
            db_config = DB_CONFIG.get(DEFAULT_DB_TYPE, {})

        agent = InsightAgent.create(
            db_config=db_config,
            db_type=DEFAULT_DB_TYPE,
            table_schemas=TABLE_SCHEMAS,
            knowledge_dir=str(KNOWLEDGE_DIR),
            output_dir=str(user_output),
            memory_dir=str(user_memory),
            max_steps=MAX_STEPS,
            # 向量库按账号隔离；索引来源 = 共享基础知识库 + 该用户上传目录
            vector_store_dir=str(user_memory / "vector_store"),
            extra_knowledge_dirs=[str(user_kb)],
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


def _fmt_stream_bubble(step: int, tool: str, text: str) -> str:
    """把流式进度渲染成 assistant 气泡内的 HTML。"""
    bits = ["模型思考中..."]
    if step:
        bits.append(f"第 {step} 步")
    if tool:
        bits.append(f"调用 {tool}")
    head = " · ".join(bits)
    body = (
        '<div class="thinking-bubble">'
        '<span class="thinking-spinner"></span>'
        f'<span class="thinking-text">{_html.escape(head)}</span>'
        '</div>'
    )
    if text:
        body += f'<div class="stream-text">{_html.escape(text)}</div>'
    return body


async def respond(message: str, history: list, request: gr.Request):
    """
    处理用户发送消息并以流式方式返回 Assistant 回复（async generator）。
    history 采用 Gradio messages 格式：[{"role": "user"/"assistant", "content": "..."}]
    每次 yield 返回 3 元组：(msg_input_value, history, time_text)。

    流式流程：
    1. 立即把用户消息上屏，assistant 气泡内显示思考动画；
    2. 消费 ReActAgent.arun_stream 的流式事件，实时把模型输出 / 步骤 / 工具调用
       渲染进 assistant 气泡；
    3. 收到 AGENT_FINISH 后，用干净的最终答案替换流式气泡。
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

    # 第一步：立即上屏用户消息 + 思考动画
    history = history + [{"role": "user", "content": message}]
    yield "", history + [{"role": "assistant", "content": THINKING_HTML}], ""

    start_time = time.time()
    current_step = 0
    last_tool = ""
    stream_text = ""
    final_answer = None
    error_text = None
    last_yield = 0.0

    def snapshot() -> list:
        return history + [{
            "role": "assistant",
            "content": _fmt_stream_bubble(current_step, last_tool, stream_text),
        }]

    try:
        async with _alock_for(uid):
            agent = await asyncio.to_thread(get_agent, uid)
            async for event in agent.arun_stream(message):
                etype = getattr(event.type, "value", str(event.type))
                data = event.data or {}

                if etype == "step_start":
                    current_step = data.get("step", current_step)
                    last_tool = ""
                    stream_text = ""
                elif etype == "llm_chunk":
                    stream_text += data.get("chunk", "") or ""
                elif etype == "tool_call_finish":
                    last_tool = data.get("tool_name", last_tool) or last_tool
                    stream_text = ""
                elif etype == "agent_finish":
                    final_answer = data.get("result") or stream_text or final_answer
                elif etype == "error":
                    error_text = data.get("error", "未知错误")
                else:
                    continue

                # 节流：LLM 分块很密，约 60ms 刷新一次即可，步骤/工具事件立即刷新
                now = time.time()
                if etype in ("step_start", "tool_call_finish") or now - last_yield >= 0.06:
                    last_yield = now
                    yield "", snapshot(), ""

        elapsed = time.time() - start_time
        if error_text and not final_answer:
            answer = f"❌ 发生错误: {error_text}"
            time_text = f"⚠️ 处理失败，用时 {elapsed:.1f} 秒"
            logger.warning("Agent 流式返回错误: user=%s, err=%s", uid, error_text)
        else:
            answer = final_answer or "（没有获取到回答）"
            time_text = f"✅ 思考完成，用时 {elapsed:.1f} 秒"
            logger.info("Agent 流式完成，答案长度=%d 字符，耗时=%.2f秒", len(str(answer)), elapsed)
    except Exception as e:
        elapsed = time.time() - start_time
        logger.exception("Web 对话处理失败: user=%s, err=%s", uid, e)
        answer = f"❌ 发生错误: {e}"
        time_text = f"⚠️ 处理失败，用时 {elapsed:.1f} 秒"

    # 用干净的最终答案替换流式气泡
    yield "", history + [{"role": "assistant", "content": str(answer)}], time_text


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


# 上传模式（与面板里 Radio 选项文案一致）
MODE_DB = "📊 导入数据库（CSV / Excel）"
MODE_KB = "📚 加入知识库（PDF / Word / TXT / MD）"


def toggle_upload_panel(opened: bool):
    """+ 号按钮：展开 / 收起上传面板。"""
    opened = not bool(opened)
    return opened, gr.Column(visible=opened)


def handle_upload(mode: str, files, request: gr.Request):
    """处理上传：按模式导入数据库或加入知识库。返回 (状态markdown, 清空文件组件)。"""
    uid = _uid_of(request)

    # gr.File(multiple) 在 filepath 模式下返回 list[str]；单个可能为 str
    if files is None:
        files = []
    if isinstance(files, str):
        files = [files]
    files = [f for f in files if f]

    if not files:
        return "⚠️ 请先选择要上传的文件。", gr.File(value=None)

    logger.info("收到上传: user=%s, mode=%s, 文件数=%d", uid, mode, len(files))
    mode = mode or ""

    # ---- 模式一：导入数据库 ----
    if mode.startswith("📊") or "数据库" in mode:
        db_path = ensure_user_db(uid)
        ok = 0
        last_table = None
        lines = []
        for f in files:
            name = os.path.basename(f)
            try:
                df = data_importer.read_table_file(f)
                table = data_importer.sanitize_table_name(name)
                rows, cols = data_importer.import_to_sqlite(df, table, db_path)
                ok += 1
                last_table = table
                lines.append(f"✅ `{name}` → 表 `{table}`（{rows} 行 / {len(cols)} 列）")
            except Exception as e:
                logger.warning("导入失败 %s: %s", name, e)
                lines.append(f"❌ `{name}` 导入失败：{e}")
        if ok:
            head = (
                f"已导入 {ok}/{len(files)} 个文件到数据库 `{os.path.basename(db_path)}`。\n\n"
                f"现在可以直接问我，例如“统计 `{last_table}` 的行数”或“看看 `{last_table}` 前几行”。"
            )
        else:
            head = f"导入完成，但没有文件成功入库（共 {len(files)} 个）。"
        return head + "\n\n" + "\n".join(lines), gr.File(value=None)

    # ---- 模式二：加入知识库 ----
    kb_dir = _user_kb_dir(uid)
    kb_dir.mkdir(parents=True, exist_ok=True)
    saved = []
    lines = []
    for f in files:
        name = os.path.basename(f)
        if not data_importer.is_knowledge_file(f):
            lines.append(f"⚠️ `{name}` 不是支持的文档格式（PDF/Word/TXT/MD/CSV/JSON），已跳过")
            continue
        try:
            shutil.copy2(f, kb_dir / name)
            saved.append(name)
            lines.append(f"📄 已保存 `{name}`")
        except Exception as e:
            logger.warning("保存知识文档失败 %s: %s", name, e)
            lines.append(f"❌ `{name}` 保存失败：{e}")

    if not saved:
        return "⚠️ 没有文件被成功保存到知识库。\n\n" + "\n".join(lines), gr.File(value=None)

    try:
        count = _rebuild_user_kb(uid)
        head = f"已加入知识库 {len(saved)} 个文档，当前索引共 {count} 条。可以直接就文档内容提问。"
    except Exception as e:
        logger.warning("重建知识索引失败: %s", e)
        head = f"已保存 {len(saved)} 个文档，但索引重建失败：{e}（将在下次对话时重试）。"
    return head + "\n\n" + "\n".join(lines), gr.File(value=None)


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
/* 用户气泡：与"发送"按钮一致的紫底白字（#4f46e5） */
.chatbot .message.user,
.chatbot .user {
    background: #4f46e5 !important;
    border-color: #4f46e5 !important;
    color: #ffffff !important;
    border-radius: 16px 16px 4px 16px !important;
}
/* Gradio 内层 markdown/文本容器默认是深色字，需一并强制为白色 */
.chatbot .message.user .prose,
.chatbot .message.user .prose *,
.chatbot .message.user .flex-wrap,
.chatbot .message.user .flex-wrap *,
.chatbot .user .prose,
.chatbot .user .prose *,
.chatbot .user .flex-wrap,
.chatbot .user .flex-wrap * {
    color: #ffffff !important;
    text-align: left !important;
}
/* 机器人气泡 */
.chatbot .message.bot,
.chatbot .bot {
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
/* 流式输出文本（实时追加在思考动画下方） */
.stream-text {
    margin-top: 8px !important;
    white-space: pre-wrap !important;
    word-break: break-word !important;
    color: #111827 !important;
    font-size: 14px !important;
    line-height: 1.6 !important;
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

/* 上传面板 */
.upload-panel {
    background: #ffffff !important;
    border: 1px solid #e5e7eb !important;
    border-radius: 16px !important;
    box-shadow: 0 4px 20px rgba(0, 0, 0, 0.06) !important;
    padding: 14px 16px !important;
    margin: 12px 0 4px 0 !important;
    gap: 8px !important;
}
.upload-title {
    font-size: 15px !important;
    font-weight: 600 !important;
    color: #111827 !important;
}
.upload-status {
    font-size: 13px !important;
    color: #374151 !important;
    line-height: 1.6 !important;
}
.upload-status p {
    margin: 2px 0 !important;
}
.upload-actions {
    margin-top: 4px !important;
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
        fill_height=True,
    ) as demo:

        with gr.Column(elem_classes=["main-panel"]):
            # 顶部标题栏：左侧标题，右侧 + 号（上传入口）
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
                    plus_btn = gr.Button(
                        "➕",
                        size="sm",
                        elem_classes=["new-chat-icon"],
                        variant="secondary",
                    )

            # 对话显示区（Gradio 6 默认就是 messages 格式）
            chatbot = gr.Chatbot(
                label="",
                elem_classes=["chatbot"],
                height=480,
                buttons=["copy"],
            )

            # 上传面板（点击 + 号展开 / 收起）
            panel_state = gr.State(False)
            with gr.Column(elem_classes=["upload-panel"], visible=False) as upload_panel:
                gr.Markdown('<div class="upload-title">📎 上传文件</div>')
                upload_mode = gr.Radio(
                    choices=[MODE_DB, MODE_KB],
                    value=MODE_DB,
                    label="上传类型",
                    elem_classes=["upload-mode"],
                )
                upload_files = gr.File(
                    label="选择文件（可多选）",
                    file_count="multiple",
                    type="filepath",
                    elem_classes=["upload-file"],
                )
                with gr.Row(elem_classes=["upload-actions"]):
                    do_upload_btn = gr.Button(
                        "⬆️ 上传并处理",
                        variant="primary",
                        scale=2,
                        elem_classes=["send-btn"],
                    )
                    new_chat_btn2 = gr.Button(
                        "🗑 新建对话",
                        variant="secondary",
                        scale=1,
                    )
                upload_status = gr.Markdown("", elem_classes=["upload-status"])

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
        new_chat_btn2.click(
            fn=clear_history,
            inputs=[],
            outputs=[chatbot],
        )
        plus_btn.click(
            fn=toggle_upload_panel,
            inputs=[panel_state],
            outputs=[panel_state, upload_panel],
        )
        do_upload_btn.click(
            fn=handle_upload,
            inputs=[upload_mode, upload_files],
            outputs=[upload_status, upload_files],
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
        css=CUSTOM_CSS,
    )
