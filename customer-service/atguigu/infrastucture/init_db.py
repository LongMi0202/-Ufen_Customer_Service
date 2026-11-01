from atguigu.common.event_loop import run_async
from atguigu.infrastucture.db import db_engine
from atguigu.models import models as _models  # noqa: F401
from atguigu.models.models import Base
from sqlalchemy import text


async def init_database() -> None:
    """创建当前服务 ORM 模型声明的数据库表和索引。"""
    async with db_engine.begin() as connection:
        for table in ("conversations", "handoffs"):
            await connection.execute(text(f"ALTER TABLE IF EXISTS {table} ADD COLUMN IF NOT EXISTS tenant_id VARCHAR(40) NOT NULL DEFAULT 'tenant_a'"))
            await connection.execute(text(f"ALTER TABLE IF EXISTS {table} ADD COLUMN IF NOT EXISTS welfare_project_id VARCHAR(40) NOT NULL DEFAULT 'legacy'"))
        await connection.execute(text("DROP INDEX IF EXISTS uq_open_conversation_per_user"))
        await connection.execute(text("DROP INDEX IF EXISTS uq_open_handoff_per_user"))
        await connection.execute(text("ALTER TABLE IF EXISTS messages DROP CONSTRAINT IF EXISTS uq_message_id"))
        await connection.run_sync(Base.metadata.create_all)
        await connection.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_open_conversation_per_user ON conversations(tenant_id,welfare_project_id,user_id) WHERE mode IN ('AI','QUEUED','HUMAN')"))
        await connection.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_open_handoff_per_user ON handoffs(tenant_id,welfare_project_id,user_id) WHERE status IN ('waiting','active')"))
        await connection.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_conversation_message_id ON messages(conversation_id,message_id)"))



if __name__ == "__main__":
    run_async(init_database())
