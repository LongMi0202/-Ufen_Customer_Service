import asyncio
import json
import re
from dataclasses import replace
from typing import TypedDict
from uuid import uuid4

from langchain_core.messages import HumanMessage
from langgraph.graph import END, START, StateGraph

from atguigu.agent.harness.run.context import ContextCompiler
from atguigu.agent.harness.validator.output import ValidatedAgentOutput
from atguigu.agent.llm.output import AgentOutput, HandoffRequest, HandoffReason
from atguigu.agent.multi.audit import TaskAudit
from atguigu.agent.multi.budget import BudgetExceeded, EvidenceConflict, RunSuperseded, SharedBudget, ValidityUnavailable
from atguigu.agent.multi.domain import DomainRunner
from atguigu.agent.multi.model import StructuredCaller
from atguigu.agent.multi.prompts import PLANNER_PROMPT, REVIEW_PROMPT, ROUTER_PROMPT, SYNTHESIS_PROMPT
from atguigu.agent.multi.protocol import DomainResult, RouteDecision, SupervisorReview, TaskPlan, TaskSpec
from atguigu.agent.multi.validity import TurnValidity
from atguigu.common.config import get_settings
from atguigu.common.utils import get_utcnow


class WorkflowState(TypedDict, total=False):
    route: RouteDecision
    plan: TaskPlan
    results: dict[str, DomainResult]
    task_ids: dict[str, str]
    output: AgentOutput
    replanned: bool
    next_node: str


class MultiAgentExecutor:
    """Request-local graph. Credentials/budgets remain in the closure, outside graph state."""
    def __init__(self, model, agents, validator, process_semaphore, audit=None):
        self.model, self.agents, self.validator = model, agents, validator
        self.audit = audit or TaskAudit()
        self.runner = DomainRunner(agents, self.audit, validator, process_semaphore)
        self.trace = {"graph_version": "welfare-multi-v1", "prompt_version": "welfare-multi-v1"}
        self.token_usage = {"input_tokens": 0, "output_tokens": 0}
        self.state: WorkflowState = {"results": {}, "task_ids": {}, "replanned": False}

    async def execute(self, request, runtime_context):
        self.settings = get_settings()
        self.request = request
        self.messages = ContextCompiler().compile_messages(request)
        self.budget = SharedBudget(self.settings, TurnValidity(self.settings, request, runtime_context))
        self.context = replace(runtime_context, budget=self.budget)
        self.caller = StructuredCaller(self.model, self.budget, self.trace)
        self.input_text = "\n".join(str(message.content) for message in self.messages)
        graph = StateGraph(WorkflowState)
        for name in ("route", "terminal", "direct", "plan", "dispatch", "review", "synthesize"):
            graph.add_node(name, getattr(self, f"_{name}"))
        graph.add_edge(START, "route")
        graph.add_conditional_edges("route", lambda state: state["next_node"])
        graph.add_edge("terminal", END)
        graph.add_conditional_edges("direct", lambda state: state["next_node"])
        graph.add_edge("plan", "dispatch")
        graph.add_conditional_edges("dispatch", lambda state: state["next_node"])
        graph.add_conditional_edges("review", lambda state: state["next_node"])
        graph.add_edge("synthesize", END)
        compiled = graph.compile()
        workflow = monitor = None
        try:
            async with asyncio.timeout(self.settings.agent_run_timeout_seconds):
                workflow = asyncio.create_task(compiled.ainvoke(self.state, config={"recursion_limit": 40}))
                monitor = asyncio.create_task(self._monitor())
                done, _ = await asyncio.wait((workflow, monitor), return_when=asyncio.FIRST_COMPLETED)
                if monitor in done:
                    await monitor  # raises RunSuperseded on stale input or invalid lease
                self.state = await workflow
                await self.budget.checkpoint()
                output = await self._validate_final(self.state["output"])
        except (BudgetExceeded, TimeoutError):
            await self._stop(workflow)
            self.trace["degraded"] = "预算或时限不足"
            output = await self._safe_partial()
        except RunSuperseded:
            self.budget.cancelled = True
            raise
        except ValidityUnavailable:
            raise  # A failed callback needs a Worker retry, not a superseded turn.
        except EvidenceConflict:
            output = ValidatedAgentOutput(**self._handoff("本轮查询出现无法消解的业务数据冲突，需要人工核对。").model_dump())
        except Exception as exc:
            await self._stop(workflow)
            # Invalid plans/aggregation must never become unchecked business claims.
            self.trace["degraded"] = type(exc).__name__
            self.trace["error"] = str(exc)[:700]
            output = await self._safe_partial()
        finally:
            for task in (workflow, monitor):
                if task is not None and not task.done():
                    task.cancel()
            await asyncio.gather(*(task for task in (workflow, monitor) if task is not None), return_exceptions=True)
            self.token_usage = self.budget.usage()
            self.trace["budget"] = self.budget.snapshot()
        return output.model_copy(update={"token_usage": self.token_usage})

    async def _monitor(self):
        while True:
            await self.budget.checkpoint()
            await asyncio.sleep(1)

    async def _route(self, state):
        # Only exact, context-free control utterances bypass the classifier.
        current = ContextCompiler()._build_current_message(self.request.messages).strip()
        if current in {"转人工", "人工客服", "我要人工客服", "请转人工"}:
            route = RouteDecision(route_kind="HANDOFF", reason="用户明确要求人工")
        elif not self.request.history and current in {"你好", "您好", "hi", "hello"}:
            route = RouteDecision(route_kind="CHAT", reason="无业务上下文的问候")
        else:
            route = await self.caller.call(RouteDecision, ROUTER_PROMPT, self.messages, "router")
        verified_hints = []
        for hint in route.entity_hints:
            try:
                self._check_bindings([hint])
            except ValueError:
                # The original product name/question is preserved for domain search.
                self.trace.setdefault("discarded_entity_hints", []).append(hint.model_dump(mode="json"))
            else:
                verified_hints.append(hint)
        route = route.model_copy(update={"entity_hints": verified_hints})
        self.trace["route"] = route.model_dump(mode="json")
        self.context = replace(self.context, allow_page_actions=route.operation_intent != "QUERY")
        target = {"SINGLE": "direct", "MULTI": "plan"}.get(route.route_kind, "terminal")
        self.state = {**state, "route": route, "next_node": target}
        return self.state

    async def _terminal(self, state):
        route = state["route"]
        if route.route_kind == "HANDOFF":
            output = self._handoff("用户请求人工协助：" + self.input_text[-350:], True)
        elif route.route_kind == "CLARIFY":
            output = AgentOutput(reply_type="CLARIFY", reply_content=route.clarification)
        else:
            output = AgentOutput(reply_type="ANSWER", reply_content="您好，我可以帮您查询福利商品、订单、物流、售后、积分及相关规则。请告诉我您想了解的问题。")
        return {"output": output}

    async def _direct(self, state):
        route = state["route"]
        spec = TaskSpec(task_id="DIRECT", domain=route.domains[0],
            goal=ContextCompiler()._build_current_message(self.request.messages)[:1200], entity_bindings=route.entity_hints)
        result, task_id = await self.runner.run(spec, self.context, self.messages, [], set(), direct=True)
        self.state = {**state, "results": {spec.task_id: result}, "task_ids": {spec.task_id: task_id},
                      "plan": TaskPlan(tasks=[spec])}
        required = set(result.required_domains) - set(route.domains)
        if required:
            domains = list(dict.fromkeys([*route.domains, *sorted(required)]))
            new_route = RouteDecision(route_kind="MULTI", domains=domains,
                operation_intent=route.operation_intent,
                entity_hints=route.entity_hints, reason="领域Agent报告任务需要其他领域，升级主控")
            self.trace["escalation"] = new_route.model_dump(mode="json")
            self.state.update(route=new_route, next_node="plan")
            return self.state
        if result.candidate_reply and result.result_status in {"SUCCESS", "PARTIAL", "NEEDS_CLARIFICATION"}:
            output = result.candidate_reply
        elif result.result_status == "NEEDS_CLARIFICATION":
            output = AgentOutput(reply_type="CLARIFY", reply_content="请补充：" + "；".join(result.missing_information))
        else:
            output = AgentOutput(reply_type="DECLINE", reply_content="暂时未取得可靠的查询结果，请稍后重试或联系人工客服。")
        self.state.update(output=output, next_node=END)
        return self.state

    async def _plan(self, state):
        existing = state.get("plan")
        context = {"route": state["route"].model_dump(mode="json"),
                   "completed_results": {key: value.model_dump(mode="json") for key, value in state["results"].items()}}
        if existing:
            context["existing_plan"] = existing.model_dump(mode="json")
            context["instruction"] = "必须完整保留已有任务及其ID/goal/依赖，追加任务引用已有成功证据，不重复查询已完成事实。"
        plan = await self.caller.call(TaskPlan, PLANNER_PROMPT,
            [*self.messages, HumanMessage(json.dumps(context, ensure_ascii=False))], "planner")
        if existing:
            # Previously executed tasks are immutable server state, not text for the model to reproduce.
            by_domain = {task.domain: task for task in existing.tasks}
            aliases = {task.task_id: by_domain[task.domain].task_id for task in plan.tasks if task.domain in by_domain}
            additions = [task.model_copy(update={"depends_on": [aliases.get(key, key) for key in task.depends_on]})
                         for task in plan.tasks if task.domain not in by_domain]
            plan = TaskPlan(tasks=[*existing.tasks, *additions])
        if len(plan.tasks) > self.settings.multi_max_tasks:
            raise ValueError("计划超过根任务预算")
        if not set(state["route"].domains) <= {task.domain for task in plan.tasks}:
            raise ValueError("计划遗漏用户要求的领域")
        for task in plan.tasks:
            self._check_bindings(task.entity_bindings)
        self.trace.setdefault("plans", []).append(plan.model_dump(mode="json"))
        self.state = {**state, "plan": plan}
        return {"plan": plan}

    async def _dispatch(self, state):
        await self.budget.checkpoint()
        results, task_ids = dict(state["results"]), dict(state["task_ids"])
        pending = [task for task in state["plan"].tasks if task.task_id not in results]
        for task in pending:
            if all(key in results for key in task.depends_on) and any(
                    results[key].result_status not in {"SUCCESS", "PARTIAL"} for key in task.depends_on):
                needs_info = any(results[key].result_status == "NEEDS_CLARIFICATION" for key in task.depends_on)
                result = DomainResult(task_id=task.task_id, domain=task.domain,
                    result_status="NEEDS_CLARIFICATION" if needs_info else "FAILED",
                    summary="上游任务未确认，未执行依赖查询。", missing_information=[task.goal],
                    error="DEPENDENCY_BLOCKED", observed_at=get_utcnow().isoformat())
                db_id = f"task_{uuid4().hex}"
                await self.audit.start(task_id=db_id, run_id=self.context.run_id, spec=task,
                    context={"task": task.model_dump(mode="json")})
                await self.audit.finish(db_id, "BLOCKED", result.model_dump(mode="json"), "DEPENDENCY_BLOCKED", 0, {})
                results[task.task_id], task_ids[task.task_id] = result, db_id
        ready = [task for task in pending if task.task_id not in results and all(key in results for key in task.depends_on)]
        ready = ready[:self.settings.multi_concurrency]
        async def run(task):
            ancestors = self._ancestors(task, state["plan"])
            upstream = [results[key] for key in ancestors]
            upstream_ids = {task_ids[key] for key in ancestors if results[key].result_status in {"SUCCESS", "PARTIAL"}}
            result, db_id = await self.runner.run(task, self.context, self.messages, upstream, upstream_ids)
            return task.task_id, result, db_id
        # Gather cancellation also cancels all in-flight children; every child audits its final state.
        children = [asyncio.create_task(run(task)) for task in ready]
        try:
            completed = await asyncio.gather(*children)
        finally:
            for child in children:
                if not child.done():
                    child.cancel()
            await asyncio.gather(*children, return_exceptions=True)
        for key, result, db_id in completed:
            results[key], task_ids[key] = result, db_id
        target = "dispatch" if len(results) < len(state["plan"].tasks) else "review"
        if target == "dispatch" and not ready and len(results) == len(state["results"]):
            raise ValueError("依赖调度无法推进")
        self.state = {**state, "results": results, "task_ids": task_ids, "next_node": target}
        return self.state

    async def _review(self, state):
        self.state = dict(state)
        conflicts = self._conflicting_tasks(await self._eligible_calls())
        if conflicts:
            if state.get("replanned"):
                raise EvidenceConflict("补充查询后仍存在事实冲突")
            return await self._refresh_conflicts(state, conflicts)
        if state.get("replanned"):
            return {"next_node": "synthesize"}
        review = await self.caller.call(SupervisorReview, REVIEW_PROMPT,
            [*self.messages, HumanMessage(json.dumps({"plan": state["plan"].model_dump(mode="json"),
                "results": {key: value.model_dump(mode="json") for key, value in state["results"].items()},
                "budget": self.budget.snapshot()}, ensure_ascii=False))], "supervisor_review")
        self.trace["review"] = review.model_dump(mode="json")
        if review.decision == "REPLAN":
            ids = {task.task_id for task in state["plan"].tasks}
            if any(task.task_id in ids for task in review.additional_tasks):
                raise ValueError("补充任务不能覆盖已有任务")
            plan = TaskPlan(plan_version=2, tasks=[*state["plan"].tasks, *review.additional_tasks])
            if len(plan.tasks) > self.settings.multi_max_tasks:
                raise BudgetExceeded("补充计划超过任务预算")
            for task in review.additional_tasks:
                self._check_bindings(task.entity_bindings, state)
            self.trace.setdefault("plans", []).append(plan.model_dump(mode="json"))
            self.state = {**state, "plan": plan, "replanned": True, "next_node": "dispatch"}
            return self.state
        if review.decision in {"CLARIFY", "HANDOFF"}:
            output = self._handoff(review.message) if review.decision == "HANDOFF" else AgentOutput(
                reply_type="CLARIFY", reply_content=review.message)
            return {"output": output, "next_node": END}
        return {"next_node": "synthesize"}

    async def _synthesize(self, state):
        self.state = {**state}
        calls = await self._eligible_calls()
        output = await self.caller.call(AgentOutput, SYNTHESIS_PROMPT,
            [*self.messages, HumanMessage(json.dumps({
                "domain_results": {key: value.model_dump(mode="json") for key, value in state["results"].items()},
                "evidence": [{"tool_name": call.tool_name, "result": call.result} for call in calls]},
                ensure_ascii=False))], "synthesis")
        return {"output": output}

    async def _eligible_calls(self):
        task_ids = {self.state["task_ids"][key] for key, result in self.state["results"].items()
                    if result.result_status in {"SUCCESS", "PARTIAL"}}
        return await self.audit.calls(self.context.run_id, task_ids)

    async def _validate_final(self, output):
        calls = await self._eligible_calls()
        output = self._constrain_action(output)
        if self._conflicting_tasks(calls):
            raise EvidenceConflict("最终事实存在冲突")
        try:
            return self.validator.validate_with_calls(output, calls)
        except ValueError as exc:
            self.trace.setdefault("final_validation_errors", []).append(str(exc)[:600])
            # One bounded summary correction; domain queries are not repeated.
            corrected = await self.caller.call(AgentOutput, SYNTHESIS_PROMPT,
                [*self.messages, HumanMessage(json.dumps({"candidate": output.model_dump(mode="json"),
                    "validation_error": str(exc)[:600], "evidence": [call.result for call in calls]},
                    ensure_ascii=False) + "\n纯政策条件中的状态词请用中文引号逐字引用本次知识原文，不要改写成当前订单的状态。")], "final_correction")
            return self.validator.validate_with_calls(self._constrain_action(corrected), calls)

    def _conflicting_tasks(self, calls):
        # Conflicting facts need a fresh authoritative observation; aggregation cannot pick one at random.
        values = {}
        conflicts = set()
        evidence = {call.result.get("evidence_id"): call for call in calls if call.result}
        for task_key, result in self.state["results"].items():
            if result.result_status not in {"SUCCESS", "PARTIAL"}:
                continue
            for fact in result.business_facts:
                call = evidence.get(fact.tool_call_id)
                path = fact.field_path
                # Order/fulfilment/refund states are different facts, even for the same order ID.
                if call and call.tool_name == "get_points_refund_status":
                    path = "refund." + path
                elif call and call.tool_name == "get_logistics":
                    path = "logistics." + path
                elif call and call.tool_name == "get_order" and not path.startswith("refund."):
                    path = "order." + path
                key = (fact.entity_id, path)
                if key in values and values[key][0] != fact.value:
                    conflicts.update((task_key, values[key][1]))
                values[key] = (fact.value, task_key)
        return conflicts

    async def _refresh_conflicts(self, state, conflicts):
        """One recovery round; invalidate old attempts and every consumer of their observations."""
        plan = state["plan"]
        affected = set(conflicts)
        while True:
            expanded = affected | {task.task_id for task in plan.tasks if affected.intersection(task.depends_on)}
            if expanded == affected:
                break
            affected = expanded
        if self.budget.task_attempts + len(affected) > self.settings.multi_max_tasks:
            raise EvidenceConflict("剩余委派预算不足以复查冲突及其下游")
        self.trace["conflict_recovery"] = {"tasks": sorted(affected), "max_rounds": 1}
        results, task_ids = dict(state["results"]), dict(state["task_ids"])
        for key in affected:
            await self.audit.supersede(task_ids[key])
            results.pop(key)
            task_ids.pop(key)
        self.state = {**state, "results": results, "task_ids": task_ids, "replanned": True}
        pending = {task.task_id: task for task in plan.tasks if task.task_id in affected}
        while pending:
            ready = [task for task in pending.values() if all(key in results for key in task.depends_on)]
            if not ready:
                raise EvidenceConflict("复查任务依赖无法推进")
            for task in ready:
                if any(results[key].result_status not in {"SUCCESS", "PARTIAL"} for key in task.depends_on):
                    raise EvidenceConflict("复查上游未确认")
                ancestors = self._ancestors(task, plan)
                result, db_id = await self.runner.run(task, self.context, self.messages,
                    [results[key] for key in ancestors], {task_ids[key] for key in ancestors}, attempt=2)
                results[task.task_id], task_ids[task.task_id] = result, db_id
                pending.pop(task.task_id)
        if any(results[key].result_status != "SUCCESS" for key in affected):
            raise EvidenceConflict("冲突复查未获得完整可靠结果")
        if self._conflicting_tasks(await self._eligible_calls()):
            raise EvidenceConflict("复查后事实仍有冲突")
        self.state["next_node"] = "synthesize"
        return self.state

    def _constrain_action(self, output):
        route = self.state.get("route")
        if route is None or route.operation_intent == "QUERY":
            return output.model_copy(update={"page_action_request": None})
        return output

    async def _safe_partial(self):
        calls = await self._eligible_calls()
        if self._conflicting_tasks(calls):
            return ValidatedAgentOutput(**self._handoff("查询证据有冲突，本轮未能完成复查，请人工核对。").model_dump())
        policies = {item["knowledge_id"]: item for call in calls if call.success and call.tool_name == "search_knowledge"
                    for item in (call.result.get("data") or [])}
        parts = []
        for result in self.state["results"].values():
            if result.result_status not in {"SUCCESS", "PARTIAL"}:
                continue
            if result.domain == "policy":
                cited = [policies[key] for key in result.knowledge_references if key in policies]
                if not cited:
                    continue
                candidate = AgentOutput(reply_type="ANSWER", reply_content="\n".join(
                    f"规则依据《{item['title']}》：“{item['content']}”" for item in cited),
                    knowledge_references=[item["knowledge_id"] for item in cited])
            else:
                candidate = result.candidate_reply or AgentOutput(reply_type="ANSWER", reply_content=result.summary,
                    business_facts=result.business_facts, knowledge_references=result.knowledge_references)
            if candidate.reply_type == "ANSWER":
                parts.append(candidate)
        if parts:
            suffix = "\n\n其余问题本次未能确认，可补充信息重试或联系人工客服。" if any(
                result.result_status != "SUCCESS" for result in self.state["results"].values()) else ""
            output = AgentOutput(reply_type="ANSWER", reply_content=("\n\n".join(part.reply_content for part in parts))[:3700] + suffix,
                business_facts=[fact for part in parts for fact in part.business_facts],
                knowledge_references=list(dict.fromkeys(key for part in parts for key in part.knowledge_references)))
            try:
                return self.validator.validate_with_calls(output, calls)
            except ValueError as exc:
                self.trace["partial_validation_error"] = str(exc)[:600]
        return ValidatedAgentOutput(reply_type="DECLINE", reply_content="本次暂时未能完成可靠的查询，请稍后重试或联系人工客服。")

    def _check_bindings(self, bindings, state=None):
        allowed = {value.upper().replace("-", "_") for value in
            re.findall(r"(?<![A-Za-z0-9_])(?:ORDER|PRODUCT|AS|PA|PB|BATCH)[_-][A-Z0-9_-]+", self.input_text, re.IGNORECASE)}
        if state:
            allowed |= {entity.entity_id for result in state["results"].values()
                        if result.result_status in {"SUCCESS", "PARTIAL"} for entity in result.entities}
        if any(binding.entity_id.upper().replace("-", "_") not in allowed for binding in bindings):
            raise ValueError("对象绑定不在用户输入或已验证上游实体中")

    @staticmethod
    def _ancestors(task, plan):
        by_id = {spec.task_id: spec for spec in plan.tasks}
        result = set(task.depends_on)
        queue = list(result)
        while queue:
            for key in by_id[queue.pop()].depends_on:
                if key not in result:
                    result.add(key)
                    queue.append(key)
        return sorted(result)

    @staticmethod
    async def _stop(task):
        if task is not None:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    @staticmethod
    def _handoff(summary, explicit=False):
        return AgentOutput(reply_type="REQUEST_HANDOFF", reply_content="已为您提交人工客服协助请求，请等待客服接入。",
            handoff_request=HandoffRequest(reason_code=HandoffReason.USER_REQUESTED if explicit else HandoffReason.RISK_REVIEW,
                                           summary=summary[:500]))
