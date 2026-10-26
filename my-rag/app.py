import os
import re
import shutil
import logging
import streamlit as st
import pandas as pd
import time

os.environ["NO_PROXY"] = "localhost,127.0.0.1"
os.environ["USER_AGENT"] = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"

from config import DATA_DIR, INDEX_DIR, BM25_PKL, DB_PATH, backend_settings, DEMO_EMAIL
import auth as auth_mod
import portal_client
import seed_demo
from database import (
    init_db, get_all_resumes_from_db, delete_resume_from_db, clear_user_resumes_db,
    search_resumes_by_tags, insert_or_update_resume_db, update_resume_problem_status,
    update_resume_status, get_pending_resumes,
    safe_remove, calculate_file_md5, is_file_hash_exists,
)
from extractor import extract_resume_info_with_llm, evaluate_resume_against_jd
from rag_engine import (
    load_vectorstore, load_bm25, update_indexes_incremental,
    remove_file_from_indexes, remove_owner_from_indexes,
    answer_question_with_memory, _load_single_file_documents,
)
from langchain_ollama import ChatOllama, OllamaEmbeddings
from langchain_community.document_loaders import WebBaseLoader

st.set_page_config(page_title="智能 HR 简历管理与问答系统", page_icon="👔", layout="wide")


def _inject_streamlit_secrets():
    """把 Streamlit Cloud 的 Secrets 注入为环境变量（本地开发由 auth.py 读取 .env）。
    必须在 init_models() 之前执行：backend_settings() 是调用时实时读环境变量求值的。"""
    try:
        for key in (
            "LLM_BACKEND", "API_BASE", "API_KEY", "LLM_MODEL", "EMBED_MODEL",
            "SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASS", "SMTP_FROM",
        ):
            if key in st.secrets and key not in os.environ:
                os.environ[key] = str(st.secrets[key])
    except Exception:
        pass  # 本地运行无 secrets 文件时静默跳过


_inject_streamlit_secrets()

# ==================== 🗄️ 数据库结构初始化（必须早于任何登录校验） ====================
# 原先这两句在登录门之后（st.stop() 之后），全新目录首次访问时 users 表还没建，
# 注册会报 "no such table: users" 并被吞成「账号创建失败」。
# 接入门户 SSO 后更隐蔽：cookie 有效会直接绕过登录页，用户只会看到「知识库是空的」。
init_db()
seed_demo.ensure_demo_account()

# ==================== 🔐 邮箱注册 / 登录体系 ====================
# - 密码 PBKDF2 加盐哈希存储（auth.py）
# - 注册需邮箱验证码：配置 SMTP 真实发送，未配置自动降级演示模式（页面直接显示验证码）
# - 每个账号的简历文件 / DB 记录 / RAG 索引按 owner_email 严格隔离
# - 旧账号 hr01/hr02/hr03 迁移后对应 hrXX@demo.local，登录框输入 hr03 亦可

# 模块级全局：后台解析队列每完成一批就 +1，前端据此按当前用户重载索引
KB_VERSION = 0


def _enter_app(email: str):
    """登录/注册成功后的统一入口：写入会话并清理上一账号的残留数据。"""
    st.session_state.logged_in = True
    st.session_state.current_user = email
    st.session_state.chat_history = []
    st.session_state.display_history = []
    st.session_state.rag_objects = None
    st.session_state.uploader_key = 0
    st.session_state.widget_key = 0
    st.rerun()


def _clear_user_session():
    """清空当前会话里的账号与残留数据（退出登录时用）。"""
    st.session_state.logged_in = False
    st.session_state.current_user = ""
    st.session_state.chat_history = []
    st.session_state.display_history = []
    st.session_state.rag_objects = None
    st.session_state.widget_key = 0
    st.session_state.kb_version = -1


def _sso_email() -> str:
    """读取门户下发的会话 Cookie 并校验。

    Cookie 的作用域是主机名、不含端口，所以门户（8000）种下的 rs_session
    会被浏览器自动带到本应用（8501）——这就是三个进程共享登录态的全部机制。
    独立运行（不起门户）时没有该 Cookie，这里返回空串，走下面的登录页。
    """
    token = ""
    try:
        token = st.context.cookies.get(portal_client.COOKIE_NAME) or ""
    except Exception:
        token = ""  # 老版本 Streamlit 无 st.context.cookies
    if not token:
        try:
            token = st.query_params.get(portal_client.COOKIE_NAME) or ""
        except Exception:
            token = ""
    return portal_client.verify_session(token) if token else ""


if "logged_in" not in st.session_state:
    st.session_state.logged_in = False
    st.session_state.current_user = ""

# ---------- 门户单点登录：已从门户进来就直接进入，跳过登录页 ----------
if not st.session_state.logged_in:
    _sso_user = _sso_email()
    if _sso_user:
        st.session_state.logged_in = True
        st.session_state.current_user = _sso_user
        st.session_state.chat_history = []
        st.session_state.display_history = []
        st.session_state.rag_objects = None
        st.session_state.uploader_key = 0
        st.session_state.widget_key = 0
        logging.info(f"[SSO] 门户会话校验通过，自动登录: {_sso_user}")

if not st.session_state.logged_in:
    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        st.title("👔 智能 HR 人才库与问答系统")
        st.caption("多账号体系 · 各账号人才数据互相隔离")
        tab_login, tab_register, tab_reset = st.tabs(["🔑 登录", "📝 邮箱注册", "❓ 忘记密码"])

        # ---------- 登录 ----------
        with tab_login:
            with st.form("login_form"):
                identifier = st.text_input("账号（邮箱，或旧账号如 hr03）")
                password = st.text_input("密码", type="password")
                login_clicked = st.form_submit_button("登录", type="primary", use_container_width=True)
            if login_clicked:
                ok, msg, email = auth_mod.authenticate(identifier, password)
                if ok:
                    _enter_app(email)
                else:
                    st.error(f"⚠️ {msg}")
            st.divider()
            if st.button("🎯 一键体验（无需注册，直接进入演示账号）", use_container_width=True):
                _enter_app(DEMO_EMAIL)
            st.caption("演示账号内置 5 份合成简历，可完整体验上传解析 / 标签检索 / RAG 问答 / JD 匹配")
            if not auth_mod.smtp_configured():
                st.caption("💡 当前未配置 SMTP，注册验证码将以「演示模式」在页面直接显示")

        # ---------- 注册 ----------
        with tab_register:
            st.caption("注册即创建您独立的人才库，上传的简历仅自己可见。")
            with st.form("register_form"):
                reg_email = st.text_input("邮箱")
                c1, c2 = st.columns(2)
                reg_pwd = c1.text_input("密码（≥6位，含字母和数字）", type="password")
                reg_pwd2 = c2.text_input("确认密码", type="password")
                reg_code = st.text_input("邮箱验证码")
                send_clicked = st.form_submit_button("📨 发送验证码")
                reg_clicked = st.form_submit_button("✅ 注册并登录", type="primary")
            if send_clicked:
                ok, msg, demo_code = auth_mod.request_verification_code(reg_email, "register")
                if ok and demo_code:
                    st.warning(f"【演示模式】验证码：**{demo_code}**（5 分钟内有效，未真实发送邮件）")
                elif ok:
                    st.success(msg)
                else:
                    st.error(msg)
            if reg_clicked:
                ok, msg = auth_mod.register_user(reg_email, reg_pwd, reg_pwd2, reg_code)
                if ok:
                    _enter_app(auth_mod.normalize_login_id(reg_email))
                else:
                    st.error(f"⚠️ {msg}")

        # ---------- 忘记密码 ----------
        with tab_reset:
            st.caption("通过注册邮箱的验证码重置密码。")
            with st.form("reset_request_form"):
                rs_email = st.text_input("邮箱")
                rs_send = st.form_submit_button("📨 发送验证码")
            if rs_send:
                ok, msg, demo_code = auth_mod.request_verification_code(rs_email, "reset")
                if ok and demo_code:
                    st.warning(f"【演示模式】验证码：**{demo_code}**（5 分钟内有效，未真实发送邮件）")
                elif ok:
                    st.success(msg)
                else:
                    st.error(msg)
            with st.form("reset_confirm_form"):
                rs_code = st.text_input("验证码")
                d1, d2 = st.columns(2)
                rs_pwd = d1.text_input("新密码", type="password")
                rs_pwd2 = d2.text_input("确认新密码", type="password")
                rs_ok = st.form_submit_button("🔁 重置密码", type="primary")
            if rs_ok:
                ok, msg = auth_mod.reset_password(rs_email, rs_code, rs_pwd, rs_pwd2)
                if ok:
                    st.success(f"✅ {msg}")
                else:
                    st.error(f"⚠️ {msg}")
    # 关键：未登录时强制停止向下渲染，保护后续所有业务代码不被执行
    st.stop()
# ----------------------------

# 初始化数据库结构与演示账号已在文件上方（登录校验之前）完成，见 init_db() 调用处。

# ==================== 👤 当前登录用户与其数据目录 ====================
CURRENT_USER = st.session_state.current_user
USER_DATA_DIR = os.path.join(DATA_DIR, CURRENT_USER)
os.makedirs(USER_DATA_DIR, exist_ok=True)

@st.cache_resource
def init_models(fast_model_name="qwen2.5:7b"):
    """初始化 LLM / 快速解析模型 / Embedding 模型，后端由 backend_settings() 决定：
      - ollama（默认）：本地 Ollama，零成本，适合开发机与内网部署；
      - api：OpenAI 兼容接口（硅基流动 / DeepSeek / Moonshot 等），
        适合 Streamlit Cloud 等没有 Ollama 的环境，密钥走 st.secrets。"""
    s = backend_settings()
    if s["backend"] == "api":
        # ☁️ 云端后端
        from langchain_openai import ChatOpenAI, OpenAIEmbeddings
        common = dict(base_url=s["api_base"], api_key=s["api_key"],
                      request_timeout=300, max_retries=1)
        # 解析要求稳定输出 JSON，用 temperature=0；问答保留少量多样性
        llm = ChatOpenAI(model=s["llm_model"], temperature=0.2, **common)
        fast_llm = ChatOpenAI(model=s["llm_model"], temperature=0, **common)
        # check_embedding_ctx_length=False：非 OpenAI 官方模型跳过 tiktoken 长度校验
        embed_model = OpenAIEmbeddings(
            model=s["embed_model"], check_embedding_ctx_length=False, **common
        )
    else:
        # 🏠 本地后端：Ollama
        # deepseek-r1:8b：推理模型，保留用于问答（推理能力强但较慢）
        llm = ChatOllama(model="deepseek-r1:8b", base_url="http://127.0.0.1:11434",
                         sync_client_kwargs={"timeout": 600}, async_client_kwargs={"timeout": 600})
        # 非推理指令模型，专职结构化字段抽取；模型名由侧边栏下拉框动态传入
        # num_ctx=4096 确保单份简历能被完整读入上下文而不被截断
        fast_llm = ChatOllama(model=fast_model_name, base_url="http://127.0.0.1:11434",
                              num_ctx=4096,
                              sync_client_kwargs={"timeout": 600}, async_client_kwargs={"timeout": 600})
        embed_model = OllamaEmbeddings(model="nomic-embed-text", base_url="http://127.0.0.1:11434")
    return llm, fast_llm, embed_model


# ==================== 🗂️ 索引兼容性自检（切换后端时维度不匹配必须重建） ====================
def _index_marker_path() -> str:
    return os.path.join(INDEX_DIR, ".embed_model")


def _ensure_index_compatible(embed_model):
    """校验本地索引与当前 embedding 模型是否匹配；不匹配则把旧索引挪到备份目录。
    （本地 nomic-embed-text 为 768 维，云端 bge-m3 为 1024 维，混用会直接报错）

    ⚠️ 两个必须的细节：
    1. 用 utf-8-sig 读取 —— 历史遗留的标记文件带 UTF-8 BOM，而 \\ufeff 不是空白字符，
       `.strip()` 去不掉，用 utf-8 读会导致每次启动都误判「模型变更」而清空索引。
    2. 不删除，只改名 —— 索引重建依赖可用的 embedding 服务，服务不可用时若已删除，
       就彻底没得救了；改名成 .stale 至少能回滚。
    """
    name = getattr(embed_model, "model", "") or ""
    if not os.path.exists(INDEX_DIR):
        return
    try:
        with open(_index_marker_path(), "r", encoding="utf-8-sig") as f:
            if f.read().strip() == name:
                return  # 匹配，索引继续可用
    except OSError:
        pass  # 标记缺失：按不匹配处理，保守重建

    stale_dir = f"{INDEX_DIR}.stale"
    try:
        if os.path.exists(stale_dir):
            shutil.rmtree(stale_dir, ignore_errors=True)  # 只清理上一次的废弃副本
        os.rename(INDEX_DIR, stale_dir)
        logging.warning(f"检测到 Embedding 模型变更（当前：{name}），旧索引已移至 {stale_dir}")
    except OSError as e:
        logging.warning(f"旧索引改名失败（保留原索引继续尝试使用）: {e}")
        return

    if os.path.exists(BM25_PKL):
        stale_pkl = f"{BM25_PKL}.stale"
        try:
            if os.path.exists(stale_pkl):
                safe_remove(stale_pkl)
            os.rename(BM25_PKL, stale_pkl)
        except OSError:
            pass


def _write_index_marker(embed_model):
    """索引构建/更新成功后记录 embedding 模型名，供下次启动做兼容性校验。"""
    name = getattr(embed_model, "model", "") or ""
    if not name:
        return
    try:
        os.makedirs(INDEX_DIR, exist_ok=True)
        with open(_index_marker_path(), "w", encoding="utf-8") as f:
            f.write(name)
    except OSError as e:
        logging.warning(f"写入索引模型标记失败（不影响使用）: {e}")

@st.cache_resource
def start_background_worker():
    """启动后台异步队列守护线程：每 3 秒扫描一次 pending 简历，串行解析入队任务。
    跨账号消费：pending 任务携带归属人，源文件从各用户目录读取，解析结果写回对应 owner。
    使用 @st.cache_resource 确保全局仅启动一次。"""
    import threading as _threading
    global KB_VERSION

    def _worker_loop():
        global KB_VERSION
        while True:
            try:
                # 利用 Streamlit 的缓存机制，在后台线程内安全获取模型实例
                _, fast_llm, _ = init_models()
                pending = get_pending_resumes()
                if pending:
                    logging.info(f"[后台队列] 检测到 {len(pending)} 份待解析任务，开始串行处理…")
                    processed_any = False
                    for task in pending:
                        fname = task["filename"]
                        owner = task["owner_email"]
                        file_path = os.path.join(DATA_DIR, owner, fname)
                        if not os.path.exists(file_path):
                            # 源文件已不存在，标记失败避免长期悬挂
                            update_resume_status(fname, "failed", owner)
                            processed_any = True
                            continue
                        try:
                            ok, msg, data = extract_resume_info_with_llm(fname, file_path, fast_llm)
                            if ok and data:
                                data["file_hash"] = calculate_file_md5(file_path)
                                data["parse_status"] = "completed"
                                data["owner_email"] = owner
                                insert_or_update_resume_db(data)
                                logging.info(f"[后台队列] 解析完成：{fname}（owner={owner}）")
                            else:
                                # 解析失败：仍更新数据库（字段保持排队/原值），状态置为 failed
                                insert_or_update_resume_db({
                                    "owner_email": owner,
                                    "filename": fname,
                                    "name": "解析失败", "age": "未知", "gender": "未知",
                                    "university": "未知", "major": "未知", "birth_year": "未知",
                                    "tags": "", "degree": "未知", "work_years": "未知",
                                    "parse_status": "failed",
                                })
                                logging.warning(f"[后台队列] 解析失败：{fname}；原因：{msg}")
                            processed_any = True
                        except Exception as e:
                            logging.error(f"[后台队列] 处理异常：{fname}；异常：{e}")
                            update_resume_status(fname, "failed", owner)
                            processed_any = True
                    # 整批处理完后增量更新各归属人的向量库，保证问答检索到最新内容
                    if processed_any:
                        try:
                            _, _, embed_model = init_models()
                            for owner in {t["owner_email"] for t in pending}:
                                update_indexes_incremental(embed_model, owner)
                        except Exception as e:
                            logging.error(f"[后台队列] 刷新知识库失败：{e}")
                        KB_VERSION += 1  # 通知前端：索引已更新，各会话按需重载
            except Exception as e:
                logging.error(f"[后台队列] 主循环异常（将继续重试）：{e}")
            # 每隔 3 秒轮询一次
            _threading.Event().wait(3)

    t = _threading.Thread(target=_worker_loop, daemon=True)
    t.start()
    return t

if "chat_history" not in st.session_state:
    st.session_state.chat_history = []
if "display_history" not in st.session_state:
    st.session_state.display_history = []
if "rag_objects" not in st.session_state:
    st.session_state.rag_objects = None
if "uploader_key" not in st.session_state:
    st.session_state.uploader_key = 0
if "widget_key" not in st.session_state:
    st.session_state.widget_key = 0
if "kb_version" not in st.session_state:
    st.session_state.kb_version = -1

# 全局启动一次后台异步队列守护线程（@st.cache_resource 保证只起一个）
start_background_worker()

def _reparse_files(file_names):
    """对已入库源文件强制重新解析：将状态打回 'pending' 交后台队列异步处理。
    仅更新数据库状态与少量展示字段（如“重新排队中…”），真正的 LLM 解析由守护线程完成。
    复用于侧边栏「重新解析选中简历」与主表格「重新解析选中」批量操作。"""
    if not file_names:
        return
    reparse_failed = False
    try:
        for f_name in file_names:
            file_path = os.path.join(USER_DATA_DIR, f_name)
            if not os.path.exists(file_path):
                logging.warning(f"重新解析跳过：源文件不存在 {f_name}")
                continue
            # 把展示字段更新为“重新排队中…”，并把状态打回 pending 触发后台异步解析
            insert_or_update_resume_db({
                "owner_email": CURRENT_USER,
                "filename": f_name,
                "name": "重新排队中…", "age": "未知", "gender": "未知",
                "university": "重新排队中…", "major": "重新排队中…", "birth_year": "未知",
                "tags": "", "degree": "未知", "work_years": "未知",
                "parse_status": "pending",
            })
            # 确保状态字段被置为 pending（上述 insert 已带 parse_status，但再显式置一次更稳妥）
            update_resume_status(f_name, "pending", CURRENT_USER)
        st.success(f"🎉 已将 {len(file_names)} 份简历加入后台重新解析队列，请稍候刷新看板查看进度！")
    except Exception as e:
        reparse_failed = True
        logging.error(f"批量加入重新解析队列失败：{e}")
        st.error(f"⚠️ 批量重新解析过程中发生错误：{e}")
    # 重置选中相关组件：递增 widget_key，使复选框与表格以全新 key 重建，回到默认未选中状态
    st.session_state.widget_key += 1
    # 仅成功时刷新看板，避免异常提示被 rerun 吞掉
    if not reparse_failed:
        st.rerun()

def refresh_knowledge_base():
    # 增量更新：加载旧索引(无则新建)，仅追加当前用户的新文档，不再清空/全量重建
    try:
        vectorstore, bm25_data, documents = update_indexes_incremental(embed_model, CURRENT_USER)
        st.session_state.rag_objects = (vectorstore, bm25_data, documents)
        _write_index_marker(embed_model)
        return len(documents) if documents else 0
    except Exception as e:
        st.error(f"加载知识库失败: {e}")
        return 0

# ==================== 🛠️ 左侧边栏：文件管理与自动解析 ====================
with st.sidebar:
    st.title("📁 人才库管理中心")
    st.write("---")

    st.subheader("🤖 模型配置")
    _bs = backend_settings()
    if _bs["backend"] == "api":
        # 云端后端：模型由 Secrets 配置决定，不展示本地模型下拉框
        st.caption(f"☁️ 云端后端：对话模型 `{_bs['llm_model']}`")
        st.caption(f"🧲 Embedding：`{_bs['embed_model']}`")
        st.caption(f"🔗 接口：`{_bs['api_base']}`")
        selected_model = _bs["llm_model"]
    else:
        available_models = ["qwen2.5:7b", "qwen3.5:4b", "qwen3.5:9b", "deepseek-r1:8b"]
        selected_model = st.selectbox("选择用于结构化抽取的模型", available_models, index=0)
    # 将全局模型初始化移动到下拉框之后，使选中的模型即时生效（@st.cache_resource 按参数名隔离缓存）
    llm, fast_llm, embed_model = init_models(selected_model)
    _ensure_index_compatible(embed_model)
    if st.session_state.rag_objects is None:
        refresh_knowledge_base()
        st.session_state.kb_version = KB_VERSION
    elif st.session_state.kb_version != KB_VERSION:
        # 后台队列完成了新的解析（可能是本人刚上传的简历），按当前用户重载索引
        refresh_knowledge_base()
        st.session_state.kb_version = KB_VERSION
    st.write("---")

    st.subheader("📤 上传候选人简历")
    uploaded_files = st.file_uploader("支持 PDF, Word, TXT, HTML", type=["pdf", "docx", "txt", "html", "htm"], accept_multiple_files=True, key=f"uploader_{st.session_state.uploader_key}")

    col_save, col_clear = st.columns(2)
    with col_save:
        save_clicked = st.button("🚀 保存并智能解析简历", key="save_file", type="primary", use_container_width=True)
    with col_clear:
        if st.button("🗑️ 清空列表", key="clear_uploader", use_container_width=True):
            # 动态 Key 重置：递增 uploader_key 改变 file_uploader 的 key，使其卸载并重建为空
            st.session_state.uploader_key += 1
            st.rerun()

    if save_clicked:
        if uploaded_files:
            os.makedirs(USER_DATA_DIR, exist_ok=True)
            saved_count = 0
            for file in uploaded_files:
                file_name = file.name
                file_path = os.path.join(USER_DATA_DIR, file_name)
                try:
                    # 仅保存文件到 DATA_DIR（字节已在主线程读取，跨线程写 UploadedFile 有隐患，故主线程写）
                    with open(file_path, "wb") as f:
                        f.write(file.getbuffer())
                except Exception as e:
                    logging.error(f"保存上传文件失败，已跳过: {file_name}；异常: {e}")
                    continue
                saved_count += 1
                # 构造基础 data（关键字段填“排队解析中…”，状态置 pending 交由后台队列异步解析）
                insert_or_update_resume_db({
                    "owner_email": CURRENT_USER,
                    "filename": file_name,
                    "name": "排队解析中…", "age": "未知", "gender": "未知",
                    "university": "排队解析中…", "major": "排队解析中…", "birth_year": "未知",
                    "tags": "", "degree": "未知", "work_years": "未知",
                    "parse_status": "pending",
                })
            if saved_count > 0:
                st.success(
                    f"🎉 已保存 {saved_count} 份文件并加入后台解析队列，系统将自动解析并更新状态，请稍候刷新看板查看进度！"
                )
            else:
                st.warning("⚠️ 没有文件被成功保存，请检查上传内容后重试。")
            # 上传完成后强制清空上传列表：递增 uploader_key 使上传组件重置为空
            st.session_state.uploader_key += 1
            st.rerun()

    st.write("---")

    st.subheader("📦 简历源文件管理")
    if os.path.exists(USER_DATA_DIR) and os.listdir(USER_DATA_DIR):
        file_list = os.listdir(USER_DATA_DIR)
        
        select_all = st.checkbox("全选所有简历", key=f"select_all_resumes_{st.session_state.widget_key}")
        default_sel = file_list if select_all else []
        selected_files = st.multiselect("批量选中简历进行离职/淘汰处理：", file_list, default=default_sel)

        if selected_files:
            col_del, col_reparse = st.columns(2)
            with col_del:
                if st.button("🗑️ 删除已选中简历", width='stretch'):
                    for f_name in selected_files:
                        safe_remove(os.path.join(USER_DATA_DIR, f_name))
                        delete_resume_from_db(f_name, CURRENT_USER) # 同步删除数据库记录
                    # 增量移除索引中的对应文件（不重建整个索引）
                    for f_name in selected_files:
                        remove_file_from_indexes(f_name, CURRENT_USER, embed_model)
                    vs = load_vectorstore(embed_model)
                    bm = load_bm25()
                    docs = bm["documents"] if bm else []
                    st.session_state.rag_objects = (vs, bm, docs)
                    st.rerun()
            with col_reparse:
                if st.button("🔄 重新解析选中简历", type="primary", width='stretch'):
                    # 复用模块级 _reparse_files：直接读取源文件、跳过 MD5 去重、LLM 抽取、覆盖入库
                    _reparse_files(selected_files)
                
        st.write("---")

        with st.expander("📄 查看所有源文件列表", expanded=False):
            for name in file_list:
                file_path = os.path.join(USER_DATA_DIR, name)
                col_name, col_dl, col_del = st.columns([2, 1, 1])
                col_name.text(f"📄 {name[:8]}...")
                with open(file_path, "rb") as f:
                    col_dl.download_button("下载", data=f.read(), file_name=name, mime="application/octet-stream", key=f"dl_{name}")
                if col_del.button("删除", key=f"del_{name}"):
                    safe_remove(file_path)
                    delete_resume_from_db(name, CURRENT_USER) # 同步删除数据库记录
                    # 增量移除索引中的对应文件（不重建整个索引）
                    remove_file_from_indexes(name, CURRENT_USER, embed_model)
                    vs = load_vectorstore(embed_model)
                    bm = load_bm25()
                    docs = bm["documents"] if bm else []
                    st.session_state.rag_objects = (vs, bm, docs)
                    st.rerun()
            else:
                st.info("暂无简历")
        
    if st.button("🗑️ 清空我的全部人才数据", width='stretch'):
        try:
            # 仅清空当前账号：删除本人文件目录、本人简历记录、本人在索引中的 chunk（不影响其他账号）
            shutil.rmtree(USER_DATA_DIR, ignore_errors=True)
            os.makedirs(USER_DATA_DIR, exist_ok=True)
            # 就地清空本人记录而非删除 .db 文件，避免 Windows 文件锁导致删除失败却误报成功
            clear_user_resumes_db(CURRENT_USER)
            remove_owner_from_indexes(CURRENT_USER, embed_model)
        except Exception as e:
            st.error(f"⚠️ 清空失败: {e}")
            st.stop()
        st.session_state.rag_objects = None
        st.session_state.chat_history = []
        st.session_state.display_history = []
        st.success("您的人才库已彻底清空！")
        st.rerun()

    st.write("---")
    st.caption(f"👤 当前登录：{st.session_state.current_user}")
    if st.button("🚪 安全退出系统", width='stretch'):
        _clear_user_session()
        # 退出必须回到门户：会话是门户签发的，只在本地清 session_state 清不掉 Cookie，
        # 再次访问本应用会被 SSO 立刻自动登录回来。门户的 /logout 会撤销服务端会话并删 Cookie。
        st.markdown(
            f'<meta http-equiv="refresh" content="0; url={portal_client.PORTAL_URL}/logout">',
            unsafe_allow_html=True,
        )
        st.info("已退出。若浏览器没有自动跳转，请点下面的按钮返回门户。")
        st.link_button("🏠 返回门户登录页", f"{portal_client.PORTAL_URL}/logout", width='stretch')
        st.stop()

    st.link_button("🏠 返回门户", portal_client.PORTAL_URL, width='stretch')

# ==================== 📊 右侧主界面：HR 看板与问答 ====================
st.title("👔 智能 HR 人才库与问答系统")

# 1. 渲染结构化数据库看板（仅当前账号）
db_records = get_all_resumes_from_db(CURRENT_USER)
if db_records:
    st.subheader("📋 候选人基本信息总览 (自动抽取)")
    df = pd.DataFrame(db_records)
    # 将「解析状态」列移到「源文件」右侧，便于直观看到 pending / completed / failed 变化
    cols = list(df.columns)
    if "解析状态" in cols:
        cols.remove("解析状态")
        if "源文件" in cols:
            idx = cols.index("源文件") + 1
        else:
            idx = 1
        cols.insert(idx, "解析状态")
        df = df[cols]
    # 可交互表格：开启多行选中 + 标记问题复选框列
    st.dataframe(
        df,
        width='stretch',
        hide_index=True,
        key=f"candidate_df_{st.session_state.widget_key}",
        selection_mode="multi-row",
        on_select="rerun",
        column_config={
            "解析状态": st.column_config.TextColumn(
                "解析状态",
                help="后台队列解析状态：pending=排队中，completed=完成，failed=失败",
            ),
            "标记问题": st.column_config.CheckboxColumn(
                "标记问题",
                help="该简历是否标记为解析异常",
                default=False,
            ),
        },
    )

    # —— 表格多选后的批量快捷操作 ——
    # 从 session_state 读取表格选中行（行号对应 db_records 顺序），转为源文件名列表
    selection = st.session_state.get(f"candidate_df_{st.session_state.widget_key}")
    selected_idx = selection.selection.rows if selection else []
    selected_files = [db_records[i]["源文件"] for i in selected_idx] if selected_idx else []

    if selected_files:
        st.info(f"已选中 {len(selected_files)} 份简历：{', '.join(selected_files)}")
        act_col1, act_col2, act_col3 = st.columns(3)

        # 【🚩 标记/取消问题】
        with act_col1:
            if st.button("🚩 标记/取消问题", use_container_width=True):
                try:
                    for f_name in selected_files:
                        # 取回当前标记状态后反转
                        cur = next((r.get("标记问题", False) for r in db_records if r["源文件"] == f_name), False)
                        update_resume_problem_status(f_name, not bool(cur), CURRENT_USER)
                    st.success(f"✅ 已更新 {len(selected_files)} 份简历的问题标记！")
                except Exception as e:
                    logging.error(f"批量标记问题失败：{e}")
                    st.error(f"⚠️ 批量标记失败：{e}")
                st.rerun()

        # 【🔄 重新解析选中】
        with act_col2:
            if st.button("🔄 重新解析选中", type="primary", use_container_width=True):
                _reparse_files(selected_files)

        # 【🗑️ 删除选中】
        with act_col3:
            if st.button("🗑️ 删除选中", use_container_width=True):
                try:
                    for f_name in selected_files:
                        file_path = os.path.join(USER_DATA_DIR, f_name)
                        safe_remove(file_path)
                        delete_resume_from_db(f_name, CURRENT_USER)  # 同步删除数据库记录
                        remove_file_from_indexes(f_name, CURRENT_USER, embed_model)  # 从 RAG 索引移除
                    vs = load_vectorstore(embed_model)
                    bm = load_bm25()
                    docs = bm["documents"] if bm else []
                    st.session_state.rag_objects = (vs, bm, docs)
                    st.success(f"✅ 已删除 {len(selected_files)} 份简历（含源文件、数据库记录与索引）！")
                except Exception as e:
                    logging.error(f"批量删除失败：{e}")
                    st.error(f"⚠️ 批量删除失败：{e}")
                st.rerun()

    st.divider()
else:
    st.info("👈 请在左侧上传候选人简历，系统将自动提取其基本信息呈现于此。")

# 动态状态显示：当存在 pending 任务时，每 3 秒自动刷新看板以反映后台队列进展
if any(r.get("解析状态") == "pending" for r in db_records):
    st.caption("⏳ 检测到待解析任务，看板每 3 秒自动刷新…")
    time.sleep(3)
    st.rerun()

# 1.5 基于标签的结构化检索（不依赖大模型 / RAG，纯 SQLite LIKE 过滤）
with st.expander("🏷️ 按标签快速筛选人才（结构化 SQL 检索）", expanded=False):
    st.caption("输入标签，多个用逗号或空格分隔；将返回同时包含所有标签的候选人（AND 语义）。")
    tag_input = st.text_input(
        "标签，如：985, Python, 3年经验", key="tag_search_input",
        placeholder="985, Python",
    )
    fuzzy_match = st.checkbox("开启模糊匹配（例如输入'数字媒体'可匹配'数字媒体技术'）", value=True, key="fuzzy_match_cb")
    if st.button("🔎 标签检索", key="tag_search_btn"):
        tags = [t.strip() for t in re.split(r"[,，\s]+", tag_input) if t.strip()]
        if not tags:
            st.warning("请输入至少一个标签")
        else:
            with st.spinner("🔍 正在执行结构化标签检索..."):
                hits = search_resumes_by_tags(tags, fuzzy=fuzzy_match, owner_email=CURRENT_USER)
            if not hits:
                st.info("未匹配到任何候选人，请尝试更少 / 更宽泛的标签。")
            else:
                st.success(f"✅ 命中 {len(hits)} 份简历")
                hit_df = pd.DataFrame([
                    {
                        "源文件": h["filename"],
                        "姓名": h["name"],
                        "匹配标签": ", ".join(h["matched_tags"]),
                        "全部标签": ", ".join(h["tags"]),
                    }
                    for h in hits
                ])
                st.dataframe(hit_df, width='stretch', hide_index=True)
    st.divider()

# 1.6 JD 智能匹配（直接比对简历原文与 JD，不依赖 RAG 索引）
st.divider()
st.subheader("🤝 JD 智能匹配")
jd_text = st.text_area(
    "粘贴岗位 JD 描述：", height=160, key="jd_text",
    placeholder="例如：本科及以上学历，精通 Python，熟悉 Vue，3 年以上后端开发经验……",
)
candidates = get_all_resumes_from_db(CURRENT_USER)
cand_options = [r["源文件"] for r in candidates] if candidates else []
selected = st.multiselect("选择参与匹配的候选人（可多选）：", options=cand_options, key="jd_cands")
if st.button("🚀 开始 JD 智能匹配", key="jd_match_btn", disabled=not (jd_text.strip() and selected)):
    with st.spinner("🤖 大模型正在比对简历与 JD，请稍候..."):
        for fn in selected:
            fpath = os.path.join(USER_DATA_DIR, fn)
            chunks = _load_single_file_documents(fpath, fn, CURRENT_USER) if os.path.exists(fpath) else []
            resume_text = "\n".join(d.page_content for d in chunks) or "(无法读取简历文本)"
            r = evaluate_resume_against_jd(resume_text, jd_text, llm)
            score_disp = r["score"] if r["score"] is not None else "N/A"
            with st.expander(f"📌 {fn} ｜ 匹配分：{score_disp}", expanded=True):
                st.markdown("**✅ 匹配优势**")
                if r["pros"]:
                    for p in r["pros"]:
                        st.markdown(f"- {p}")
                else:
                    st.markdown("- （无）")
                st.markdown("**⚠️ 欠缺部分**")
                if r["cons"]:
                    for c in r["cons"]:
                        st.markdown(f"- {c}")
                else:
                    st.markdown("- （无明显欠缺）")
    st.divider()

# 2. 渲染 RAG 聊天问答区
st.subheader("💬 人才细节深度追问")
for chat in st.session_state.display_history:
    with st.chat_message(chat["role"]): st.write(chat["content"])

if user_query := st.chat_input("向系统提问，如：找出有 Vue 开发经验的人..."):
    with st.chat_message("user"): st.write(user_query)
    st.session_state.display_history.append({"role": "user", "content": user_query})
    
    if st.session_state.rag_objects is None:
        st.error("当前本地知识库为空！")
    else:
        vectorstore, bm25_data, documents = st.session_state.rag_objects
        import io, sys
        old_stdout = sys.stdout
        sys.stdout = buffer = io.StringIO()
        
        with st.chat_message("assistant"):
            with st.spinner("🔍 正在穿透检索简历细节..."):
                reply = answer_question_with_memory(
                    user_query, vectorstore, bm25_data, documents, llm, st.session_state.chat_history, CURRENT_USER
                )
                st.write(reply)
                
        sys.stdout = old_stdout
        debug_info = buffer.getvalue()
        if debug_info:
            with st.expander("📊 查看检索评分详情"):
                st.code(debug_info, language="text")
        st.session_state.display_history.append({"role": "assistant", "content": reply})
