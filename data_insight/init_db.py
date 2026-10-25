import sqlite3
import os

db_path = 'test_retail.db'

# 如果数据库文件存在但为空，先删除
if os.path.exists(db_path) and os.path.getsize(db_path) == 0:
    os.remove(db_path)

# 创建数据库并建立表
conn = sqlite3.connect(db_path)
cursor = conn.cursor()

# 创建 orders 表
cursor.execute('''
CREATE TABLE IF NOT EXISTS orders (
    order_id INTEGER PRIMARY KEY,
    order_date TEXT,
    region TEXT,
    city TEXT,
    store_id INTEGER,
    store_name TEXT,
    channel TEXT,
    category TEXT,
    product_name TEXT,
    quantity INTEGER,
    order_amount REAL,
    cost_amount REAL,
    customer_id INTEGER,
    is_member INTEGER
)
''')

# 创建 customers 表
cursor.execute('''
CREATE TABLE IF NOT EXISTS customers (
    customer_id INTEGER PRIMARY KEY,
    register_date TEXT,
    region TEXT,
    city TEXT,
    gender TEXT,
    age_group TEXT,
    total_orders INTEGER,
    total_amount REAL
)
''')

# 创建 stores 表
cursor.execute('''
CREATE TABLE IF NOT EXISTS stores (
    store_id INTEGER PRIMARY KEY,
    store_name TEXT,
    region TEXT,
    city TEXT,
    store_type TEXT,
    open_date TEXT
)
''')

# 插入示例订单数据（包含上个月的数据）
import random
from datetime import datetime, timedelta
import calendar

# 获取当前日期
now = datetime.now()
current_year = now.year
current_month = now.month

# 上个月
if current_month == 1:
    last_month = 12
    last_month_year = current_year - 1
else:
    last_month = current_month - 1
    last_month_year = current_year

# 上个月的天数
last_month_days = calendar.monthrange(last_month_year, last_month)[1]

print(f"当前日期: {now}")
print(f"上个月: {last_month_year}-{last_month:02d}")

# 插入 orders 数据（上个月的数据）
orders_data = []
order_id = 1

regions = ['华东', '华南', '华北', '华中', '西南', '西北', '东北']
cities_by_region = {
    '华东': ['上海', '杭州', '南京', '苏州', '宁波'],
    '华南': ['广州', '深圳', '东莞', '佛山', '珠海'],
    '华北': ['北京', '天津', '石家庄', '太原', '呼和浩特'],
    '华中': ['武汉', '长沙', '郑州', '南昌', '合肥'],
    '西南': ['成都', '重庆', '昆明', '贵阳', '西安'],
    '西北': ['西安', '兰州', '银川', '西宁', '乌鲁木齐'],
    '东北': ['沈阳', '大连', '长春', '哈尔滨', '吉林']
}
categories = ['电子产品', '服装鞋帽', '食品饮料', '家居用品', '美妆护肤', '图书文具']
channels = ['线上', '线下']
store_names = ['旗舰店', '标准店', '社区店', '折扣店', '体验店']
products = {
    '电子产品': ['智能手机', '笔记本电脑', '平板电脑', '耳机', '充电宝'],
    '服装鞋帽': ['T恤', '牛仔裤', '运动鞋', '羽绒服', '连衣裙'],
    '食品饮料': ['坚果礼盒', '咖啡', '巧克力', '矿泉水', '饼干'],
    '家居用品': ['台灯', '毛巾', '收纳盒', '枕头', '水杯'],
    '美妆护肤': ['面霜', '精华液', '口红', '面膜', '防晒霜'],
    '图书文具': ['笔记本', '钢笔', '畅销书', '文具套装', '日历']
}

# 生成上个月的订单数据
for day in range(1, last_month_days + 1):
    # 每天生成 20-50 个订单
    num_orders = random.randint(20, 50)
    for _ in range(num_orders):
        region = random.choice(regions)
        city = random.choice(cities_by_region[region])
        category = random.choice(categories)
        product = random.choice(products[category])
        channel = random.choice(channels)
        quantity = random.randint(1, 5)
        unit_price = round(random.uniform(20, 2000), 2)
        order_amount = round(quantity * unit_price, 2)
        cost_amount = round(order_amount * random.uniform(0.4, 0.7), 2)
        store_id = random.randint(1, 20)
        store_name = random.choice(store_names)
        customer_id = random.randint(1, 500)
        is_member = 1 if random.random() < 0.4 else 0
        
        order_date = f"{last_month_year}-{last_month:02d}-{day:02d}"
        
        orders_data.append((
            order_id, order_date, region, city, store_id, store_name,
            channel, category, product, quantity, order_amount, cost_amount,
            customer_id, is_member
        ))
        order_id += 1

# 插入数据
cursor.executemany('''
    INSERT INTO orders (order_id, order_date, region, city, store_id, store_name,
                       channel, category, product_name, quantity, order_amount, cost_amount,
                       customer_id, is_member)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
''', orders_data)

# 插入 customers 数据
customers_data = []
for cid in range(1, 501):
    register_date = f"{last_month_year}-{random.randint(1, 12):02d}-{random.randint(1, 28):02d}"
    region = random.choice(regions)
    city = random.choice(cities_by_region[region])
    gender = random.choice(['男', '女'])
    age_group = random.choice(['18-25', '26-35', '36-45', '46-55', '55+'])
    total_orders = random.randint(0, 30)
    total_amount = round(total_orders * random.uniform(50, 500), 2)
    customers_data.append((cid, register_date, region, city, gender, age_group, total_orders, total_amount))

cursor.executemany('''
    INSERT INTO customers (customer_id, register_date, region, city, gender, age_group, total_orders, total_amount)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
''', customers_data)

# 插入 stores 数据
stores_data = []
for sid in range(1, 21):
    region = random.choice(regions)
    city = random.choice(cities_by_region[region])
    store_type = random.choice(['旗舰店', '标准店', '社区店', '折扣店'])
    open_date = f"{random.randint(2018, 2024)}-{random.randint(1, 12):02d}-{random.randint(1, 28):02d}"
    stores_data.append((sid, f"{city}{store_type}", region, city, store_type, open_date))

cursor.executemany('''
    INSERT INTO stores (store_id, store_name, region, city, store_type, open_date)
    VALUES (?, ?, ?, ?, ?, ?)
''', stores_data)

conn.commit()

# 验证数据
cursor.execute("SELECT COUNT(*) FROM orders")
print(f"订单总数: {cursor.fetchone()[0]}")

cursor.execute("""
    SELECT strftime('%Y-%m', order_date) as month, SUM(order_amount) as total_sales
    FROM orders
    GROUP BY month
    ORDER BY month
""")
sales = cursor.fetchall()
print("各月销售额:")
for month, amount in sales:
    print(f"  {month}: ¥{amount:,.2f}")

conn.close()
print("数据库初始化完成！")
