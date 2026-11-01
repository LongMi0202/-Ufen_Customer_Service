from typing import Literal

from pydantic import BaseModel


class CurrentUser(BaseModel):
    user_id: str
    role: Literal["customer", "harness", "admin", "agent"]
    tenant_id: str
    welfare_project_id: str
