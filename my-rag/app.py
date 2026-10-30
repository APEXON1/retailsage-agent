"""智能 HR 人才库与问答系统 —— Gradio UI 层（原 Streamlit 版重写）。

三应用 UI 统一到 Gradio 后，本文件只负责「界面 + 交互编排」，全部业务能力
（账号/鉴权、SQLite 看板、结构化抽取、FAISS+BM25 混合检索、带记忆问答）
仍复用既有后端模块：config / auth / database / extractor / rag_engine / seed_demo / portal_client。

两种运行方式（与洞察端 app.py / run_insight_web.py 对称）：
  1) 单机：`python app.py` —— 无门户，走应用内「登录 / 注册 / 找回密码」页；
  2) 门户托管：`python run_kb_web.py` —— 前置 Gate 校验 rs_session，
     命中后 gr.Request().username 即当前账号，直接进入主界面（跳过登录页）。

范式转换备忘（Streamlit → Gradio）：
  st.session_state → gr.State；st.rerun/st.stop → 事件返回值；
  @st.cache_resource → 模块级 lru_cache；st.sidebar → 左侧 Column；
  st.expander → gr.Accordion；st.chat_message/chat_input → gr.Chatbot + gr.Textbox；
  每 3 秒 st.rerun 轮询 → gr.Timer(3)。
"""

from __future__ import annotations

import os
import re
import io
import shutil
import logging
import threading
import contextlib
from functools import lru_cache

import pandas as pd
import gradio as gr

os.environ.setdefault("NO_PROXY", "localhost,127.0.0.1")
os.environ.setdefault("no_proxy", "localhost,127.0.0.1")
os.environ.setdefault("USER_AGENT", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")

from config import DATA_DIR, INDEX_DIR, BM25_PKL, backend_settings, DEMO_EMAIL, DEMO_PASSWORD
import auth as auth_mod
import portal_client
import seed_demo
from database import (
    init_db, get_all_resumes_from_db, delete_resume_from_db, clear_user_resumes_db,
    search_resumes_by_tags, insert_or_update_resume_db, update_resume_problem_status,
    update_resume_status, get_pending_resumes, safe_remove, calculate_file_md5,
)
from extractor import extract_resume_info_with_llm, evaluate_resume_against_jd
from rag_engine import (
    load_vectorstore, load_bm25, update_indexes_incremental,
    remove_file_from_indexes, remove_owner_from_indexes,
    answer_question_with_memory, _load_single_file_documents,
)
from langchain_ollama import ChatOllama, OllamaEmbeddings

from rs_theme import THEME_CSS, build_theme

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")

# 结构化抽取可选模型（仅本地 ollama 后端用；云端 api 后端由 backend_settings 决定）
EXTRACT_MODELS = ["qwen2.5:7b", "qwen3.5:4b", "qwen3.5:9b", "deepseek-r1:8b"]
DEFAULT_EXTRACT_MODEL = EXTRACT_MODELS[0]

# ==================== 🗄️ 启动初始化（进程内一次） ====================
# 必须早于任何登录校验：全新目录首次访问时若 users/resumes 表尚未建，注册与看板都会报错。
init_db()
seed_demo.ensure_demo_account()

# 模块级全局：后台解析队列每完成一批就 +1，前端据此按需重载索引
KB_VERSION = 0


# ==================== 🤖 模型初始化（原 @st.cache_resource → lru_cache） ====================
@lru_cache(maxsize=None)
def get_models(fast_model_name: str = DEFAULT_EXTRACT_MODEL):
    """初始化 LLM / 快速解析模型 / Embedding 模型，后端由 backend_settings() 决定：
      - ollama（默认）：本地 Ollama，零成本；
      - api：OpenAI 兼容接口（硅基流动 / DeepSeek / Moonshot 等），密钥走环境变量。
    lru_cache 按模型名缓存，避免每次交互重建客户端。"""
    s = backend_settings()
    if s["backend"] == "api":
        from langchain_openai import ChatOpenAI, OpenAIEmbeddings
        common = dict(base_url=s["api_base"], api_key=s["api_key"], request_timeout=300, max_retries=1)
        llm = ChatOpenAI(model=s["llm_model"], temperature=0.2, **common)
        fast_llm = ChatOpenAI(model=s["llm_model"], temperature=0, **common)
        embed_model = OpenAIEmbeddings(model=s["embed_model"], check_embedding_ctx_length=False, **common)
    else:
        llm = ChatOllama(model="deepseek-r1:8b", base_url="http://127.0.0.1:11434",
                         sync_client_kwargs={"timeout": 600}, async_client_kwargs={"timeout": 600})
        fast_llm = ChatOllama(model=fast_model_name, base_url="http://127.0.0.1:11434", num_ctx=4096,
                              sync_client_kwargs={"timeout": 600}, async_client_kwargs={"timeout": 600})
        embed_model = OllamaEmbeddings(model="nomic-embed-text", base_url="http://127.0.0.1:11434")
    return llm, fast_llm, embed_model


# ==================== 🗂️ 索引兼容性自检（切换后端时维度不匹配必须重建） ====================
def _index_marker_path() -> str:
    return os.path.join(INDEX_DIR, ".embed_model")


def _ensure_index_compatible(embed_model):
    """校验本地索引与当前 embedding 模型是否匹配；不匹配则把旧索引挪到备份目录。
    1. 用 utf-8-sig 读取（历史标记文件可能带 BOM，\\ufeff 不是空白，.strip() 去不掉）；
    2. 不删除只改名成 .stale —— 索引重建依赖 embedding 服务，服务不可用时还能回滚。"""
    name = getattr(embed_model, "model", "") or ""
    if not os.path.exists(INDEX_DIR):
        return
    try:
        with open(_index_marker_path(), "r", encoding="utf-8-sig") as f:
            if f.read().strip() == name:
                return
    except OSError:
        pass
    stale_dir = f"{INDEX_DIR}.stale"
    try:
        if os.path.exists(stale_dir):
            shutil.rmtree(stale_dir, ignore_errors=True)
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


# ==================== 🔎 RAG 对象缓存（全局索引，按 KB_VERSION 失效） ====================
# 索引是「全账号共享、检索时按 owner 过滤」，故可进程内共享一份缓存；
# 后台队列完成解析会 +KB_VERSION，本地删除/清空会显式失效，下次问答自动重载。
_RAG_LOCK = threading.Lock()
_RAG_CACHE = {"version": -1, "obj": None}


def _invalidate_rag():
    with _RAG_LOCK:
        _RAG_CACHE["version"] = -1
        _RAG_CACHE["obj"] = None


def _get_rag(embed_model):
    with _RAG_LOCK:
        if _RAG_CACHE["obj"] is None or _RAG_CACHE["version"] != KB_VERSION:
            vs = load_vectorstore(embed_model)
            bm = load_bm25()
            docs = bm["documents"] if bm else []
            _RAG_CACHE["obj"] = (vs, bm, docs)
            _RAG_CACHE["version"] = KB_VERSION
        return _RAG_CACHE["obj"]


# ==================== 🌀 后台异步解析队列（daemon 线程，进程内启动一次） ====================
def _worker_loop():
    global KB_VERSION
    while True:
        try:
            _, fast_llm, _ = get_models(DEFAULT_EXTRACT_MODEL)
            pending = get_pending_resumes()
            if pending:
                logging.info(f"[后台队列] 检测到 {len(pending)} 份待解析任务，开始串行处理…")
                processed_any = False
                for task in pending:
                    fname = task["filename"]
                    owner = task["owner_email"]
                    file_path = os.path.join(DATA_DIR, owner, fname)
                    if not os.path.exists(file_path):
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
                            insert_or_update_resume_db({
                                "owner_email": owner, "filename": fname,
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
                if processed_any:
                    try:
                        _, _, embed_model = get_models(DEFAULT_EXTRACT_MODEL)
                        for owner in {t["owner_email"] for t in pending}:
                            update_indexes_incremental(embed_model, owner)
                        _write_index_marker(embed_model)
                    except Exception as e:
                        logging.error(f"[后台队列] 刷新知识库失败：{e}")
                    KB_VERSION += 1
                    _invalidate_rag()
        except Exception as e:
            logging.error(f"[后台队列] 主循环异常（将继续重试）：{e}")
        threading.Event().wait(3)


_WORKER_STARTED = False
_WORKER_LOCK = threading.Lock()


def start_background_worker():
    global _WORKER_STARTED
    with _WORKER_LOCK:
        if _WORKER_STARTED:
            return
        _WORKER_STARTED = True
    threading.Thread(target=_worker_loop, daemon=True).start()


# 启动时做一次索引兼容性自检并起后台队列
try:
    _, _, _embed0 = get_models(DEFAULT_EXTRACT_MODEL)
    _ensure_index_compatible(_embed0)
    _write_index_marker(_embed0)
except Exception as _e:  # 模型客户端构造失败不应阻断 UI 启动
    logging.warning(f"启动期索引自检跳过：{_e}")
start_background_worker()


# ==================== 🧮 视图数据装配 ====================
_EMPTY_CAND_COLS = ["源文件", "解析状态", "姓名", "性别", "年龄", "出生年月",
                    "毕业院校", "专业", "学历层次", "工作年限", "标签", "标记问题"]


def _records_to_df(records):
    """把 DB 记录列表转成看板 DataFrame，并把「解析状态」列挪到「源文件」右侧。"""
    if not records:
        return pd.DataFrame(columns=_EMPTY_CAND_COLS)
    df = pd.DataFrame(records)
    cols = list(df.columns)
    if "解析状态" in cols:
        cols.remove("解析状态")
        idx = cols.index("源文件") + 1 if "源文件" in cols else 0
        cols.insert(idx, "解析状态")
        df = df[cols]
    return df


def _file_table_df(user):
    user_dir = os.path.join(DATA_DIR, user) if user else ""
    if not user_dir or not os.path.isdir(user_dir):
        return pd.DataFrame(columns=["文件名", "大小(KB)"]), []
    names = sorted(n for n in os.listdir(user_dir) if os.path.isfile(os.path.join(user_dir, n)))
    rows = []
    for n in names:
        try:
            kb = round(os.path.getsize(os.path.join(user_dir, n)) / 1024, 1)
        except OSError:
            kb = 0
        rows.append({"文件名": n, "大小(KB)": kb})
    return (pd.DataFrame(rows, columns=["文件名", "大小(KB)"]) if rows
            else pd.DataFrame(columns=["文件名", "大小(KB)"])), names


def _view_tuple(user):
    """主界面所有「随数据变化」组件的一次性刷新载荷（顺序须与 VIEW_COMPONENTS 一致）。"""
    records = get_all_resumes_from_db(user) if user else []
    df = _records_to_df(records)
    filenames = [r["源文件"] for r in records]
    file_df, disk = _file_table_df(user)
    pending = [r for r in records if r.get("解析状态") == "pending"]
    parse_md = f"⏳ {len(pending)} 份简历解析中，看板每 3 秒自动刷新…" if pending else ""
    account_md = f"👤 当前登录：{user}" if user else ""
    return (
        df,
        parse_md,
        gr.update(choices=filenames, value=[]),      # cand_select
        gr.update(choices=disk, value=[]),           # src_group
        file_df,                                      # file_table
        gr.update(choices=disk, value=None),         # download_dd
        gr.update(choices=filenames, value=[]),      # jd_cands
        account_md,
    )


# ==================== 🎛️ 事件处理函数 ====================
def _after_login(user, model_name):
    """进入主界面前，为该账号做一次增量索引自愈（已索引文件会被跳过，通常很快）。"""
    if user:
        os.makedirs(os.path.join(DATA_DIR, user), exist_ok=True)
        try:
            _, _, embed = get_models(model_name or DEFAULT_EXTRACT_MODEL)
            update_indexes_incremental(embed, user)
            _write_index_marker(embed)
            _invalidate_rag()
        except Exception as e:
            logging.warning(f"进入时增量刷新索引失败（不影响看板）：{e}")
    return (*_view_tuple(user), [], [])


def _on_load(request: gr.Request):
    """页面加载：门户托管时 request.username 即 SSO 账号，直接进入；单机则显示登录页。"""
    username = None
    try:
        username = request.username
    except Exception:
        username = None
    if username:
        os.makedirs(os.path.join(DATA_DIR, username), exist_ok=True)
        logging.info(f"[SSO] 门户会话校验通过，自动登录: {username}")
        return gr.update(visible=False), gr.update(visible=True), username, True, f"👤 门户账号：{username}"
    return gr.update(visible=True), gr.update(visible=False), "", False, ""


def do_login(identifier, password):
    ok, msg, email = auth_mod.authenticate(identifier, password)
    if not ok:
        return gr.update(visible=True), gr.update(visible=False), "", False, f"⚠️ {msg}"
    os.makedirs(os.path.join(DATA_DIR, email), exist_ok=True)
    return gr.update(visible=False), gr.update(visible=True), email, False, f"✅ {msg}"


def do_demo_login():
    seed_demo.ensure_demo_account()
    os.makedirs(os.path.join(DATA_DIR, DEMO_EMAIL), exist_ok=True)
    return gr.update(visible=False), gr.update(visible=True), DEMO_EMAIL, False, f"🎯 已进入演示账号：{DEMO_EMAIL}"


def do_send_code(email, purpose):
    ok, msg, demo_code = auth_mod.request_verification_code(email, purpose)
    if ok and demo_code:
        return f"🔔【演示模式】验证码：**{demo_code}**（5 分钟内有效，未真实发送邮件）"
    return ("✅ " + msg) if ok else f"⚠️ {msg}"


def do_register(email, pwd, pwd2, code):
    ok, msg = auth_mod.register_user(email, pwd, pwd2, code)
    if not ok:
        return gr.update(visible=True), gr.update(visible=False), "", False, "", f"⚠️ {msg}"
    account = auth_mod.normalize_login_id(email)
    os.makedirs(os.path.join(DATA_DIR, account), exist_ok=True)
    return gr.update(visible=False), gr.update(visible=True), account, False, f"✅ {msg}", ""


def do_reset(email, code, pwd, pwd2):
    ok, msg = auth_mod.reset_password(email, code, pwd, pwd2)
    return ("✅ " + msg) if ok else f"⚠️ {msg}"


def do_logout(sso):
    """退出：门户托管回门户 /logout 撤销服务端会话并删 Cookie；单机则回到登录页。"""
    html = ""
    if sso:
        html = f'<meta http-equiv="refresh" content="0; url={portal_client.PORTAL_URL}/logout">'
    return gr.update(visible=True), gr.update(visible=False), "", False, html


def do_upload(files, user):
    if not user:
        return (*_view_tuple(user), "⚠️ 请先登录", None)
    if not files:
        return (*_view_tuple(user), "⚠️ 请先选择要上传的文件", None)
    user_dir = os.path.join(DATA_DIR, user)
    os.makedirs(user_dir, exist_ok=True)
    saved = 0
    for f in files:
        name = os.path.basename(f)
        dst = os.path.join(user_dir, name)
        try:
            shutil.copyfile(f, dst)
        except Exception as e:
            logging.error(f"保存上传文件失败，已跳过: {name}；异常: {e}")
            continue
        saved += 1
        insert_or_update_resume_db({
            "owner_email": user, "filename": name,
            "name": "排队解析中…", "age": "未知", "gender": "未知",
            "university": "排队解析中…", "major": "排队解析中…", "birth_year": "未知",
            "tags": "", "degree": "未知", "work_years": "未知", "parse_status": "pending",
        })
    msg = (f"🎉 已保存 {saved} 份文件并加入后台解析队列，系统将自动解析并更新看板！"
           if saved else "⚠️ 没有文件被成功保存，请检查上传内容后重试。")
    return (*_view_tuple(user), msg, None)


def do_delete_files(files, user, model_name):
    if not user or not files:
        return (*_view_tuple(user), "⚠️ 请先选择要删除的文件")
    _, _, embed = get_models(model_name or DEFAULT_EXTRACT_MODEL)
    try:
        for fn in files:
            safe_remove(os.path.join(DATA_DIR, user, fn))
            delete_resume_from_db(fn, user)
            remove_file_from_indexes(fn, user, embed)
        _invalidate_rag()
        return (*_view_tuple(user), f"✅ 已删除 {len(files)} 份简历（含源文件、数据库记录与索引）！")
    except Exception as e:
        logging.error(f"批量删除失败：{e}")
        return (*_view_tuple(user), f"⚠️ 批量删除失败：{e}")


def do_reparse(files, user):
    if not user or not files:
        return (*_view_tuple(user), "⚠️ 请先选择要重新解析的文件")
    queued = 0
    for fn in files:
        fpath = os.path.join(DATA_DIR, user, fn)
        if not os.path.exists(fpath):
            logging.warning(f"重新解析跳过：源文件不存在 {fn}")
            continue
        insert_or_update_resume_db({
            "owner_email": user, "filename": fn,
            "name": "重新排队中…", "age": "未知", "gender": "未知",
            "university": "重新排队中…", "major": "重新排队中…", "birth_year": "未知",
            "tags": "", "degree": "未知", "work_years": "未知", "parse_status": "pending",
        })
        update_resume_status(fn, "pending", user)
        queued += 1
    return (*_view_tuple(user), f"🎉 已将 {queued} 份简历加入后台重新解析队列，请稍候查看看板进度！")


def do_mark(files, user):
    if not user or not files:
        return (*_view_tuple(user), "⚠️ 请先在看板下方选择候选人")
    records = get_all_resumes_from_db(user)
    try:
        for fn in files:
            cur = next((r.get("标记问题", False) for r in records if r["源文件"] == fn), False)
            update_resume_problem_status(fn, not bool(cur), user)
        return (*_view_tuple(user), f"✅ 已切换 {len(files)} 份简历的问题标记！")
    except Exception as e:
        logging.error(f"批量标记问题失败：{e}")
        return (*_view_tuple(user), f"⚠️ 批量标记失败：{e}")


def do_clear_all(confirm, user, model_name):
    if not user:
        return (*_view_tuple(user), [], [], "⚠️ 请先登录", False)
    if not confirm:
        return (*_view_tuple(user), [], [], "⚠️ 请先勾选「我确认清空」再执行", True)
    _, _, embed = get_models(model_name or DEFAULT_EXTRACT_MODEL)
    try:
        user_dir = os.path.join(DATA_DIR, user)
        shutil.rmtree(user_dir, ignore_errors=True)
        os.makedirs(user_dir, exist_ok=True)
        clear_user_resumes_db(user)
        remove_owner_from_indexes(user, embed)
        _invalidate_rag()
    except Exception as e:
        logging.error(f"清空失败：{e}")
        return (*_view_tuple(user), [], [], f"⚠️ 清空失败: {e}", False)
    return (*_view_tuple(user), [], [], "✅ 您的人才库已彻底清空！", False)


def set_download(name, user):
    if not name or not user:
        return gr.update(value=None)
    p = os.path.join(DATA_DIR, user, name)
    return gr.update(value=p if os.path.exists(p) else None)


def refresh_dashboard(user):
    """gr.Timer 周期调用：仅刷新看板与解析状态提示，避免打断用户正在进行的选择。"""
    if not user:
        return gr.update(), ""
    records = get_all_resumes_from_db(user)
    df = _records_to_df(records)
    pending = [r for r in records if r.get("解析状态") == "pending"]
    md = f"⏳ {len(pending)} 份简历解析中，看板每 3 秒自动刷新…" if pending else ""
    return df, md


def do_tag_search(tag_input, fuzzy, user):
    if not user:
        return pd.DataFrame(columns=["源文件", "姓名", "匹配标签", "全部标签"]), "⚠️ 请先登录"
    tags = [t.strip() for t in re.split(r"[,，\s]+", tag_input or "") if t.strip()]
    if not tags:
        return pd.DataFrame(columns=["源文件", "姓名", "匹配标签", "全部标签"]), "⚠️ 请输入至少一个标签"
    hits = search_resumes_by_tags(tags, fuzzy=fuzzy, owner_email=user)
    if not hits:
        return (pd.DataFrame(columns=["源文件", "姓名", "匹配标签", "全部标签"]),
                "未匹配到任何候选人，请尝试更少 / 更宽泛的标签。")
    df = pd.DataFrame([{
        "源文件": h["filename"], "姓名": h["name"],
        "匹配标签": ", ".join(h["matched_tags"]), "全部标签": ", ".join(h["tags"]),
    } for h in hits])
    return df, f"✅ 命中 {len(hits)} 份简历"


def do_jd(jd, cands, user, model_name):
    if not user:
        return "⚠️ 请先登录"
    if not (jd or "").strip() or not cands:
        return "⚠️ 请填写岗位 JD 并至少选择一位候选人。"
    llm, _, _ = get_models(model_name or DEFAULT_EXTRACT_MODEL)
    blocks = []
    for fn in cands:
        fpath = os.path.join(DATA_DIR, user, fn)
        chunks = _load_single_file_documents(fpath, fn, user) if os.path.exists(fpath) else []
        resume_text = "\n".join(d.page_content for d in chunks) or "(无法读取简历文本)"
        r = evaluate_resume_against_jd(resume_text, jd, llm)
        score = r["score"] if r["score"] is not None else "N/A"
        pros = "\n".join(f"- {p}" for p in r["pros"]) or "- （无）"
        cons = "\n".join(f"- {c}" for c in r["cons"]) or "- （无明显欠缺）"
        blocks.append(f"### 📌 {fn} ｜ 匹配分：{score}\n\n**✅ 匹配优势**\n{pros}\n\n**⚠️ 欠缺部分**\n{cons}")
    return "\n\n---\n\n".join(blocks)


def ask(question, history, chat_hist, user, model_name):
    question = (question or "").strip()
    if not question:
        return history, chat_hist, "", gr.update()
    if not user:
        return history, chat_hist, "", "⚠️ 请先登录"
    llm, _, embed = get_models(model_name or DEFAULT_EXTRACT_MODEL)
    vs, bm, docs = _get_rag(embed)
    if vs is None or bm is None:
        history = history + [{"role": "user", "content": question},
                             {"role": "assistant", "content": "当前本地知识库为空！请先在左侧上传并等待解析完成。"}]
        return history, chat_hist, "", gr.update()
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            reply = answer_question_with_memory(question, vs, bm, docs, llm, chat_hist, user)
    except Exception as e:
        logging.error(f"问答失败：{e}")
        reply = f"⚠️ 问答失败：{e}"
    debug = buf.getvalue()
    history = history + [{"role": "user", "content": question}, {"role": "assistant", "content": reply}]
    return history, chat_hist, "", (debug if debug else gr.update())


# ==================== 🎨 知识库专有样式（与 THEME_CSS 拼接） ====================
_KB_CSS = """
.kb-wrap { max-width: 1440px; margin: auto !important; }
.kb-title h1 { font-size: 24px !important; font-weight: 600 !important; color: var(--rs-text) !important; margin: 0 !important; }
.kb-subtitle { font-size: 13px !important; color: var(--rs-text-dim) !important; margin-top: 4px !important; }
.kb-sidebar {
    background: var(--rs-panel) !important;
    border: 1px solid var(--rs-border) !important;
    border-radius: 16px !important;
    box-shadow: var(--rs-shadow) !important;
    padding: 14px 16px !important;
    position: sticky !important; top: 16px !important; align-self: flex-start !important;
    max-height: calc(100vh - 32px) !important; overflow-y: auto !important;
}
.kb-sidebar-title { font-size: 17px !important; font-weight: 600 !important; color: var(--rs-text) !important; }
.kb-section { font-size: 14px !important; font-weight: 600 !important; color: var(--rs-text) !important; margin-top: 6px !important; }
.kb-hr { border: none !important; border-top: 1px solid var(--rs-border) !important; margin: 12px 0 !important; }
.kb-card {
    background: var(--rs-panel) !important;
    border: 1px solid var(--rs-border) !important;
    border-radius: 16px !important;
    box-shadow: var(--rs-shadow) !important;
    padding: 14px 16px !important;
}
.kb-status { font-size: 13px !important; color: var(--rs-text-dim) !important; min-height: 18px !important; }
.kb-login-card {
    max-width: 520px; margin: 40px auto !important;
    background: var(--rs-panel) !important; border: 1px solid var(--rs-border) !important;
    border-radius: 18px !important; box-shadow: var(--rs-shadow-lift) !important; padding: 24px 28px !important;
}
"""

KB_CSS = THEME_CSS + _KB_CSS


# ==================== 🧱 UI 构建 ====================
def build_kb() -> gr.Blocks:
    _bs = backend_settings()
    # Gradio 6：theme/css 由 launch()（单机）或 mount_gradio_app()（门户托管）传入，
    # 不再放在 Blocks() 构造器（6.0 起已弃用，且 mount 会忽略构造器里的 theme）。
    with gr.Blocks(title="智能 HR 人才库与问答系统") as demo:
        # ---- 会话级状态 ----
        user_state = gr.State("")
        sso_state = gr.State(False)
        chat_hist_state = gr.State([])

        # ================= 登录面板（单机模式） =================
        with gr.Column(visible=True) as login_panel:
            with gr.Column(elem_classes=["kb-login-card"]):
                gr.Markdown('<div class="kb-title"><h1>👔 智能 HR 人才库与问答系统</h1></div>'
                            '<div class="kb-subtitle">多账号体系 · 各账号人才数据互相隔离</div>')
                login_msg = gr.Markdown("", elem_classes=["kb-status"])
                with gr.Tabs():
                    with gr.Tab("🔑 登录"):
                        login_id = gr.Textbox(label="账号（邮箱，或旧账号如 hr03）")
                        login_pwd = gr.Textbox(label="密码", type="password")
                        login_btn = gr.Button("登录", variant="primary")
                        demo_btn = gr.Button("🎯 一键体验（无需注册，直接进入演示账号）")
                        _smtp_hint = ("💡 当前未配置 SMTP，注册验证码将以「演示模式」在页面直接显示"
                                      if not auth_mod.smtp_configured() else "")
                        if _smtp_hint:
                            gr.Markdown(f"<div class='kb-subtitle'>{_smtp_hint}</div>")
                    with gr.Tab("📝 邮箱注册"):
                        gr.Markdown("<div class='kb-subtitle'>注册即创建您独立的人才库，上传的简历仅自己可见。</div>")
                        reg_email = gr.Textbox(label="邮箱")
                        reg_pwd = gr.Textbox(label="密码（≥6位，含字母和数字）", type="password")
                        reg_pwd2 = gr.Textbox(label="确认密码", type="password")
                        reg_code = gr.Textbox(label="邮箱验证码")
                        reg_msg = gr.Markdown("", elem_classes=["kb-status"])
                        send_code_btn = gr.Button("📨 发送验证码")
                        register_btn = gr.Button("✅ 注册并登录", variant="primary")
                    with gr.Tab("❓ 忘记密码"):
                        gr.Markdown("<div class='kb-subtitle'>通过注册邮箱的验证码重置密码。</div>")
                        rs_email = gr.Textbox(label="邮箱")
                        rs_send_btn = gr.Button("📨 发送验证码")
                        rs_code = gr.Textbox(label="验证码")
                        rs_pwd = gr.Textbox(label="新密码", type="password")
                        rs_pwd2 = gr.Textbox(label="确认新密码", type="password")
                        rs_msg = gr.Markdown("", elem_classes=["kb-status"])
                        reset_btn = gr.Button("🔁 重置密码", variant="primary")

        # ================= 主面板（登录后） =================
        with gr.Row(visible=False, elem_classes=["kb-wrap"]) as main_panel:
            # ---------- 左侧：人才库管理中心 ----------
            with gr.Column(scale=1, min_width=320, elem_classes=["kb-sidebar"]):
                gr.Markdown('<div class="kb-sidebar-title">📁 人才库管理中心</div>')
                status_md = gr.Markdown("", elem_classes=["kb-status"])
                gr.HTML('<hr class="kb-hr">')

                gr.Markdown('<div class="kb-section">🤖 模型配置</div>')
                if _bs["backend"] == "api":
                    gr.Markdown(f"<div class='kb-subtitle'>☁️ 云端后端<br>对话模型：<code>{_bs['llm_model']}</code><br>"
                                f"Embedding：<code>{_bs['embed_model']}</code><br>接口：<code>{_bs['api_base']}</code></div>")
                    model_dd = gr.Dropdown(choices=[_bs["llm_model"]], value=_bs["llm_model"], visible=False)
                else:
                    model_dd = gr.Dropdown(choices=EXTRACT_MODELS, value=DEFAULT_EXTRACT_MODEL,
                                           label="结构化抽取模型")
                gr.HTML('<hr class="kb-hr">')

                gr.Markdown('<div class="kb-section">📤 上传候选人简历</div>')
                upload_files = gr.File(label="支持 PDF / Word / TXT / HTML（可多选）",
                                       file_count="multiple", type="filepath",
                                       file_types=[".pdf", ".docx", ".txt", ".html", ".htm"])
                upload_btn = gr.Button("🚀 保存并智能解析简历", variant="primary")
                clear_upload_btn = gr.Button("🗑️ 清空列表")
                gr.HTML('<hr class="kb-hr">')

                gr.Markdown('<div class="kb-section">📦 简历源文件管理</div>')
                src_group = gr.CheckboxGroup(choices=[], label="批量选中源文件")
                with gr.Row():
                    del_files_btn = gr.Button("🗑️ 删除选中", scale=1)
                    reparse_files_btn = gr.Button("🔄 重新解析", variant="primary", scale=1)
                with gr.Accordion("📄 查看所有源文件", open=False):
                    file_table = gr.Dataframe(headers=["文件名", "大小(KB)"], interactive=False,
                                              label="源文件列表", row_count=(1, "dynamic"))
                    download_dd = gr.Dropdown(choices=[], label="选择要下载的文件")
                    download_btn = gr.DownloadButton("⬇️ 下载选中文件", value=None)
                gr.HTML('<hr class="kb-hr">')

                clear_all_confirm = gr.Checkbox(label="我确认清空本人全部人才数据（不可恢复）", value=False)
                clear_all_btn = gr.Button("🗑️ 清空我的全部人才数据")
                gr.HTML('<hr class="kb-hr">')

                account_md = gr.Markdown("", elem_classes=["kb-status"])
                logout_btn = gr.Button("🚪 安全退出系统")
                gr.HTML(f'<p class="portal-link"><a href="{portal_client.PORTAL_URL}" target="_self">🏠 返回门户</a></p>')
                logout_html = gr.HTML("")

            # ---------- 右侧：看板 / 检索 / JD / 问答 ----------
            with gr.Column(scale=3, elem_classes=["main-panel"]):
                gr.Markdown('<div class="kb-title"><h1>👔 智能 HR 人才库与问答系统</h1></div>')

                with gr.Column(elem_classes=["kb-card"]):
                    gr.Markdown('<div class="kb-section">📋 候选人基本信息总览（自动抽取）</div>')
                    parse_status_md = gr.Markdown("", elem_classes=["kb-status"])
                    candidate_df = gr.Dataframe(headers=_EMPTY_CAND_COLS, interactive=False,
                                                label="候选人看板", row_count=(1, "dynamic"), wrap=True)
                    cand_select = gr.CheckboxGroup(choices=[], label="选择候选人做批量操作（标记 / 重新解析 / 删除）")
                    with gr.Row():
                        mark_btn = gr.Button("🚩 标记/取消问题", scale=1)
                        cand_reparse_btn = gr.Button("🔄 重新解析选中", variant="primary", scale=1)
                        cand_del_btn = gr.Button("🗑️ 删除选中", scale=1)

                with gr.Accordion("🏷️ 按标签快速筛选人才（结构化 SQL 检索）", open=False):
                    gr.Markdown("<div class='kb-subtitle'>输入标签，多个用逗号或空格分隔；返回同时包含所有标签的候选人（AND 语义）。</div>")
                    tag_input = gr.Textbox(label="标签，如：985, Python, 3年经验", placeholder="985, Python")
                    fuzzy_cb = gr.Checkbox(label="开启模糊匹配（如输入'数字媒体'可匹配'数字媒体技术'）", value=True)
                    tag_search_btn = gr.Button("🔎 标签检索", variant="primary")
                    tag_status = gr.Markdown("", elem_classes=["kb-status"])
                    tag_result_df = gr.Dataframe(headers=["源文件", "姓名", "匹配标签", "全部标签"],
                                                 interactive=False, label="检索结果", row_count=(1, "dynamic"))

                with gr.Column(elem_classes=["kb-card"]):
                    gr.Markdown('<div class="kb-section">🤝 JD 智能匹配</div>')
                    jd_text = gr.Textbox(label="粘贴岗位 JD 描述", lines=5,
                                         placeholder="例如：本科及以上学历，精通 Python，熟悉 Vue，3 年以上后端开发经验……")
                    jd_cands = gr.CheckboxGroup(choices=[], label="选择参与匹配的候选人（可多选）")
                    jd_btn = gr.Button("🚀 开始 JD 智能匹配", variant="primary")
                    jd_result = gr.Markdown("")

                with gr.Column(elem_classes=["kb-card"]):
                    gr.Markdown('<div class="kb-section">💬 人才细节深度追问</div>')
                    chatbot = gr.Chatbot(label="", height=380, elem_classes=["chatbot"])
                    with gr.Row(elem_classes=["input-area"]):
                        qa_input = gr.Textbox(placeholder="向系统提问，如：找出有 Vue 开发经验的人...",
                                              show_label=False, container=False, elem_classes=["input-box"],
                                              lines=1, scale=8)
                        qa_btn = gr.Button("发送", variant="primary", scale=1, elem_classes=["send-btn"])
                    with gr.Accordion("📊 查看检索评分详情", open=False):
                        debug_code = gr.Textbox(label="检索评分详情", lines=10, interactive=False)

        # ---- 主界面刷新组件（顺序须与 _view_tuple 一致）----
        VIEW_COMPONENTS = [candidate_df, parse_status_md, cand_select, src_group,
                           file_table, download_dd, jd_cands, account_md]

        # ================= 事件绑定 =================
        # 页面加载：SSO 检测 → 进入或显示登录；随后做一次索引自愈 + 视图刷新
        demo.load(_on_load, outputs=[login_panel, main_panel, user_state, sso_state, login_msg]) \
            .then(_after_login, inputs=[user_state, model_dd], outputs=VIEW_COMPONENTS + [chatbot, chat_hist_state])

        # 登录 / 演示 / 注册
        login_btn.click(do_login, inputs=[login_id, login_pwd],
                        outputs=[login_panel, main_panel, user_state, sso_state, login_msg]) \
            .then(_after_login, inputs=[user_state, model_dd], outputs=VIEW_COMPONENTS + [chatbot, chat_hist_state])
        demo_btn.click(do_demo_login,
                       outputs=[login_panel, main_panel, user_state, sso_state, login_msg]) \
            .then(_after_login, inputs=[user_state, model_dd], outputs=VIEW_COMPONENTS + [chatbot, chat_hist_state])
        send_code_btn.click(do_send_code, inputs=[reg_email, gr.State("register")], outputs=[reg_msg])
        register_btn.click(do_register, inputs=[reg_email, reg_pwd, reg_pwd2, reg_code],
                           outputs=[login_panel, main_panel, user_state, sso_state, login_msg, reg_msg]) \
            .then(_after_login, inputs=[user_state, model_dd], outputs=VIEW_COMPONENTS + [chatbot, chat_hist_state])
        rs_send_btn.click(do_send_code, inputs=[rs_email, gr.State("reset")], outputs=[rs_msg])
        reset_btn.click(do_reset, inputs=[rs_email, rs_code, rs_pwd, rs_pwd2], outputs=[rs_msg])

        # 退出
        logout_btn.click(do_logout, inputs=[sso_state],
                         outputs=[login_panel, main_panel, user_state, sso_state, logout_html])

        # 上传 / 源文件管理
        upload_btn.click(do_upload, inputs=[upload_files, user_state],
                         outputs=VIEW_COMPONENTS + [status_md, upload_files])
        clear_upload_btn.click(lambda: (None, "已清空上传列表"), outputs=[upload_files, status_md])
        del_files_btn.click(do_delete_files, inputs=[src_group, user_state, model_dd],
                            outputs=VIEW_COMPONENTS + [status_md])
        reparse_files_btn.click(do_reparse, inputs=[src_group, user_state],
                                outputs=VIEW_COMPONENTS + [status_md])
        download_dd.change(set_download, inputs=[download_dd, user_state], outputs=[download_btn])
        clear_all_btn.click(do_clear_all, inputs=[clear_all_confirm, user_state, model_dd],
                            outputs=VIEW_COMPONENTS + [chatbot, chat_hist_state, status_md, clear_all_confirm])

        # 看板批量操作
        mark_btn.click(do_mark, inputs=[cand_select, user_state], outputs=VIEW_COMPONENTS + [status_md])
        cand_reparse_btn.click(do_reparse, inputs=[cand_select, user_state], outputs=VIEW_COMPONENTS + [status_md])
        cand_del_btn.click(do_delete_files, inputs=[cand_select, user_state, model_dd],
                           outputs=VIEW_COMPONENTS + [status_md])

        # 标签检索 / JD 匹配 / RAG 问答
        tag_search_btn.click(do_tag_search, inputs=[tag_input, fuzzy_cb, user_state],
                             outputs=[tag_result_df, tag_status])
        jd_btn.click(do_jd, inputs=[jd_text, jd_cands, user_state, model_dd], outputs=[jd_result])
        qa_btn.click(ask, inputs=[qa_input, chatbot, chat_hist_state, user_state, model_dd],
                     outputs=[chatbot, chat_hist_state, qa_input, debug_code], concurrency_limit=1)
        qa_input.submit(ask, inputs=[qa_input, chatbot, chat_hist_state, user_state, model_dd],
                        outputs=[chatbot, chat_hist_state, qa_input, debug_code], concurrency_limit=1)

        # 存在 pending 时周期刷新看板（替代 Streamlit 每 3 秒 st.rerun）
        timer = gr.Timer(value=3)
        timer.tick(refresh_dashboard, inputs=[user_state], outputs=[candidate_df, parse_status_md])

        demo.queue(default_concurrency_limit=8)

    return demo


if __name__ == "__main__":
    print("✅ 启动智能 HR 知识库（单机模式，端口 8501）…")
    print("⏳ 首次问答/解析时会自动初始化模型，请确保本地 Ollama 已就绪或已配置云端 API。")
    build_kb().launch(server_name="127.0.0.1", server_port=8501, share=False, inbrowser=True,
                      theme=build_theme(), css=KB_CSS)
