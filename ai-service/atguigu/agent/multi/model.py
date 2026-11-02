from langchain.agents.middleware import AgentMiddleware

from atguigu.agent.multi.protocol import DomainAnswer
from atguigu.agent.multi.budget import tool_attempt_reserved


class BudgetMiddleware(AgentMiddleware):
    async def awrap_tool_call(self, request, handler):
        context = request.runtime.context
        name = request.tool_call["name"]
        if context.allowed_tool_names is None or name not in context.allowed_tool_names:
            raise PermissionError("领域工具范围不允许此调用")
        await context.budget.checkpoint()
        # Count parameter/schema failures too, before LangChain validates the tool arguments.
        await context.budget.reserve_tool(context.task_id)
        marker = tool_attempt_reserved.set((context.run_id, context.task_id, request.tool_call["id"]))
        try:
            return await handler(request)
        finally:
            tool_attempt_reserved.reset(marker)

    async def awrap_model_call(self, request, handler):
        context = request.runtime.context
        budget = context.budget
        await budget.checkpoint()
        from langchain_core.utils.function_calling import convert_to_openai_tool
        schemas = [convert_to_openai_tool(tool) for tool in request.tools]
        schemas.append(DomainAnswer.model_json_schema())
        reservation = await budget.reserve_model(
            [request.system_message, *request.messages], schemas, context.task_id)
        usage = None
        try:
            response = await handler(request.override(model_settings={**request.model_settings,
                "parallel_tool_calls": False, "max_tokens": budget.settings.multi_model_output_tokens}))
            items = [getattr(message, "usage_metadata", None) for message in response.result]
            items = [item for item in items if item]
            if items:
                usage = {key: sum(item.get(key, 0) for item in items)
                         for key in ("input_tokens", "output_tokens")}
            return response
        finally:
            await budget.settle_model(reservation, usage, context.task_id)


class StructuredCaller:
    def __init__(self, model, budget, trace):
        self.model = model
        self.budget = budget
        self.trace = trace

    async def call(self, schema, prompt, messages, stage):
        from langchain_core.messages import HumanMessage, SystemMessage
        from pydantic import ValidationError

        current = [SystemMessage(prompt), *messages]
        last_error = None
        for attempt in range(2):
            await self.budget.checkpoint()
            reservation = await self.budget.reserve_model(current, schema.model_json_schema())
            usage = None
            try:
                raw = await self.model.with_structured_output(schema, method="function_calling",
                    include_raw=True).ainvoke(current)
                usage = getattr(raw.get("raw"), "usage_metadata", None)
                parsed = raw.get("parsed")
                if raw.get("parsing_error") or parsed is None:
                    raise ValueError("结构化结果不符合协议")
                result = schema.model_validate(parsed)
                self.trace.setdefault("model_stages", []).append({"stage": stage, "attempt": attempt + 1,
                    "success": True, "token_usage": usage})
                return result
            except (ValidationError, ValueError) as exc:
                last_error = exc
                self.trace.setdefault("model_stages", []).append({"stage": stage, "attempt": attempt + 1,
                    "success": False, "error": str(exc)[:500], "token_usage": usage})
                current.append(HumanMessage("上一次输出未通过结构校验。请严格按协议重新输出：" + str(exc)[:700]))
            finally:
                await self.budget.settle_model(reservation, usage)
        raise ValueError(f"{stage}结构化输出失败") from last_error
