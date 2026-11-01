from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from ecommerce_service.common.auth import Principal
from ecommerce_service.models import (Tenant, WelfareProject, ProjectMember, User, Product, ProjectProduct,
    PointsAccount, PointsBatch, PointsTransaction, PointsRefund, Order, Logistics, AfterSale)
from ecommerce_service.welfare import redeem


def seed_welfare_data(db):
    for key, name in [("tenant_a", "华享科技（模拟企业A）"), ("tenant_b", "悦享制造（模拟企业B）")]:
        if not db.get(Tenant, key): db.add(Tenant(id=key, name=name))
    db.flush()
    for key, tenant, name in [("welfare_a", "tenant_a", "年度员工福利"),
                              ("birthday_a", "tenant_a", "生日专属福利"),
                              ("welfare_b", "tenant_b", "季度员工激励")]:
        if not db.get(WelfareProject, key):
            db.add(WelfareProject(id=key, tenant_id=tenant, name=name, policy_version="demo-v1"))
    for key, name, tenant in [("user_003", "陈晨", "tenant_b"), ("agent_002", "客服小林", "tenant_b"),
                               ("admin_002", "企业B管理员", "tenant_b")]:
        if not db.get(User, key): db.add(User(id=key, name=name, membership="企业员工", tenant_id=tenant))
    if not db.get(Product, "PRODUCT_CAKE"):
        db.add(Product(id="PRODUCT_CAKE", name="生日蛋糕礼盒", category="生日福利", price=0, stock=50,
                       status="active", description="模拟生日项目专属实物蛋糕礼盒，配送到家", specs={"规格": "生日礼盒"}))
    db.flush()
    for project, prices in [("welfare_a", {"PRODUCT_001":800, "PRODUCT_002":120, "PRODUCT_003":520, "PRODUCT_004":200, "PRODUCT_005":80}),
                            ("birthday_a", {"PRODUCT_CAKE":520}),
                            ("welfare_b", {"PRODUCT_001":900, "PRODUCT_004":240, "PRODUCT_005":100})]:
        for product_id, price in prices.items():
            if not db.get(ProjectProduct, (project, product_id)):
                db.add(ProjectProduct(project_id=project, product_id=product_id, points_price=price, per_user_limit=5))
    for user in db.query(User).all():
        projects = ["welfare_b"] if user.tenant_id == "tenant_b" else ["welfare_a", "birthday_a"]
        for project in projects:
            if not db.get(ProjectMember, (user.id, project)):
                db.add(ProjectMember(user_id=user.id, project_id=project))
            if user.id.startswith(("agent_", "admin_")): continue
            account_id = f"PA_{user.id}_{project}"
            if db.get(PointsAccount, account_id): continue
            total = 520 if project == "birthday_a" else 8000 if project == "welfare_a" else 3000
            acct = PointsAccount(id=account_id, tenant_id=user.tenant_id, user_id=user.id, project_id=project, balance=total)
            db.add(acct); db.flush()
            lots = [(total, datetime(2028,12,31,15,59,59,tzinfo=UTC))] if project == "birthday_a" else [
                (1000, datetime.now(UTC)+timedelta(days=20)),
                (total-1000, datetime.now(UTC)+timedelta(days=365 if project == "welfare_a" else 90))]
            for index, (amount, expiry) in enumerate(lots):
                db.add(PointsBatch(id=f"PB_{user.id}_{project}_{index}", account_id=account_id,
                                  source="演示福利发放", granted=amount, remaining=amount, expires_at=expiry))
            db.add(PointsTransaction(id=f"PT_GRANT_{user.id}_{project}", account_id=account_id,
                                    kind="grant", delta=total, reason="演示项目首次福利发放"))
    db.flush()
    # The old demo catalogue deliberately contains sold-out examples. Seed only the stock
    # needed for new initial orders, once; ordinary restarts never refill sold stock.
    for product_id in ("PRODUCT_001", "PRODUCT_002", "PRODUCT_003", "PRODUCT_004"):
        if not db.get(Order, {"PRODUCT_001":"ORDER_A_001", "PRODUCT_002":"ORDER_A_002", "PRODUCT_003":"ORDER_A_004", "PRODUCT_004":"ORDER_A_003"}[product_id]):
            product=db.get(Product,product_id)
            product.stock=max(product.stock,20)
            product.status="active"
    db.flush()
    p = Principal(user_id="user_001", role="customer", tenant_id="tenant_a", welfare_project_id="welfare_a")
    for order_id, product_id, status in [("ORDER_A_001","PRODUCT_001","paid"),
                                        ("ORDER_A_002","PRODUCT_002","shipped"),
                                        ("ORDER_A_003","PRODUCT_004","cancelled"),
                                        ("ORDER_A_004","PRODUCT_003","completed")]:
        if db.get(Order, order_id): continue
        order = redeem(db, p, SimpleNamespace(product_id=product_id, quantity=1, address="上海市虹口区演示收货地址"), order_id=order_id)
        order.status = status
        if status == "shipped":
            db.add(Logistics(order_id=order_id, company="演示快递", tracking_number="UFEN20260002",
                             status="in_transit", latest_event="包裹正在配送途中",
                             events=[{"time":datetime.now(UTC).isoformat(),"description":"包裹已发出"}]))
        if order_id in {"ORDER_A_003", "ORDER_A_004"}:
            after_sale_id = "AS_A_001" if order_id == "ORDER_A_004" else None
            if after_sale_id:
                db.add(AfterSale(id=after_sale_id, user_id=p.user_id, order_id=order_id, kind="return",
                                 reason="演示：退货已寄回，等待审核", status="processing"))
            db.add(PointsRefund(id=f"REFUND_{order_id}", order_id=order_id, after_sale_id=after_sale_id,
                                account_id="PA_user_001_welfare_a", expected_points=order.total_points,
                                returned_points=0, status="pending", reason="演示待核对退分，需要人工客服处理"))
    db.commit()
