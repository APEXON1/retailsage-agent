"""
RAG 系统测试 - 验证全链路和 RAGAS 评测
"""

import sys
import io
from pathlib import Path

# 修复 Windows 控制台编码问题
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

# 添加项目路径
project_dir = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_dir))


def test_document_loader():
    """测试文档加载器"""
    print("=" * 60)
    print("测试 DocumentLoader")
    print("=" * 60)

    from data_insight.rag.document_loader import DocumentLoader

    loader = DocumentLoader()

    # 测试加载知识目录
    knowledge_dir = project_dir / "data_insight" / "knowledge"
    if knowledge_dir.exists():
        documents = loader.load(str(knowledge_dir))
        print(f"\n✅ 加载了 {len(documents)} 个文档")
        for i, doc in enumerate(documents[:3]):
            print(f"  文档 {i+1}: {doc.content[:50]}...")
            print(f"    元数据: {doc.metadata}")
    else:
        print(f"\n⚠️ 知识目录不存在: {knowledge_dir}")

    print("\n✅ DocumentLoader 测试完成")


def test_text_splitter():
    """测试文本分块器"""
    print("\n" + "=" * 60)
    print("测试 TextSplitter")
    print("=" * 60)

    from data_insight.rag.text_splitter import TextSplitter

    splitter = TextSplitter(chunk_size=200, chunk_overlap=30)

    # 测试文本
    test_text = """
    GMV（成交总额）是电商行业最核心的指标之一。它代表一段时间内的总销售额，
    包含已付款和未付款订单。GMV的计算公式为：SUM(order_amount)。

    客单价是另一个重要指标，表示每个订单的平均金额。计算公式为：GMV / 订单数。
    客单价的高低直接影响整体销售额。

    转化率衡量的是从访客到付费用户的转化效率。高转化率意味着流量利用效率高。
    转化率的计算公式为：成交客户数 / 访问客户数 × 100%。

    复购率反映客户的忠诚度。高复购率说明客户对产品满意，愿意重复购买。
    复购率的计算公式为：重复购买客户数 / 总客户数 × 100%。
    """

    chunks = splitter.split(test_text, metadata={"source": "test"})
    print(f"\n✅ 切分为 {len(chunks)} 个文本块")
    for i, chunk in enumerate(chunks):
        print(f"\n  块 {i+1} (长度: {len(chunk.content)}):")
        print(f"    {chunk.content[:80]}...")

    print("\n✅ TextSplitter 测试完成")


def test_rag_tool():
    """测试 RAG 工具"""
    print("\n" + "=" * 60)
    print("测试 RAGTool")
    print("=" * 60)

    import tempfile

    from data_insight.rag.rag_tool import RAGTool

    knowledge_dir = project_dir / "data_insight" / "knowledge"

    if not knowledge_dir.exists():
        print(f"\n⚠️ 知识目录不存在，跳过测试")
        return

    with tempfile.TemporaryDirectory() as tmpdir:
        try:
            rag_tool = RAGTool(
                knowledge_dir=str(knowledge_dir),
                vector_store_dir=tmpdir
            )

            # 测试检索
            print("\n1. 测试检索 'GMV':")
            result = rag_tool.run({"query": "GMV是什么", "top_k": 3})
            print(f"   状态: {result.status.value}")
            print(f"   结果:\n{result.text[:300]}...")

            print("\n2. 测试检索 '复购率':")
            result = rag_tool.run({"query": "复购率怎么计算", "top_k": 3})
            print(f"   状态: {result.status.value}")
            print(f"   结果:\n{result.text[:300]}...")

            print("\n✅ RAGTool 测试完成")

        except ImportError as e:
            print(f"\n⚠️ 缺少依赖: {e}")
            print("请安装: pip install chromadb sentence-transformers")


def test_ragas_evaluation():
    """RAGAS 评测（需要安装 ragas）"""
    print("\n" + "=" * 60)
    print("RAGAS 评测")
    print("=" * 60)

    try:
        from ragas import evaluate
        from ragas.metrics import faithfulness, answer_relevancy, context_precision, context_recall
        print("\n✅ ragas 已安装")

        # 准备评测数据
        # 注意：完整的 RAGAS 评测需要 LLM 调用，这里只验证导入
        print("  评测指标:")
        print("  - Faithfulness（忠实度）: 回答是否基于检索到的上下文")
        print("  - Answer Relevancy（答案相关性）: 回答与问题的相关程度")
        print("  - Context Precision（上下文精确度）: 检索结果的精确程度")
        print("  - Context Recall（上下文召回率）: 检索结果的召回程度")
        print("\n  完整评测需要配置 LLM 并运行实际查询")

    except ImportError:
        print("\n⚠️ ragas 未安装，请运行: pip install ragas")
        print("  安装后可以进行 RAG 全链路质量评测")


if __name__ == "__main__":
    test_document_loader()
    test_text_splitter()
    test_rag_tool()
    test_ragas_evaluation()

    print("\n" + "=" * 60)
    print("✅ 所有 RAG 测试完成！")
    print("=" * 60)
