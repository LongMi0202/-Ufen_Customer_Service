from typing import Literal

from pydantic import BaseModel


class CurrentUser(BaseModel):
    user_id: str
    role: Literal["customer", "agent", "admin"] = "customer"
    tenant_id: str
    welfare_project_id: str
