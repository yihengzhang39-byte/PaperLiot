"""Bounded scheduling for Tool calls already selected by an LLM."""

from concurrent.futures import ThreadPoolExecutor
from collections.abc import Sequence
import inspect

from app.agents.agent_context import AgentContext
from app.runtime.tool_executor import (
    RuntimeEventSink,
    ToolExecutionResult,
    execute_tool_call,
    parse_arguments,
    trace_tool_event,
)
from app.runtime.tool_registry import ToolRegistry
from app.services.llm_service import AgentToolCall


def _is_concurrency_safe(
    tool_call: AgentToolCall,
    registry: ToolRegistry,
    context: AgentContext | None,
) -> bool:
    """Return True only for an explicit, successful safety declaration."""
    try:
        arguments = parse_arguments(tool_call.arguments)
    except (TypeError, ValueError):
        return False
    definition = registry.definition(tool_call.name)
    if definition is None or definition.is_concurrency_safe is None:
        return False
    try:
        inspect.signature(definition.tool).bind(**arguments)
    except (TypeError, ValueError):
        return False
    if context is not None and any(
        name not in context.state or context.state[name] is None for name in definition.requires
    ):
        return False
    try:
        return definition.is_concurrency_safe(arguments) is True
    except Exception:
        return False


def _record_trace(
    trace: list[dict[str, object]] | None,
    tool_call: AgentToolCall,
    result: ToolExecutionResult,
    step: int | None,
) -> None:
    trace_tool_event(trace, "tool_requested", tool_call, step)
    if not (result.error or "").startswith(("Invalid arguments:", "Missing required state:")):
        trace_tool_event(trace, "tool_started", tool_call, step)
    trace_tool_event(trace, "tool_succeeded" if result.ok else "tool_failed", tool_call, step)


def _execute_pool(
    tool_calls: list[AgentToolCall],
    registry: ToolRegistry,
    *,
    context: AgentContext | None,
    max_parallel_tool_calls: int,
    trace: list[dict[str, object]] | None,
    step: int | None,
    event_sink: RuntimeEventSink | None,
) -> list[ToolExecutionResult]:
    def execute(tool_call: AgentToolCall) -> ToolExecutionResult:
        return execute_tool_call(tool_call, registry, context=context, step=step, event_sink=event_sink)

    if max_parallel_tool_calls == 1:
        results = [execute(tool_call) for tool_call in tool_calls]
    else:
        with ThreadPoolExecutor(max_workers=min(max_parallel_tool_calls, len(tool_calls))) as pool:
            futures = [pool.submit(execute, tool_call) for tool_call in tool_calls]
            results = [future.result() for future in futures]
    for tool_call, result in zip(tool_calls, results, strict=True):
        _record_trace(trace, tool_call, result, step)
    return results


def execute_scheduled_tool_calls(
    tool_calls: Sequence[AgentToolCall],
    registry: ToolRegistry,
    *,
    context: AgentContext | None = None,
    max_parallel_tool_calls: int,
    trace: list[dict[str, object]] | None = None,
    step: int | None = None,
    event_sink: RuntimeEventSink | None = None,
) -> list[ToolExecutionResult]:
    """Run contiguous safe calls in bounded pools and all others as barriers."""
    if isinstance(max_parallel_tool_calls, bool) or not isinstance(max_parallel_tool_calls, int) or max_parallel_tool_calls < 1:
        raise ValueError("max_parallel_tool_calls must be a positive integer")
    results: list[ToolExecutionResult] = []
    safe_pool: list[AgentToolCall] = []

    def flush_pool() -> None:
        nonlocal safe_pool
        for start in range(0, len(safe_pool), max_parallel_tool_calls):
            results.extend(
                _execute_pool(
                    safe_pool[start : start + max_parallel_tool_calls],
                    registry,
                    context=context,
                    max_parallel_tool_calls=max_parallel_tool_calls,
                    trace=trace,
                    step=step,
                    event_sink=event_sink,
                )
            )
        safe_pool = []

    for tool_call in tool_calls:
        if _is_concurrency_safe(tool_call, registry, context):
            safe_pool.append(tool_call)
            continue
        flush_pool()
        results.extend(
            _execute_pool(
                [tool_call],
                registry,
                context=context,
                max_parallel_tool_calls=1,
                trace=trace,
                step=step,
                event_sink=event_sink,
            )
        )
    flush_pool()
    return results
