"""探针：确认 .env 里配置的云端 API 是否真的能跑 embedding 与对话。

为什么要先探：my-rag 一旦判定 embedding 模型变更，就会触发向量索引重建；
如果 API 不通，重建必然失败。先探后改，避免在坏状态下切换。

用法：
    cd my-rag && .venv\\Scripts\\python.exe ..\\tools\\probe_api.py
"""

import os
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent / "my-rag"


def load_env():
    path = BASE / ".env"
    if not path.exists():
        sys.exit(f"找不到 {path}")
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


load_env()
api_base = os.environ.get("API_BASE", "")
api_key = os.environ.get("API_KEY", "")
embed_model = os.environ.get("EMBED_MODEL", "")
llm_model = os.environ.get("LLM_MODEL", "")
print(f"API_BASE    = {api_base}")
print(f"EMBED_MODEL = {embed_model}")
print(f"LLM_MODEL   = {llm_model}")
print(f"API_KEY     = {api_key[:12]}…（长度 {len(api_key)}）")
print()

ok_all = True

print("— 1) Embedding —")
try:
    from langchain_openai import OpenAIEmbeddings

    emb = OpenAIEmbeddings(
        model=embed_model,
        base_url=api_base,
        api_key=api_key,
        check_embedding_ctx_length=False,
    )
    vec = emb.embed_query("门店销售额同比增长")
    print(f"   ✅ 可用，维度 = {len(vec)}")
except Exception as e:
    ok_all = False
    print(f"   ❌ 失败：{type(e).__name__}: {str(e)[:400]}")

print()
print("— 2) 对话模型 —")
try:
    from langchain_openai import ChatOpenAI

    llm = ChatOpenAI(model=llm_model, base_url=api_base, api_key=api_key, timeout=30)
    resp = llm.invoke("只回复两个字：可用")
    print(f"   ✅ 可用，回复 = {str(getattr(resp, 'content', resp))[:80]}")
except Exception as e:
    ok_all = False
    print(f"   ❌ 失败：{type(e).__name__}: {str(e)[:400]}")

print()
print("结论：", "两个都能用，可以切 API 后端" if ok_all else "存在不可用项，先不要切 API 后端")
sys.exit(0 if ok_all else 1)
