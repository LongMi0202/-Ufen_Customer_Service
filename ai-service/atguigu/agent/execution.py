"""Select architecture inside the same commit and service, retaining the public contract."""
from atguigu.agent.harness.run.executor import AgentExecutor
from atguigu.agent.multi.executor import MultiAgentExecutor
from atguigu.common.config import get_settings


class ModeExecutor:
    def __init__(self, app_state, output_validator):
        self.app_state, self.output_validator = app_state, output_validator
        self.trace = None
        self.token_usage = {"input_tokens": 0, "output_tokens": 0}

    async def execute(self, request, context):
        mode = request.execution_mode or get_settings().agent_mode
        if mode == "single":
            executor = AgentExecutor(self.app_state.agent, self.output_validator)
        else:
            executor = MultiAgentExecutor(self.app_state.multi_model, self.app_state.domain_agents,
                self.output_validator, self.app_state.domain_semaphore)
        try:
            output = await executor.execute(request, context)
            self.token_usage = output.token_usage
            return output
        finally:
            self.trace = getattr(executor, "trace", None)
            self.token_usage = getattr(executor, "token_usage", self.token_usage)
