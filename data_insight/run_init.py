import sys
sys.path.insert(0, '.')
from tools.create_test_db import create_test_database
create_test_database()
print("数据库初始化完成！")
