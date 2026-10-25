import sqlite3
import os
import sys

def run_check_db():
    """模拟执行 check_db.py"""
    db_path = "test_retail.db"
    print(f"检查数据库文件: {db_path}")
    print(f"文件是否存在: {os.path.exists(db_path)}")
    print(f"文件大小: {os.path.getsize(db_path) if os.path.exists(db_path) else 0} 字节")

    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # 获取所有表
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = cursor.fetchall()
        print(f"\n数据库中的表 ({len(tables)}):")
        for t in tables:
            table_name = t[0]
            cursor.execute(f'SELECT COUNT(*) FROM "{table_name}"')
            count = cursor.fetchone()[0]
            print(f"  - {table_name}: {count} 行")
        
        conn.close()
        print("\n✅ 数据库连接正常!")
        return True
    except Exception as e:
        print(f"\n❌ 错误: {e}")
        return False

def run_run_check():
    """模拟执行 run_check.py"""
    print("Hello from run_check.py!")
    return True

# 执行
success = run_check_db()

if not success:
    print("\n⚠️  check_db.py 执行失败，尝试运行 run_check.py...")
    run_run_check()
