# Day01：客服服务项目初始化

## 1. 学习目标

### 1.1 任务目标

今天完成客服服务的项目初始化，并打通一条**最小可用链路**：

1. 用 uv 建立 Python 项目，并梳理分层目录；
2. 完成配置管理与异步数据库接入；
3. 启动接口服务；
4. 实现访问令牌认证；
5. 实现“获取当前会话”接口：最小响应模型、服务示例、路由注册与依赖注入。

完成本节后，项目应能启动，认证逻辑可独立使用，会话接口可返回最小示例数据。

---

## 2. 核心概念

动手写代码前，先扫一遍今天会反复出现的几个概念。后面章节只讲它们在本项目中的落点，不再重复解释原理。

### 2.1 访问令牌

访问令牌是一段可校验的身份凭证。本项目使用 **JWT**：把用户信息编码进一段字符串，服务端用密钥校验真伪，无需再查登录态。

前端或内部服务在请求头里携带：

```text
Authorization: Bearer <token>
```

令牌解码后通常得到用户编号和角色。<span style="color:red">客服服务不负责用户登录发号，但要能解析令牌，并按角色决定能否访问接口。</span>今天的认证模块围绕这件事展开。

### 2.2 事件循环

Python 异步程序依赖**事件循环**调度协程。<span style="color:red">Windows 默认事件循环与异步数据库驱动不完全兼容</span>，直接启动协程访问 PostgreSQL 可能出错。

因此项目统一创建选择器事件循环：

```python
asyncio.SelectorEventLoop(selectors.SelectSelector())
```

封装成统一启动方法后，服务入口和建表命令都走同一套方式，保证异步数据库可在 Windows 下稳定工作。

### 2.3 服务启动器

接口服务需要 ASGI 服务器承接 HTTP 请求。今天不使用命令行一键启动，而是在代码里创建服务启动器：

```python
uvicorn.Server(uvicorn.Config(...))
```

关键点是 <span style="color:red">`loop="none"`</span>：让服务器复用当前已创建的事件循环，而不是自己再开一套。这样“事件循环兼容”和“服务启动”才能接到一起。

### 2.4 配置与注入

- 配置映射：把环境文件映射成配置对象
- 依赖注入：路由按需获取认证服务、会话服务

后面进入项目结构时，可以按“配置 → 数据库 → 启动 → 认证 → 会话”的顺序，对照这些概念落在哪些文件。

---

## 3. 目录结构

带着上面的概念看代码组织。客服服务按“**入口 → 应用层 → 公共能力 → 基础设施 → 模型**”分层。先看整体，再看各层职责。

### 3.1 整体结构

```text
customer-service/
├── .env
├── pyproject.toml
└── atguigu/
    ├── main.py                 # 进程启动入口
    ├── common/                 # 配置与通用工具
    ├── infrastucture/          # 数据库等基础设施(后续完善)
    ├── models/                 # 数据模型
    └── app/
        ├── app.py              # 应用实例
        ├── dependencies.py     # 依赖注入组装
        ├── schemas/            # 请求与响应模型
        ├── services/           # 业务服务
        ├── routers/            # 接口路由
        └── repositories/       # 数据访问（后续完善）
```

### 3.2 各层职责

- `common`：配置读取、事件循环等跨层工具
- `infrastucture`：数据库引擎、会话工厂、建表命令
- `models`：表结构定义
- `app/schemas`：接口输入输出结构
- `app/services`：业务规则与流程编排
- `app/routers`：接口入口
- `app/dependencies`：把服务组装给路由使用
- `main.py`：统一启动接口进程

请求处理顺序通常是：

```text
路由 → 服务 → 仓储/数据库
```

今天先完成**路由、服务、认证与基础设施**，仓储层后续再补。

---

## 4. 配置管理

服务启动前先加载配置，避免在代码中散落硬编码。

### 4.1 配置模型

`atguigu/common/config.py` 定义配置类，主要字段包括：

- 数据库连接
- 令牌密钥与算法
- 服务监听地址
- 前端跨域来源
- 后续能力预留项

配置类通过项目根目录的**绝对路径**读取 `.env`，未识别字段忽略。<span style="color:red">这样无论从哪个目录启动，都能找到配置文件。</span>

### 4.2 环境变量

当前本地配置示例：

```env
DATABASE_URL=postgresql+psycopg://customer_service:customer_service@127.0.0.1:5432/customer_service
JWT_SECRET=ecommerce-secret
JWT_ALGORITHM=HS256
API_HOST=0.0.0.0
API_PORT=8000
CORS_ORIGINS=["http://localhost:5173","http://127.0.0.1:5173","http://localhost:5174","http://127.0.0.1:5174"]
```

<span style="color:red">注意：令牌密钥必须与电商服务、AI 服务保持一致，否则跨服务令牌无法互认。</span>

### 4.3 配置读取

```python
ENV_FILE = Path(__file__).resolve().parents[2] / ".env"

model_config = SettingsConfigDict(env_file=ENV_FILE, extra="ignore")

@lru_cache
def get_settings() -> Settings:
    return Settings()
```

`ENV_FILE` 指向项目根目录下的 `.env`，**不依赖当前工作目录**。`get_settings()` 带缓存，进程内复用同一实例。认证服务、数据库引擎和启动入口都通过它读取配置。

---

## 5. 数据库接入

配置就绪后，接入 PostgreSQL。客服服务使用异步数据访问，因此还要接上前面的事件循环方案。

### 5.1 引擎与会话

`atguigu/infrastucture/db.py` 创建：

- 异步引擎
- 异步会话工厂
- 模型基类

```python
db_engine = create_async_engine(
    get_settings().database_url,
    pool_pre_ping=True,
)
SessionFactory = async_sessionmaker(
    bind=db_engine,
    class_=AsyncSession,
    expire_on_commit=False,
)
```

连接池预检用于减少连接失效后的请求失败。

### 5.2 会话获取

所有数据模型继承统一基类。请求级数据库访问通过会话获取函数提供：

```python
class Base(DeclarativeBase):
    """ORM 数据库模型的声明性基类。

    所有业务实体的 SQLAlchemy 模型均继承此类。
    """

    pass

async def get_db():
    async with SessionFactory() as db:
        try:
            yield db
        except Exception:
            await db.rollback()
            raise
```

发生异常时回滚，避免脏事务残留。

### 5.3 事件循环接入

第 2 章的事件循环，在项目里落在 `common/event_loop.py`

```python
def run_async(coroutine):
    return asyncio.run(
        coroutine,
        loop_factory=lambda: asyncio.SelectorEventLoop(
            selectors.SelectSelector()
        ),
    )
```

建表命令通过它执行；下一章启动接口时也会复用。

### 5.4 建表命令

`atguigu/infrastucture/init_db.py` 负责按模型创建表

执行流程：

1. 导入模型模块，让表定义注册到元数据
2. 按元数据创建表结构

当前模型文件仍为空。命令可以执行，但还不会创建业务表；表结构随后续课程补齐。

---

## 6. 应用入口

基础设施准备好后，把接口服务跑起来。

### 6.1 应用实例

`atguigu/app/app.py` 创建应用实例，并注册路由与跨域：

```python
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from atguigu.app.routers.chat import conversation
from atguigu.common.config import get_settings

app = FastAPI(description="电商智能客服")
app.include_router(conversation.router)

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

**跨域**：浏览器不允许前端页面随便请求另一个源（协议/域名/端口不同）的接口。  
**解决**：在后端加 CORS 中间件，把允许访问的前端地址写进 `cors_origins`。

另外三项含义：

- **`allow_credentials=True`**：允许携带 Cookie、Authorization 等凭证
- **`allow_methods=["*"]`**：允许前端使用的所有 HTTP 方法
- **`allow_headers=["*"]`**：允许前端携带的所有请求头

### 6.2 进程启动

`atguigu/main.py` 把第 2 章的服务启动器落到启动流程里：

```python
server = uvicorn.Server(
    uvicorn.Config(
        "atguigu.app.app:app",
        host=settings.api_host,
        port=settings.api_port,
        loop="none",
    )
)
await server.serve()
```

外层仍用统一异步启动方法，因此接口进程和数据库初始化<span style="color:red">共用同一套事件循环策略</span>。

### 6.3 启动验证

服务启动后访问：

```text
http://127.0.0.1:8000/docs
```

能打开接口文档页面，说明入口链路已通。接下来实现认证，并挂上第一个业务接口。

---

## 7. 认证模块

服务能启动后，下一步让接口认得“谁在调用”。认证模块按“**当前用户 → 认证服务 → 依赖注入**”组织，落实第 2 章的访问令牌概念。

### 7.1 当前用户

`atguigu/app/schemas/auth.py` 定义令牌解码后的用户结构：

```python
class CurrentUser(BaseModel):
    user_id: str
    role: Literal["customer", "agent", "admin"] = "customer"
```

三类角色：

- 普通用户：浏览商品、管理自己的订单并咨询客服
- 人工客服：接入转人工工单并回复用户
- 管理员：除客服能力外，还可接管工单、维护知识库和查看系统指标

### 7.2 认证服务

`atguigu/app/services/auth.py` 封装认证能力。接口请求进入后，按以下流程完成身份校验：

```mermaid
flowchart LR
    Header["读取请求头"]
    Extract{"令牌格式正确"}
    Unauthorized["返回未授权"]
    Decode["解析并校验令牌"]
    Valid{"令牌有效"}
    CurrentUser["得到当前用户"]
    RoleCheck{"角色符合要求"}
    Forbidden["返回无权限"]
    Pass["放行业务处理"]

    Header --> Extract
    Extract -->|"否"| Unauthorized
    Extract -->|"是"| Decode
    Decode --> Valid
    Valid -->|"否"| Unauthorized
    Valid -->|"是"| CurrentUser
    CurrentUser --> RoleCheck
    RoleCheck -->|"否"| Forbidden
    RoleCheck -->|"是"| Pass
```

另外，认证服务还提供签发短期访问令牌的能力，供内部调用使用。会话接口会要求普通用户身份，<span style="color:red">确保只有客户能打开自己的当前会话</span>。

### 7.3 依赖注入

依赖文件中注册认证服务获取方式，路由按需注入，避免在每个接口里手动创建实例。

至此，认证能力已经可被路由复用。下一步用它保护会话接口。

---

## 8. 会话模块

今天的第一条业务接口是“**获取当前会话**”。实现顺序采用总分总：先定最小响应模型，再写服务与路由，最后注册并验证。

这里先把“会话”理解成：用户和客服系统之间的一次聊天容器。

### 8.1 会话模型

 使用**最小响应模型**：

```python
class CurrentConversationResponse(BaseModel):
    id: str
    mode: str = "AI"
    processing: dict | None = None
```

字段含义：

- `id`：会话编号
- `mode`：当前由谁处理，先用普通字符串，默认 `"AI"`
- `processing`：是否有正在进行的处理；没有时为 `None`

后续课程再把 `mode` 收成枚举，把 `processing` 收成正式结构。今天先用简单类型把接口跑通。

### 8.2 会话服务

`atguigu/app/services/chat/conversation.py` 负责会话编排。

```python
async def get_current_conversation(self, user_id: str) -> dict[str, Any]:
    # 1. 获取或创建当前有效会话 TODO
    # 2. 组装会话响应 TODO
    # 3. 提交会话状态变化 TODO
    return {
        "id": f"demo-conversation-{user_id}",
        "mode": "AI",
        "processing": None,
    }
```

### 8.3 会话路由

`atguigu/app/routers/chat/conversation.py` 定义获取当前会话接口。请求进入后按以下流程处理：

```mermaid
flowchart LR
    Request["接收获取当前会话请求"]
    Auth["调用认证服务校验普通用户"]
    AuthOk{"身份校验通过"}
    Reject["返回未授权或无权限"]
    UserId["取出当前用户编号"]
    Service["调用会话服务"]
    Response["返回最小会话响应"]

    Request --> Auth
    Auth --> AuthOk
    AuthOk -->|"否"| Reject
    AuthOk -->|"是"| UserId
    UserId --> Service
    Service --> Response
```

对应代码：

```python
@router.post(
    "/conversations/current",
    response_model=CurrentConversationResponse,
)
async def get_current_conversation(
    auth_service: AuthServiceDep,
    conversation_service: ConversationServiceDep,
    authorization: Annotated[str | None, Header()] = None,
):
    current_user = auth_service.get_authorized_user(authorization, "customer")
    return await conversation_service.get_current_conversation(
        current_user.user_id
    )
```

### 8.4 注册与验证

在应用实例中注册会话路由，并在依赖文件中提供会话服务注入。

验证方式：

1. 启动服务
2. 打开接口文档，确认出现获取当前会话接口
3. 使用电商服务签发的普通用户令牌调用接口

预期结果：

- 认证失败：返回未授权或无权限
- 认证成功：返回类似下面的数据

```json
{
  "id": "demo-conversation-user_001",
  "mode": "AI",
  "processing": null
}
```

<span style="color:red">今天目标是打通“认证 → 服务 → 最小响应”，不是完成会话持久化。</span>