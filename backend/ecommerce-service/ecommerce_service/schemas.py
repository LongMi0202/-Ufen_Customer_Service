from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field


class DemoLoginRequest(BaseModel):
    user_id: str = "user_001"
    role: Literal["customer", "agent", "admin"] = "customer"
    welfare_project_id: str | None = None


class RedemptionRequest(BaseModel):
    product_id: str
    quantity: int = Field(default=1, ge=1, le=10)
    address: str = Field(min_length=5, max_length=300)


class AddressUpdate(BaseModel):
    address: str = Field(min_length=5, max_length=300)


class AfterSaleCreate(BaseModel):
    kind: Literal["refund", "return", "exchange"]
    reason: str = Field(min_length=2, max_length=300)


def money(value: Decimal) -> float:
    return float(value)
