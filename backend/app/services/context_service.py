"""Measure complete Agent inputs and enforce a shared input/output window."""

import json
from dataclasses import asdict, dataclass
from typing import Any

from app.core.config import ContextConfig


@dataclass(frozen=True)
class ContextBudget:
    W: int
    I: int
    O: int
    S: int
    B: int
    T: float
    G: float
    measurement_kind: str
    measurement_method: str
    trigger_reached: bool
    hard_budget_exceeded: bool
    input_limit: int | None = None
    output_limit: int | None = None
    input_limit_exceeded: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    def require_sendable(self) -> None:
        if self.hard_budget_exceeded or self.input_limit_exceeded:
            raise ContextBudgetError(self)


class ContextBudgetError(RuntimeError):
    code = "context_budget_exceeded"

    def __init__(self, budget: ContextBudget) -> None:
        self.budget = budget
        super().__init__(
            f"上下文预算不足：I={budget.I}, B={budget.B}，必须 I < B；"
            f"独立输入上限={budget.input_limit}，安全余量 S={budget.S}。当前有效上下文无法发送，已停止请求；不会通过删除当前轮次绕过预算。"
        )


def measure_input(messages: list[dict[str, Any]], tools: list[dict[str, Any]], *, model: str) -> tuple[int, str]:
    """Count every serialized field, including schemas, tool arguments and results."""
    serialized = json.dumps({"messages": messages, "tools": tools}, ensure_ascii=False)
    overhead = 16 * (len(messages) + len(tools) + 1)
    try:
        import tiktoken
    except ImportError:
        tiktoken = None
    if tiktoken is not None:
        try:
            encoding = tiktoken.encoding_for_model(model)
        except (KeyError, ValueError):
            encoding = None
        if encoding is not None:
            return len(encoding.encode(serialized, disallowed_special=())) + overhead, f"tiktoken:{encoding.name}:json_envelope"
    # ponytail: UTF-8 bytes overestimate common text; replace with the provider's
    # full chat/template counter when available. Hidden framing is still unknown.
    return len(serialized.encode("utf-8")) + overhead, "utf8_bytes:json_envelope"


def check_request(
    messages: list[dict[str, Any]], tools: list[dict[str, Any]], config: ContextConfig, *, model: str = "",
) -> ContextBudget:
    """Return a reusable check; record it, then call require_sendable before sending."""
    count, method = measure_input(messages, tools, model=model)
    budget = config.window - config.max_output_tokens - config.safety_tokens
    trigger = min(config.trigger_ratio * config.window, budget)
    target = min(config.target_ratio * config.window, config.target_budget_ratio * budget)
    return ContextBudget(
        W=config.window, I=count, O=config.max_output_tokens, S=config.safety_tokens,
        B=budget, T=trigger, G=target,
        measurement_kind="estimated", measurement_method=method,
        trigger_reached=count >= trigger, hard_budget_exceeded=count >= budget,
        input_limit=config.input_limit, output_limit=config.output_limit,
        input_limit_exceeded=config.input_limit is not None and count + config.safety_tokens > config.input_limit,
    )
