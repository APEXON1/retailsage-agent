"""
文档加载器 - 从多种格式加载知识文档

支持 JSON、Markdown、CSV 等格式的文档加载。
"""

import os
import json
from typing import List, Dict, Any


class Document:
    """文档对象"""
    def __init__(self, content: str, metadata: Dict[str, Any] = None):
        self.content = content
        self.metadata = metadata or {}

    def to_dict(self) -> Dict[str, Any]:
        return {"content": self.content, "metadata": self.metadata}


class DocumentLoader:
    """文档加载器 - 支持多种格式"""

    def __init__(self):
        self._loaders = {
            ".json": self._load_json,
            ".md": self._load_markdown,
            ".txt": self._load_text,
            ".csv": self._load_csv,
        }

    def load(self, path: str) -> List[Document]:
        """
        加载文件或目录

        Args:
            path: 文件路径或目录路径

        Returns:
            Document 列表
        """
        if os.path.isdir(path):
            return self._load_directory(path)
        elif os.path.isfile(path):
            return self._load_file(path)
        else:
            raise FileNotFoundError(f"路径不存在: {path}")

    def _load_directory(self, dir_path: str) -> List[Document]:
        """加载目录下所有支持的文件"""
        documents = []
        for root, _, files in os.walk(dir_path):
            for file in files:
                ext = os.path.splitext(file)[1].lower()
                if ext in self._loaders:
                    filepath = os.path.join(root, file)
                    try:
                        docs = self._load_file(filepath)
                        documents.extend(docs)
                    except Exception as e:
                        print(f"⚠️ 加载文件失败 {filepath}: {e}")
        return documents

    def _load_file(self, filepath: str) -> List[Document]:
        """加载单个文件"""
        ext = os.path.splitext(filepath)[1].lower()
        loader = self._loaders.get(ext)
        if not loader:
            raise ValueError(f"不支持的文件格式: {ext}")
        return loader(filepath)

    def _load_json(self, filepath: str) -> List[Document]:
        """加载 JSON 文件（支持指标字典等结构化数据）"""
        with open(filepath, 'r', encoding='utf-8') as f:
            data = json.load(f)

        documents = []
        filename = os.path.basename(filepath)

        if isinstance(data, list):
            # 列表格式：每个元素是一个文档
            for i, item in enumerate(data):
                if isinstance(item, dict):
                    content = json.dumps(item, ensure_ascii=False, indent=2)
                    metadata = {"source": filename, "index": i, "type": "json_item"}
                else:
                    content = str(item)
                    metadata = {"source": filename, "index": i, "type": "json_value"}
                documents.append(Document(content=content, metadata=metadata))

        elif isinstance(data, dict):
            # 字典格式：按 key 拆分为多个文档
            for key, value in data.items():
                if isinstance(value, (dict, list)):
                    content = json.dumps({key: value}, ensure_ascii=False, indent=2)
                else:
                    content = f"{key}: {value}"
                metadata = {"source": filename, "key": key, "type": "json_entry"}
                documents.append(Document(content=content, metadata=metadata))

        return documents

    def _load_markdown(self, filepath: str) -> List[Document]:
        """加载 Markdown 文件（按标题分块）"""
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()

        filename = os.path.basename(filepath)
        documents = []

        # 按二级标题分块
        sections = content.split("\n## ")
        for i, section in enumerate(sections):
            if section.strip():
                if i == 0 and not section.startswith("## "):
                    # 第一段（可能是 frontmatter 或一级标题）
                    metadata = {"source": filename, "section": "header", "type": "markdown"}
                else:
                    title = section.split("\n")[0].strip()
                    metadata = {"source": filename, "section": title, "type": "markdown"}
                documents.append(Document(content=section.strip(), metadata=metadata))

        return documents

    def _load_text(self, filepath: str) -> List[Document]:
        """加载纯文本文件"""
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()

        filename = os.path.basename(filepath)
        return [Document(content=content, metadata={"source": filename, "type": "text"})]

    def _load_csv(self, filepath: str) -> List[Document]:
        """加载 CSV 文件（每行一个文档）"""
        import csv

        documents = []
        filename = os.path.basename(filepath)

        with open(filepath, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for i, row in enumerate(reader):
                content = "; ".join(f"{k}: {v}" for k, v in row.items() if v)
                metadata = {"source": filename, "row": i, "type": "csv_row"}
                documents.append(Document(content=content, metadata=metadata))

        return documents
