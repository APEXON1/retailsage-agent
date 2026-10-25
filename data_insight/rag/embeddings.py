"""
向量化模块 - 文本转向量

支持本地模型（sentence-transformers）和 API 两种方式。
支持 HF_ENDPOINT 环境变量设置镜像源，解决 huggingface.co 连接超时问题。
"""

import os
from typing import List, Optional


def _setup_hf_mirror():
    """
    设置 HuggingFace 镜像源

    优先级：
    1. 环境变量 HF_ENDPOINT（已设置则不覆盖）
    2. 默认使用 hf-mirror.com 镜像
    """
    # 如果用户已设置 HF_ENDPOINT，尊重用户配置
    if os.getenv("HF_ENDPOINT"):
        print(f"[HF Mirror] 使用自定义镜像: {os.getenv('HF_ENDPOINT')}")
        return

    # 默认使用国内镜像源
    default_mirror = "https://hf-mirror.com"
    os.environ["HF_ENDPOINT"] = default_mirror
    print(f"[HF Mirror] 使用镜像源: {default_mirror}")


# 模块加载时自动设置镜像
_setup_hf_mirror()


class EmbeddingManager:
    """向量化管理器"""

    def __init__(self, model_name: str = "shibing624/text2vec-base-chinese", use_api: bool = False):
        """
        初始化向量化管理器

        Args:
            model_name: 模型名称（本地模型名或 API 模型名）
            use_api: 是否使用 API 方式
        """
        self.model_name = model_name
        self.use_api = use_api
        self._model = None

    def _get_model(self):
        """懒加载模型"""
        if self._model is None:
            if self.use_api:
                # API 方式暂不实现，使用本地模型
                raise NotImplementedError("API embedding 暂未实现，请使用本地模型")
            else:
                try:
                    from sentence_transformers import SentenceTransformer
                    print(f"[Embedding] 加载模型: {self.model_name}")
                    self._model = SentenceTransformer(self.model_name)
                    print(f"[Embedding] 模型加载成功")
                except ImportError:
                    raise ImportError(
                        "请安装 sentence-transformers: pip install sentence-transformers"
                    )
        return self._model

    def embed(self, texts: List[str]) -> List[List[float]]:
        """
        将文本列表转换为向量

        Args:
            texts: 文本列表

        Returns:
            向量列表（每个向量是 float 列表）
        """
        if not texts:
            return []

        model = self._get_model()
        embeddings = model.encode(texts, show_progress_bar=False)
        return embeddings.tolist()

    def embed_query(self, query: str) -> List[float]:
        """
        将单个查询转换为向量

        Args:
            query: 查询文本

        Returns:
            向量（float 列表）
        """
        return self.embed([query])[0]

    @property
    def dimension(self) -> int:
        """获取向量维度"""
        model = self._get_model()
        return model.get_sentence_embedding_dimension()
