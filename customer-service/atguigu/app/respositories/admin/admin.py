from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from atguigu.models.models import Conversation, Handoff, Message


class AdminMetricsRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_metrics(self, tenant_id: str, project_id: str) -> dict[str, int]:
        filters = (Conversation.tenant_id == tenant_id, Conversation.welfare_project_id == project_id)
        async def count(model, *conditions):
            return await self.session.scalar(select(func.count()).select_from(model).where(*conditions))
        messages = await self.session.scalar(select(func.count(Message.id)).join(Conversation).where(*filters))
        return {
            "conversations": await count(Conversation, *filters), "messages": messages,
            "handoffs": await count(Handoff, Handoff.tenant_id == tenant_id, Handoff.welfare_project_id == project_id),
            "queued": await count(Conversation, *filters, Conversation.mode == "QUEUED"),
            "human": await count(Conversation, *filters, Conversation.mode == "HUMAN"),
        }
