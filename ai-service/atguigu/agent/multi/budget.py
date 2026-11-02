"""Atomic root budgets, including calls made by concurrent domain agents."""
import asyncio
import json
from collections import defaultdict
from contextvars import ContextVar
from time import monotonic

tool_attempt_reserved = ContextVar("tool_attempt_reserved", default=None)


class BudgetExceeded(RuntimeError):
    pass


class RunSuperseded(RuntimeError):
    pass


class ValidityUnavailable(RuntimeError):
    """Cannot authorize further work; retry the turn rather than silently discard it."""


class EvidenceConflict(RuntimeError):
    pass


class SharedBudget:
    def __init__(self, settings, validity_check=None):
        self.settings = settings
        self.deadline = monotonic() + settings.agent_run_timeout_seconds
        self.validity_check = validity_check
        self.lock = asyncio.Lock()
        self.model_calls = 0
        self.tool_calls = 0
        self.task_attempts = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self.estimated_tokens = 0
        self.reserved_tokens = 0
        self.task_tools = defaultdict(int)
        self.task_usage = defaultdict(lambda: {"input_tokens": 0, "output_tokens": 0, "model_calls": 0})
        self.cancelled = False
        self.semaphore = asyncio.Semaphore(settings.multi_concurrency)

    def check(self):
        if self.cancelled:
            raise RunSuperseded("本轮输入或执行租约已失效")
        if monotonic() >= self.deadline:
            raise BudgetExceeded("根请求执行超时")

    async def checkpoint(self):
        self.check()
        if self.validity_check is not None and not await self.validity_check():
            self.cancelled = True
            raise RunSuperseded("本轮输入或执行租约已失效")

    @property
    def remaining_seconds(self):
        return max(0, self.deadline - monotonic())

    async def reserve_model(self, messages, schema=None, task_id=None, max_output_tokens=None):
        self.check()
        # Provider-neutral estimate, with headroom. This is not an exact tokenizer or billing cap.
        # Reserve output too; settle actual provider usage or retain the estimate if absent.
        content = json.dumps(messages, default=str, ensure_ascii=False)
        if schema is not None:
            content += json.dumps(schema, default=str, ensure_ascii=False)
        non_ascii = sum(ord(char) > 127 for char in content)
        estimated_input = int(((len(content) - non_ascii) / 3 + non_ascii * 1.5) * 1.15) + 256
        reservation = estimated_input + (max_output_tokens or self.settings.multi_model_output_tokens)
        async with self.lock:
            self.check()
            if self.model_calls >= self.settings.multi_max_model_calls:
                raise BudgetExceeded("根请求模型调用次数已用尽")
            used = self.input_tokens + self.output_tokens + self.estimated_tokens + self.reserved_tokens
            if used + reservation > self.settings.multi_token_budget:
                raise BudgetExceeded("根请求Token预算不足，无法安全预留下一次调用")
            self.model_calls += 1
            self.reserved_tokens += reservation
            self.task_usage[task_id]["model_calls"] += 1
        return reservation

    async def settle_model(self, reservation, usage, task_id=None):
        async with self.lock:
            self.reserved_tokens -= reservation
            if usage and "input_tokens" in usage and "output_tokens" in usage:
                for key in ("input_tokens", "output_tokens"):
                    value = int(usage.get(key, 0))
                    setattr(self, key, getattr(self, key) + value)
                    self.task_usage[task_id][key] += value
            else:
                # Failed calls can still be billed. Do not free their whole reservation.
                self.estimated_tokens += reservation

    async def reserve_task(self):
        async with self.lock:
            self.check()
            if self.task_attempts >= self.settings.multi_max_tasks:
                raise BudgetExceeded("领域任务执行预算已用尽")
            self.task_attempts += 1

    async def reserve_tool(self, task_id):
        async with self.lock:
            self.check()
            if self.tool_calls >= self.settings.multi_max_tools:
                raise BudgetExceeded("根请求查询工具预算已用尽")
            if self.task_tools[task_id] >= self.settings.multi_task_max_tools:
                raise BudgetExceeded("领域任务查询工具预算已用尽")
            self.tool_calls += 1
            self.task_tools[task_id] += 1

    def snapshot(self):
        return {"model_calls": self.model_calls, "tool_calls": self.tool_calls,
                "task_attempts": self.task_attempts, "input_tokens": self.input_tokens,
                "output_tokens": self.output_tokens, "estimated_tokens": self.estimated_tokens,
                "reserved_tokens": self.reserved_tokens, "token_budget": self.settings.multi_token_budget}

    def usage(self):
        return {"input_tokens": self.input_tokens, "output_tokens": self.output_tokens}
