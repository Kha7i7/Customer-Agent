"""初始化数据库：建表 + 写入演示数据（幂等，不删除已有数据）

用法：python scripts/init_db.py
已有演示商户账号时直接跳过，重复执行不会重复写入。
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select, text

from database import Base, SessionLocal, engine
from models import Conversation, Message, Order, OrderItem, Product, Review, Shop, User, UserProfile
from security import hash_password

NOW = datetime.now()

# 旧库增量迁移：新增列（幂等）
MIGRATIONS = [
    "ALTER TABLE shops ADD COLUMN IF NOT EXISTS agent_name VARCHAR(40) DEFAULT '客服小星'",
    "ALTER TABLE shops ADD COLUMN IF NOT EXISTS agent_welcome VARCHAR(200)",
    "ALTER TABLE shops ADD COLUMN IF NOT EXISTS agent_prompt TEXT",
    "ALTER TABLE orders ADD COLUMN IF NOT EXISTS refund_reason VARCHAR(200)",
]


def _migrate() -> None:
    with engine.begin() as conn:
        for sql in MIGRATIONS:
            conn.execute(text(sql))


def main() -> None:
    Base.metadata.create_all(bind=engine)
    _migrate()
    print("表结构就绪")

    with SessionLocal() as db:
        if db.scalar(select(User).where(User.username == "merchant1")) is not None:
            print("演示数据已存在，跳过")
            return

        m1 = User(username="merchant1", password_hash=hash_password("merchant123"),
                  role="merchant", phone="13900000001")
        m2 = User(username="merchant2", password_hash=hash_password("merchant123"),
                  role="merchant", phone="13900000002")
        demo = User(username="demo", password_hash=hash_password("demo123"), role="customer",
                    phone="13700000001", address="浙江省杭州市西湖区文一西路 969 号 3 幢 501")
        alice = User(username="alice", password_hash=hash_password("alice123"), role="customer",
                     phone="13700000002", address="上海市浦东新区张江路 88 号 12 楼")
        db.add_all([m1, m2, demo, alice])
        db.flush()

        shop1 = Shop(owner_id=m1.id, name="星选数码旗舰店", description="官方直营，正品保障，全国联保")
        shop2 = Shop(owner_id=m2.id, name="轻生活家居馆", description="专注提升生活质感的好物")
        db.add_all([shop1, shop2])
        db.flush()

        rows = [
            (shop1, "星选 X1 无线降噪耳机", "数码", "40dB 主动降噪，单次续航 8 小时，支持多设备切换。", 699, 120),
            (shop1, "星选 S3 智能手表", "数码", "1.43 英寸 AMOLED 屏，血氧心率监测，5ATM 防水。", 1299, 60),
            (shop1, "65W 氮化镓充电器", "数码", "双 C 口快充，折叠插脚，可同时给手机和笔记本供电。", 129, 300),
            (shop1, "10000mAh 磁吸充电宝", "数码", "磁吸无线充电，自带支架，可上飞机。", 199, 150),
            (shop2, "抗菌记忆棉枕", "家居", "慢回弹记忆棉，可拆洗抗菌枕套，护颈设计。", 159, 200),
            (shop2, "香薰加湿器", "家居", "4L 大容量，静音雾化，带夜灯与缺水断电保护。", 249, 80),
            (shop2, "全棉四件套", "家居", "60 支长绒棉，亲肤透气，1.5/1.8 米床通用。", 399, 90),
            (shop2, "便携折叠收纳箱", "家居", "可折叠设计，承重 20kg，衣柜与后备箱两用。", 89, 260),
        ]
        products = [
            Product(shop_id=shop.id, name=name, category=cat, description=desc,
                    price=price, stock=stock)
            for shop, name, cat, desc, price, stock in rows
        ]
        db.add_all(products)
        db.flush()

        def add_order(no, user, shop, total, status, items, days=0, hours=0,
                      shipped=None, delivered=None):
            created = NOW - timedelta(days=days, hours=hours)
            order = Order(
                order_no=no, user_id=user.id, shop_id=shop.id, total_amount=total,
                status=status, receiver_name=user.username, receiver_phone=user.phone,
                address=user.address, created_at=created,
                shipped_at=NOW - timedelta(days=shipped) if shipped else None,
                delivered_at=NOW - timedelta(days=delivered) if delivered else None,
            )
            db.add(order)
            db.flush()
            for product, qty in items:
                db.add(OrderItem(order_id=order.id, product_id=product.id,
                                 product_name=product.name, price=product.price, quantity=qty))
                product.stock -= qty
            return order

        order1 = add_order("CS202511100001", demo, shop1, 828, "completed",
                           [(products[0], 1), (products[2], 1)], days=6, shipped=5, delivered=2)
        add_order("CS202511160002", demo, shop2, 249, "paid", [(products[5], 1)], days=1)
        add_order("CS202511150003", alice, shop1, 1299, "shipped", [(products[1], 1)],
                  days=2, shipped=1)
        add_order("CS202511180004", alice, shop2, 478, "pending",
                  [(products[6], 1), (products[7], 1)], hours=3)

        db.add(Review(order_id=order1.id, product_id=products[0].id, user_id=demo.id,
                      rating=5, service_rating=5,
                      content="降噪效果很好，客服回复也很快，查物流很省心。",
                      created_at=NOW - timedelta(days=1)))
        db.add(UserProfile(user_id=demo.id, nickname="小星用户",
                           preferences={"偏好品类": "数码配件", "期望配送": "顺丰"},
                           facts={"常用收货城市": "杭州"}))

        conv = Conversation(session_id="demo-history-0001", user_id=demo.id, shop_id=shop1.id,
                            status="closed", topic="耳机的保修政策",
                            created_at=NOW - timedelta(days=2))
        db.add(conv)
        db.flush()
        db.add_all([
            Message(conversation_id=conv.id, role="user", content="星选 X1 耳机的保修期是多久？",
                    created_at=NOW - timedelta(days=2)),
            Message(conversation_id=conv.id, role="assistant",
                    content="星选 X1 无线降噪耳机提供 12 个月官方质保，自签收之日起计算。"
                            "非人为损坏可免费维修或换新，人为损坏按官方维修价收费。",
                    sources=["01_退换货政策.md"], created_at=NOW - timedelta(days=2)),
            Message(conversation_id=conv.id, role="user", content="好的，那我先看看，有需要再联系你们。",
                    created_at=NOW - timedelta(days=2)),
        ])

        db.commit()

    print("演示数据写入完成：")
    print("  商户   merchant1 / merchant123、merchant2 / merchant123")
    print("  用户   demo / demo123、alice / alice123")


if __name__ == "__main__":
    main()
