"""Scoped points ledger and deterministic redemption/refund rules (demo-v1)."""
from datetime import UTC, datetime, timedelta
from hashlib import sha256
import json
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select, text, func

from ecommerce_service.models import (
    Order, OrderItem, Product, ProjectProduct, WelfareProject, PointsAccount,
    PointsBatch, PointsTransaction, PointsAllocation, PointsRefund, IdempotencyRecord,
)
from ecommerce_service.common.auth import Principal
from zoneinfo import ZoneInfo


def local_time(value):
    return value.astimezone(ZoneInfo("Asia/Shanghai")).isoformat()


def uid(prefix):
    return f"{prefix}_{uuid4().hex[:16].upper()}"


def scope(model, principal):
    return (model.tenant_id == principal.tenant_id, model.welfare_project_id == principal.welfare_project_id)


def owned_order(db, order_id, principal, lock=False):
    statement = select(Order).where(Order.id == order_id, *scope(Order, principal))
    if principal.role == "customer":
        statement = statement.where(Order.user_id == principal.user_id)
    if lock:
        statement = statement.with_for_update(of=Order)
    order = db.scalar(statement)
    if order is None:
        raise HTTPException(404, "当前企业、福利项目及用户范围内未找到该订单")
    return order


def account(db, principal, *, user_id=None, lock=False):
    statement = select(PointsAccount).where(
        PointsAccount.tenant_id == principal.tenant_id,
        PointsAccount.project_id == principal.welfare_project_id,
        PointsAccount.user_id == (user_id or principal.user_id),
    )
    if lock:
        statement = statement.with_for_update()
    result = db.scalar(statement)
    if result is None:
        raise HTTPException(404, "当前授权范围内未找到积分账户")
    return result


def account_payload(db, value):
    batches = db.scalars(select(PointsBatch).where(PointsBatch.account_id == value.id)).all()
    now = datetime.now(UTC)
    return {
        "account_id": value.id, "tenant_id": value.tenant_id, "welfare_project_id": value.project_id,
        "user_id": value.user_id, "available_points": sum(b.remaining for b in batches if b.expires_at > now),
        "ledger_balance": value.balance, "expired_unsettled_points": sum(b.remaining for b in batches if b.expires_at <= now),
        "expiring_points": sum(b.remaining for b in batches if now < b.expires_at <= now + timedelta(days=30)),
        "status": value.status, "version": value.version, "as_of": local_time(now),
    }


def project_product(db, product_id, principal):
    product = db.get(Product, product_id)
    config = db.get(ProjectProduct, (principal.welfare_project_id, product_id))
    if not product or not config:
        raise HTTPException(404, "当前福利项目不包含该商品")
    return product, config


def product_payload(db, product, principal):
    _, config = project_product(db, product.id, principal)
    return {"id": product.id, "name": product.name, "category": product.category,
            "price": float(product.price), "points_price": config.points_price, "stock": product.stock,
            "status": product.status, "description": product.description, "specs": product.specs,
            "product_type": "physical", "per_user_limit": config.per_user_limit,
            "tenant_id": principal.tenant_id, "welfare_project_id": principal.welfare_project_id}


def refund_payload(db, order):
    refund = db.scalar(select(PointsRefund).where(PointsRefund.order_id == order.id))
    if not refund:
        return {"order_id": order.id, "status": "not_requested", "expected_points": 0,
                "returned_points": 0, "expired_points": 0, "usable_returned_points": 0,
                "after_sale_id": None, "transaction_id": None,
                "reason": "尚未发起退分", "as_of": local_time(datetime.now(UTC))}
    expired = db.scalar(select(func.coalesce(func.sum(-PointsTransaction.delta),0)).where(
        PointsTransaction.order_id == order.id, PointsTransaction.kind == "expire"))
    return {"refund_id": refund.id, "order_id": order.id, "after_sale_id": refund.after_sale_id,
            "account_id": refund.account_id, "expected_points": refund.expected_points,
            "returned_points": refund.returned_points, "status": refund.status,
            "expired_points": expired, "usable_returned_points": refund.returned_points - expired,
            "transaction_id": refund.transaction_id, "reason": refund.reason,
            "as_of": local_time(refund.updated_at)}


def order_payload(db, order):
    return {"id": order.id, "user_id": order.user_id, "tenant_id": order.tenant_id,
            "welfare_project_id": order.welfare_project_id, "status": order.status,
            "status_label": {"paid": "已支付", "pending_shipment": "待发货", "shipped": "已发货",
                             "completed": "已完成", "cancelled": "已取消", "refunded": "已退款"}.get(order.status, order.status),
            "total_amount": float(order.total_amount), "total_points": order.total_points,
            "payment_type": order.payment_type, "address": order.address,
            "created_at": local_time(order.created_at), "version": order.version,
            "refund": refund_payload(db, order),
            "items": [{"product_id": i.product_id, "product_name": i.product_name, "quantity": i.quantity,
                       "unit_price": float(i.unit_price), "unit_points": order.total_points // max(sum(x.quantity for x in order.items), 1)} for i in order.items]}


def quote_redemption(db, principal, product_id, quantity):
    product, config = project_product(db, product_id, principal)
    points_account = account(db, principal)
    balance = account_payload(db, points_account)["available_points"]
    consumed = db.scalar(select(func.coalesce(func.sum(OrderItem.quantity), 0)).join(Order).where(
        *scope(Order, principal), Order.user_id == principal.user_id,
        Order.status.not_in(["cancelled", "refunded"]), OrderItem.product_id == product_id,
    ))
    reasons = []
    required = config.points_price * quantity
    if points_account.status != "active": reasons.append("积分账户当前不可用")
    if product.status != "active": reasons.append("商品当前不可兑换")
    if product.stock < quantity: reasons.append("商品库存不足")
    if balance < required: reasons.append("可用积分不足")
    if consumed + quantity > config.per_user_limit: reasons.append("超过当前福利项目每人兑换限额")
    return {"product_id": product_id, "quantity": quantity, "account_id": points_account.id,
            "required_points": required, "available_points": balance, "eligible": not reasons,
            "reasons": reasons, "account_version": points_account.version, "stock": product.stock,
            "tenant_id": principal.tenant_id, "welfare_project_id": principal.welfare_project_id,
            "policy_version": db.get(WelfareProject, principal.welfare_project_id).policy_version,
            "as_of": local_time(datetime.now(UTC)), "notice": "预检不扣积分、不锁库存，提交兑换时重新校验"}


def begin_operation(db, principal, key, operation, payload):
    if not key or len(key) > 160:
        raise HTTPException(400, "写操作必须提供不超过160字符的 Idempotency-Key")
    digest = sha256(f"{principal.tenant_id}:{principal.welfare_project_id}:{principal.user_id}:{key}".encode()).hexdigest()
    # Serialize duplicate keys across requests and processes, including before inserting the record.
    db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"), {"key": digest})
    fingerprint = sha256(json.dumps([operation, payload], sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    previous = db.get(IdempotencyRecord, digest)
    if previous and previous.operation != fingerprint:
        raise HTTPException(409, "同一幂等键不能用于不同操作或参数")
    return digest, fingerprint, previous.response if previous else None


def finish_operation(db, principal, digest, fingerprint, result):
    db.add(IdempotencyRecord(key=digest, user_id=principal.user_id, operation=fingerprint, response=result))
    db.commit()
    return result


def redeem(db, principal, request, *, order_id=None):
    acct = account(db, principal, lock=True)
    db.scalar(select(Product).where(Product.id == request.product_id).with_for_update())
    quote = quote_redemption(db, principal, request.product_id, request.quantity)
    if not quote["eligible"]:
        raise HTTPException(409, "；".join(quote["reasons"]))
    product, _ = project_product(db, request.product_id, principal)
    order = Order(id=order_id or uid("ORDER"), user_id=principal.user_id, tenant_id=principal.tenant_id,
                  welfare_project_id=principal.welfare_project_id, status="paid", total_amount=0,
                  total_points=quote["required_points"], payment_type="points", address=request.address,
                  items=[OrderItem(product_id=product.id, product_name=product.name,
                                   quantity=request.quantity, unit_price=0)])
    db.add(order); db.flush()
    batches = db.scalars(select(PointsBatch).where(PointsBatch.account_id == acct.id,
        PointsBatch.remaining > 0, PointsBatch.expires_at > datetime.now(UTC)).order_by(PointsBatch.expires_at, PointsBatch.id).with_for_update()).all()
    remaining = order.total_points
    for batch in batches:
        used = min(batch.remaining, remaining)
        if used:
            batch.remaining -= used
            db.add(PointsAllocation(order_id=order.id, batch_id=batch.id, points=used))
            remaining -= used
    if remaining: raise HTTPException(409, "积分状态发生变化，请重新查询")
    acct.balance -= order.total_points; acct.version += 1; product.stock -= request.quantity
    db.add(PointsTransaction(id=uid("PT"), account_id=acct.id, kind="spend", delta=-order.total_points,
                            order_id=order.id, reason="员工确认积分兑换"))
    db.flush()
    return order


def refund_order(db, order, *, after_sale_id=None):
    """One full refund per order; retain original batch expiry and record expired returns."""
    principal = Principal(tenant_id=order.tenant_id, welfare_project_id=order.welfare_project_id,
                          user_id=order.user_id, role="customer")
    acct = account(db, principal, lock=True)
    refund = db.scalar(select(PointsRefund).where(PointsRefund.order_id == order.id).with_for_update())
    if refund and refund.status == "completed": return refund
    if not refund:
        refund = PointsRefund(id=uid("REFUND"), order_id=order.id, account_id=acct.id,
                              expected_points=order.total_points, reason="退分处理中")
        db.add(refund)
    allocations = db.scalars(select(PointsAllocation).where(PointsAllocation.order_id == order.id)).all()
    if sum(a.points for a in allocations) != order.total_points:
        raise HTTPException(409, "订单积分分配证据不完整，需要人工核对")
    expired = 0
    for allocation in allocations:
        batch = db.get(PointsBatch, allocation.batch_id)
        if batch.expires_at > datetime.now(UTC): batch.remaining += allocation.points
        else: expired += allocation.points
    tx_id = uid("PT")
    db.add(PointsTransaction(id=tx_id, account_id=acct.id, kind="refund", delta=order.total_points,
                            order_id=order.id, after_sale_id=after_sale_id, reason="退回原积分账户及批次"))
    if expired:
        db.add(PointsTransaction(id=uid("PT"), account_id=acct.id, kind="expire", delta=-expired,
                                order_id=order.id, after_sale_id=after_sale_id, reason="原批次已过期，按演示规则退回后失效"))
    acct.balance += order.total_points - expired; acct.version += 1
    refund.returned_points = order.total_points; refund.status = "completed"; refund.transaction_id = tx_id
    refund.after_sale_id = after_sale_id; refund.updated_at = datetime.now(UTC)
    refund.reason = f"已退回原账户；其中原批次过期失效 {expired} 积分" if expired else "已退回原账户，沿用原批次有效期"
    for item in order.items:
        product = db.scalar(select(Product).where(Product.id == item.product_id).with_for_update())
        product.stock += item.quantity
    db.flush()
    return refund
