# 有分业务场景：积分福利商城智能客服（初版）

基于有分科技公开业务资料设计的本地演示项目。华享科技、悦享制造是模拟企业，积分及交易数据均为演示数据，不代表有分科技或其客户的真实内部系统、员工权益和政策。

## 架构与边界

保留单 Agent 架构：`用户前端 → 会话 API → Turn/AI Worker → AI Service → Skill/只读工具 → 商城业务 API`。

- 六类 Skill：商品、订单、物流、售后、政策、积分。
- 十四个工具：原九个工具及 `get_points_account`、`list_points_transactions`、`get_expiring_points`、`get_points_refund_status`、`quote_redemption`。
- 服务间通过已签名 JWT 传递 `tenant_id + welfare_project_id + user_id`，模型不提供查询身份。商品池、订单、积分、会话、人工工单、后台统计、Run 及 Redis 频道按授权范围隔离。
- Agent 仅查询和生成页面引导。用户在页面确认积分兑换、取消或售后，工作人员在后台核对后处理退款退货。第一版仅支持单商品纯积分实物兑换、整单退分。
- 商城业务层使用数据库行锁、事务、积分批次分配和请求参数指纹保证扣分、库存、订单的一致性及幂等。优先消耗最早到期的有效批次；退分保留原期限，过期返回部分记录为失效。
- 每60秒执行积分到期任务，清理过期剩余批次并写流水；只读账户查询即时排除过期积分，因此不依赖任务恰好执行的时间。
- Harness 包含上下文裁剪、动态工具范围、结构化输出、实体字段证据、政策引用、页面动作验证、有限纠错及运行审计。每轮 Agent 生成最多16次工具调用，最多两轮生成（含首次），整个生成与纠错过程120秒超时。

## 政策资料的独立保存位置

- 人工阅读：[`knowledge/policies/政策与规则知识库.md`](knowledge/policies/政策与规则知识库.md)。
- 运行数据：[`knowledge/policies/policies.json`](knowledge/policies/policies.json)。

包含有分公开客户案例、浙江邮政、广东轻工职业技术大学、点滴关怀及华为商城的公开资料摘要，以及本项目明确标注的演示规则。各条保留来源、采集日期、适用范围、版本和生效时间；未知生效时间不推断。

本地检索按企业、项目、有效时间过滤。公开参考只在明确询问对应单位时返回，不作为模拟企业承诺。规则解释返回知识引用，余额与到账事实必须查询业务工具。第一版是结构化本地规则检索，尚未实现向量 RAG、文档上传索引或在线模型评测面板。

## 启动与访问

参见 [`docker/README.md`](docker/README.md)。在根目录执行：

```powershell
docker compose up -d --wait --wait-timeout 180
docker compose ps
```

首次克隆先按 Docker 文档复制配置模板并填写本地值；源码版本和后续双架构维护方式见 [`docs/Gitee与双架构版本管理.md`](docs/Gitee与双架构版本管理.md)。

| 入口 | 地址 | 账号 |
| --- | --- | --- |
| 员工用户端 | http://127.0.0.1:5173 | `user_001`、`user_002`（企业A）；`user_003`（企业B） |
| 管理端 | http://127.0.0.1:5174 | `admin_001`（企业A）、`admin_002`（企业B） |
| 人工客服端 | http://127.0.0.1:5174 | `agent_001`（企业A）、`agent_002`（企业B） |

企业A可选择“年度员工福利”与“生日专属福利”，企业B为“季度员工激励”。不同项目有独立会话、商品池和积分账户。生日项目初始520积分，普通项目配置常规批次与临期批次。

演示订单：`ORDER_A_001` 可取消；`ORDER_A_002` 已发货并有物流；`ORDER_A_003` 已取消但退分待人工核对；`ORDER_A_004` 售后处理中。后两种情况可在后台“订单退分”中人工核对处理。操作会持久化，不会在重启时重置。

旧现金订单及旧会话通过增量迁移保留在 `legacy` 项目，未重置数据库或旧项目数据卷。初版新账号登录只允许分配的福利项目。演示账号选择接口仅用于本地演示，生产部署需要替换为正式身份认证和项目授权。

## 验证

```powershell
python docker/check-services.py
python tests/test_welfare_api.py
# Docker中运行无模型调用的单元测试（PowerShell）
$OutputEncoding = [System.Text.UTF8Encoding]::new()
Get-Content -Raw tests/test_harness.py -Encoding UTF8 | docker compose exec -T ai-service python -
Get-Content -Raw tests/test_points_domain.py -Encoding UTF8 | docker compose exec -T ecommerce python -
# 以下会真实调用配置的模型，并在演示账号中留下聊天记录
python -X utf8 tests/check_live_chat.py --scenario points
python -X utf8 tests/check_live_chat.py --scenario policy
python -X utf8 tests/check_live_chat.py --scenario refund
python -X utf8 tests/check_live_chat.py --scenario quote
python -X utf8 tests/check_live_chat.py --scenario birthday
```

API集成测试会创建测试订单再取消，恢复对应积分和库存；到期测试事务回滚。单元测试覆盖实体不匹配、单位不匹配、虚假政策引用、未到账却声称退分、跨企业政策过滤及到期任务防重。测试结果不代表线上自动解决率或生产性能指标。

初版代码索引、完整 Skill/工具清单、演示步骤和 2026-11-01 验证记录见 [`docs/初版架构与验收.md`](docs/初版架构与验收.md)。退分详情区分已退总额、退回时可用积分与原批次过期失效积分，当前账户余额独立查询。

前端在 Docker/Windows 挂载下启用轮询热更新；修改 Python 源码后重启对应服务。新增依赖仍需更新基础镜像，本地 `uv sync` 不会更新容器依赖。

## 后续扩展

先积累复合查询评测基线，再评估主控与领域多 Agent；独立知识服务、混合支付、虚拟卡券、部分退款、供应商集成和训练微调放在后续版本。已保留工具、Skill、知识适配层、证据与审计边界。
