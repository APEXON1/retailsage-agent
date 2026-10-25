"""
创建 SQLite 测试数据库

生成零售/电商模拟数据，用于测试 NL2SQL 和数据分析功能。
"""

import sqlite3
import random
from datetime import datetime, timedelta
from pathlib import Path


def create_test_database(db_path: str = None) -> str:
    """
    创建测试数据库并插入模拟数据

    Args:
        db_path: 数据库文件路径，默认为 data_insight/test_retail.db

    Returns:
        数据库文件路径
    """
    if db_path is None:
        db_path = str(Path(__file__).parent.parent / "test_retail.db")

    # 如果数据库已存在，先删除
    db_file = Path(db_path)
    if db_file.exists():
        db_file.unlink()

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    print(f"[TestDB] 创建数据库: {db_path}")

    # === 创建表结构 ===

    # 订单表
    cursor.execute("""
    CREATE TABLE orders (
        order_id VARCHAR(32) PRIMARY KEY,
        order_date DATE NOT NULL,
        region VARCHAR(20) NOT NULL,
        city VARCHAR(20),
        store_id VARCHAR(20),
        store_name VARCHAR(50),
        channel VARCHAR(10) NOT NULL,
        category VARCHAR(30) NOT NULL,
        product_name VARCHAR(100),
        quantity INTEGER NOT NULL,
        order_amount DECIMAL(10,2) NOT NULL,
        cost_amount DECIMAL(10,2),
        customer_id VARCHAR(20),
        is_member INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    # 客户表
    cursor.execute("""
    CREATE TABLE customers (
        customer_id VARCHAR(20) PRIMARY KEY,
        register_date DATE,
        region VARCHAR(20),
        city VARCHAR(20),
        gender VARCHAR(5),
        age_group VARCHAR(10),
        total_orders INTEGER DEFAULT 0,
        total_amount DECIMAL(12,2) DEFAULT 0
    )
    """)

    # 门店表
    cursor.execute("""
    CREATE TABLE stores (
        store_id VARCHAR(20) PRIMARY KEY,
        store_name VARCHAR(50),
        region VARCHAR(20),
        city VARCHAR(20),
        store_type VARCHAR(20),
        open_date DATE
    )
    """)

    # === 生成模拟数据 ===

    # 区域和城市
    regions_cities = {
        "华东": ["上海", "杭州", "南京", "苏州", "宁波"],
        "华北": ["北京", "天津", "石家庄", "济南", "青岛"],
        "华南": ["广州", "深圳", "东莞", "佛山", "厦门"],
        "华西": ["成都", "重庆", "西安", "昆明", "贵阳"],
    }

    # 商品品类和商品名
    categories_products = {
        "电子产品": ["iPhone 15", "华为Mate60", "小米14", "iPad Air", "MacBook Pro", "AirPods Pro"],
        "服装": ["羽绒服", "牛仔裤", "运动鞋", "T恤", "连衣裙", "卫衣"],
        "食品": ["牛奶", "面包", "水果礼盒", "坚果礼盒", "巧克力", "咖啡"],
        "家居": ["床单四件套", "枕头", "收纳箱", "台灯", "地毯", "窗帘"],
        "美妆": ["口红", "粉底液", "面膜", "精华液", "防晒霜", "眼影盘"],
    }

    # 门店
    stores_data = []
    store_id = 1001
    for region, cities in regions_cities.items():
        for city in cities:
            for i in range(2):  # 每个城市2家店
                store_type = random.choice(["旗舰店", "标准店", "社区店"])
                stores_data.append((
                    f"S{store_id}",
                    f"{city}{store_type}",
                    region,
                    city,
                    store_type,
                    (datetime.now() - timedelta(days=random.randint(365, 1800))).strftime("%Y-%m-%d")
                ))
                store_id += 1

    cursor.executemany(
        "INSERT INTO stores (store_id, store_name, region, city, store_type, open_date) VALUES (?, ?, ?, ?, ?, ?)",
        stores_data
    )
    print(f"  - 插入 {len(stores_data)} 家门店")

    # 客户
    customers_data = []
    for i in range(500):
        region = random.choice(list(regions_cities.keys()))
        city = random.choice(regions_cities[region])
        customers_data.append((
            f"C{10001 + i}",
            (datetime.now() - timedelta(days=random.randint(30, 730))).strftime("%Y-%m-%d"),
            region,
            city,
            random.choice(["男", "女"]),
            random.choice(["18-25", "26-35", "36-45", "46-55", "55+"]),
        ))

    cursor.executemany(
        "INSERT INTO customers (customer_id, register_date, region, city, gender, age_group) VALUES (?, ?, ?, ?, ?, ?)",
        customers_data
    )
    print(f"  - 插入 {len(customers_data)} 位客户")

    # 订单（近6个月数据）
    orders_data = []
    order_id = 100001
    start_date = datetime.now() - timedelta(days=180)

    for day_offset in range(180):
        current_date = start_date + timedelta(days=day_offset)

        # 每天生成 50-150 个订单
        daily_orders = random.randint(50, 150)

        # 添加一些波动：周末订单更多
        if current_date.weekday() >= 5:  # 周末
            daily_orders = int(daily_orders * 1.5)

        # 添加月度趋势：近几个月增长
        growth_factor = 1 + (day_offset / 180) * 0.3
        daily_orders = int(daily_orders * growth_factor)

        for _ in range(daily_orders):
            region = random.choice(list(regions_cities.keys()))
            city = random.choice(regions_cities[region])

            # 找到该城市的门店
            city_stores = [s for s in stores_data if s[2] == region and s[3] == city]
            store = random.choice(city_stores)

            category = random.choice(list(categories_products.keys()))
            product = random.choice(categories_products[category])

            # 价格根据品类不同
            price_ranges = {
                "电子产品": (200, 12000),
                "服装": (50, 2000),
                "食品": (10, 300),
                "家居": (30, 1500),
                "美妆": (30, 800),
            }
            min_price, max_price = price_ranges[category]
            quantity = random.randint(1, 5)
            unit_price = round(random.uniform(min_price, max_price), 2)
            order_amount = round(unit_price * quantity, 2)
            cost_amount = round(order_amount * random.uniform(0.4, 0.7), 2)

            customer = random.choice(customers_data)
            is_member = random.choice([0, 0, 1, 1, 1])  # 60% 会员

            orders_data.append((
                f"ORD{order_id}",
                current_date.strftime("%Y-%m-%d"),
                region,
                city,
                store[0],  # store_id
                store[1],  # store_name
                random.choice(["online", "offline"]),
                category,
                product,
                quantity,
                order_amount,
                cost_amount,
                customer[0],  # customer_id
                is_member,
            ))
            order_id += 1

    # 批量插入订单
    cursor.executemany(
        """INSERT INTO orders
        (order_id, order_date, region, city, store_id, store_name, channel,
         category, product_name, quantity, order_amount, cost_amount, customer_id, is_member)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        orders_data
    )
    print(f"  - 插入 {len(orders_data)} 个订单")

    # 更新客户统计
    cursor.execute("""
        UPDATE customers SET
            total_orders = (SELECT COUNT(*) FROM orders WHERE orders.customer_id = customers.customer_id),
            total_amount = (SELECT COALESCE(SUM(order_amount), 0) FROM orders WHERE orders.customer_id = customers.customer_id)
    """)

    conn.commit()
    conn.close()

    print(f"[TestDB] 数据库创建完成: {db_path}")
    return db_path


if __name__ == "__main__":
    create_test_database()
