from typing import Annotated

from fastapi import APIRouter, Header

from atguigu.app.dependencies import SessionDep, get_auth_service
from atguigu.app.services.admin import AdminService

router = APIRouter(prefix="/api/v1", tags=["AI Operations"])


@router.get("/admin/metrics")
async def get_admin_metrics(
        session: SessionDep,
        authorization: Annotated[str | None, Header()] = None
) -> dict:
    """返回管理员观测面板使用的 AI Run 汇总指标。"""
    user = get_auth_service().get_authorized_user(
        authorization,
        "admin"
    )
    return await AdminService(session).get_metrics(user)


@router.get("/runs")
async def list_runs(
        session: SessionDep,
        limit: int = 50,
        authorization: Annotated[str | None, Header()] = None
) -> list[dict]:
    """返回最近的 AI Run。"""
    user = get_auth_service().get_authorized_user(
        authorization,
        "admin"
    )
    return await AdminService(session).list_runs(user, limit)


@router.get("/runs/{run_id}")
async def get_run(
        run_id: str,
        session: SessionDep,
        authorization: Annotated[str | None, Header()] = None
) -> dict:
    """返回一个 AI Run 及其工具调用记录。"""
    user = get_auth_service().get_authorized_user(
        authorization,
        "admin"
    )
    return await AdminService(session).get_run(user, run_id)


@router.get("/knowledge/documents")
async def list_policy_documents(authorization: Annotated[str | None, Header()] = None):
    from atguigu.agent.harness.knowledge.service import KnowledgeQueryService
    user = get_auth_service().get_authorized_user(authorization, ("admin", "agent"))
    return [{"id":d["knowledge_id"],"category":d["source_type"],"status":"reference" if d["reference_only"] else "active",**d}
            for d in KnowledgeQueryService().documents(user.tenant_id,user.welfare_project_id)]
