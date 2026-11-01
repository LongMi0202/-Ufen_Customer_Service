from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint, CheckConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ecommerce_service.database import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(80))
    membership: Mapped[str] = mapped_column(String(40), default="普通会员")
    risk_level: Mapped[str] = mapped_column(String(20), default="normal")
    tenant_id: Mapped[str] = mapped_column(String(40), default="tenant_a", index=True)


class Product(Base):
    __tablename__ = "products"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(160), index=True)
    category: Mapped[str] = mapped_column(String(80), index=True)
    price: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    stock: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="active")
    description: Mapped[str] = mapped_column(Text)
    specs: Mapped[dict] = mapped_column(JSONB, default=dict)


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    status: Mapped[str] = mapped_column(String(30), index=True)
    total_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    tenant_id: Mapped[str] = mapped_column(String(40), default="tenant_a", index=True)
    welfare_project_id: Mapped[str] = mapped_column(String(40), default="legacy", index=True)
    total_points: Mapped[int] = mapped_column(Integer, default=0)
    payment_type: Mapped[str] = mapped_column(String(30), default="points")
    version: Mapped[int] = mapped_column(Integer, default=1)
    address: Mapped[str] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    items: Mapped[list["OrderItem"]] = relationship(
        back_populates="order", cascade="all, delete-orphan", lazy="selectin"
    )
    logistics: Mapped["Logistics | None"] = relationship(
        back_populates="order", cascade="all, delete-orphan", lazy="selectin"
    )


class OrderItem(Base):
    __tablename__ = "order_items"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), index=True)
    product_id: Mapped[str] = mapped_column(ForeignKey("products.id"))
    product_name: Mapped[str] = mapped_column(String(160))
    quantity: Mapped[int] = mapped_column(Integer)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    order: Mapped[Order] = relationship(back_populates="items")


class Logistics(Base):
    __tablename__ = "logistics"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), unique=True)
    company: Mapped[str] = mapped_column(String(80))
    tracking_number: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(30))
    latest_event: Mapped[str] = mapped_column(String(300))
    events: Mapped[list] = mapped_column(JSONB, default=list)
    order: Mapped[Order] = relationship(back_populates="logistics")


class AfterSale(Base):
    __tablename__ = "after_sales"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(40), index=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), index=True)
    kind: Mapped[str] = mapped_column(String(20))
    reason: Mapped[str] = mapped_column(String(300))
    status: Mapped[str] = mapped_column(String(30), default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Tenant(Base):
    __tablename__ = "tenants"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(100))


class WelfareProject(Base):
    __tablename__ = "welfare_projects"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    policy_version: Mapped[str] = mapped_column(String(40), default="demo-v1")


class ProjectMember(Base):
    __tablename__ = "project_members"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("welfare_projects.id"), primary_key=True)


class ProjectProduct(Base):
    __tablename__ = "project_products"
    project_id: Mapped[str] = mapped_column(ForeignKey("welfare_projects.id"), primary_key=True)
    product_id: Mapped[str] = mapped_column(ForeignKey("products.id"), primary_key=True)
    points_price: Mapped[int] = mapped_column(Integer)
    per_user_limit: Mapped[int] = mapped_column(Integer, default=5)


class PointsAccount(Base):
    __tablename__ = "points_accounts"
    __table_args__ = (UniqueConstraint("user_id", "project_id"), CheckConstraint("balance >= 0"))
    id: Mapped[str] = mapped_column(String(60), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(40), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("welfare_projects.id"), index=True)
    balance: Mapped[int] = mapped_column(Integer, default=0)
    version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(20), default="active")


class PointsBatch(Base):
    __tablename__ = "points_batches"
    __table_args__ = (CheckConstraint("remaining >= 0"),)
    id: Mapped[str] = mapped_column(String(60), primary_key=True)
    account_id: Mapped[str] = mapped_column(ForeignKey("points_accounts.id"), index=True)
    source: Mapped[str] = mapped_column(String(100))
    granted: Mapped[int] = mapped_column(Integer)
    remaining: Mapped[int] = mapped_column(Integer)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class PointsTransaction(Base):
    __tablename__ = "points_transactions"
    id: Mapped[str] = mapped_column(String(60), primary_key=True)
    account_id: Mapped[str] = mapped_column(ForeignKey("points_accounts.id"), index=True)
    kind: Mapped[str] = mapped_column(String(20))
    delta: Mapped[int] = mapped_column(Integer)
    order_id: Mapped[str | None] = mapped_column(String(40), index=True)
    after_sale_id: Mapped[str | None] = mapped_column(String(40), index=True)
    reason: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PointsAllocation(Base):
    __tablename__ = "points_allocations"
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), primary_key=True)
    batch_id: Mapped[str] = mapped_column(ForeignKey("points_batches.id"), primary_key=True)
    points: Mapped[int] = mapped_column(Integer)


class PointsRefund(Base):
    __tablename__ = "points_refunds"
    id: Mapped[str] = mapped_column(String(60), primary_key=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), unique=True)
    after_sale_id: Mapped[str | None] = mapped_column(String(40))
    account_id: Mapped[str] = mapped_column(ForeignKey("points_accounts.id"))
    expected_points: Mapped[int] = mapped_column(Integer)
    returned_points: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(30), default="pending")
    transaction_id: Mapped[str | None] = mapped_column(String(60))
    reason: Mapped[str] = mapped_column(String(200))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class IdempotencyRecord(Base):
    __tablename__ = "idempotency_records"

    key: Mapped[str] = mapped_column(String(160), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(40), index=True)
    operation: Mapped[str] = mapped_column(String(80))
    response: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
