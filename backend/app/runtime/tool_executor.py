"""Parse, dispatch, and normalize generic tool calls."""

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from app.runtime.tool_registry import ToolRegistry
from app.services.llm_service import AgentToolCall


RuntimeEventSink = Callable[[dict[str, Any]], None]


@dataclass(frozen=True)
class ToolExecutionResult:
    """Normalized output for one tool call, ready for an LLM tool message."""

    tool_call_id: str
    tool_name: str
    ok: bool
    content: str
    error: str | None = None


def parse_arguments(arguments: str | dict[str, Any]) -> dict[str, Any]:
    """Safely parse OpenAI-compatible function arguments into an object."""
    if isinstance(arguments, dict):
        return arguments
    parsed = json.loads(arguments)
    if not isinstance(parsed, dict):
        raise ValueError("Tool arguments must be a JSON object.")
    return parsed


def dispatch(tool_name: str, arguments: dict[str, Any], registry: ToolRegistry) -> Any:
    """Find and invoke a registered tool with parsed arguments."""
    tool = registry.get(tool_name)
    if tool is None:
        raise LookupError(f"Unknown tool: {tool_name}")
    return tool(**arguments)


def _serialize_result(result: Any) -> str:
    if isinstance(result, str):
        return result
    try:
        return json.dumps(result, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(result)


def _trace(
    trace: list[dict[str, Any]] | None,
    event: str,
    tool_call: AgentToolCall,
    step: int | None,
) -> None:
    if trace is None:
        return
    item: dict[str, Any] = {
        "event": event,
        "tool_name": tool_call.name,
        "tool_call_id": tool_call.id,
    }
    if step is not None:
        item["step"] = step
    trace.append(item)


def _error_result(tool_call: AgentToolCall, error: str) -> ToolExecutionResult:
    return ToolExecutionResult(
        tool_call_id=tool_call.id,
        tool_name=tool_call.name,
        ok=False,
        content=json.dumps({"ok": False, "error": error}, ensure_ascii=False),
        error=error,
    )


def _result_summary(result: ToolExecutionResult) -> str:
    """Build a UI-safe result summary without exposing tool payloads."""
    if not result.ok:
        return (result.error or "Tool execution failed.")[:200]
    try:
        payload = json.loads(result.content)
    except (TypeError, json.JSONDecodeError):
        return f"{result.tool_name} completed"
    if not isinstance(payload, dict):
        return f"{result.tool_name} completed"
    if isinstance(payload.get("chunks"), list):
        return f"retrieved {len(payload['chunks'])} relevant chunks"
    if isinstance(payload.get("papers"), list):
        return f"retrieved context from {len(payload['papers'])} papers"
    if isinstance(payload.get("section_previews"), dict):
        return f"extracted {len(payload['section_previews'])} section previews"
    if "title" in payload:
        return "retrieved paper information"
    return f"{result.tool_name} completed"


def _emit_result(
    event_sink: RuntimeEventSink | None,
    result: ToolExecutionResult,
    step: int | None,
) -> None:
    if event_sink is None:
        return
    event: dict[str, Any] = {
        "type": "tool_result",
        "tool_call_id": result.tool_call_id,
        "name": result.tool_name,
        "status": "success" if result.ok else "error",
        "summary": _result_summary(result),
    }
    if step is not None:
        event["step"] = step
    event_sink(event)


def execute_tool_call(
    tool_call: AgentToolCall,
    registry: ToolRegistry,
    *,
    trace: list[dict[str, Any]] | None = None,
    step: int | None = None,
    event_sink: RuntimeEventSink | None = None,
) -> ToolExecutionResult:
    """Execute one call and normalize all expected tool-runtime failures."""
    _trace(trace, "tool_requested", tool_call, step)
    try:
        arguments = parse_arguments(tool_call.arguments)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        _trace(trace, "tool_failed", tool_call, step)
        result = _error_result(tool_call, f"Invalid arguments: {exc}")
        _emit_result(event_sink, result, step)
        return result

    _trace(trace, "tool_started", tool_call, step)
    if event_sink is not None:
        event: dict[str, Any] = {
            "type": "tool_start",
            "tool_call_id": tool_call.id,
            "name": tool_call.name,
        }
        if step is not None:
            event["step"] = step
        event_sink(event)
    try:
        result = dispatch(tool_call.name, arguments, registry)
    except LookupError as exc:
        _trace(trace, "tool_failed", tool_call, step)
        failed = _error_result(tool_call, str(exc))
        _emit_result(event_sink, failed, step)
        return failed
    except Exception as exc:
        _trace(trace, "tool_failed", tool_call, step)
        failed = _error_result(tool_call, f"Tool execution failed: {type(exc).__name__}")
        _emit_result(event_sink, failed, step)
        return failed

    _trace(trace, "tool_succeeded", tool_call, step)
    completed = ToolExecutionResult(
        tool_call_id=tool_call.id,
        tool_name=tool_call.name,
        ok=True,
        content=_serialize_result(result),
    )
    _emit_result(event_sink, completed, step)
    return completed


def execute_tool_calls(
    tool_calls: Sequence[AgentToolCall],
    registry: ToolRegistry,
    *,
    trace: list[dict[str, Any]] | None = None,
    step: int | None = None,
    event_sink: RuntimeEventSink | None = None,
) -> list[ToolExecutionResult]:
    """Execute calls sequentially and preserve the LLM-provided order."""
    return [
        execute_tool_call(tool_call, registry, trace=trace, step=step, event_sink=event_sink)
        for tool_call in tool_calls
    ]
