"""用云端 embedding 重建 my-rag 的向量索引与 BM25（切换 embedding 模型后跑一次）。

只做文本抽取 + 切分 + 向量化，不调用大模型，所以很快。
旧索引只改名不删除：失败可从 _backup/ 或 faiss_index.stale 回滚。

用法（必须在 my-rag 的虚拟环境里跑）：
    cd my-rag && .venv\\Scripts\\python.exe ..\\tools\\rebuild_index.py
"""

import os
import shutil
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent / "my-rag"
sys.path.insert(0, str(BASE))
os.chdir(BASE)


def load_env():
    for line in (BASE / ".env").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


load_env()

from config import BM25_PKL, DATA_DIR, INDEX_DIR  # noqa: E402
from langchain_openai import OpenAIEmbeddings  # noqa: E402
from rag_engine import update_indexes_incremental  # noqa: E402

model_name = os.environ["EMBED_MODEL"]
print(f"embedding 模型: {model_name}")

embed = OpenAIEmbeddings(
    model=model_name,
    base_url=os.environ["API_BASE"],
    api_key=os.environ["API_KEY"],
    check_embedding_ctx_length=False,
)

# 1) 旧索引改名保留（与 app.py 的 _ensure_index_compatible 策略一致）
stale = INDEX_DIR + ".stale"
if os.path.exists(INDEX_DIR):
    if os.path.exists(stale):
        shutil.rmtree(stale, ignore_errors=True)
    os.rename(INDEX_DIR, stale)
    print(f"旧索引 -> {os.path.basename(stale)}")
if os.path.exists(BM25_PKL):
    stale_pkl = BM25_PKL + ".stale"
    if os.path.exists(stale_pkl):
        os.remove(stale_pkl)
    os.rename(BM25_PKL, stale_pkl)
    print(f"旧 BM25 -> {os.path.basename(stale_pkl)}")

# 2) 逐个账号目录入库（索引物理共享，chunk 的 metadata.owner 区分归属）
root = Path(DATA_DIR)
owners = sorted(p.name for p in root.iterdir() if p.is_dir()) if root.exists() else []
print("待处理账号目录:", owners)

for owner in owners:
    files = [f for f in (root / owner).iterdir() if f.is_file()]
    if not files:
        print(f"  {owner}: 无文件，跳过")
        continue
    vs, bm, docs = update_indexes_incremental(embed, owner)
    print(f"  {owner}: 文件 {len(files)} 个，累计 chunk {len(docs or [])} 条")

# 3) 写模型标记（与 app.py 的 _write_index_marker 格式一致：无 BOM 的 utf-8）
marker = Path(INDEX_DIR) / ".embed_model"
if Path(INDEX_DIR).exists():
    marker.write_text(model_name, encoding="utf-8")
    print(f"已写入标记 {marker} -> {marker.read_text(encoding='utf-8')}")

size = sum(f.stat().st_size for f in Path(INDEX_DIR).glob("*") if f.is_file()) if Path(INDEX_DIR).exists() else 0
print(f"\n完成：索引目录 {INDEX_DIR}，大小 {size / 1024:.0f} KB，"
      f"BM25 {'存在' if os.path.exists(BM25_PKL) else '缺失'}")
