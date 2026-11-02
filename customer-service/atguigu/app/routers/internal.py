from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from atguigu.app.services.admin.auth import AuthService
from atguigu.common.config import get_settings
from atguigu.common.utils import get_utcnow
from atguigu.infrastucture.db import get_db_session
from atguigu.models.models import Conversation, ConversationTurn

router = APIRouter(prefix="/internal/v1", tags=["Turn validity"])


class ValidityRequest(BaseModel):
    conversation_id: str
    snapshot_revision: int = Field(ge=1)
    turn_attempt: int = Field(ge=1)


@router.post("/turns/{turn_id}/validity")
async def turn_validity(turn_id: str, body: ValidityRequest,
        session: Annotated[AsyncSession, Depends(get_db_session)],
        authorization: Annotated[str | None, Header()] = None,
        internal_token: Annotated[str | None, Header(alias="X-Internal-Service-Token")] = None):
    if internal_token != get_settings().internal_service_token:
        raise HTTPException(403, "内部服务未授权")
    user = AuthService().get_authorized_user(authorization, "customer")
    row = (await session.execute(select(ConversationTurn, Conversation).join(
        Conversation, Conversation.id == ConversationTurn.conversation_id).where(
        ConversationTurn.id == turn_id, Conversation.id == body.conversation_id,
        Conversation.user_id == user.user_id, Conversation.tenant_id == user.tenant_id,
        Conversation.welfare_project_id == user.welfare_project_id))).one_or_none()
    if row is None:
        raise HTTPException(404, "授权范围内未找到轮次")
    turn, conversation = row
    return {"valid": turn.status == "RUNNING" and conversation.mode == "AI"
        and turn.snapshot_revision == body.snapshot_revision == conversation.input_revision
        and turn.attempts == body.turn_attempt
        and turn.locked_until is not None and turn.locked_until > get_utcnow()}
