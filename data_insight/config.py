"""
RetailSage Agent 项目配置

集中管理所有配置项，支持环境变量和默认值。
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# 加载环境变量（固定从项目根目录加载，避免不同入口读取到不同 .env）
load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env")

# === 项目路径 ===
PROJECT_ROOT = Path(__file__).parent.parent
DATA_INSIGHT_DIR = Path(__file__).parent
KNOWLEDGE_DIR = DATA_INSIGHT_DIR / "knowledge"
MEMORY_DIR = DATA_INSIGHT_DIR / "memory"
OUTPUT_DIR = DATA_INSIGHT_DIR / "output"
SKILLS_DIR = DATA_INSIGHT_DIR / "skills"
CHARTS_DIR = OUTPUT_DIR / "charts"
REPORTS_DIR = OUTPUT_DIR / "reports"
USER_PROFILES_DIR = MEMORY_DIR / "user_profiles"
CONVERSATION_HISTORY_DIR = MEMORY_DIR / "conversation_history"
VECTOR_STORE_PATH = os.getenv("VECTOR_STORE_PATH", str(MEMORY_DIR / "vector_store"))

# === 日志配置 ===
LOG_DIR = Path(os.getenv("LOG_DIR", str(DATA_INSIGHT_DIR / "logs")))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# === LLM 配置 ===
LLM_MODEL_ID = os.getenv("LLM_MODEL_ID", "deepseek-chat")
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.deepseek.com")
LLM_TIMEOUT = int(os.getenv("LLM_TIMEOUT", "60"))

# === 数据库配置 ===
DB_CONFIG = {
    "sqlite": {
        "database": os.getenv("SQLITE_DATABASE", str(DATA_INSIGHT_DIR / "test_retail.db")),
    },
    "mysql": {
        "host": os.getenv("MYSQL_HOST", "localhost"),
        "port": int(os.getenv("MYSQL_PORT", "3306")),
        "user": os.getenv("MYSQL_USER", "root"),
        "password": os.getenv("MYSQL_PASSWORD", ""),
        "database": os.getenv("MYSQL_DATABASE", "retail_db"),
    },
    "postgresql": {
        "host": os.getenv("PG_HOST", "localhost"),
        "port": int(os.getenv("PG_PORT", "5432")),
        "user": os.getenv("PG_USER", "postgres"),
        "password": os.getenv("PG_PASSWORD", ""),
        "database": os.getenv("PG_DATABASE", "ecommerce_db"),
    },
    "clickhouse": {
        "host": os.getenv("CLICKHOUSE_HOST", "localhost"),
        "port": int(os.getenv("CLICKHOUSE_PORT", "9000")),
        "user": os.getenv("CLICKHOUSE_USER", "default"),
        "password": os.getenv("CLICKHOUSE_PASSWORD", ""),
        "database": os.getenv("CLICKHOUSE_DATABASE", "analytics"),
    },
}

# 默认数据库类型（使用 SQLite 进行本地测试）
DEFAULT_DB_TYPE = os.getenv("DEFAULT_DB_TYPE", "sqlite")

# === RAG 配置 ===
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "shibing624/text2vec-base-chinese")
COLLECTION_NAME = os.getenv("COLLECTION_NAME", "data_insight_knowledge")
RAG_TOP_K = int(os.getenv("RAG_TOP_K", "5"))
RAG_RERANK_TOP_K = int(os.getenv("RAG_RERANK_TOP_K", "3"))

# HuggingFace 镜像配置（解决 huggingface.co 连接超时）
HF_ENDPOINT = os.getenv("HF_ENDPOINT", "https://hf-mirror.com")

# === Agent 配置 ===
MAX_STEPS = int(os.getenv("MAX_STEPS", "10000"))
MAX_TOOL_ITERATIONS = int(os.getenv("MAX_TOOL_ITERATIONS", "15"))

# === 缓存配置 ===
CACHE_ENABLED = os.getenv("CACHE_ENABLED", "true").lower() == "true"
CACHE_DIR = os.getenv("CACHE_DIR", str(DATA_INSIGHT_DIR / "memory" / "cache"))
SQL_CACHE_TTL = int(os.getenv("SQL_CACHE_TTL", "600"))  # SQL缓存10分钟
RAG_CACHE_TTL = int(os.getenv("RAG_CACHE_TTL", "1800"))  # RAG缓存30分钟

# === 确保输出目录存在 ===
for d in [CHARTS_DIR, REPORTS_DIR, USER_PROFILES_DIR, CONVERSATION_HISTORY_DIR, LOG_DIR]:
    d.mkdir(parents=True, exist_ok=True)
