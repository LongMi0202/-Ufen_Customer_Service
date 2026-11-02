from atguigu.common.event_loop import run_async
from atguigu.infrastructure.db import db_engine
from atguigu.models.models import Base
from sqlalchemy import text


async def init_database() -> None:
    """创建 AI Service 声明的数据库表和索引。"""
    async with db_engine.begin() as connection:
        await connection.execute(text("ALTER TABLE IF EXISTS agent_runs ADD COLUMN IF NOT EXISTS tenant_id VARCHAR(40) NOT NULL DEFAULT 'tenant_a'"))
        await connection.execute(text("ALTER TABLE IF EXISTS agent_runs ADD COLUMN IF NOT EXISTS welfare_project_id VARCHAR(40) NOT NULL DEFAULT 'legacy'"))
        await connection.execute(text("ALTER TABLE IF EXISTS agent_runs ADD COLUMN IF NOT EXISTS execution_mode VARCHAR(10) NOT NULL DEFAULT 'single'"))
        await connection.execute(text("ALTER TABLE IF EXISTS agent_runs ADD COLUMN IF NOT EXISTS execution_trace JSON"))
        await connection.execute(text("ALTER TABLE IF EXISTS agent_tool_calls ADD COLUMN IF NOT EXISTS model_tool_call_id VARCHAR(200)"))
        # Create the referenced task table before adding the nullable foreign key to old tables.
        await connection.run_sync(Base.metadata.create_all)
        await connection.execute(text("ALTER TABLE agent_tool_calls ADD COLUMN IF NOT EXISTS task_id VARCHAR(80) REFERENCES agent_tasks(id)"))
        await connection.execute(text("CREATE INDEX IF NOT EXISTS ix_agent_tool_calls_task_id ON agent_tool_calls(task_id)"))
        await connection.execute(text("CREATE INDEX IF NOT EXISTS ix_agent_runs_execution_mode ON agent_runs(execution_mode)"))


if __name__ == "__main__":
    run_async(init_database())
