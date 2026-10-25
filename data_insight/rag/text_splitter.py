"""
文本分块器 - 将长文档切分为适合向量化的短文本块

支持中文分词和语义感知的分块策略。
"""

import re
from typing import List, Dict, Any


class TextChunk:
    """文本块对象"""
    def __init__(self, content: str, metadata: Dict[str, Any] = None):
        self.content = content
        self.metadata = metadata or {}

    def to_dict(self) -> Dict[str, Any]:
        return {"content": self.content, "metadata": self.metadata}


class TextSplitter:
    """文本分块器 - 支持中文语义感知分块"""

    def __init__(self, chunk_size: int = 300, chunk_overlap: int = 100):
        """
        初始化文本分块器

        Args:
            chunk_size: 每个块的最大字符数（默认300，更聚焦）
            chunk_overlap: 块之间的重叠字符数（默认100，保留更多上下文）
        """
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap

    def split(self, text: str, metadata: Dict[str, Any] = None) -> List[TextChunk]:
        """
        将文本切分为块

        Args:
            text: 待切分的文本
            metadata: 元数据（会继承到每个块）

        Returns:
            TextChunk 列表
        """
        if not text or not text.strip():
            return []

        metadata = metadata or {}

        # 清理文本
        text = self._clean_text(text)

        # 如果文本足够短，直接返回
        if len(text) <= self.chunk_size:
            return [TextChunk(content=text, metadata=metadata)]

        # 按段落切分
        paragraphs = self._split_by_paragraph(text)

        # 合并小段落，切分大段落
        chunks = self._merge_and_split(paragraphs)

        # 添加元数据
        result = []
        for i, chunk_text in enumerate(chunks):
            chunk_metadata = {**metadata, "chunk_index": i, "total_chunks": len(chunks)}
            result.append(TextChunk(content=chunk_text, metadata=chunk_metadata))

        return result

    def _clean_text(self, text: str) -> str:
        """清理文本"""
        # 移除多余空白
        text = re.sub(r'\n{3,}', '\n\n', text)
        text = re.sub(r' {2,}', ' ', text)
        return text.strip()

    def _split_by_paragraph(self, text: str) -> List[str]:
        """按段落切分"""
        # 按双换行切分（段落边界）
        paragraphs = re.split(r'\n\s*\n', text)
        return [p.strip() for p in paragraphs if p.strip()]

    def _merge_and_split(self, paragraphs: List[str]) -> List[str]:
        """合并小段落，切分大段落"""
        chunks = []
        current_chunk = ""

        for para in paragraphs:
            # 如果当前段落本身就超过 chunk_size，需要进一步切分
            if len(para) > self.chunk_size:
                # 先保存当前累积的内容
                if current_chunk:
                    chunks.append(current_chunk.strip())
                    current_chunk = ""
                # 切分大段落
                sub_chunks = self._split_long_text(para)
                chunks.extend(sub_chunks)
            # 如果加上当前段落会超过 chunk_size
            elif len(current_chunk) + len(para) + 1 > self.chunk_size:
                chunks.append(current_chunk.strip())
                # 保留 overlap
                if self.chunk_overlap > 0 and len(current_chunk) > self.chunk_overlap:
                    current_chunk = current_chunk[-self.chunk_overlap:] + "\n" + para
                else:
                    current_chunk = para
            else:
                if current_chunk:
                    current_chunk += "\n" + para
                else:
                    current_chunk = para

        if current_chunk.strip():
            chunks.append(current_chunk.strip())

        return chunks

    def _split_long_text(self, text: str) -> List[str]:
        """切分长文本（按句子边界，保留语义完整性）"""
        # 按中文句号、问号、感叹号、分号、换行切分
        sentences = re.split(r'([。！？；\n])', text)

        # 重新组合（保留分隔符）
        merged_sentences = []
        for i in range(0, len(sentences) - 1, 2):
            merged_sentences.append(sentences[i] + sentences[i + 1])
        if len(sentences) % 2 == 1:
            merged_sentences.append(sentences[-1])

        chunks = []
        current_chunk = ""

        for sentence in merged_sentences:
            # 如果单个句子就超过 chunk_size，需要进一步切分
            if len(sentence) > self.chunk_size:
                if current_chunk:
                    chunks.append(current_chunk.strip())
                    current_chunk = ""
                # 按逗号切分长句子
                sub_parts = re.split(r'([，,])', sentence)
                merged_parts = []
                for j in range(0, len(sub_parts) - 1, 2):
                    merged_parts.append(sub_parts[j] + sub_parts[j + 1])
                if len(sub_parts) % 2 == 1:
                    merged_parts.append(sub_parts[-1])

                for part in merged_parts:
                    if len(current_chunk) + len(part) > self.chunk_size:
                        if current_chunk:
                            chunks.append(current_chunk.strip())
                        current_chunk = part
                    else:
                        current_chunk += part
            elif len(current_chunk) + len(sentence) > self.chunk_size:
                if current_chunk:
                    chunks.append(current_chunk.strip())
                # 保留 overlap 以维持上下文
                if self.chunk_overlap > 0 and len(current_chunk) > self.chunk_overlap:
                    current_chunk = current_chunk[-self.chunk_overlap:] + sentence
                else:
                    current_chunk = sentence
            else:
                current_chunk += sentence

        if current_chunk.strip():
            chunks.append(current_chunk.strip())

        return chunks if chunks else [text[:self.chunk_size]]
