"""
端到端测试 - 验证 RetailSage Agent 完整流程

测试内容：
1. RAG 知识检索
2. SQLite 数据库查询
3. Agent 创建和运行
"""

import sys
import io
from pathlib import Path

# 修复 Windows 控制台编码问题
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

# 添加项目路径
project_dir = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_dir))


def test_rag_search():
    """测试 RAG 检索功能"""
    print("=" * 60)
    print("测试 1: RAG 知识检索")
    print("=" * 60)

    from data_insight.rag.rag_tool import RAGTool

    knowledge_dir = project_dir / "data_insight" / "knowledge"
    vector_store_dir = project_dir / "data_insight" / "memory" / "vector_store_test"

    rag_tool = RAGTool(
        knowledge_dir=str(knowledge_dir),
        vector_store_dir=str(vector_store_dir)
    )

    # 测试查询
    queries = ["GMV是什么", "复购率怎么计算", "会员转化率"]
    for query in queries:
        result = rag_tool.run({"query": query, "top_k": 2})
        print(f"\n查询: {query}")
        print(f"状态: {result.status.value}")
        if result.status.value == "success":
            # 只显示前200字符
            print(f"结果: {result.text[:200]}...")

    print("\n[OK] RAG 测试完成")
    return True


def test_sqlite_query():
    """测试 SQLite 数据库查询"""
    print("\n" + "=" * 60)
    print("测试 2: SQLite 数据库查询")
    print("=" * 60)

    from data_insight.tools.db_connector_tool import DBConnectorTool

    db_path = str(project_dir / "data_insight" / "test_retail.db")

    if not Path(db_path).exists():
        print(f"[ERROR] 数据库不存在: {db_path}")
        return False

    db_tool = DBConnectorTool(
        db_config={"database": db_path},
        db_type="sqlite"
    )

    # 测试查询
    queries = [
        ("总订单数", "SELECT COUNT(*) as total FROM orders"),
        ("区域销售", "SELECT region, SUM(order_amount) as sales FROM orders GROUP BY region"),
        ("热销品类", "SELECT category, COUNT(*) as cnt FROM orders GROUP BY category ORDER BY cnt DESC LIMIT 5"),
    ]

    for name, sql in queries:
        result = db_tool.run({"sql": sql})
        print(f"\n{name}:")
        print(f"状态: {result.status.value}")
        if result.status.value == "success":
            print(f"结果: {result.text[:200]}...")

    print("\n[OK] SQLite 测试完成")
    return True


def test_agent_creation():
    """测试 Agent 创建"""
    print("\n" + "=" * 60)
    print("测试 3: Agent 创建")
    print("=" * 60)

    from data_insight.config import (
        DB_CONFIG, DEFAULT_DB_TYPE, LLM_MODEL_ID, LLM_API_KEY, LLM_BASE_URL,
        KNOWLEDGE_DIR, OUTPUT_DIR, MEMORY_DIR, MAX_STEPS
    )
    from data_insight.agents.insight_agent import InsightAgent

    # 检查配置
    print(f"LLM Model: {LLM_MODEL_ID}")
    print(f"LLM Base URL: {LLM_BASE_URL}")
    print(f"DB Type: {DEFAULT_DB_TYPE}")
    print(f"API Key configured: {bool(LLM_API_KEY and LLM_API_KEY != 'your-api-key-here')}")

    # 获取数据库配置
    db_config = DB_CONFIG.get(DEFAULT_DB_TYPE, {})

    # 表结构信息
    table_schemas = {
        "orders": {
            "description": "订单表",
            "columns": [
                {"name": "order_id", "type": "VARCHAR", "description": "订单ID"},
                {"name": "order_date", "type": "DATE", "description": "订单日期"},
                {"name": "region", "type": "VARCHAR", "description": "区域"},
                {"name": "category", "type": "VARCHAR", "description": "商品品类"},
                {"name": "order_amount", "type": "DECIMAL", "description": "订单金额"},
                {"name": "customer_id", "type": "VARCHAR", "description": "客户ID"},
            ]
        }
    }

    try:
        agent = InsightAgent.create(
            db_config=db_config,
            db_type=DEFAULT_DB_TYPE,
            table_schemas=table_schemas,
            knowledge_dir=str(KNOWLEDGE_DIR),
            output_dir=str(OUTPUT_DIR),
            memory_dir=str(MEMORY_DIR),
            max_steps=MAX_STEPS
        )
        print(f"\n[OK] Agent 创建成功: {agent.name}")
        return True
    except Exception as e:
        print(f"\n[ERROR] Agent 创建失败: {e}")
        return False


def test_agent_run():
    """测试 Agent 运行（需要 LLM API）"""
    print("\n" + "=" * 60)
    print("测试 4: Agent 运行")
    print("=" * 60)

    from data_insight.config import (
        DB_CONFIG, DEFAULT_DB_TYPE, KNOWLEDGE_DIR, OUTPUT_DIR, MEMORY_DIR, MAX_STEPS
    )
    from data_insight.agents.insight_agent import InsightAgent

    # 获取数据库配置
    db_config = DB_CONFIG.get(DEFAULT_DB_TYPE, {})

    # 表结构信息
    table_schemas = {
        "orders": {
            "description": "订单表，记录所有销售订单",
            "columns": [
                {"name": "order_id", "type": "VARCHAR", "description": "订单ID"},
                {"name": "order_date", "type": "DATE", "description": "订单日期"},
                {"name": "region", "type": "VARCHAR", "description": "区域（华东/华北/华南/华西）"},
                {"name": "category", "type": "VARCHAR", "description": "商品品类"},
                {"name": "order_amount", "type": "DECIMAL", "description": "订单金额"},
                {"name": "customer_id", "type": "VARCHAR", "description": "客户ID"},
            ]
        }
    }

    # 创建 Agent
    agent = InsightAgent.create(
        db_config=db_config,
        db_type=DEFAULT_DB_TYPE,
        table_schemas=table_schemas,
        knowledge_dir=str(KNOWLEDGE_DIR),
        output_dir=str(OUTPUT_DIR),
        memory_dir=str(MEMORY_DIR),
        max_steps=5  # 测试时减少步数
    )

    # 测试简单查询
    test_query = "查询各区域的销售总额"
    print(f"\n测试查询: {test_query}")
    print("-" * 40)

    try:
        result = agent.run(test_query)
        print(f"\nAgent 回答:\n{result}")
        print("\n[OK] Agent 运行测试完成")
        return True
    except Exception as e:
        print(f"\n[ERROR] Agent 运行失败: {e}")
        return False


def main():
    """运行所有测试"""
    print("=" * 60)
    print("RetailSage Agent 端到端测试")
    print("=" * 60)

    results = []

    # 测试 1: RAG
    try:
        results.append(("RAG 检索", test_rag_search()))
    except Exception as e:
        print(f"[ERROR] RAG 测试异常: {e}")
        results.append(("RAG 检索", False))

    # 测试 2: SQLite
    try:
        results.append(("SQLite 查询", test_sqlite_query()))
    except Exception as e:
        print(f"[ERROR] SQLite 测试异常: {e}")
        results.append(("SQLite 查询", False))

    # 测试 3: Agent 创建
    try:
        results.append(("Agent 创建", test_agent_creation()))
    except Exception as e:
        print(f"[ERROR] Agent 创建异常: {e}")
        results.append(("Agent 创建", False))

    # 测试 4: Agent 运行
    try:
        results.append(("Agent 运行", test_agent_run()))
    except Exception as e:
        print(f"[ERROR] Agent 运行异常: {e}")
        results.append(("Agent 运行", False))

    # 汇总结果
    print("\n" + "=" * 60)
    print("测试结果汇总")
    print("=" * 60)
    for name, passed in results:
        status = "PASS" if passed else "FAIL"
        print(f"  {name}: {status}")

    all_passed = all(r[1] for r in results)
    print(f"\n总体结果: {'ALL PASSED' if all_passed else 'SOME FAILED'}")
    print("=" * 60)

    return all_passed


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
