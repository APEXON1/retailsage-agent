# ==================== 🔎 RAG 核心引擎（从 main.py 解耦拆分） ====================
"""负责：文档切分与元数据注入、向量库(FAISS)/BM25 索引的加载与增量更新、
稠密+稀疏混合检索(RRF 融合)、以及带对话记忆的问答生成。"""

import os
import pickle
import re
import logging
from typing import List
from datetime import datetime

from langchain_core.messages import BaseMessage, HumanMessage, AIMessage, SystemMessage
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_core.documents import Document as LCDocument
from langchain_community.vectorstores import FAISS
from langchain_community.document_loaders import Docx2txtLoader, TextLoader, BSHTMLLoader
from rank_bm25 import BM25Okapi
import jieba

from config import (DATA_DIR, INDEX_DIR, BM25_PKL, RETRIEVE_K, FUISON_K, RRF_K, DEFAULT_OWNER)
from database import safe_remove
from extractor import ocr_pdf_to_text, _extract_pdf_text_sorted, _invoke_with_timeout


def _load_single_file_documents(file_path: str, file_name: str, owner: str) -> List[LCDocument]:
    """加载单个文件并切分为 Chunk（注入元数据、OCR 兜底），返回已切分的 Document 列表。
    owner 为归属账号邮箱，将写入每个 chunk 的 metadata，用于多账号检索隔离。"""
    try:
        upload_time = datetime.now().isoformat()
        if file_name.endswith('.txt'):
            docs = []
            for enc in ("utf-8", "gbk", "gb18030"):
                try:
                    docs = TextLoader(file_path, encoding=enc).load()
                    break
                except (UnicodeDecodeError, LookupError):
                    continue
            _inject_doc_metadata(docs, file_name, upload_time, owner)
        elif file_name.endswith('.pdf'):
            pdf_text = _extract_pdf_text_sorted(file_path)
            docs = [LCDocument(page_content=pdf_text, metadata={"source": file_name})] if pdf_text else []
            # 坐标排序提取为空 / 几乎无中文（字体混淆、扫描件）→ 尝试 OCR 兜底
            if not docs or len(re.findall(r'[一-鿿]', pdf_text)) < 3:
                ocr_text = ocr_pdf_to_text(file_path)
                if ocr_text and len(re.findall(r'[一-鿿]', ocr_text)) >= 3:
                    docs = [LCDocument(page_content=ocr_text, metadata={"source": file_name})]
            _inject_doc_metadata(docs, file_name, upload_time, owner)
        elif file_name.endswith('.docx') or file_name.endswith('.doc'):
            docs = Docx2txtLoader(file_path).load()
            _inject_doc_metadata(docs, file_name, upload_time, owner)
        elif file_name.endswith('.html') or file_name.endswith('.htm'):
            docs = BSHTMLLoader(file_path, open_encoding='utf-8').load()
            _inject_doc_metadata(docs, file_name, upload_time, owner)
        else:
            return []
    except Exception as e:
        print(f"⚠️ 警告: 加载文件 {file_name} 失败，原因为: {e}")
        return []

    if not docs:
        return []
    text_splitter = RecursiveCharacterTextSplitter(
        separators=["\n\n", "\n", " ", ""],
        chunk_size=500,
        chunk_overlap=50,
        length_function=len,
    )
    return text_splitter.split_documents(docs)


# ==================== 原有的 RAG 核心代码 ====================
def _inject_doc_metadata(docs: List[LCDocument], file_name: str, upload_time: str, owner: str) -> None:
    """向每个 Document 强制注入统一元数据，确保分块后随 chunk 一起持久化到向量库。
    owner 为归属账号邮箱，是检索时数据隔离的依据。"""
    for d in docs:
        d.metadata["filename"] = file_name
        d.metadata["owner"] = owner
        d.metadata["upload_time"] = upload_time
        # 若解析器提供了页码（如 PyPDFLoader 的 metadata["page"]），则保留原值；
        # 不支持分页的文档（txt/docx/html）标记为 0。
        if d.metadata.get("page") is None:
            d.metadata["page"] = 0


def load_vectorstore(embed_model):
    """加载已有 FAISS 索引；本地不存在则返回 None。"""
    if os.path.exists(INDEX_DIR):
        return FAISS.load_local(INDEX_DIR, embed_model, allow_dangerous_deserialization=True)
    return None


def load_bm25():
    """加载已有 BM25 索引（含对齐的 Document 列表）；不存在则返回 None。"""
    if os.path.exists(BM25_PKL):
        with open(BM25_PKL, "rb") as f:
            return pickle.load(f)
    return None


def update_indexes_incremental(embed_model, owner: str):
    """增量更新向量库与 BM25（真正的「按需读取」）：
    1) 加载已有索引（无则视为首次初始化）；
    2) 提前确定已索引文件集合 indexed_files（以 BM25 的 documents 为准，保证与稀疏检索对齐），
       元素为 (owner, filename) 二元组，跨账号互不干扰；
    3) 仅对当前用户目录 DATA_DIR/owner 中「尚未索引」的文件调用解析，跳过已索引文件；
    4) 处理完成后统一持久化。
    返回 (vectorstore, bm25_data, documents)，documents 与 BM25 语料严格对齐。"""
    vectorstore = load_vectorstore(embed_model)
    bm25_data = load_bm25()
    # 兼容旧格式（无 documents）：重置为 None，下面统一按「首次初始化」重建 BM25
    if bm25_data is not None and "documents" not in bm25_data:
        bm25_data = None

    # 提前获取已索引文件集合：以 BM25 的 documents 为准，保证与稀疏检索对齐
    if bm25_data is not None:
        indexed_files = {
            (d.metadata.get("owner") or DEFAULT_OWNER, d.metadata.get("filename"))
            for d in bm25_data["documents"]
        }
    elif vectorstore is not None:
        indexed_files = {
            (d.metadata.get("owner") or DEFAULT_OWNER, d.metadata.get("filename"))
            for d in vectorstore.docstore._dict.values()
        }
    else:
        indexed_files = set()

    # 按需读取：只解析当前用户目录中尚未索引的文件（跳过已索引，避免重复全量解析）
    new_docs = []
    user_dir = os.path.join(DATA_DIR, owner)
    if os.path.exists(user_dir):
        for file_name in os.listdir(user_dir):
            file_path = os.path.join(user_dir, file_name)
            if not os.path.isfile(file_path):
                continue
            if (owner, file_name) in indexed_files:
                continue  # 已索引，跳过
            new_docs.extend(_load_single_file_documents(file_path, file_name, owner))

    # 既无旧索引、也无新文件：直接返回空，避免无谓建库
    if not indexed_files and not new_docs:
        return None, None, []

    if new_docs:
        # FAISS 增量追加
        if vectorstore is None:
            batch_size = 30
            vectorstore = FAISS.from_documents(documents=new_docs[:batch_size], embedding=embed_model)
            for i in range(batch_size, len(new_docs), batch_size):
                vectorstore.add_documents(new_docs[i:i + batch_size])
        else:
            vectorstore.add_documents(new_docs)
        # BM25 增量：复用旧语料，仅对新增文档分词并追加
        if bm25_data is not None:
            documents = bm25_data["documents"] + new_docs
            tokenized = bm25_data.get("tokenized",
                                     [tokenize_mixed(d.page_content) for d in bm25_data["documents"]]) \
                        + [tokenize_mixed(d.page_content) for d in new_docs]
        else:
            # 首次初始化（bm25_data 为空）：直接使用刚刚按需解析的 new_docs，不再全量读取
            documents = new_docs
            tokenized = [tokenize_mixed(d.page_content) for d in documents]
        bm25 = BM25Okapi(tokenized)
        bm25_data = {"bm25": bm25, "documents": documents, "tokenized": tokenized}
    else:
        # 无新增：沿用既有 BM25（首次初始化且 indexed_files 非空却无 new_docs，属边界情况）
        if bm25_data is not None:
            documents = bm25_data["documents"]
        else:
            documents = new_docs  # 兜底：理论上不会到达（前已拦截 both-empty）

    # 统一持久化保存一次
    if vectorstore is not None:
        vectorstore.save_local(INDEX_DIR)
    if bm25_data is not None:
        with open(BM25_PKL, "wb") as f:
            pickle.dump(bm25_data, f)

    return vectorstore, bm25_data, documents


def remove_file_from_indexes(filename: str, owner: str, embed_model):
    """增量移除：从 FAISS 与 BM25 中删除指定用户某文件的全部 chunk，并持久化（不重建整个索引）。"""
    # 1) FAISS：按 (metadata.owner, metadata.filename) 定位并删除对应 id（始终执行）
    if os.path.exists(INDEX_DIR):
        vs = FAISS.load_local(INDEX_DIR, embed_model, allow_dangerous_deserialization=True)
        ids = [doc_id for doc_id, doc in vs.docstore._dict.items()
               if (doc.metadata.get("owner") or DEFAULT_OWNER) == owner
               and doc.metadata.get("filename") == filename]
        if ids:
            vs.delete(ids)
        vs.save_local(INDEX_DIR)
    # 2) BM25：新格式按 (owner, filename) 过滤；旧格式从剩余文件重建
    if os.path.exists(BM25_PKL):
        with open(BM25_PKL, "rb") as f:
            bm25_data = pickle.load(f)
        if "documents" in bm25_data:
            docs = bm25_data["documents"]
            tok = bm25_data.get("tokenized", [])
            keep = [i for i, d in enumerate(docs)
                    if not ((d.metadata.get("owner") or DEFAULT_OWNER) == owner
                            and d.metadata.get("filename") == filename)]
            new_docs = [docs[i] for i in keep]
            new_tok = [tok[i] for i in keep] if tok else [tokenize_mixed(d.page_content) for d in new_docs]
            if new_docs:
                bm25 = BM25Okapi(new_tok)
                with open(BM25_PKL, "wb") as f:
                    pickle.dump({"bm25": bm25, "documents": new_docs, "tokenized": new_tok}, f)
            else:
                safe_remove(BM25_PKL)
        else:
            # 旧格式：从剩余文件全量重建（一次性迁移）
            update_indexes_incremental(embed_model, owner)


def remove_owner_from_indexes(owner: str, embed_model):
    """从 FAISS 与 BM25 中移除某账号的全部 chunk（用于「清空我的数据」），不影响其他账号。"""
    # 1) FAISS：删除该 owner 的全部 id
    if os.path.exists(INDEX_DIR):
        vs = FAISS.load_local(INDEX_DIR, embed_model, allow_dangerous_deserialization=True)
        ids = [doc_id for doc_id, doc in vs.docstore._dict.items()
               if (doc.metadata.get("owner") or DEFAULT_OWNER) == owner]
        if ids:
            vs.delete(ids)
        vs.save_local(INDEX_DIR)
    # 2) BM25：过滤掉该 owner 的文档；若索引已空则删除 pkl
    if os.path.exists(BM25_PKL):
        with open(BM25_PKL, "rb") as f:
            bm25_data = pickle.load(f)
        if "documents" in bm25_data:
            docs = bm25_data["documents"]
            tok = bm25_data.get("tokenized", [])
            keep = [i for i, d in enumerate(docs)
                    if (d.metadata.get("owner") or DEFAULT_OWNER) != owner]
            new_docs = [docs[i] for i in keep]
            if new_docs:
                new_tok = [tok[i] for i in keep] if tok else [tokenize_mixed(d.page_content) for d in new_docs]
                bm25 = BM25Okapi(new_tok)
                with open(BM25_PKL, "wb") as f:
                    pickle.dump({"bm25": bm25, "documents": new_docs, "tokenized": new_tok}, f)
            else:
                safe_remove(BM25_PKL)

def tokenize_mixed(text: str) -> List[str]:
    """中英文混合分词：正则提取英文/数字单词，剩余中文部分用 jieba 切分，合并为 token 列表。"""
    if not text:
        return []
    text = text.lower()
    # 步骤1：正则提取连续的英文/数字单词（如 "Python", "c++", "2020"）
    english_tokens = re.findall(r"[a-zA-Z0-9]+", text)
    # 步骤2：将英文/数字部分替换为空格，避免 jieba 重复切分
    remaining = re.sub(r"[a-zA-Z0-9]+", " ", text)
    # 步骤3：剩余（中文为主）部分交给 jieba 分词，并过滤纯标点/空白 token
    chinese_tokens = [
        t for t in jieba.lcut(remaining)
        if t and t.strip() and not re.fullmatch(r"\W+", t.strip())
    ]
    # 步骤4：合并两部分结果为统一 token 列表
    return english_tokens + chinese_tokens


def reciprocal_rank_fusion(results: List[List[dict]], k: int = 60) -> List[dict]:
    doc_scores = {}
    for retriever_results in results:
        for rank, item in enumerate(retriever_results, start=1):
            doc_key = item["doc"].page_content
            if doc_key not in doc_scores:
                doc_scores[doc_key] = {"doc": item["doc"], "rrf_score": 0.0}
            doc_scores[doc_key]["rrf_score"] += 1.0 / (k + rank)
    return sorted(doc_scores.values(), key=lambda x: x["rrf_score"], reverse=True)

def dense_search(vectorstore, query: str, k: int, owner: str) -> List[dict]:
    if vectorstore is None:
        return []
    # 多账号隔离：过量召回后按 owner 过滤（索引为全账号共享，检索结果只保留本人文档）
    fetch_k = max(k * 4, 20)
    docs = vectorstore.similarity_search(query, k=fetch_k)
    owned = [d for d in docs if (d.metadata.get("owner") or DEFAULT_OWNER) == owner][:k]
    return [{"doc": doc, "score": 1.0} for doc in owned]


def sparse_search(bm25, documents, query: str, k: int, owner: str):
    if bm25 is None or not documents:
        return []
    # 多账号隔离：先按 owner 过滤候选文档，再仅对本人文档做 BM25 打分
    owned_docs = [d for d in documents if (d.metadata.get("owner") or DEFAULT_OWNER) == owner]
    if not owned_docs:
        return []
    doc_texts = [d.page_content for d in owned_docs]
    tokenized_query = tokenize_mixed(query)
    scores = bm25.get_scores(tokenized_query)
    top_indices = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:k]
    return [{"doc": owned_docs[idx], "score": float(scores[idx])} for idx in top_indices if scores[idx] > 0]


def hybrid_search(vectorstore, bm25_data, documents, query: str, owner: str) -> List[LCDocument]:
    if vectorstore is None or bm25_data is None:
        return []
    dense_results = dense_search(vectorstore, query, k=RETRIEVE_K, owner=owner)
    sparse_results = sparse_search(bm25_data["bm25"], documents, query, k=RETRIEVE_K, owner=owner)
    fused = reciprocal_rank_fusion([dense_results, sparse_results], k=RRF_K)
    return [item["doc"] for item in fused[:FUISON_K]]

def _append_citation_sources(answer: str, docs: List[LCDocument]) -> str:
    filenames: List[str] = []
    seen = set()
    for doc in docs:
        fname = doc.metadata.get("filename", "")
        # 从文件名中提取候选人姓名（假设文件名包含姓名，去掉后缀和多余符号）
        # 这里使用一种启发式：如果回答文本中真的提到了这个文件名里的中文字符，才将其列为引用
        name_match = re.search(r'[\u4e00-\u9fa5]{2,4}', fname)
        if fname and fname not in seen:
            if name_match and name_match.group() in answer:
                seen.add(fname)
                filenames.append(fname)
            elif not name_match: # 兜底逻辑
                seen.add(fname)
                filenames.append(fname)

    if not filenames:
        # 如果启发式过滤后没有来源，退回到原逻辑以防万一
        for doc in docs:
            fname = doc.metadata.get("filename")
            if fname and fname not in seen:
                seen.add(fname)
                filenames.append(fname)

    if not filenames:
        return answer
    citation = "引用来源：" + ", ".join(filenames)
    return f"{answer.rstrip()}\n\n{citation}"


# 硬性防幻觉 System Prompt
_QA_SYSTEM_PROMPT = """你是一个基于本地私有知识库的智能人事助手。
请严格依据下面提供的【候选人信息上下文】和【历史对话记忆】进行回答。
【硬性防幻觉指令】：如果你无法在提供的上下文片段中找到答案，请明确回答"知识库没有相关信息，无法提供准确答案。"，绝对禁止编造信息。
【目标锁定指令】：如果检索到的上下文包含多位候选人的信息，请严格只提取和分析用户提问中指定的那位候选人，直接忽略其他无关人员的数据，不要在回答中提及他们。"""


def answer_question_with_memory(question: str, vectorstore, bm25_data, documents, llm, chat_history: List[BaseMessage], owner: str):
    docs = hybrid_search(vectorstore, bm25_data, documents, question, owner)
    context = "\n\n".join([doc.page_content for doc in docs])
    
    history_text = ""
    for msg in chat_history[-6:]:
        role = "用户" if isinstance(msg, HumanMessage) else "AI"
        history_text += f"{role}: {msg.content}\n"
        
    prompt = f"""【候选人信息上下文】:
{context}

【历史对话记忆】:
{history_text}

【当前用户问题】: {question}

请给出专业、清晰的回答："""

    try:
        response = _invoke_with_timeout(llm, [SystemMessage(content=_QA_SYSTEM_PROMPT), HumanMessage(content=prompt)], timeout=300)
    except Exception as e:
        logging.error(f"问答调用大模型失败: {e}")
        return f"⚠️ 模型响应超时或失败，请稍后重试（{e}）"
    ans_content = response.content if hasattr(response, 'content') else str(response)
    # 后处理：从 Top-K 文档 metadata 提取 filename 并去重追加引用来源
    final_answer = _append_citation_sources(ans_content, docs)
    chat_history.append(HumanMessage(content=question))
    chat_history.append(AIMessage(content=final_answer))
    return final_answer
