# 本机 Docker Compose

在项目根目录执行命令。项目名固定为 `kefu-hu-gaizao`，使用独立网络和数据卷，不复用旧 `kefu-hu` 项目的数据。

## 配置

- 复用本机已有的 `postgres:16-alpine`、`redis:7-alpine` 和五个 `kefu-hu-*` 应用镜像。`pull_policy: never` 禁止自动拉取或覆盖镜像；本配置不构建镜像。
- 当前源码以只读目录挂载到应用容器，依赖使用镜像中已安装的版本。修改源码后执行 `docker compose restart`；如果新增依赖，需要更新镜像后再运行。
- 根目录 `.env` 提供数据库账号及密码、共享 JWT 密钥和内部服务令牌。首次在其他目录部署时，复制 `.env.example` 为 `.env` 并填写这些值；数据库密码使用 URL 安全字符（例如十六进制字符串）。已有数据卷初始化后，修改环境变量不会自动修改数据库账号或密码。
- 模型配置继续读取 `ai-service/.env` 中的 `LLM_*`。该文件必须存在；修改后执行 `docker compose up -d --force-recreate ai-service`。
- 客服服务的业务参数读取 `customer-service/.env`，该文件也必须存在。Compose 的 `environment` 优先级更高，统一覆盖数据库地址、Redis 地址、服务地址、鉴权和监听地址，原有虚拟机地址不会用于容器通信。
- 三个数据库由 `docker/init-databases.sql` 在新 PostgreSQL 数据卷首次启动时创建；AI 和客服服务启动时建表，电商服务启动时建表并填充演示数据。
- PostgreSQL 和 Redis 仅供容器网络访问，没有映射到 Windows。应用访问 `postgres:5432`、`redis:6379`、`ecommerce:8001` 和 `ai-service:8002`；浏览器使用下表中的本机地址。

## 启动和检查

首次克隆代码时，先准备三个本地配置文件：

```powershell
Copy-Item .env.example .env
Copy-Item ai-service/.env.example ai-service/.env
Copy-Item customer-service/.env.example customer-service/.env
```

仅在对应 `.env` 尚不存在时复制，避免覆盖已有配置。填写根目录共享密钥、数据库密码及 `ai-service/.env` 的模型密钥后再启动。`.env.example` 是占位模板，不包含当前本机密钥。

```powershell
docker compose config --quiet
docker compose up -d --wait --wait-timeout 180
docker compose ps
docker compose logs --tail 100 ai-worker realtime-worker
python docker/check-services.py
```

不要将 `docker compose config` 的完整输出对外分享，其中包含解析后的密码和模型密钥；检查语法使用 `--quiet`。

| 服务 | 地址或账号 |
| --- | --- |
| 用户端 | http://127.0.0.1:5173 ，`user_001` / `user_002` |
| 管理端 | http://127.0.0.1:5174 ，选择 `admin_001` |
| 人工客服端 | http://127.0.0.1:5174 ，选择 `agent_001` |
| 客服 API | http://127.0.0.1:8000/docs |
| 电商 API | http://127.0.0.1:8001/docs |
| AI API | http://127.0.0.1:8002/docs |

API 和前端健康检查验证进程可响应；真实模型查询通过 `tests/check_live_chat.py` 单独验证。AI Worker 与实时消息 Worker 必须同时运行，才能完成消息处理与推送。后台已支持本地政策库查阅和人工退分；文档上传索引与在线评测不在第一版范围。完整业务说明、企业账号和回归命令见根目录 README.md。

## 停止和保留数据

```powershell
docker compose stop
docker compose start
# 移除本项目容器和网络，保留数据卷：
docker compose down
```

数据存储于 `kefu-hu-gaizao_postgres-data` 和 `kefu-hu-gaizao_redis-data`。`docker compose down -v` 会删除本项目的数据卷，日常停止时不要添加 `-v`。
