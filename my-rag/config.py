# 常量配置（集中管理，供 main / database / app 等模块复用）

import os
from pathlib import Path

# 所有路径锚定到本文件所在目录，与启动时的 CWD 无关：
# 既支持「在 my-rag 目录内独立启动」，也支持「从项目根目录由 launcher 拉起」。
BASE_DIR = Path(__file__).resolve().parent

DATA_DIR = str(BASE_DIR / "data")
INDEX_DIR = str(BASE_DIR / "faiss_index")
BM25_PKL = str(BASE_DIR / "bm25_index.pkl")
# 账号库已升级为项目级共享库（门户 + 两个应用共用同一套账号），位于项目根目录 accounts/ 下
DB_PATH = str(BASE_DIR.parent / "accounts" / "accounts.db")
RETRIEVE_K = 5
FUISON_K = 5
RRF_K = 60

# ==================== 👥 多账号体系配置 ====================
# 存量数据与旧账号（hr01/hr02/hr03）归属的默认账号邮箱
DEFAULT_OWNER = "hr03@demo.local"
# 旧账号登录时自动补全的域名（输入 hr03 → hr03@demo.local）
LEGACY_LOGIN_DOMAIN = "demo.local"
# 注册/重置密码验证码有效期（秒）
VERIFICATION_CODE_TTL_SECONDS = 300
# 同一邮箱两次发送验证码的最小间隔（秒），防验证码轰炸
VERIFICATION_CODE_COOLDOWN_SECONDS = 60
# 密码最小长度
PASSWORD_MIN_LENGTH = 6

# ==================== 🎯 演示账号（一键体验用） ====================
# 预置演示账号：登录页「一键体验」按钮直接进入，数据为合成简历，与真实数据隔离
DEMO_EMAIL = "demo@demo.local"
# 演示账号密码（同时含字母和数字，满足强度校验；仅用于演示账号，可公开）
DEMO_PASSWORD = "demo123456"
# 合成简历种子目录：仓库内随代码分发，首次启动自动复制到演示账号目录
DEMO_SEED_DIR = str(BASE_DIR / "data_demo")

# ==================== 🤖 LLM 后端切换（本地 Ollama / 云端 OpenAI 兼容 API） ====================
# Streamlit Cloud 等无 Ollama 的环境，用 OpenAI 兼容接口（硅基流动 / DeepSeek / Moonshot 均可）：
#   LLM_BACKEND=api
#   API_BASE=https://api.siliconflow.cn/v1
#   API_KEY=sk-xxx
#   LLM_MODEL=Qwen/Qwen2.5-7B-Instruct        # 解析 + 问答共用一个对话模型
#   EMBED_MODEL=BAAI/bge-m3                   # 向量模型（与本地 nomic-embed-text 维度不同，切换后自动重建索引）
# 不配置时默认 ollama（本地开发机，零成本）


def backend_settings() -> dict:
    """实时读取后端配置（函数而非常量：Streamlit Cloud 的 Secrets 晚于 import 注入，
    必须在调用时求值才能拿到）。"""
    return {
        "backend": os.environ.get("LLM_BACKEND", "ollama").strip().lower(),
        "api_base": os.environ.get("API_BASE", "https://api.siliconflow.cn/v1").strip(),
        "api_key": os.environ.get("API_KEY", "").strip(),
        "llm_model": os.environ.get("LLM_MODEL", "Qwen/Qwen2.5-7B-Instruct").strip(),
        "embed_model": os.environ.get("EMBED_MODEL", "BAAI/bge-m3").strip(),
    }
