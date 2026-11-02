"""Deterministic Harness/graph tests; no paid model calls. Run inside ai-service image."""
import asyncio
import unittest
from dataclasses import replace
from unittest.mock import AsyncMock, patch

from pydantic import ValidationError

from atguigu.agent.harness.run.runtime import AgentRuntimeContext
from atguigu.agent.harness.tools.executor import ToolExecutor
from atguigu.agent.harness.validator.output import AgentOutputValidator
from atguigu.agent.llm.output import AgentOutput, BusinessFactReference
from atguigu.agent.multi.budget import BudgetExceeded, EvidenceConflict, RunSuperseded, SharedBudget, ValidityUnavailable
from atguigu.agent.harness.tools.snapshot import ToolCallSnapshot
from atguigu.agent.multi.executor import MultiAgentExecutor
from atguigu.agent.multi.protocol import DomainResult, RouteDecision, TaskPlan, TaskSpec, SupervisorReview
from atguigu.app.schemas.run import AgentRunRequest
from atguigu.common.config import get_settings


def settings(**changes):
    return get_settings().model_copy(update=changes)


def request(text="查询订单和退分"):
    return AgentRunRequest(conversation_id="conv_test", turn_id="turn_test", messages=[
        {"message_id": "message_test", "type": "text", "content": {"text": text}}])


def runtime():
    return AgentRuntimeContext("run_test", "conv_test", "user_001", "not-a-real-token", "tenant_a", "welfare_a")


class ProtocolTests(unittest.TestCase):
    def test_route_cardinality_and_extra_identity(self):
        for item in [dict(route_kind="SINGLE", domains=["points", "policy"], reason="test"),
                     dict(route_kind="MULTI", domains=["points"], reason="test"),
                     dict(route_kind="CLARIFY", reason="test"),
                     dict(route_kind="CHAT", reason="test", user_id="other")]:
            with self.assertRaises(ValidationError):
                RouteDecision(**item)

    def test_dag_rejects_cycle_missing_dependency_duplicate(self):
        for tasks in [[TaskSpec(task_id="T1", domain="order", goal="a", depends_on=["T2"]),
                       TaskSpec(task_id="T2", domain="points", goal="b", depends_on=["T1"])],
                      [TaskSpec(task_id="T1", domain="order", goal="a", depends_on=["UNKNOWN"])],
                      [TaskSpec(task_id="T1", domain="order", goal="a"), TaskSpec(task_id="T1", domain="points", goal="b")]]:
            with self.assertRaises(ValidationError):
                TaskPlan(tasks=tasks)

    def test_chinese_adjacent_identifier_and_fabricated_binding(self):
        executor = MultiAgentExecutor(None, {}, None, asyncio.Semaphore(2))
        executor.input_text = "我想知道ORDER_A_003是否取消"
        from atguigu.agent.multi.protocol import EntityBinding
        executor._check_bindings([EntityBinding(entity_type="order", entity_id="ORDER_A_003")])
        with self.assertRaises(ValueError):
            executor._check_bindings([EntityBinding(entity_type="order", entity_id="ORDER_B_001")])


class BudgetTests(unittest.IsolatedAsyncioTestCase):
    async def test_atomic_parallel_tool_budget(self):
        budget = SharedBudget(settings(multi_max_tools=3, multi_task_max_tools=10))
        async def reserve():
            try:
                await budget.reserve_tool("task"); return True
            except BudgetExceeded:
                return False
        self.assertEqual(sum(await asyncio.gather(*(reserve() for _ in range(10)))), 3)
        self.assertEqual(budget.tool_calls, 3)

    async def test_model_usage_and_failed_reservation_settlement(self):
        budget = SharedBudget(settings())
        reserved = await budget.reserve_model(["test"], task_id="task")
        await budget.settle_model(reserved, {"input_tokens": 10, "output_tokens": 5}, "task")
        failed = await budget.reserve_model(["test"])
        await budget.settle_model(failed, None)
        self.assertEqual(budget.usage(), {"input_tokens": 10, "output_tokens": 5})
        self.assertEqual(budget.reserved_tokens, 0)
        self.assertEqual(budget.estimated_tokens, failed)

    async def test_cancel_and_model_count_stop_work(self):
        budget = SharedBudget(settings(multi_max_model_calls=1))
        reserved = await budget.reserve_model(["test"])
        await budget.settle_model(reserved, {"input_tokens": 2, "output_tokens": 1})
        with self.assertRaises(BudgetExceeded):
            await budget.reserve_model(["test"])
        budget.cancelled = True
        with self.assertRaises(RunSuperseded):
            await budget.reserve_tool("task")

    async def test_execution_allowlist_cannot_be_bypassed(self):
        executor = ToolExecutor()
        executor._create_tool_call = AsyncMock()
        operation = AsyncMock()
        with self.assertRaises(PermissionError):
            await executor.execute(replace(runtime(), allowed_tool_names=frozenset({"get_order"})),
                "call", "get_points_account", {}, operation, dict)
        operation.assert_not_awaited()
        executor._create_tool_call.assert_not_awaited()

    async def test_stale_turn_check_stops_before_operation(self):
        budget = SharedBudget(settings(), AsyncMock(return_value=False))
        with self.assertRaises(RunSuperseded):
            await budget.checkpoint()

    async def test_bound_order_cannot_be_swapped_by_model(self):
        executor = ToolExecutor()
        executor._create_tool_call = AsyncMock()
        operation = AsyncMock()
        context = replace(runtime(), allowed_tool_names=frozenset({"get_order"}),
                          bound_entities=(("order", "ORDER_A_003"),))
        with self.assertRaises(PermissionError):
            await executor.execute(context, "call", "get_order", {"order_id": "ORDER_A_001"}, operation, dict)
        operation.assert_not_awaited()


class MemoryAudit:
    def __init__(self):
        self.states = {}
    async def start(self, **kwargs):
        self.states[kwargs["task_id"]] = "RUNNING"
    async def finish(self, task_id, state, *args):
        self.states[task_id] = state
    async def calls(self, run_id, task_ids):
        return ()
    async def supersede(self, task_id):
        self.states[task_id] = "SUPERSEDED"


class FakeCaller:
    decisions = {}
    stages = []
    def __init__(self, *args):
        pass
    async def call(self, schema, prompt, messages, stage):
        self.stages.append(stage)
        value = self.decisions[stage]
        return value() if callable(value) else value


class GraphTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        FakeCaller.stages = []
        self.audit = MemoryAudit()
        self.executor = MultiAgentExecutor(None, {}, AgentOutputValidator(None), asyncio.Semaphore(2), self.audit)
        self.events = []
        self.active = 0
        self.max_active = 0

    async def fake_run(self, spec, context, messages, upstream, upstream_ids, direct=False):
        await context.budget.reserve_task()
        self.events.append(("start", spec.task_id, [item.task_id for item in upstream]))
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await asyncio.sleep(0.01)
        finally:
            self.active -= 1
        self.events.append(("end", spec.task_id))
        return DomainResult(task_id=spec.task_id, domain=spec.domain, result_status="SUCCESS", summary="查询完成",
            candidate_reply=AgentOutput(reply_type="ANSWER", reply_content="查询完成"), observed_at="2026-11-02"), "db_" + spec.task_id

    async def run_graph(self, text="查询订单和退分"):
        self.executor.runner.run = self.fake_run
        with patch("atguigu.agent.multi.executor.StructuredCaller", FakeCaller):
            return await self.executor.execute(request(text), runtime())

    async def test_single_bypasses_supervisor(self):
        FakeCaller.decisions = {"router": RouteDecision(route_kind="SINGLE", domains=["points"], reason="test")}
        result = await self.run_graph("我的积分是多少")
        self.assertEqual(result.reply_type, "ANSWER")
        self.assertEqual(FakeCaller.stages, ["router"])
        self.assertEqual(len(self.events), 2)

    async def test_parallel_and_dependency_order(self):
        FakeCaller.decisions = {
            "router": RouteDecision(route_kind="MULTI", domains=["order", "points", "policy"], reason="test"),
            "planner": TaskPlan(tasks=[TaskSpec(task_id="T1", domain="order", goal="定位订单"),
                TaskSpec(task_id="T2", domain="policy", goal="规则"),
                TaskSpec(task_id="T3", domain="points", goal="退分", depends_on=["T1"])]),
            "supervisor_review": SupervisorReview(decision="FINISH"),
            "synthesis": AgentOutput(reply_type="ANSWER", reply_content="查询完成")}
        result = await self.run_graph()
        self.assertEqual(result.reply_type, "ANSWER")
        self.assertEqual(self.max_active, 2)
        self.assertLess(self.events.index(("end", "T1")), self.events.index(("start", "T3", ["T1"])))

    async def test_failed_dependency_is_blocked_without_query(self):
        FakeCaller.decisions = {
            "router": RouteDecision(route_kind="MULTI", domains=["order", "points"], reason="test"),
            "planner": TaskPlan(tasks=[TaskSpec(task_id="T1", domain="order", goal="定位订单"),
                TaskSpec(task_id="T2", domain="points", goal="退分", depends_on=["T1"])]),
            "supervisor_review": SupervisorReview(decision="CLARIFY", message="请提供订单编号")}
        async def failed(spec, *args, **kwargs):
            self.events.append(spec.task_id)
            return DomainResult(task_id=spec.task_id, domain=spec.domain, result_status="NEEDS_CLARIFICATION",
                summary="订单不唯一", missing_information=["订单编号"], observed_at="2026-11-02"), "db_T1"
        self.fake_run = failed
        result = await self.run_graph()
        self.assertEqual(self.events, ["T1"])
        self.assertEqual(result.reply_type, "CLARIFY")
        self.assertIn("BLOCKED", self.audit.states.values())

    async def test_explicit_handoff_has_no_model_or_domain_call(self):
        result = await self.run_graph("请转人工")
        self.assertEqual(result.reply_type, "REQUEST_HANDOFF")
        self.assertEqual(FakeCaller.stages, [])
        self.assertEqual(self.events, [])

    async def test_monitor_cancels_running_graph(self):
        FakeCaller.decisions = {"router": RouteDecision(route_kind="SINGLE", domains=["points"], reason="test")}
        cancelled = asyncio.Event()
        async def slow(*args, **kwargs):
            try:
                await asyncio.sleep(10)
            finally:
                cancelled.set()
        self.fake_run = slow
        count = 0
        async def validity(_):
            nonlocal count
            count += 1
            return count < 3
        with patch("atguigu.agent.multi.validity.TurnValidity.__call__", validity):
            with self.assertRaises(RunSuperseded):
                await self.run_graph()
        self.assertTrue(cancelled.is_set())

    async def test_validity_service_failure_propagates_for_worker_retry(self):
        FakeCaller.decisions = {"router": RouteDecision(route_kind="SINGLE", domains=["points"], reason="test")}
        async def unavailable(_):
            raise ValidityUnavailable("temporarily unavailable")
        with patch("atguigu.agent.multi.validity.TurnValidity.__call__", unavailable):
            with self.assertRaises(ValidityUnavailable):
                await self.run_graph()

    async def test_query_cannot_publish_unsolicited_action(self):
        self.executor.state["route"] = RouteDecision(route_kind="SINGLE", domains=["order"], reason="test")
        output = AgentOutput(reply_type="ANSWER", reply_content="查询完成",
                            page_action_request={"action_code": "OPEN_POINTS"})
        self.assertIsNone(self.executor._constrain_action(output).page_action_request)
        self.executor.state["route"].operation_intent = "WRITE_GUIDANCE"
        self.assertIsNotNone(self.executor._constrain_action(output).page_action_request)

    async def test_order_and_refund_status_are_different_facts(self):
        def result(task_id, domain, evidence_id, value):
            return DomainResult(task_id=task_id, domain=domain, result_status="SUCCESS", summary="查询完成",
                business_facts=[BusinessFactReference(tool_call_id=evidence_id, entity_id="ORDER_A_003",
                    field_path="status", value=value, unit="status")], observed_at="2026-11-02")
        self.executor.state["results"] = {"T1": result("T1", "order", "e1", "cancelled"),
                                          "T2": result("T2", "points", "e2", "pending")}
        calls = [ToolCallSnapshot("get_order", {}, {"evidence_id": "e1"}, True),
                 ToolCallSnapshot("get_points_refund_status", {}, {"evidence_id": "e2"}, True)]
        self.assertEqual(self.executor._conflicting_tasks(calls), set())
        calls[1] = ToolCallSnapshot("get_order", {}, {"evidence_id": "e2"}, True)
        self.assertEqual(self.executor._conflicting_tasks(calls), {"T1", "T2"})

    async def test_conflict_recovery_supersedes_previous_attempts(self):
        self.executor.settings = settings()
        self.executor.context = replace(runtime(), budget=SharedBudget(settings()))
        self.executor.budget = self.executor.context.budget
        plan = TaskPlan(tasks=[TaskSpec(task_id="T1", domain="order", goal="查询"),
                               TaskSpec(task_id="T2", domain="points", goal="查询", depends_on=["T1"])])
        state = {"plan": plan, "results": {key: DomainResult(task_id=key, domain=domain,
            result_status="SUCCESS", summary="查询完成", observed_at="2026-11-02")
            for key, domain in (("T1", "order"), ("T2", "points"))},
            "task_ids": {"T1": "old_1", "T2": "old_2"}, "replanned": False}
        self.executor.state = state
        self.executor.messages = []
        attempts = []
        async def refreshed(spec, *args, attempt=1, **kwargs):
            attempts.append((spec.task_id, attempt))
            return DomainResult(task_id=spec.task_id, domain=spec.domain, result_status="SUCCESS",
                summary="查询完成", observed_at="2026-11-02"), "new_" + spec.task_id
        self.executor.runner.run = refreshed
        updated = await self.executor._refresh_conflicts(state, {"T1"})
        self.assertEqual(attempts, [("T1", 2), ("T2", 2)])
        self.assertEqual(set(self.audit.states.values()), {"SUPERSEDED"})
        self.assertTrue(updated["replanned"])
        self.assertEqual(updated["task_ids"], {"T1": "new_T1", "T2": "new_T2"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
