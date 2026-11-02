# Gitee 与单／多 Agent 版本管理

## 当前安排

本项目使用一个 Git 仓库管理源码。已固化单 Agent 版本，并在 `feature/multi-agent` 实现单／多 Agent 可切换运行，具体见[多Agent实现与运行验收.md](多Agent实现与运行验收.md)。

远程仓库：[Gitee / ufen_customer_service](https://gitee.com/shaolongmi/ufen_-customer_-service)。

- `main`：可验证的项目代码；后续合并多 Agent 后，同时保留两种执行模式。
- `single-agent-v1`：当前单 Agent 初版的固定标签，包含源码、工具、知识库、测试及架构文档。
- `feature/multi-agent`：多 Agent 开发与验收分支，保留单 Agent 的可运行入口。

已加入 `agent/single/`、`agent/multi/` 与 `AGENT_MODE=single/multi`。Docker Compose 默认 `multi`，根目录 `.env` 可切换；历史单 Agent 工厂和公共 Harness 继续保留。Git 标签固定历史代码，功能分支中两种架构可持续改进。

## Gitee 仓库准备

建议创建私有空仓库，选择不初始化 README、.gitignore 或 License。现有本地项目已经包含这些配置中的必要文件。

将仓库 SSH 或 HTTPS 克隆地址提供给开发者即可，不在聊天中提供密码、访问令牌或私钥。HTTPS 认证由本机 Git 凭据管理器处理；SSH 需要本机密钥与 Gitee 账户中的公钥匹配。两种方式任选其一。

如果远程已经有提交，先读取远程历史，再决定克隆、合并或建立独立分支；不直接覆盖或强制推送。

在远程地址配置且认证可用后，首次同步主分支、固定标签和开发分支：

```powershell
git push -u origin main
git push origin single-agent-v1
git push -u origin feature/multi-agent
```

实际分支与同步状态以 `git branch -vv` 和远程仓库页面为准。

## 日常开发与查阅

常用只读命令：

```powershell
git status
git log --oneline --decorate -10
git show single-agent-v1:ai-service/atguigu/agent/factory.py
git diff single-agent-v1 -- ai-service/atguigu/agent
```

查旧文件可以使用 `git show`，不需要切换或覆盖正在修改的工作目录。确需同时运行不同历史版本时，再考虑 `git worktree` 并配置独立端口及业务数据。

开发新功能使用功能分支；通过回归后合并主分支。不要长期把两种架构拆成两个独立仓库，否则公共业务修复、知识规则和工具变更容易不同步。

## 配置与数据的边界

实际 `.env`、虚拟环境、node_modules、缓存、日志和 tmp 已排除；配置模板 `.env.example` 保留在版本中。

新克隆目录启动前，按 Docker 文档创建根目录、AI 服务与客服服务的 `.env`。根目录共享密钥使用本地新值，模型密钥仅填写到本地 AI 服务配置。当前工作目录已有的实际配置不会因 Git 初始化而修改。

Git 保存源码和知识规则，不保存 Docker 镜像、PostgreSQL/Redis 数据卷或当前数据库中的积分、订单及聊天记录。需要迁移运行数据时另行备份；固定源码标签不等于固定运行数据快照。

## 架构对照评测

多 Agent 功能完成后，优先在同一个 Git 提交下分别运行 single 和 multi 模式；在固定模型、工具、知识和业务快照上执行同一用例集。

每次结果记录提交号、执行模式、用例及数据版本、模型参数、各角色 Prompt/Skill 版本、知识版本、调用次数、全链路 Token、总耗时、成功状态及错误原因。

主控、领域 Agent、路由、汇总、纠错和失败重试都计入消耗；并行请求耗时按实际请求开始到最终结果完成的墙钟时间统计。评测报告同时列任务成功率、单领域/复合问题延迟和单位成功任务成本。
