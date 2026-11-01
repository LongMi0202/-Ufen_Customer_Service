from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class ToolFailureType(StrEnum):
    """定义工具未返回成功结果的原因类型"""

    BUSINESS = "BUSINESS"   #  ToolResult 的success：False(失败成功) 的success：True(成功的成功)
    SERVICE_CALL = "SERVICE_CALL"
    CONTRACT = "CONTRACT"


class ToolFailureCode(StrEnum):
    """定义工具执行层生成的稳定失败结果码"""

    TOOL_CALL_FAILED = "TOOL_CALL_FAILED"
    INVALID_TOOL_RESULT = "INVALID_TOOL_RESULT"


class ToolResult[ResultData](BaseModel):
    """定义业务工具返回给 Agent 的统一结果"""

    success: bool
    code: str
    message: str
    data: ResultData | None = None
    failure_type: ToolFailureType | None = None
    evidence_id: str | None = None


class ProductData(BaseModel):
    """定义 Agent 查询商品需要的数据"""

    id: str
    name: str
    category: str
    price: Decimal = Field(ge=0)
    status: str
    description: str
    specs: dict[str, Any]
    points_price: int = Field(ge=0)
    welfare_project_id: str
    tenant_id: str
    per_user_limit: int


class ProductStockData(BaseModel):
    """定义 Agent 查询商品实时库存需要的数据"""

    product_id: str
    stock: int = Field(ge=0)
    status: str


class OrderItemData(BaseModel):
    """定义 Agent 回答订单商品问题需要的数据"""

    product_id: str
    product_name: str
    quantity: int = Field(gt=0)
    unit_price: Decimal = Field(ge=0)


class OrderData(BaseModel):
    """定义 Agent 查询和校验订单需要的数据"""

    id: str
    status: str
    total_amount: Decimal = Field(ge=0)
    created_at: datetime
    items: list[OrderItemData]
    total_points: int = Field(ge=0)
    tenant_id: str
    welfare_project_id: str
    version: int
    payment_type: str
    refund: dict[str, Any]


class PointsAccountData(BaseModel):
    account_id: str
    tenant_id: str
    welfare_project_id: str
    user_id: str
    available_points: int = Field(ge=0)
    ledger_balance: int = Field(ge=0)
    expired_unsettled_points: int = Field(ge=0)
    expiring_points: int = Field(ge=0)
    status: str
    version: int
    as_of: datetime


class PointsTransactionData(BaseModel):
    id: str
    account_id: str
    kind: str
    delta: int
    order_id: str | None
    after_sale_id: str | None
    reason: str
    created_at: datetime


class ExpiringPointsData(BaseModel):
    account_id: str
    days: int
    total_points: int = Field(ge=0)
    batches: list[dict[str, Any]]
    as_of: datetime


class PointsRefundData(BaseModel):
    order_id: str
    status: str
    expected_points: int = Field(ge=0)
    returned_points: int = Field(ge=0)
    expired_points: int = Field(ge=0)
    usable_returned_points: int = Field(ge=0)
    after_sale_id: str | None
    transaction_id: str | None
    reason: str
    as_of: datetime


class RedemptionQuoteData(BaseModel):
    product_id: str
    account_id: str
    quantity: int
    required_points: int = Field(ge=0)
    available_points: int = Field(ge=0)
    eligible: bool
    reasons: list[str]
    account_version: int
    stock: int
    tenant_id: str
    welfare_project_id: str
    policy_version: str
    as_of: datetime
    notice: str


class LogisticsEventData(BaseModel):
    """定义物流轨迹"""

    time: str
    description: str


class LogisticsData(BaseModel):
    """定义 Agent 查询订单物流需要的数据"""

    order_id: str
    company: str
    tracking_number: str
    status: str
    latest_event: str
    events: list[LogisticsEventData]


class AfterSaleData(BaseModel):
    """定义 Agent 查询售后记录需要的数据"""

    id: str
    order_id: str
    kind: str
    reason: str
    status: str
    created_at: datetime
