from dataclasses import dataclass
from typing import Any
from typing_extensions import NotRequired

from langchain.agents import AgentState

from atguigu.agent.harness.skills.definition import SkillCode
from atguigu.agent.llm.output import AgentOutput


@dataclass(frozen=True)
class AgentRuntimeContext:
    """提供给 Agent 和业务工具的单次运行信息"""

    run_id: str
    conversation_id: str
    user_id: str
    access_token: str
    tenant_id: str
    welfare_project_id: str
    task_id: str | None = None
    allowed_tool_names: frozenset[str] | None = None
    bound_entities: tuple[tuple[str, str], ...] = ()
    budget: Any = None  # Request-local shared budget; never serialized into graph state.


class AgentExecutionState(AgentState[AgentOutput]):  # type:ignore
    """保存 Agent 多步骤执行期间可以变化的状态。"""

    active_skill_code: NotRequired[SkillCode]
