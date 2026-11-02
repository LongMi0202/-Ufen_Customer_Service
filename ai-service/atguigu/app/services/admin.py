from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from atguigu.models.models import AgentRun, AgentToolCall, AgentTask


class AdminService:
    def __init__(self, session: AsyncSession):
        self.session = session

    def scope(self, user):
        return (AgentRun.tenant_id == user.tenant_id, AgentRun.welfare_project_id == user.welfare_project_id)

    async def get_metrics(self, user):
        row = (await self.session.execute(select(func.count(), func.avg(AgentRun.latency_ms),
            func.sum(AgentRun.input_tokens),func.sum(AgentRun.output_tokens)).select_from(AgentRun).where(*self.scope(user)))).one()
        return dict(zip(("agent_runs","average_latency_ms","input_tokens","output_tokens"),[v or 0 for v in row]))

    async def list_runs(self, user, limit=50, mode=None):
        query = select(AgentRun).where(*self.scope(user))
        if mode:
            query = query.where(AgentRun.execution_mode == mode)
        runs=(await self.session.scalars(query.order_by(AgentRun.started_at.desc()).limit(max(1,min(limit,100))))).all()
        return [{"id":r.id,"conversation_id":r.conversation_id,"tenant_id":r.tenant_id,"welfare_project_id":r.welfare_project_id,
                 "execution_mode":r.execution_mode,"route":(r.execution_trace or {}).get("route"),
                 "state":r.state,"model_name":r.model_name,"latency_ms":r.latency_ms,"input_tokens":r.input_tokens,"output_tokens":r.output_tokens} for r in runs]

    async def get_run(self, user, run_id):
        run=await self.session.scalar(select(AgentRun).where(AgentRun.id==run_id,*self.scope(user)))
        if not run:raise HTTPException(404,"授权范围内未找到运行记录")
        calls=(await self.session.scalars(select(AgentToolCall).where(AgentToolCall.run_id==run.id).order_by(AgentToolCall.created_at))).all()
        tasks=(await self.session.scalars(select(AgentTask).where(AgentTask.run_id==run.id).order_by(AgentTask.started_at))).all()
        return {"run":{"id":run.id,"state":run.state,"tenant_id":run.tenant_id,"welfare_project_id":run.welfare_project_id,
                       "execution_mode":run.execution_mode,"execution_trace":run.execution_trace,"latency_ms":run.latency_ms,
                       "prompt_version":run.prompt_version,"outcome":run.result,"input_tokens":run.input_tokens,
                       "output_tokens":run.output_tokens,"error":run.error},
                "tasks":[{"id":t.id,"task_id":t.logical_task_id,"domain":t.domain,"skill_code":t.skill_code,
                          "prompt_version":t.prompt_version,"attempt":t.attempt,"state":t.state,"depends_on":t.depends_on,
                          "input_context":t.input_context,"result":t.result,"error":t.error,"latency_ms":t.latency_ms,
                          "input_tokens":t.input_tokens,"output_tokens":t.output_tokens,"model_calls":t.model_calls} for t in tasks],
                "tool_calls":[{"tool_call_id":c.tool_call_id,"task_id":c.task_id,"model_tool_call_id":c.model_tool_call_id,"tool_name":c.tool_name,"arguments":c.arguments,
                               "result":c.result,"success":c.success,"latency_ms":c.latency_ms} for c in calls]}
