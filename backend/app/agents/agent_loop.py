"""Business-independent LLM and tool-call execution loop."""

import json
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from app.agents.agent_context import AgentContext
from app.runtime.tool_executor import execute_tool_calls
from app.services.debug_trace_service import DebugTraceService
from app.runtime.tool_registry import Tool, ToolRegistry
from app.services.llm_service import (
    AgentLLMDelta,
    AgentLLMResponse,
    AgentToolCall,
    call_llm_with_tools,
    call_llm_with_tools_stream,
    reset_llm_debug_trace,
    set_llm_debug_trace,
)


LLMCaller = Callable[[list[dict[str, Any]], list[dict[str, Any]]], AgentLLMResponse]
LLMStreamCaller = Callable[[list[dict[str, Any]], list[dict[str, Any]]], Iterable[AgentLLMDelta]]
AgentEventSink = Callable[[dict[str, Any]], None]


class AgentMaxStepsError(RuntimeError):
    """Raised when an agent consumes its configured LLM-call budget."""

    def __init__(self, step: int, max_steps: int) -> None:
        super().__init__(f"Agent exceeded maximum number of steps ({step}/{max_steps}).")
        self.step = step
        self.max_steps = max_steps


@dataclass(frozen=True)
class AgentLoopResult:
    """The final answer and its mutated execution context."""

    final_answer: str
    context: AgentContext
    finish_reason: str = "final_answer"
    events: list[dict[str, Any]] = field(default_factory=list)


def _tool_call_message(tool_call: AgentToolCall) -> dict[str, Any]:
    arguments = tool_call.arguments
    if isinstance(arguments, dict):
        arguments = json.dumps(arguments, ensure_ascii=False)
    return {
        "id": tool_call.id,
        "type": "function",
        "function": {"name": tool_call.name, "arguments": arguments},
    }


def _event_arguments(arguments: str | dict[str, Any]) -> dict[str, Any]:
    """Parse arguments only for the UI event; Tool Runtime remains authoritative."""
    if isinstance(arguments, dict):
        return arguments
    try:
        parsed = json.loads(arguments)
    except (TypeError, json.JSONDecodeError):
        return {"raw": arguments}
    return parsed if isinstance(parsed, dict) else {"raw": arguments}


def _merge_stream_tool_calls(
    deltas: Iterable[AgentLLMDelta],
    on_content: Callable[[str], None],
) -> tuple[AgentLLMResponse, bool]:
    """Collect fragmented calls while forwarding real assistant content deltas."""
    content_parts: list[str] = []
    calls: dict[int, dict[str, str]] = {}
    saw_tool_call = False
    for delta in deltas:
        for tool_delta in delta.tool_calls or []:
            saw_tool_call = True
            current = calls.setdefault(tool_delta.index, {"id": "", "name": "", "arguments": ""})
            if tool_delta.id:
                current["id"] = tool_delta.id
            if tool_delta.name:
                current["name"] = tool_delta.name
            current["arguments"] += tool_delta.arguments_delta
        if delta.content:
            content_parts.append(delta.content)
            on_content(delta.content)
    tool_calls = [
        AgentToolCall(
            id=value["id"] or f"call_{index + 1}",
            name=value["name"],
            arguments=value["arguments"] or "{}",
        )
        for index, value in sorted(calls.items())
        if value["name"]
    ]
    return AgentLLMResponse(content="".join(content_parts), tool_calls=tool_calls), bool(tool_calls)


def run_agent(
    context: AgentContext,
    *,
    tools: Mapping[str, Tool] | None = None,
    tool_registry: ToolRegistry | None = None,
    tool_specs: list[dict[str, Any]] | None = None,
    llm_call: LLMCaller = call_llm_with_tools,
    llm_stream: LLMStreamCaller | None = None,
    event_sink: AgentEventSink | None = None,
    debug_trace: DebugTraceService | None = None,
) -> AgentLoopResult:
    """Run LLM -> tool calls -> tool results until the LLM returns an answer."""
    if tools is not None and tool_registry is not None:
        raise ValueError("Pass either tools or tool_registry, not both.")
    registry = tool_registry or ToolRegistry.from_mapping(tools or {})
    tool_specs = tool_specs or []
    trace = context.metadata.setdefault("agent_trace", [])
    message_origins = context.metadata.setdefault("message_origins", [])
    if not isinstance(message_origins, list):
        message_origins = []
        context.metadata["message_origins"] = message_origins
    while len(message_origins) < len(context.messages):
        message_origins.append({"origin": "unknown"})
    events: list[dict[str, Any]] = []

    def emit(event_type: str, **data: Any) -> None:
        event = {"type": event_type, **data}
        events.append(event)
        if event_sink is not None:
            event_sink(event)

    def emit_runtime(event: dict[str, Any]) -> None:
        event_type = event.get("type")
        if isinstance(event_type, str):
            emit(event_type, **{key: value for key, value in event.items() if key != "type"})

    for message in reversed(context.messages):
        if message.get("role") == "user" and isinstance(message.get("content"), str):
            emit("user_message", content=message["content"])
            break
    emit("agent_start", max_steps=context.max_steps)

    try:
        while context.step < context.max_steps:
            context.step += 1
            emit("step_start", step=context.step)
            emit("llm_start", step=context.step)
            provider_adapter = llm_call is call_llm_with_tools or llm_stream is call_llm_with_tools_stream
            if debug_trace is not None and not provider_adapter:
                debug_trace.record_llm_input(context.messages, message_origins, step=context.step)

            streamed_content: list[str] = []
            debug_token = set_llm_debug_trace(debug_trace, context.step, message_origins) if debug_trace is not None else None
            try:
                if llm_stream is None:
                    response = llm_call(context.messages, tool_specs)
                else:
                    def emit_stream_content(delta: str) -> None:
                        if delta:
                            streamed_content.append(delta)
                            emit("llm_delta", step=context.step, delta=delta)

                    response, _ = _merge_stream_tool_calls(
                        llm_stream(context.messages, tool_specs),
                        emit_stream_content,
                    )
            finally:
                if debug_token is not None:
                    reset_llm_debug_trace(debug_token)

            if debug_trace is not None:
                debug_trace.record_llm_output(response, step=context.step)

            context.messages.append(
                {
                    "role": "assistant",
                    "content": response.content,
                    "tool_calls": [_tool_call_message(tool_call) for tool_call in response.tool_calls],
                }
            )
            message_origins.append({"origin": "current_run_assistant"})
            trace.append({"step": context.step, "tool_call_count": len(response.tool_calls)})

            # Only provider-visible assistant content is emitted here; hidden
            # reasoning fields are never part of AgentLLMResponse.
            emit("llm_message", step=context.step, content=response.content)
            if response.content.strip():
                emit("assistant_trace", step=context.step, content=response.content)

            if not response.tool_calls:
                emit("final_start", step=context.step)
                for delta in streamed_content or [response.content]:
                    if delta:
                        emit("final_delta", step=context.step, delta=delta)
                emit("final_end", step=context.step)
                emit("step_end", step=context.step, status="final")
                emit("agent_done")
                trace.append({"step": context.step, "finish_reason": "final_answer"})
                return AgentLoopResult(final_answer=response.content, context=context, events=events)

            for order_index, tool_call in enumerate(response.tool_calls):
                emit(
                    "tool_call",
                    step=context.step,
                    tool_call_id=tool_call.id,
                    name=tool_call.name,
                    arguments=_event_arguments(tool_call.arguments),
                )
                if debug_trace is not None:
                    debug_trace.record_tool_call(tool_call, step=context.step, order_index=order_index)
            tool_results = execute_tool_calls(
                response.tool_calls,
                registry,
                trace=trace,
                step=context.step,
                event_sink=emit_runtime,
                context=context,
            )
            for order_index, result in enumerate(tool_results):
                if debug_trace is not None:
                    debug_trace.record_tool_result(result, step=context.step, order_index=order_index)
                context.messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": result.tool_call_id,
                        "name": result.tool_name,
                        "content": result.content,
                    }
                )
                message_origins.append({"origin": "current_run_tool_result"})
            emit("step_end", step=context.step, status="tool_calls")

        trace.append({"step": context.step, "finish_reason": "max_steps"})
        raise AgentMaxStepsError(context.step, context.max_steps)
    except AgentMaxStepsError:
        emit("error", code="agent_max_steps", message="Paper Agent reached max steps.")
        emit("agent_done", status="error")
        raise
    except Exception:
        emit("error", code="agent_failed", message="Paper Agent execution failed.")
        emit("agent_done", status="error")
        raise
