# 多 Agent 初版：实现、运行与验收

更新日期：2026-11-02。已按审核方案实现可切换的单／多 Agent，当前 Docker Compose 默认运行 `multi`。这是本地模拟福利商城的初版实现。

## 运行入口和模式切换

- 员工端：<http://127.0.0.1:5173/chat>，选择员工与福利项目后提问。
- 管理端：<http://127.0.0.1:5174>，选择管理员，进入监控指标，可筛选单／多智能体、展开 Run、领域任务和工具证据。
- 人工客服端：同一个 5174 页面切换到客服账号，进入人工工作台。
- AI 健康检查：<http://127.0.0.1:8002/health>，`agent_mode` 表示普通聊天请求使用的默认模式。

继续复用现有镜像、数据库和数据卷：

```powershell
docker compose up -d
docker compose ps
```

在**根目录** `.env` 中设置 `AGENT_MODE=single` 或 `AGENT_MODE=multi`，再重建 AI 服务容器即可切换。不会重建业务数据库：

```powershell
docker compose up -d --force-recreate ai-service
```

也可在 PowerShell 临时指定模式：

```powershell
$env:AGENT_MODE = "single"
docker compose up -d --force-recreate ai-service
```

恢复多 Agent 时，把值改为 `multi` 并再次执行上述重建命令。Shell 环境变量优先于根目录 `.env`；切换前确认当前窗口是否设置过该变量。

直接在主机运行 AI 服务时，配置读取 `ai-service/.env`；应用自身保留 `single` 默认值，模板已提供 `AGENT_MODE=multi`。Docker 则由根目录 Compose 的配置覆盖，默认 `multi`。`uv sync` 用于主机虚拟环境，容器继续使用现有依赖。本次将镜像中已有的 LangGraph 显式加入直接依赖，锁文件没有升级其他依赖。

内部诊断接口可以按请求传入 `execution_mode=single/multi`，便于在同一进程与同一提交下对照。该接口同时要求内部服务令牌和员工 JWT，员工聊天接口不接受执行模式或查询身份参数。

## 已实现的执行结构

多 Agent 模式在同一 AI Service 内运行主控与六个领域 Agent；每个领域都有独立的消息状态和工具循环。

```text
员工消息
  → Customer Service 读取本轮消息、历史、会话身份与 Turn 版本
  → AI Worker 携带签名 JWT、snapshot_revision、turn_attempt
  → ModeExecutor 选择 single 或 multi
  → MultiAgentExecutor / LangGraph
      route：规则处理明确人工/无上下文问候，其余问题模型结构化分类
        SINGLE → direct：目标领域 Agent
        MULTI  → plan → dispatch → review → synthesize
        CLARIFY / HANDOFF / CHAT → terminal
  → Harness 统一校验，校验失败最多补充一次汇总纠错
  → Customer Service 再核对 Turn 快照和执行租约
  → 消息／人工工单／Outbox 原子提交
  → Redis、WebSocket → 前端
```

`SINGLE` 是多 Agent 工作流中的“单领域直达”路由；它与整个系统的 `execution_mode=single` 是两个概念。后台同时显示执行模式和路由分类。

前置路由通过同一模型适配器调用 `with_structured_output(RouteDecision, method="function_calling")`。协议验证领域枚举、SINGLE/MULTI 领域数量、澄清问题、对象来源及操作意图；程序不根据一个“积分”关键词决定整段问题属于哪个领域。明确的人工请求和无上下文简单问候可跳过模型。

任务规划得到带 `depends_on` 的有向无环计划，代码检查重复 ID、未知依赖、环、领域覆盖及对象绑定。`dispatch` 每批调度最多两个已就绪任务，依赖未确认的任务不会带空对象继续执行；并行任务使用各自数据库 Session。

主控最多一次补充规划。真实事实冲突触发一次受预算限制的复查，相关旧任务和依赖它们的结果被替代，工具审计仍保留；复查失败或冲突仍存在时请求人工核对。订单状态、物流状态、退分状态按不同字段语义比较，不把“订单 cancelled、退分 pending”当作冲突。

## Agent、Skill 与工具

| Agent | 固定 Skill | 查询工具 |
| --- | --- | --- |
| 商品 | product-service | search_products、get_product、get_product_stock、quote_redemption |
| 订单 | order-service | list_orders、get_order |
| 物流 | logistics-service | list_orders、get_order、get_logistics |
| 售后 | aftersales-service | get_order、list_after_sales |
| 政策 | policy-service | search_knowledge |
| 积分 | points-service | get_points_account、list_points_transactions、get_expiring_points、get_points_refund_status、quote_redemption |

六类 Skill、13 个去重查询工具继续复用；工具目录包含原 `load_skill` 共14项。多 Agent 由程序注入固定 Skill，不额外调用 `load_skill`；单 Agent 继续动态加载。主控和路由节点不注册商城工具。

工具执行器再次检查 Agent 工具白名单和任务绑定的订单／商品编号，所有子任务继承根请求的企业、员工和福利项目 JWT。模型不能通过任务字段改变授权身份。知识检索仍使用现有本地版本化政策资料，未升级为向量 RAG。

## Harness、预算和审计

| 能力 | 实现 |
| --- | --- |
| 请求总预算 | 默认120秒、最多8次领域任务、24次查询工具尝试、24次模型调用 |
| 子任务 | 默认45秒、最多6次查询工具尝试，受根请求剩余时间约束 |
| 并发 | 根请求领域任务默认2；进程领域任务默认6 |
| Token | 初始配置60000；发起前估算输入并预留输出，调用后按供应商用量结算；未知用量保留估算 |
| 模型输出 | 领域及路由／规划默认最多2000输出 Token，主控最终回复默认4000，避免证据较多时结构被截断；角色协议、领域结果、最终回答均校验 |
| 证据 | 工具生成全局唯一 evidence_id，另存供应商原 tool_call_id；最终回答只使用本 Run 参与结果的任务证据 |
| 政策 | 引用必须来自本次成功检索；检索按企业、项目、有效期过滤 |
| 页面动作 | 查询路由移除非用户要求的卡片；实体必须经工具确认；取消／改址检查订单状态 |
| 取消 | 运行中周期检查 Turn 版本、模式、租约和尝试编号，失效后取消本地任务；发布前再次复核 |
| 可恢复故障 | 有效性服务临时不可用会使 Run 失败并由 Worker 重试，不把未回答的输入静默丢弃 |

最初审核稿建议30000 Token；真实复合请求包含领域循环、结构化证据和主控汇总，试运行发现该值偏紧，因此初版改为60000。它是应用侧估算预算，不能保证供应商实际计费绝不越过该数值；并发预留、误差和失败请求计费都应纳入观察。可在 `ai-service/.env` 设置 `MULTI_TOKEN_BUDGET`、`MULTI_CONCURRENCY`、`MULTI_TASK_TIMEOUT_SECONDS` 后重建 AI 容器。

数据库启动时执行向后兼容的增量建表／加列：

- `AgentRun` 增加 `execution_mode` 和 `execution_trace`；记录路由、计划、预算、纠错、重规划及降级。
- 新增 `AgentTask`，记录逻辑任务、领域、Skill、依赖、尝试、输入输出、Token、耗时及状态。
- `AgentToolCall` 增加可空 `task_id` 与 `model_tool_call_id`；历史单 Agent 工具记录保持可读。

单／多 Agent 的根 Token 均包含架构中所有模型调用及纠错，失败调用有实际用量时也计入；供应商未回传的消耗无法精确得知。根耗时采用实际墙钟时间，不累加并行领域耗时。

当前确定性校验能检查字段、数值、引用、动作与范围，无法完整证明自然语言所有结论的语义正确性。初版没有增加持久化 LangGraph checkpoint；进程中断沿用 Turn 租约及只读重试。取消本地等待也不代表供应商必然停止生成或停止计费。

## 写操作流程

以“修改 ORDER_A_001 地址”为例：订单 Agent 调用 `get_order` → Harness 确认订单处于可修改状态 → 返回“前往修改地址”页面卡片 → 用户进入页面核对当前订单和地址 → 编辑并点击保存 → 商城 API 重新鉴权、校验状态、幂等键并提交事务。

Agent 不调用 PATCH、不写地址、不扣积分、不取消订单。AI Run 的 confirm 是发布准备结果，用户页面“保存地址”才是业务写入。查询期间与正式保存之间如果订单已发货，业务后端会拒绝改址。

## 验证与架构对照

确定性回归包括多 Agent 调度／预算／取消、原 Harness 证据与政策、积分过期／退分、业务 API，以及跨服务 Turn 鉴权／版本／租约保护。管理端和员工端均执行生产构建；浏览器联调验证员工提问经 Worker 和 WebSocket 得到回答，管理员能够切换模式筛选并展开领域任务。

对照脚本使用标准库，无需给主机安装 LangChain：

```powershell
python -X utf8 tests/benchmark_agents.py --suite review --modes single multi
python -X utf8 tests/benchmark_agents.py --suite extended --modes single multi
```

`tests/agent_cases.json` 包含三个审核场景和其余跨域／生日项目／人工意图样例。脚本每次生成独立诊断 Run，固定企业和项目，记录源码提交与摘要、数据集版本、业务快照摘要、模型配置、两种模式的回复、初始及升级路由、工具、耗时、模型调用和全链路 Token。不会创建真实聊天工单或执行商城写入。

默认结果：`tmp/agent-evaluation/latest.json`。可用 `--output` 指定文件、`--cases` 选样例、`--repeat` 重复试验。报告明确区分自动冒烟检查与待人工审核的语义结果，保留失败与重试消耗。诊断计时不包含聊天合并、Turn 排队和实时推送时间；正式延迟对比应独立、固定并发、多次运行。

本地小样本不能证明线上85%成功率、75%自动解决率，也不能证明多 Agent 必然更快或更省 Token。结果以实际报告为准。

本次验收实测、修正记录和结果文件见[多Agent初版验收记录_2026-11-02.md](多Agent初版验收记录_2026-11-02.md)。

## 源码索引和版本

| 内容 | 入口 |
| --- | --- |
| 模式分发 | `ai-service/atguigu/agent/execution.py` |
| 单 Agent | `agent/single/executor.py` → 原 `agent/harness/run/executor.py`，工厂仍为 `agent/factory.py` |
| 多 Agent 图 | `ai-service/atguigu/agent/multi/executor.py` |
| 协作协议与角色指引 | `agent/multi/protocol.py`、`prompts.py` |
| 六领域工厂与运行 | `agent/multi/domain.py` |
| 预算、模型封装、任务审计 | `agent/multi/budget.py`、`model.py`、`audit.py` |
| Turn 有效性回调 | `agent/multi/validity.py`、`customer-service/atguigu/app/routers/internal.py` |
| 版本及发布保护 | `customer-service/atguigu/worker/ai/worker.py` |
| 后台任务详情 | `frontend/admin-frontend/src/RunInspection.vue` |

开发保持在 `feature/multi-agent`。`single-agent-v1` 固定单 Agent 历史，`main` 保留原基线；本功能分支内已可同时运行两种模式。提交记录分开保存审核材料、实现和验收，完成初版验证后用 `multi-agent-v1` 标签固定。后续公共业务修复继续在同一仓库维护，不复制两个独立项目目录。

技术接口参照：[LangGraph Graph API](https://docs.langchain.com/oss/python/langgraph/graph-api)、[LangChain 自定义中间件](https://docs.langchain.com/oss/python/langchain/middleware/custom)、[模型结构化输出](https://docs.langchain.com/oss/python/langchain/models#structured-output)。
