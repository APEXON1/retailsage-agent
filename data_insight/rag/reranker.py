"""
重排序器 - 对检索结果进行精排

使用 LLM 或简单的相关性评分对检索结果进行重排序。
"""

from typing import List, Dict, Any, Optional


class Reranker:
    """重排序器 - 对检索结果进行精排"""

    def __init__(self, llm=None, use_llm: bool = False):
        """
        初始化重排序器

        Args:
            llm: HelloAgentsLLM 实例（用于 LLM 重排序）
            use_llm: 是否使用 LLM 重排序（默认使用简单规则）
        """
        self.llm = llm
        self.use_llm = use_llm

    def rerank(self, query: str, results: List[Dict[str, Any]], top_k: int = 3) -> List[Dict[str, Any]]:
        """
        对检索结果进行重排序

        Args:
            query: 原始查询
            results: 检索结果列表
            top_k: 返回结果数量

        Returns:
            重排序后的结果列表
        """
        if not results:
            return []

        if self.use_llm and self.llm:
            return self._llm_rerank(query, results, top_k)
        else:
            return self._simple_rerank(query, results, top_k)

    def _simple_rerank(self, query: str, results: List[Dict[str, Any]], top_k: int) -> List[Dict[str, Any]]:
        """
        简单重排序（基于关键词匹配和文本长度）

        策略：
        1. 查询关键词在内容中出现的次数
        2. 内容长度适中（不要太短也不要太长）
        3. 与原始分数加权
        """
        query_chars = set(query.lower())
        scored_results = []

        for result in results:
            content = result.get("content", "")
            original_score = result.get("score", 0)

            # 关键词匹配分数
            content_lower = content.lower()
            keyword_matches = sum(1 for char in query_chars if char in content_lower and char.strip())
            keyword_score = min(keyword_matches / max(len(query_chars), 1), 1.0)

            # 长度分数（适中长度更好）
            length = len(content)
            if 50 < length < 500:
                length_score = 1.0
            elif length <= 50:
                length_score = 0.5
            else:
                length_score = max(0.3, 1.0 - (length - 500) / 2000)

            # 综合分数
            final_score = original_score * 0.5 + keyword_score * 0.3 + length_score * 0.2

            scored_results.append({
                **result,
                "rerank_score": final_score
            })

        # 按重排序分数排序
        scored_results.sort(key=lambda x: x["rerank_score"], reverse=True)
        return scored_results[:top_k]

    def _llm_rerank(self, query: str, results: List[Dict[str, Any]], top_k: int) -> List[Dict[str, Any]]:
        """
        LLM 重排序（更精确但更慢）

        使用 LLM 评估每个结果与查询的相关性。
        """
        if not results:
            return []

        # 构建评估 Prompt
        results_text = ""
        for i, result in enumerate(results):
            content = result.get("content", "")[:200]  # 截断避免过长
            results_text += f"\n[{i}] {content}\n"

        prompt = f"""请评估以下检索结果与查询的相关性。

查询: {query}

检索结果:
{results_text}

请为每个结果打分（0-10分），只返回分数列表，格式: 0:8,1:6,2:9
分数越高表示与查询越相关。"""

        try:
            messages = [{"role": "user", "content": prompt}]
            response = self.llm.invoke(messages)
            score_text = response.content.strip()

            # 解析分数
            scores = {}
            for item in score_text.split(","):
                parts = item.strip().split(":")
                if len(parts) == 2:
                    try:
                        idx = int(parts[0].strip())
                        score = float(parts[1].strip())
                        scores[idx] = score
                    except ValueError:
                        continue

            # 应用分数
            for i, result in enumerate(results):
                result["rerank_score"] = scores.get(i, result.get("score", 0))

            # 排序
            results.sort(key=lambda x: x.get("rerank_score", 0), reverse=True)
            return results[:top_k]

        except Exception as e:
            print(f"⚠️ LLM 重排序失败，使用简单重排序: {e}")
            return self._simple_rerank(query, results, top_k)
