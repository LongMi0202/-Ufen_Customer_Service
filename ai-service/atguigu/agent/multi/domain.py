import asyncio
import json
import logging
from dataclasses import replace
from time import perf_counter
from uuid import uuid4

from langchain.agents import create_agent
from langchain.agents.structured_output import ToolStrategy
from langchain_core.messages import HumanMessage

from atguigu.agent.harness.errors import AgentOutputValidationError
from atguigu.agent.harness.run.runtime import AgentRuntimeContext
from atguigu.agent.harness.skills.catalog import SKILL_CATALOG
from atguigu.agent.harness.tools.catalog import TOOL_CATALOG
from atguigu.agent.harness.validator.evidence import entities, validate_evidence
from atguigu.agent.llm.output import AgentOutput
from atguigu.agent.multi.budget import BudgetExceeded, RunSuperseded, ValidityUnavailable
from atguigu.agent.multi.model import BudgetMiddleware
from atguigu.agent.multi.prompts import domain_prompt
from atguigu.agent.multi.protocol import Domain, DomainAnswer, DomainResult
from atguigu.common.utils import get_utcnow

logger = logging.getLogger(__name__)


def create_domain_agents(model):
    agents = {}
    for domain in Domain:
        skill = SKILL_CATALOG.get_skill(domain.skill)
        agents[domain] = create_agent(model=model,
            tools=[TOOL_CATALOG.get_tool_definition(name).tool for name in skill.tools],
            system_prompt=domain_prompt(domain), response_format=ToolStrategy(DomainAnswer),
            context_schema=AgentRuntimeContext, middleware=[BudgetMiddleware()],
            name=f"welfare_{domain.value}")
    return agents


class DomainRunner:
    def __init__(self, agents, audit, validator, process_semaphore):
        self.agents = agents
        self.audit = audit
        self.validator = validator
        self.process_semaphore = process_semaphore

    async def run(self, spec, root_context, messages, upstream, upstream_task_ids, direct=False, attempt=1):
        budget = root_context.budget
        await budget.reserve_task()
        task_id = f"task_{uuid4().hex}"
        skill = SKILL_CATALOG.get_skill(spec.domain.skill)
        context = replace(root_context, task_id=task_id, allowed_tool_names=frozenset(skill.tools),
            domain_name=spec.domain.value,
            bound_entities=tuple((item.entity_type, item.entity_id) for item in spec.entity_bindings))
        task_input = {"task": spec.model_dump(mode="json"), "direct": direct, "allow_page_actions": context.allow_page_actions,
            "scope": {"tenant_id": context.tenant_id, "welfare_project_id": context.welfare_project_id,
                      "user_id": context.user_id},
            "upstream": [result.model_dump(mode="json") for result in upstream]}
        upstream_calls = await self.audit.calls(context.run_id, upstream_task_ids)
        # Raw evidence is passed with its original server-generated ID, never renumbered.
        task_input["upstream_evidence"] = [call.result for call in upstream_calls if call.success]
        await self.audit.start(task_id=task_id, run_id=context.run_id, spec=spec, context=task_input, attempt=attempt)
        started = perf_counter()
        state, error, result = "FAILED", None, None
        corrections = []
        try:
            async with asyncio.timeout(min(budget.settings.multi_task_timeout_seconds, budget.remaining_seconds)):
                async with budget.semaphore, self.process_semaphore:
                    await budget.checkpoint()
                    current = [*messages, HumanMessage("本领域任务（原始用户输入仍在上方）：\n" +
                        json.dumps(task_input, ensure_ascii=False))]
                    for attempt in range(2):
                        raw = await self.agents[spec.domain].ainvoke({"messages": current}, context=context,
                            config={"recursion_limit": 32})
                        try:
                            answer = DomainAnswer.model_validate(raw["structured_response"])
                            if not context.allow_page_actions:
                                answer = answer.model_copy(update={"action_suggestions": [],
                                    "candidate_reply": answer.candidate_reply.model_copy(update={"page_action_request": None})
                                    if answer.candidate_reply else None})
                            own_calls = await self.audit.calls(context.run_id, {task_id})
                            calls = (*upstream_calls, *own_calls)
                            answer = self._validate(answer, calls)
                            result = DomainResult(**answer.model_dump(), task_id=spec.task_id,
                                domain=spec.domain, observed_at=get_utcnow().isoformat(),
                                evidence_ids=[call.result["evidence_id"] for call in calls if call.success])
                            state = answer.result_status
                            break
                        except (ValueError, AgentOutputValidationError) as exc:
                            corrections.append(str(exc)[:600])
                            logger.info("领域结果纠错 domain=%s task_id=%s reason=%s", spec.domain, task_id, str(exc)[:500])
                            if attempt:
                                raise
                            current = [*raw["messages"], HumanMessage(
                                "领域结果未通过证据或结构校验，请修正，不重复已成功的查询：" + str(exc)[:600])]
        except asyncio.CancelledError:
            state, error = "CANCELLED", "根请求或本领域任务已取消"
            raise
        except (RunSuperseded, ValidityUnavailable):
            state, error = "CANCELLED", "本轮已失效"
            raise
        except Exception as exc:
            logger.exception("领域任务失败 domain=%s task_id=%s", spec.domain, task_id)
            state = "TIMED_OUT" if isinstance(exc, TimeoutError) else "FAILED"
            error = "查询预算不足" if isinstance(exc, BudgetExceeded) else type(exc).__name__
            result = DomainResult(task_id=spec.task_id, domain=spec.domain, result_status="FAILED",
                summary="该领域任务尚未取得可用于回答的可靠结果。", missing_information=[spec.goal],
                error=error, observed_at=get_utcnow().isoformat())
        finally:
            audit_result = result.model_dump(mode="json") if result else None
            if audit_result is not None and corrections:
                audit_result["validation_corrections"] = corrections
            await asyncio.shield(self.audit.finish(task_id, state,
                audit_result, error,
                int((perf_counter() - started) * 1000), dict(budget.task_usage[task_id])))
        return result, task_id

    def _validate(self, answer, calls):
        summary = AgentOutput(reply_type="ANSWER" if answer.result_status in {"SUCCESS", "PARTIAL"} else "CLARIFY",
            reply_content=answer.summary, business_facts=answer.business_facts,
            knowledge_references=answer.knowledge_references)
        validate_evidence(summary, calls)
        self.validator.validate_with_calls(summary, calls)
        known_ids = {str(obj[key]) for call in calls if call.success
            for obj in entities(call.result.get("data"))
            for key in ("id", "account_id", "order_id", "product_id", "batch_id", "after_sale_id") if obj.get(key) is not None}
        if any(entity.entity_id not in known_ids for entity in answer.entities):
            raise ValueError("领域实体未被成功工具确认")
        if answer.result_status == "SUCCESS" and not any(call.success for call in calls):
            raise ValueError("缺少成功查询，不能标记SUCCESS")
        if answer.result_status == "NEEDS_CLARIFICATION" and not answer.missing_information:
            raise ValueError("需要澄清时必须提供具体缺失信息")
        for suggestion in answer.action_suggestions:
            self.validator.page_action_validator.validate(suggestion, calls)
        if answer.candidate_reply:
            candidate = self.validator.validate_with_calls(answer.candidate_reply, calls)
            if answer.result_status == "FAILED" and candidate.reply_type == "ANSWER":
                raise ValueError("失败领域任务不能给出肯定的候选业务回答")
            answer = answer.model_copy(update={"candidate_reply": AgentOutput.model_validate(candidate.model_dump())})
        return answer
