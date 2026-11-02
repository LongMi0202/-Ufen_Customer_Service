from sqlalchemy import select

from atguigu.agent.harness.tools.snapshot import ToolCallSnapshot
from atguigu.common.utils import get_utcnow
from atguigu.infrastructure.db import session_factory
from atguigu.models.models import AgentTask, AgentToolCall


class TaskAudit:
    """Use an independent session per operation: parallel agents never share AsyncSession."""

    async def start(self, *, task_id, run_id, spec, context, attempt=1):
        async with session_factory() as session:
            session.add(AgentTask(id=task_id, run_id=run_id, logical_task_id=spec.task_id,
                domain=spec.domain, skill_code=spec.domain.skill, prompt_version="welfare-domain-v1",
                attempt=attempt, state="RUNNING", depends_on=spec.depends_on, input_context=context))
            await session.commit()

    async def finish(self, task_id, state, result, error, latency_ms, usage):
        async with session_factory() as session:
            task = await session.get(AgentTask, task_id)
            task.state = state
            task.result = result
            task.error = error
            task.latency_ms = latency_ms
            task.finished_at = get_utcnow()
            for name in ("input_tokens", "output_tokens", "model_calls"):
                setattr(task, name, usage.get(name, 0))
            await session.commit()

    async def calls(self, run_id, task_ids):
        if not task_ids:
            return ()
        async with session_factory() as session:
            calls = (await session.scalars(select(AgentToolCall).where(
                AgentToolCall.run_id == run_id, AgentToolCall.task_id.in_(task_ids)
            ).order_by(AgentToolCall.created_at))).all()
            return tuple(ToolCallSnapshot.from_tool_call(call) for call in calls)

    async def supersede(self, task_id):
        async with session_factory() as session:
            task = await session.get(AgentTask, task_id)
            task.state = "SUPERSEDED"
            await session.commit()
