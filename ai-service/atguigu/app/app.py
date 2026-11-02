from contextlib import asynccontextmanager
import asyncio

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from atguigu.app.routers import admin, run
from atguigu.agent.factory import create_support_agent
from atguigu.agent.llm.adapter import ModelAdapter
from atguigu.agent.multi.domain import create_domain_agents
from atguigu.common.config import get_settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    调用时机：应用启动前、应用关闭前
    """
    # 初始化资源动作
    app.state.agent = create_support_agent()
    settings = get_settings()
    app.state.multi_model = ModelAdapter.create_model(max_retries=0, max_tokens=settings.multi_model_output_tokens)
    app.state.domain_agents = create_domain_agents(app.state.multi_model)
    app.state.domain_semaphore = asyncio.Semaphore(settings.multi_process_concurrency)

    yield  # 处理路由请求的分界线

    # 释放资源的动作（数据库的资源、后台任务、中间件的资源redis）


app = FastAPI(title="Ecommerce AI Service", lifespan=lifespan)


@app.get("/health", tags=["Health"])
async def health():
    return {"status": "ok", "service": "ai-service", "agent_mode": get_settings().agent_mode}


app.include_router(run.router)
app.include_router(admin.router)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5174",
        "http://127.0.0.1:5174"
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"]
)
