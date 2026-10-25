"""
向量存储管理器 - 基于 ChromaDB

提供文档向量的存储、检索和管理能力。
"""

import os
from typing import List, Dict, Any, Optional


class VectorStoreManager:
    """向量存储管理器 - 基于 ChromaDB"""

    def __init__(self, persist_dir: str = "./memory/vector_store",
                 collection_name: str = "data_insight_knowledge"):
        """
        初始化向量存储管理器

        Args:
            persist_dir: 持久化目录
            collection_name: 集合名称
        """
        self.persist_dir = persist_dir
        self.collection_name = collection_name
        self._client = None
        self._collection = None

        os.makedirs(persist_dir, exist_ok=True)

    def _get_collection(self):
        """懒加载 ChromaDB 集合"""
        if self._collection is None:
            try:
                import chromadb
                from chromadb.config import Settings

                self._client = chromadb.PersistentClient(
                    path=self.persist_dir,
                    settings=Settings(anonymized_telemetry=False)
                )
                self._collection = self._client.get_or_create_collection(
                    name=self.collection_name,
                    metadata={"hnsw:space": "cosine"}
                )
            except ImportError:
                raise ImportError("请安装 chromadb: pip install chromadb")
        return self._collection

    def add_documents(self, documents: List[Dict[str, Any]], embeddings: List[List[float]]):
        """
        添加文档到向量存储

        Args:
            documents: 文档列表，每个文档包含 content 和 metadata
            embeddings: 对应的向量列表
        """
        if not documents or not embeddings:
            return

        collection = self._get_collection()

        ids = [f"doc_{i}" for i in range(len(documents))]
        texts = [doc["content"] for doc in documents]
        metadatas = [doc.get("metadata", {}) for doc in documents]

        # ChromaDB 的 metadata 必须是基本类型
        clean_metadatas = []
        for meta in metadatas:
            clean_meta = {}
            for k, v in meta.items():
                if isinstance(v, (str, int, float, bool)):
                    clean_meta[k] = v
                else:
                    clean_meta[k] = str(v)
            clean_metadatas.append(clean_meta)

        collection.add(
            ids=ids,
            embeddings=embeddings,
            documents=texts,
            metadatas=clean_metadatas
        )

    def search(self, query_embedding: List[float], top_k: int = 5) -> List[Dict[str, Any]]:
        """
        向量相似度检索

        Args:
            query_embedding: 查询向量
            top_k: 返回结果数量

        Returns:
            检索结果列表
        """
        collection = self._get_collection()

        results = collection.query(
            query_embeddings=[query_embedding],
            n_results=top_k,
            include=["documents", "metadatas", "distances"]
        )

        # 转换结果格式
        search_results = []
        if results and results["documents"]:
            for i, doc in enumerate(results["documents"][0]):
                distance = results["distances"][0][i] if results["distances"] else 0
                # ChromaDB 使用 cosine distance，转换为相似度
                similarity = 1 - distance
                metadata = results["metadatas"][0][i] if results["metadatas"] else {}

                search_results.append({
                    "content": doc,
                    "metadata": metadata,
                    "similarity": similarity
                })

        return search_results

    def get_count(self) -> int:
        """获取文档数量"""
        collection = self._get_collection()
        return collection.count()

    def delete_all(self):
        """删除所有文档"""
        collection = self._get_collection()
        # 删除集合并重新创建
        self._client.delete_collection(self.collection_name)
        self._collection = self._client.get_or_create_collection(
            name=self.collection_name,
            metadata={"hnsw:space": "cosine"}
        )
