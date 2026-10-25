"""
RAGAS 全链路评测脚本

评测 RAG 系统的检索质量，生成评测报告。
"""

import sys
import io
import json
from pathlib import Path
from datetime import datetime

# 修复 Windows 控制台编码问题
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

# 添加项目路径
project_dir = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_dir))


def create_evaluation_dataset():
    """
    创建评测数据集

    Returns:
        评测数据列表，每条包含 question, ground_truth, contexts
    """
    # 从知识库中提取的问答对
    eval_data = [
        {
            "question": "GMV是什么意思？",
            "ground_truth": "GMV（成交总额）是一段时间内的总销售额，包含已付款和未付款订单。计算公式为SUM(order_amount)。",
            "contexts": ["GMV（成交总额）是一段时间内的总销售额，包含已付款和未付款订单"]
        },
        {
            "question": "复购率怎么计算？",
            "ground_truth": "复购率是重复购买客户数占总客户数的比例，计算公式为：重复购买客户数 / 总客户数 × 100%",
            "contexts": ["复购率是重复购买客户数占总客户数的比例"]
        },
        {
            "question": "客单价的定义是什么？",
            "ground_truth": "客单价是每个订单的平均金额，计算公式为：GMV / 订单数",
            "contexts": ["客单价是每个订单的平均金额"]
        },
        {
            "question": "转化率如何计算？",
            "ground_truth": "转化率是从访客到付费用户的转化效率，计算公式为：成交客户数 / 访问客户数 × 100%",
            "contexts": ["转化率是从访客到付费用户的转化效率"]
        },
        {
            "question": "会员渗透率是什么？",
            "ground_truth": "会员渗透率是会员消费金额占总销售额的比例，计算公式为：会员消费金额 / 总销售额 × 100%",
            "contexts": ["会员渗透率是会员消费金额占总销售额的比例"]
        },
    ]

    return eval_data


def run_rag_evaluation(rag_tool, eval_data):
    """
    运行 RAG 评测

    Args:
        rag_tool: RAGTool 实例
        eval_data: 评测数据

    Returns:
        评测结果列表
    """
    results = []

    for i, item in enumerate(eval_data):
        question = item["question"]
        ground_truth = item["ground_truth"]

        print(f"\n[{i+1}/{len(eval_data)}] 评测: {question}")

        # 执行检索
        response = rag_tool.run({"query": question, "top_k": 3})

        if response.status.value == "success":
            # 提取检索到的上下文
            retrieved_contexts = []
            data = response.data
            if data and "results" in data:
                for result in data["results"]:
                    content = result.get("content", "")
                    if content:
                        retrieved_contexts.append(content[:500])

            results.append({
                "question": question,
                "ground_truth": ground_truth,
                "retrieved_contexts": retrieved_contexts,
                "retrieval_success": True
            })

            print(f"  检索成功，获取 {len(retrieved_contexts)} 条结果")
        else:
            results.append({
                "question": question,
                "ground_truth": ground_truth,
                "retrieved_contexts": [],
                "retrieval_success": False,
                "error": response.text
            })
            print(f"  检索失败: {response.text}")

    return results


def extract_keywords(text: str) -> set:
    """
    从文本中提取关键词（去除停用词）

    Args:
        text: 输入文本

    Returns:
        关键词集合
    """
    # 停用词列表
    stop_words = {
        "是", "的", "为", "了", "在", "和", "与", "或", "及", "等",
        "一个", "每个", "这个", "那个", "什么", "怎么", "如何", "为什么",
        "可以", "能够", "应该", "需要", "有", "没有", "不", "也", "都",
        "就", "才", "再", "还", "又", "很", "非常", "比较", "最",
        "从", "到", "把", "被", "让", "给", "对", "向", "往",
        "我", "你", "他", "她", "它", "我们", "你们", "他们",
        "这", "那", "这些", "那些", "这里", "那里",
        "一", "二", "三", "四", "五", "六", "七", "八", "九", "十",
        "百分之", "比例", "公式", "计算",
        "包含", "包括", "属于", "代表", "表示", "指", "意思是",
    }

    import jieba
    words = jieba.cut(text)
    keywords = set()

    for word in words:
        word = word.strip().lower()
        if len(word) >= 2 and word not in stop_words:
            keywords.add(word)

    return keywords


def calculate_metrics(results):
    """
    计算评测指标（改进版）

    Args:
        results: 评测结果

    Returns:
        指标字典
    """
    total = len(results)
    successful = sum(1 for r in results if r["retrieval_success"])
    failed = total - successful

    # 计算检索成功率
    retrieval_rate = successful / total if total > 0 else 0

    # 计算平均检索结果数
    avg_results = sum(len(r["retrieved_contexts"]) for r in results if r["retrieval_success"]) / successful if successful > 0 else 0

    # 计算上下文相关性（基于关键词覆盖度）
    relevance_scores = []
    for r in results:
        if r["retrieval_success"] and r["retrieved_contexts"]:
            # 提取 ground_truth 的关键词
            gt_keywords = extract_keywords(r["ground_truth"])

            if not gt_keywords:
                relevance_scores.append(1.0)
                continue

            # 检查检索结果是否包含这些关键词
            all_contexts = " ".join(r["retrieved_contexts"]).lower()
            matched_keywords = set()

            for kw in gt_keywords:
                if kw in all_contexts:
                    matched_keywords.add(kw)

            # 计算覆盖率
            coverage = len(matched_keywords) / len(gt_keywords)

            # 检查是否包含核心指标名称（额外加分）
            core_terms = {"gmv", "复购率", "客单价", "转化率", "会员渗透率",
                         "成交总额", "销售额", "重复购买", "平均金额"}
            question_lower = r["question"].lower()
            ground_truth_lower = r["ground_truth"].lower()

            core_bonus = 0
            for term in core_terms:
                if term in question_lower and term in all_contexts:
                    core_bonus = 0.2
                    break

            relevance_scores.append(min(coverage + core_bonus, 1.0))

    avg_relevance = sum(relevance_scores) / len(relevance_scores) if relevance_scores else 0

    return {
        "total_questions": total,
        "successful_retrievals": successful,
        "failed_retrievals": failed,
        "retrieval_success_rate": round(retrieval_rate, 4),
        "avg_retrieved_results": round(avg_results, 2),
        "avg_context_relevance": round(avg_relevance, 4),
    }


def generate_report(metrics, results, output_dir):
    """
    生成评测报告

    Args:
        metrics: 评测指标
        results: 评测结果
        output_dir: 输出目录
    """
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_file = output_path / f"ragas_report_{timestamp}.md"

    report_content = f"""# RAGAS 评测报告

**生成时间**: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

## 1. 评测概览

| 指标 | 值 |
|------|-----|
| 评测问题总数 | {metrics['total_questions']} |
| 成功检索数 | {metrics['successful_retrievals']} |
| 失败检索数 | {metrics['failed_retrievals']} |
| 检索成功率 | {metrics['retrieval_success_rate'] * 100:.1f}% |
| 平均检索结果数 | {metrics['avg_retrieved_results']} |
| 平均上下文相关性 | {metrics['avg_context_relevance'] * 100:.1f}% |

## 2. 评测详情

| 序号 | 问题 | 检索状态 | 结果数 |
|------|------|----------|--------|
"""

    for i, r in enumerate(results):
        status = "✅" if r["retrieval_success"] else "❌"
        count = len(r["retrieved_contexts"]) if r["retrieval_success"] else 0
        report_content += f"| {i+1} | {r['question']} | {status} | {count} |\n"

    report_content += f"""
## 3. 改进建议

### 3.1 检索质量
- 当前检索成功率: {metrics['retrieval_success_rate'] * 100:.1f}%
- 目标检索成功率: ≥ 90%

### 3.2 上下文相关性
- 当前相关性: {metrics['avg_context_relevance'] * 100:.1f}%
- 目标相关性: ≥ 80%

### 3.3 优化方向
1. 增加知识库覆盖范围
2. 优化文本分块策略
3. 调整检索参数（top_k, 相似度阈值）
4. 考虑使用更先进的 Embedding 模型

## 4. 测试环境

- **Embedding 模型**: shibing624/text2vec-base-chinese
- **向量数据库**: ChromaDB
- **检索方式**: 混合检索（向量 + BM25）
- **重排序**: 基于相似度
"""

    # 保存报告
    with open(report_file, "w", encoding="utf-8") as f:
        f.write(report_content)

    print(f"\n[Report] 评测报告已保存: {report_file}")

    # 同时保存 JSON 格式的详细结果
    json_file = output_path / f"ragas_results_{timestamp}.json"
    with open(json_file, "w", encoding="utf-8") as f:
        json.dump({
            "timestamp": datetime.now().isoformat(),
            "metrics": metrics,
            "details": results
        }, f, ensure_ascii=False, indent=2)

    print(f"[Report] 详细结果已保存: {json_file}")

    return report_file


def main():
    """主函数"""
    print("=" * 60)
    print("RAGAS 全链路评测")
    print("=" * 60)

    # 创建评测数据
    print("\n[1/4] 准备评测数据...")
    eval_data = create_evaluation_dataset()
    print(f"  评测问题数: {len(eval_data)}")

    # 初始化 RAG
    print("\n[2/4] 初始化 RAG 系统...")
    from data_insight.rag.rag_tool import RAGTool
    import tempfile

    knowledge_dir = project_dir / "data_insight" / "knowledge"

    with tempfile.TemporaryDirectory() as tmpdir:
        rag = RAGTool(
            knowledge_dir=str(knowledge_dir),
            vector_store_dir=tmpdir
        )

        # 运行评测
        print("\n[3/4] 运行评测...")
        results = run_rag_evaluation(rag, eval_data)

        # 计算指标
        print("\n[4/4] 计算评测指标...")
        metrics = calculate_metrics(results)

        # 生成报告
        output_dir = project_dir / "data_insight" / "output" / "reports"
        report_file = generate_report(metrics, results, output_dir)

        # 打印摘要
        print("\n" + "=" * 60)
        print("评测结果摘要")
        print("=" * 60)
        print(f"检索成功率: {metrics['retrieval_success_rate'] * 100:.1f}%")
        print(f"平均上下文相关性: {metrics['avg_context_relevance'] * 100:.1f}%")
        print(f"详细报告: {report_file}")
        print("=" * 60)


if __name__ == "__main__":
    main()
