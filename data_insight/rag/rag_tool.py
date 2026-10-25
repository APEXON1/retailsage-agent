"""
RAG 检索工具 - 封装 RAG 全链路为 ToolResponse

将 RAG 检索能力封装为 HelloAgents 工具，供 Agent 调用。
支持检索结果缓存，提升性能。
"""

import os
from typing import List, Dict, Any
from hello_agents.tools.base import Tool, ToolParameter
from hello_agents.tools.response import ToolResponse
from hello_agents.tools.errors import ToolErrorCode

from .document_loader import DocumentLoader
from .text_splitter import TextSplitter
from .embeddings import EmbeddingManager
from .vector_store import VectorStoreManager
from .retriever import HybridRetriever
from .reranker import Reranker


class RAGTool(Tool):
    """RAG 检索工具 - 知识增强检索"""

    def __init__(self, knowledge_dir: str, vector_store_dir: str,
                 embedding_model: str = "shibing624/text2vec-base-chinese",
                 collection_name: str = "data_insight_knowledge",
                 llm=None, enable_cache: bool = True):
        """
        初始化 RAG 工具

        Args:
            knowledge_dir: 知识文档目录
            vector_store_dir: 向量存储目录
            embedding_model: Embedding 模型名称
            collection_name: ChromaDB 集合名称
            llm: HelloAgentsLLM 实例（用于重排序）
            enable_cache: 是否启用缓存
        """
        super().__init__(
            name="rag_search",
            description="知识库检索工具。检索零售/电商行业知识，包括指标定义、业务规则、分析模板等。"
                        "当需要理解业务术语或查找分析方法时使用。"
        )
        self.knowledge_dir = knowledge_dir
        self.vector_store_dir = vector_store_dir
        self.enable_cache = enable_cache

        # 初始化缓存
        self._cache = None
        if enable_cache:
            try:
                from ..tools.cache_tool import get_cache
                self._cache = get_cache(max_age_seconds=1800)  # 30分钟缓存
            except ImportError:
                pass

        # 初始化组件（优化分块参数）
        self.document_loader = DocumentLoader()
        self.text_splitter = TextSplitter(chunk_size=300, chunk_overlap=100)
        self.embedding_manager = EmbeddingManager(model_name=embedding_model)
        self.vector_store = VectorStoreManager(
            persist_dir=vector_store_dir,
            collection_name=collection_name
        )
        self.retriever = HybridRetriever(
            vector_store=self.vector_store,
            embedding_manager=self.embedding_manager
        )
        self.reranker = Reranker(llm=llm, use_llm=False)
        self._indexed = False

    def get_parameters(self) -> List[ToolParameter]:
        return [
            ToolParameter(
                name="query",
                type="string",
                description="检索查询（如 'GMV的定义'、'复购率怎么计算'）",
                required=True
            ),
            ToolParameter(
                name="top_k",
                type="integer",
                description="返回结果数量，默认3",
                required=False
            )
        ]

    def run(self, parameters: dict) -> ToolResponse:
        """
        执行 RAG 检索

        Args:
            parameters: {"query": "...", "top_k": 3}

        Returns:
            ToolResponse 包含检索结果
        """
        query = parameters.get("query", "").strip()
        top_k = parameters.get("top_k", 3)

        if not query:
            return ToolResponse.error(
                code=ToolErrorCode.INVALID_PARAM,
                message="检索查询不能为空"
            )

        try:
            # 检查缓存
            cache_key = {"query": query, "top_k": top_k}
            if self._cache:
                cached_result = self._cache.get("rag", cache_key)
                if cached_result is not None:
                    # 从缓存恢复 ToolResponse
                    return ToolResponse.success(
                        text=cached_result["text"],
                        data=cached_result["data"]
                    )

            # 确保已索引
            if not self._indexed:
                self._build_index()

            # 检索
            results = self.retriever.retrieve(query, top_k=top_k * 2)

            # 重排序
            results = self.reranker.rerank(query, results, top_k=top_k)

            if not results:
                return ToolResponse.success(
                    text=f"未找到与 '{query}' 相关的知识。",
                    data={"query": query, "results": []}
                )

            # 构建返回文本
            text_parts = [f"🔍 知识库检索结果（查询: {query}）:\n"]
            for i, result in enumerate(results):
                content = result.get("content", "")
                metadata = result.get("metadata", {})
                score = result.get("rerank_score", result.get("score", 0))
                source = metadata.get("source", "未知来源")

                text_parts.append(f"--- 结果 {i+1} (相关度: {score:.2f}, 来源: {source}) ---")
                text_parts.append(content[:300])  # 截断过长内容
                text_parts.append("")

            result_text = "\n".join(text_parts)
            result_data = {
                "query": query,
                "results": results,
                "count": len(results)
            }

            # 保存到缓存
            if self._cache:
                self._cache.set("rag", cache_key, {
                    "text": result_text,
                    "data": result_data
                })

            return ToolResponse.success(
                text=result_text,
                data=result_data
            )

        except Exception as e:
            # 提供友好的错误提示
            error_msg = str(e)
            if "model" in error_msg.lower() or "load" in error_msg.lower():
                friendly_msg = f"模型加载失败: {error_msg}. 请检查网络连接或模型路径"
            elif "index" in error_msg.lower():
                friendly_msg = f"索引构建失败: {error_msg}. 请检查知识库文件"
            elif "connection" in error_msg.lower() or "timeout" in error_msg.lower():
                friendly_msg = f"网络连接失败: {error_msg}. 请检查网络设置"
            else:
                friendly_msg = f"RAG检索失败: {error_msg}"

            return ToolResponse.error(
                code=ToolErrorCode.EXECUTION_ERROR,
                message=friendly_msg
            )

    def _build_index(self):
        """构建知识索引"""
        if not os.path.exists(self.knowledge_dir):
            print(f"⚠️ 知识目录不存在: {self.knowledge_dir}")
            self._indexed = True
            return

        print(f"📚 正在构建知识索引: {self.knowledge_dir}")

        # 加载文档
        documents = self.document_loader.load(self.knowledge_dir)
        print(f"  加载了 {len(documents)} 个文档")

        # 分块
        all_chunks = []
        for doc in documents:
            chunks = self.text_splitter.split(doc.content, doc.metadata)
            all_chunks.extend(chunks)
        print(f"  切分为 {len(all_chunks)} 个文本块")

        # 索引
        if all_chunks:
            self.retriever.index(all_chunks)
            print(f"  索引完成，向量库中共 {self.vector_store.get_count()} 条记录")

        self._indexed = True

    def rebuild_index(self):
        """重建索引"""
        self.vector_store.delete_all()
        self._indexed = False
        self._build_index()
