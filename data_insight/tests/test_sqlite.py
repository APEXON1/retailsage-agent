"""
SQLite 数据库测试

测试 DBConnectorTool 的 SQLite 支持和基本查询功能。
"""

import sys
import io
from pathlib import Path

# 修复 Windows 控制台编码问题
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

# 添加项目路径
project_dir = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_dir))

from data_insight.tools.db_connector_tool import DBConnectorTool


def test_sqlite_connection():
    """测试 SQLite 连接和基本查询"""
    print("=" * 60)
    print("测试 SQLite 连接")
    print("=" * 60)

    # 数据库路径
    db_path = str(project_dir / "data_insight" / "test_retail.db")

    if not Path(db_path).exists():
        print(f"\n[ERROR] 数据库文件不存在: {db_path}")
        print("请先运行: python -m data_insight.tools.create_test_db")
        return None

    # 创建工具实例
    db_tool = DBConnectorTool(
        db_config={"database": db_path},
        db_type="sqlite"
    )

    # 测试1: 列出所有表
    print("\n1. 列出所有表:")
    result = db_tool.list_tables()
    print(f"   状态: {result.status.value}")
    print(f"   结果:\n{result.text}")

    # 测试2: 查询订单总数
    print("\n2. 查询订单总数:")
    result = db_tool.run({"sql": "SELECT COUNT(*) as total_orders FROM orders"})
    print(f"   状态: {result.status.value}")
    print(f"   结果:\n{result.text}")

    # 测试3: 查询各区域销售额
    print("\n3. 查询各区域销售额:")
    result = db_tool.run({
        "sql": """
        SELECT region,
               COUNT(*) as order_count,
               SUM(order_amount) as total_sales
        FROM orders
        GROUP BY region
        ORDER BY total_sales DESC
        """
    })
    print(f"   状态: {result.status.value}")
    print(f"   结果:\n{result.text}")

    # 测试4: 查询表结构
    print("\n4. 查询 orders 表结构:")
    result = db_tool.get_table_schema("orders")
    print(f"   状态: {result.status.value}")
    print(f"   结果:\n{result.text}")

    # 测试5: 查询近30天销售趋势
    print("\n5. 查询近30天销售趋势:")
    result = db_tool.run({
        "sql": """
        SELECT order_date,
               COUNT(*) as order_count,
               SUM(order_amount) as daily_sales
        FROM orders
        WHERE order_date >= date('now', '-30 days')
        GROUP BY order_date
        ORDER BY order_date
        LIMIT 15
        """
    })
    print(f"   状态: {result.status.value}")
    print(f"   结果:\n{result.text}")

    print("\n" + "=" * 60)
    print("SQLite 测试完成!")
    print("=" * 60)

    return db_tool


if __name__ == "__main__":
    test_sqlite_connection()
