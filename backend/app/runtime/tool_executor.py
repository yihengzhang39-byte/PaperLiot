"""Parse, dispatch, and normalize generic tool calls."""

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from typing import Any

from app.agents.agent_context import AgentContext
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
    history_result: dict[str, Any] | None = None


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


def trace_tool_event(
    trace: list[dict[str, object]] | None,
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
        history_result={"tool": tool_call.name, "status": "error", "summary": error[:300]},
    )


def _missing_required_state(tool_name: str, registry: ToolRegistry, context: AgentContext | None) -> list[str]:
    if context is None:
        return []
    definition = registry.definition(tool_name)
    if definition is None:
        return []
    return [name for name in definition.requires if name not in context.state or context.state[name] is None]


def _result_produced_successfully(result: ToolExecutionResult) -> bool:
    """Treat explicit Tool payload failures as failures for state production."""
    if not result.ok:
        return False
    try:
        payload = json.loads(result.content)
    except (TypeError, json.JSONDecodeError):
        return True
    return not isinstance(payload, dict) or payload.get("success") is not False


def _update_produced_state(result: ToolExecutionResult, registry: ToolRegistry, context: AgentContext | None) -> None:
    if context is None or not _result_produced_successfully(result):
        return
    definition = registry.definition(result.tool_name)
    if definition is not None:
        context.state.update({name: True for name in definition.produces})


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
    parser = payload.get("parser")
    if parser in {"grobid", "pymupdf"}:
        if payload.get("success") is False:
            return f"{parser} failed: {str(payload.get('error', 'unknown error'))[:140]}"
        if isinstance(payload.get("pages"), list):
            return f"{parser} extracted {len(payload['pages'])} page(s)"
        if isinstance(payload.get("sections"), list):
            missing = payload.get("missing_fields", [])
            suffix = ", title missing" if "title" in missing else ""
            return f"{parser} parsed {len(payload['sections'])} sections{suffix}"
    if isinstance(payload.get("chunks"), list):
        return f"retrieved {len(payload['chunks'])} relevant chunks"
    if isinstance(payload.get("papers"), list):
        return f"retrieved context from {len(payload['papers'])} papers"
    if isinstance(payload.get("section_previews"), dict):
        return f"extracted {len(payload['section_previews'])} section previews"
    if "title" in payload:
        return "retrieved paper information"
    return f"{result.tool_name} completed"


def _history_status(result: ToolExecutionResult) -> str:
    if not result.ok:
        return "error"
    try:
        payload = json.loads(result.content)
    except (TypeError, json.JSONDecodeError):
        return "success"
    return "error" if isinstance(payload, dict) and payload.get("success") is False else "success"


def _project_history_result(arguments: dict[str, Any], result: ToolExecutionResult, registry: ToolRegistry) -> dict[str, Any]:
    definition = registry.definition(result.tool_name)
    if definition and definition.project_history_result:
        try:
            projected = definition.project_history_result(arguments, result)
            if isinstance(projected, dict):
                return projected
        except Exception:
            pass
    status = _history_status(result)
    projected: dict[str, Any] = {"tool": result.tool_name, "status": status}
    if status == "error":
        projected["summary"] = (result.error or "Tool reported failure.")[:300]
    else:
        projected["summary"] = _result_summary(result)[:300]
    return projected


def _emit_result(
    event_sink: RuntimeEventSink | None,
    result: ToolExecutionResult,
    step: int | None,
) -> None:
    if event_sink is None:
        return
    status = "success" if result.ok else "error"
    if result.ok:
        try:
            payload = json.loads(result.content)
        except (TypeError, json.JSONDecodeError):
            payload = None
        if isinstance(payload, dict) and payload.get("success") is False:
            status = "error"
    event: dict[str, Any] = {
        "type": "tool_result",
        "tool_call_id": result.tool_call_id,
        "name": result.tool_name,
        "status": status,
        "success": status == "success",
        "summary": _result_summary(result),
        "history_result": result.history_result or {"tool": result.tool_name, "status": status, "summary": _result_summary(result)[:300]},
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
    context: AgentContext | None = None,
) -> ToolExecutionResult:
    """Execute one call and normalize all expected tool-runtime failures."""
    trace_tool_event(trace, "tool_requested", tool_call, step)
    try:
        arguments = parse_arguments(tool_call.arguments)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        trace_tool_event(trace, "tool_failed", tool_call, step)
        result = _error_result(tool_call, f"Invalid arguments: {exc}")
        _emit_result(event_sink, result, step)
        return result

    missing_state = _missing_required_state(tool_call.name, registry, context)
    if missing_state:
        trace_tool_event(trace, "tool_failed", tool_call, step)
        result = _error_result(tool_call, f"Missing required state: {', '.join(missing_state)}")
        _emit_result(event_sink, result, step)
        return result

    trace_tool_event(trace, "tool_started", tool_call, step)
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
        trace_tool_event(trace, "tool_failed", tool_call, step)
        failed = _error_result(tool_call, str(exc))
        _emit_result(event_sink, failed, step)
        return failed
    except Exception as exc:
        trace_tool_event(trace, "tool_failed", tool_call, step)
        failed = _error_result(tool_call, f"Tool execution failed: {type(exc).__name__}")
        _emit_result(event_sink, failed, step)
        return failed

    trace_tool_event(trace, "tool_succeeded", tool_call, step)
    completed = ToolExecutionResult(
        tool_call_id=tool_call.id,
        tool_name=tool_call.name,
        ok=True,
        content=_serialize_result(result),
    )
    completed = replace(completed, history_result=_project_history_result(arguments, completed, registry))
    _update_produced_state(completed, registry, context)
    _emit_result(event_sink, completed, step)
    return completed


def execute_tool_calls(
    tool_calls: Sequence[AgentToolCall],
    registry: ToolRegistry,
    *,
    trace: list[dict[str, Any]] | None = None,
    step: int | None = None,
    event_sink: RuntimeEventSink | None = None,
    context: AgentContext | None = None,
) -> list[ToolExecutionResult]:
    """Schedule safe calls concurrently while preserving result order."""
    from app.core.config import get_tool_runtime_config
    from app.runtime.tool_scheduler import execute_scheduled_tool_calls

    return execute_scheduled_tool_calls(
        tool_calls,
        registry,
        context=context,
        max_parallel_tool_calls=get_tool_runtime_config().max_parallel_tool_calls,
        trace=trace,
        step=step,
        event_sink=event_sink,
    )
