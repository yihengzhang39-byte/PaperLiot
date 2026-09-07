"""Business-independent LLM and tool-call execution loop."""

import json
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from app.agents.agent_context import AgentContext
from app.core.config import ContextConfigError, get_context_config, get_tool_result_config
from app.services import llm_service
from app.services.context_service import ContextBudgetError, check_request, measure_input
from app.services.context_compaction_service import compact_for_request
from app.runtime.tool_executor import execute_tool_calls
from app.services.tool_result_service import shrink_readable_result
from app.services.debug_trace_service import DebugTraceService
from app.runtime.tool_registry import Tool, ToolRegistry
from app.services.llm_service import (
    AgentLLMDelta,
    AgentLLMResponse,
    AgentToolCall,
    LLMOutputTruncatedError,
    LLMStreamInterruptedError,
    LLMProviderError,
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
    llm_response: AgentLLMResponse | None = None


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
    usage = None
    finish_reason = None
    for delta in deltas:
        if delta.usage is not None:
            usage = {**(usage or {}), **delta.usage}
        if delta.finish_reason is not None:
            finish_reason = delta.finish_reason
        for tool_delta in delta.tool_calls or []:
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
    return AgentLLMResponse(content="".join(content_parts), tool_calls=tool_calls, usage=usage, finish_reason=finish_reason), bool(tool_calls)


def _prepare_overflow_retry(context, tools, llm_config, budget_config, *, summary_call, debug_trace, emit_runtime):
    """Only reduce the failed request; tool execution and turn state stay in place."""
    recovery = context.metadata.setdefault("context_recovery", {"retries": 0, "events": []})
    before = check_request(context.messages, tools, budget_config, model=llm_config.model)
    changes = []
    for index, message in enumerate(context.messages):
        if index < context.history_end or message.get("role") != "tool":
            continue
        content = message.get("content", "")
        candidate = shrink_readable_result(message.get("name", ""), content)
        if candidate != content:
            projected = {**message, "content": candidate}
            if measure_input([projected], [], model=llm_config.model)[0] < measure_input([message], [], model=llm_config.model)[0]:
                context.messages[index] = projected
                changes.append({"tool_call_id": message.get("tool_call_id"), "name": message.get("name"), "before_chars": len(content), "after_chars": len(candidate)})
    after = compact_for_request(context, tools, llm_config, budget_config, summary_call=summary_call, debug_trace=debug_trace, event_sink=emit_runtime, force=True)
    reason = "input_reduced" if after.I < before.I else "no_input_reduction"
    if after.hard_budget_exceeded or after.input_limit_exceeded:
        reason = "hard_budget_exceeded"
    can_retry = reason == "input_reduced"
    compaction = context.metadata.get("compaction", {})
    event = {"status": "retrying" if can_retry else "stopped", "reason": reason, "step": context.step,
             "trigger_reason": "provider_context_exceeded", "input_tokens_before": before.I, "input_tokens_after": after.I,
             "measurement_kind": after.measurement_kind, "budget": after.as_dict(), "tool_reductions": changes,
             "summary_calls": compaction.get("summary_calls", 0), "compaction_stop_reason": compaction.get("stop_reason"),
             "revision": compaction.get("revision", 0)}
    if can_retry:
        recovery["retries"] += 1  # Reserve before the retry, shared across every step.
    event["retries"] = recovery["retries"]
    recovery["events"].append(event)
    if debug_trace is not None:
        debug_trace.record_recovery(event, step=context.step)
        debug_trace.record_context_budget(after.as_dict(), step=context.step)
    context.metadata.setdefault("context_budgets", []).append({"step": context.step, "phase": "overflow_recovery", **after.as_dict()})
    emit_runtime({"type": "context_status", "step": context.step, "phase": event["status"],
                  "message": "正在缩减上下文后重试" if can_retry else "上下文仍无法满足模型容量，本次请求已停止；原始聊天仍保留。",
                  "reason": reason, "summary_calls": event["summary_calls"], "retries": recovery["retries"]})
    return after if can_retry else None


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
    summary_call=None,
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
        try:
            llm_config = llm_service.get_llm_config()
        except ValueError as exc:
            raise ContextConfigError(str(exc)) from exc
        budget_config = get_context_config(llm_config)
        get_tool_result_config()
        while context.step < context.max_steps:
            context.step += 1
            emit("step_start", step=context.step)
            budget = compact_for_request(context, tool_specs, llm_config, budget_config, summary_call=summary_call, debug_trace=debug_trace, event_sink=emit_runtime)
            context.metadata.setdefault("context_budgets", []).append({"step": context.step, **budget.as_dict()})
            if debug_trace is not None:
                debug_trace.record_context_budget(budget.as_dict(), step=context.step)
                if context.history is not None:
                    debug_trace.record_compaction({key: value for key, value in context.metadata["compaction"].items() if key != "attempts"}, step=context.step)
            if budget.hard_budget_exceeded or budget.input_limit_exceeded:
                sources = {
                    "system 和 memory": measure_input(context.messages[:1], [], model=llm_config.model)[0],
                    "工具定义": measure_input([], tool_specs, model=llm_config.model)[0],
                    "旧历史": measure_input(context.messages[1:context.history_end], [], model=llm_config.model)[0],
                    "当前轮次": measure_input(context.messages[context.history_end:], [], model=llm_config.model)[0],
                }
                context.metadata["budget_sources"] = sources
                error = ContextBudgetError(budget)
                error.args = (str(error) + " 主要来源（分别估算）：" + json.dumps(sources, ensure_ascii=False),)
                raise error
            emit("llm_start", step=context.step)
            caller = llm_call if llm_stream is None else llm_stream
            provider_adapter = caller in (call_llm_with_tools, call_llm_with_tools_stream)
            provider_options = {
                "max_output_tokens": budget.O,
                "output_token_parameter": budget_config.output_token_parameter,
                "config": llm_config,
            } if provider_adapter else {}
            recovery = context.metadata.setdefault("context_recovery", {"retries": 0, "events": []})
            for request_attempt in range(2):
                if debug_trace is not None and not provider_adapter:
                    debug_trace.record_llm_input(context.messages, message_origins, step=context.step)
                streamed_content: list[str] = []
                progress = {"content": False, "tool_calls": False, "usage": None, "finish_reason": None}
                debug_token = set_llm_debug_trace(debug_trace, context.step, message_origins) if debug_trace is not None else None
                try:
                    if llm_stream is None:
                        response = llm_call(context.messages, tool_specs, **provider_options)
                    else:
                        def emit_stream_content(delta: str) -> None:
                            if delta:
                                streamed_content.append(delta)
                                emit("llm_delta", step=context.step, delta=delta)

                        def observed_deltas():
                            for delta in llm_stream(context.messages, tool_specs, **provider_options):
                                progress["content"] |= bool(delta.content)
                                progress["tool_calls"] |= bool(delta.tool_calls)
                                if delta.usage is not None:
                                    progress["usage"] = {**(progress["usage"] or {}), **delta.usage}
                                if delta.finish_reason is not None:
                                    progress["finish_reason"] = delta.finish_reason
                                yield delta

                        response, _ = _merge_stream_tool_calls(observed_deltas(), emit_stream_content)
                except Exception as exc:
                    if debug_trace is not None:
                        debug_trace.record_llm_error(exc, step=context.step, usage=progress["usage"], finish_reason=progress["finish_reason"])
                    context.metadata.setdefault("llm_outputs", []).append({"step": context.step, "request_attempt": request_attempt,
                        "status": "error", "usage": progress["usage"], "finish_reason": progress["finish_reason"], "code": getattr(exc, "code", "llm_error")})
                    partial = progress["content"] or progress["tool_calls"]
                    overflow = isinstance(exc, LLMProviderError) and exc.is_context_overflow
                    if partial or request_attempt or not overflow or recovery["retries"] >= 1:
                        if partial or request_attempt or overflow:
                            event = {"step": context.step, "status": "interrupted" if partial else "failed", "retries": recovery["retries"],
                                     "reason": "partial_output" if partial else "retry_failed" if request_attempt else "retry_quota_exhausted",
                                     "received_content": progress["content"], "received_tool_calls": progress["tool_calls"]}
                            recovery["events"].append(event)
                            if debug_trace is not None:
                                debug_trace.record_recovery(event, step=context.step)
                        if partial:
                            interrupted = LLMStreamInterruptedError()
                            interrupted._debug_recorded = True
                            raise interrupted from exc
                        if overflow:
                            exc.args = ("Provider 上下文仍超限，本 turn 的一次恢复重试已用完，请求已停止；原始聊天仍保留。",)
                        exc._debug_recorded = True
                        raise
                    # No body/tool fragments have been received and no tools are replayed.
                    retried_budget = _prepare_overflow_retry(context, tool_specs, llm_config, budget_config,
                        summary_call=summary_call, debug_trace=debug_trace, emit_runtime=emit_runtime)
                    if retried_budget is None:
                        exc.args = ("Provider 拒绝上下文；没有可用的缩减进展或摘要额度，本次请求已停止。当前问题或固定输入过大时，旧历史压缩也可能无法解决；原始聊天仍保留。",)
                        exc._debug_recorded = True
                        raise
                    budget = retried_budget
                else:
                    if request_attempt:
                        event = {"step": context.step, "status": "failed" if response.finish_reason == "length" else "succeeded", "retries": recovery["retries"], "reason": "output_truncated" if response.finish_reason == "length" else "retry_completed"}
                        recovery["events"].append(event)
                        if debug_trace is not None:
                            debug_trace.record_recovery(event, step=context.step)
                        if response.finish_reason != "length":
                            emit("context_status", step=context.step, phase="retried", message="上下文缩减后的重试已完成。")
                    break
                finally:
                    if debug_token is not None:
                        reset_llm_debug_trace(debug_token)

            if debug_trace is not None:
                debug_trace.record_llm_output(response, step=context.step)
            context.metadata.setdefault("llm_outputs", []).append({
                "step": context.step, "usage": response.usage, "finish_reason": response.finish_reason,
            })
            if response.finish_reason == "length":
                raise LLMOutputTruncatedError(response)

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
                return AgentLoopResult(final_answer=response.content, context=context, events=events, llm_response=response)

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
                        "content": result.model_content if result.model_content is not None else result.content,
                    }
                )
                message_origins.append({"origin": "current_run_tool_result"})
            emit("step_end", step=context.step, status="tool_calls")

        trace.append({"step": context.step, "finish_reason": "max_steps"})
        raise AgentMaxStepsError(context.step, context.max_steps)
    except (ContextBudgetError, ContextConfigError, LLMProviderError, LLMOutputTruncatedError, LLMStreamInterruptedError) as exc:
        trace.append({"step": context.step, "finish_reason": exc.code})
        if debug_trace is not None and not getattr(exc, "_debug_recorded", False):
            debug_trace.record_llm_error(exc, step=context.step)
        emit("error", step=context.step, code=exc.code, message=str(exc))
        emit("agent_done", status="error")
        raise
    except AgentMaxStepsError:
        emit("error", code="agent_max_steps", message="Paper Agent reached max steps.")
        emit("agent_done", status="error")
        raise
    except Exception:
        emit("error", code="agent_failed", message="Paper Agent execution failed.")
        emit("agent_done", status="error")
        raise
