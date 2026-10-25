"""
混合检索器 - 向量检索 + BM25 关键词检索

融合两种检索方式的结果，提升召回率。
"""

from typing import List, Dict, Any, Optional
from .document_loader import Document


class HybridRetriever:
    """混合检索器 - 向量 + BM25，支持查询扩展"""

    def __init__(self, vector_store, embedding_manager,
                 vector_weight: float = 0.6, bm25_weight: float = 0.4):
        """
        初始化混合检索器

        Args:
            vector_store: VectorStoreManager 实例
            embedding_manager: EmbeddingManager 实例
            vector_weight: 向量检索权重（默认0.6）
            bm25_weight: BM25 检索权重（默认0.4，关键词匹配更重要）
        """
        self.vector_store = vector_store
        self.embedding_manager = embedding_manager
        self.vector_weight = vector_weight
        self.bm25_weight = bm25_weight
        self._bm25 = None
        self._corpus = []
        self._corpus_metadata = []

        # 查询扩展映射（同义词/别名）
        self._query_expansion = {
            "gmv": ["成交总额", "销售额", "营收", "流水"],
            "销售额": ["gmv", "成交总额", "营收"],
            "复购率": ["回头率", "重复购买率", "复购"],
            "客单价": ["平均订单金额", "每单金额"],
            "转化率": ["成交率", "付费率"],
            "会员渗透率": ["会员消费占比", "会员占比"],
        }

    def index(self, documents: List[Document]):
        """
        索引文档（同时构建向量索引和 BM25 索引）

        Args:
            documents: Document 列表
        """
        if not documents:
            return

        # 1. 构建向量索引
        texts = [doc.content for doc in documents]
        embeddings = self.embedding_manager.embed(texts)

        doc_dicts = [doc.to_dict() for doc in documents]
        self.vector_store.add_documents(doc_dicts, embeddings)

        # 2. 构建 BM25 索引
        self._corpus = texts
        self._corpus_metadata = [doc.metadata for doc in documents]
        self._build_bm25_index(texts)

    def _build_bm25_index(self, texts: List[str]):
        """构建 BM25 索引"""
        try:
            import jieba
            from rank_bm25 import BM25Okapi

            # 中文分词
            tokenized_corpus = [list(jieba.cut(text)) for text in texts]
            self._bm25 = BM25Okapi(tokenized_corpus)
        except ImportError:
            # jieba 或 rank_bm25 未安装，仅使用向量检索
            self._bm25 = None

    def _expand_query(self, query: str) -> List[str]:
        """
        查询扩展 - 添加同义词和别名

        Args:
            query: 原始查询

        Returns:
            扩展后的查询列表
        """
        queries = [query]
        query_lower = query.lower()

        # 检查是否需要扩展
        for key, expansions in self._query_expansion.items():
            if key in query_lower:
                queries.extend(expansions)
                break

        return queries

    def retrieve(self, query: str, top_k: int = 5) -> List[Dict[str, Any]]:
        """
        混合检索（支持查询扩展）

        Args:
            query: 查询文本
            top_k: 返回结果数量

        Returns:
            检索结果列表（按相关度排序）
        """
        results = {}

        # 查询扩展
        expanded_queries = self._expand_query(query)

        # 1. 向量检索（对所有扩展查询）
        try:
            for q in expanded_queries:
                query_embedding = self.embedding_manager.embed_query(q)
                vector_results = self.vector_store.search(query_embedding, top_k=top_k)

                for i, result in enumerate(vector_results):
                    content = result["content"]
                    # 向量相似度作为分数，扩展查询的分数略低
                    similarity = result.get("similarity", 0)
                    score = similarity * self.vector_weight
                    if q != query:
                        score *= 0.8  # 扩展查询的权重降低

                    if content not in results:
                        results[content] = {
                            "content": content,
                            "metadata": result.get("metadata", {}),
                            "score": 0
                        }
                    results[content]["score"] = max(results[content]["score"], score)
        except Exception as e:
            print(f"[Warning] 向量检索失败: {e}")

        # 2. BM25 检索（对所有扩展查询）
        if self._bm25 is not None:
            try:
                import jieba

                for q in expanded_queries:
                    query_tokens = list(jieba.cut(q))
                    bm25_scores = self._bm25.get_scores(query_tokens)

                    # 归一化 BM25 分数
                    max_score = max(bm25_scores) if len(bm25_scores) > 0 else 1
                    if max_score > 0:
                        normalized_scores = [s / max_score for s in bm25_scores]
                    else:
                        normalized_scores = bm25_scores

                    # 取 Top-K
                    sorted_indices = sorted(
                        range(len(bm25_scores)),
                        key=lambda i: bm25_scores[i],
                        reverse=True
                    )[:top_k]

                    for idx in sorted_indices:
                        content = self._corpus[idx]
                        score = normalized_scores[idx] * self.bm25_weight
                        if q != query:
                            score *= 0.8  # 扩展查询的权重降低

                        if content not in results:
                            results[content] = {
                                "content": content,
                                "metadata": self._corpus_metadata[idx] if idx < len(self._corpus_metadata) else {},
                                "score": 0
                            }
                        results[content]["score"] = max(results[content]["score"], score)
            except Exception as e:
                print(f"[Warning] BM25 检索失败: {e}")

        # 3. 按综合分数排序
        sorted_results = sorted(results.values(), key=lambda x: x["score"], reverse=True)
        return sorted_results[:top_k]
