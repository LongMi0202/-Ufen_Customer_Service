from enum import StrEnum
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from atguigu.agent.harness.skills.definition import SkillCode
from atguigu.agent.llm.output import AgentOutput, BusinessFactReference, PageActionRequest


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Domain(StrEnum):
    PRODUCT = "product"
    ORDER = "order"
    LOGISTICS = "logistics"
    AFTERSALES = "aftersales"
    POLICY = "policy"
    POINTS = "points"

    @property
    def skill(self) -> SkillCode:
        return SkillCode(f"{self.value}-service")


class EntityBinding(StrictModel):
    entity_type: Literal["order", "product", "after_sale", "account", "batch"]
    entity_id: str = Field(min_length=1, max_length=100)


class RouteDecision(StrictModel):
    route_kind: Literal["SINGLE", "MULTI", "CLARIFY", "HANDOFF", "CHAT"]
    domains: list[Domain] = Field(default_factory=list, max_length=6)
    operation_intent: Literal["QUERY", "NAVIGATE", "WRITE_GUIDANCE"] = "QUERY"
    entity_hints: list[EntityBinding] = Field(default_factory=list, max_length=12)
    clarification: str | None = Field(default=None, max_length=500)
    reason: str = Field(max_length=500)

    @model_validator(mode="after")
    def check_route(self) -> Self:
        if len(set(self.domains)) != len(self.domains):
            raise ValueError("领域不能重复")
        if self.route_kind == "SINGLE" and len(self.domains) != 1:
            raise ValueError("SINGLE 必须且只能选择一个领域")
        if self.route_kind == "MULTI" and len(self.domains) < 2:
            raise ValueError("MULTI 至少选择两个领域")
        if self.route_kind == "CLARIFY" and not self.clarification:
            raise ValueError("CLARIFY 必须有具体澄清问题")
        if self.route_kind in {"CHAT", "HANDOFF"} and self.domains:
            raise ValueError("终止路由不能委派领域任务")
        return self


class TaskSpec(StrictModel):
    task_id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]{0,29}$")
    domain: Domain
    goal: str = Field(min_length=1, max_length=1200)
    depends_on: list[str] = Field(default_factory=list, max_length=8)
    entity_bindings: list[EntityBinding] = Field(default_factory=list, max_length=12)
    expected_outputs: list[str] = Field(default_factory=list, max_length=10)


class TaskPlan(StrictModel):
    plan_version: int = Field(default=1, ge=1, le=2)
    tasks: list[TaskSpec] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def validate_dag(self) -> Self:
        ids = [task.task_id for task in self.tasks]
        if len(set(ids)) != len(ids):
            raise ValueError("任务ID重复")
        pending = {task.task_id: set(task.depends_on) for task in self.tasks}
        if any(not deps <= set(ids) for deps in pending.values()):
            raise ValueError("依赖不存在")
        resolved: set[str] = set()
        while pending:
            ready = {key for key, deps in pending.items() if deps <= resolved}
            if not ready:
                raise ValueError("任务依赖存在环")
            resolved |= ready
            pending = {key: value for key, value in pending.items() if key not in ready}
        return self


class DomainAnswer(StrictModel):
    result_status: Literal["SUCCESS", "PARTIAL", "NEEDS_CLARIFICATION", "FAILED"]
    summary: str = Field(min_length=1, max_length=2500)
    entities: list[EntityBinding] = Field(default_factory=list, max_length=20)
    business_facts: list[BusinessFactReference] = Field(default_factory=list, max_length=30)
    knowledge_references: list[str] = Field(default_factory=list, max_length=20)
    missing_information: list[str] = Field(default_factory=list, max_length=10)
    required_domains: list[Domain] = Field(default_factory=list, max_length=6)
    error: str | None = Field(default=None, max_length=500)
    action_suggestions: list[PageActionRequest] = Field(default_factory=list, max_length=1)
    candidate_reply: AgentOutput | None = None


class DomainResult(DomainAnswer):
    # Identity, timestamps and evidence IDs are set by the server, never trusted from a model.
    task_id: str
    domain: Domain
    evidence_ids: list[str] = Field(default_factory=list)
    observed_at: str


class SupervisorReview(StrictModel):
    decision: Literal["FINISH", "REPLAN", "CLARIFY", "HANDOFF"]
    additional_tasks: list[TaskSpec] = Field(default_factory=list, max_length=4)
    message: str = Field(default="", max_length=500)

    @model_validator(mode="after")
    def check_review(self) -> Self:
        if (self.decision == "REPLAN") != bool(self.additional_tasks):
            raise ValueError("只有REPLAN需要且必须提供补充任务")
        if self.decision in {"CLARIFY", "HANDOFF"} and not self.message:
            raise ValueError("澄清或人工接管必须提供原因")
        return self
