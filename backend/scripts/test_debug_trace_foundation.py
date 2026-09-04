"""Pure-local checks for D1 debug-trace facts; no provider or network calls."""

import json
import tempfile
from pathlib import Path
from typing import Any

from app.agents.agent_context import AgentContext
from app.agents.agent_loop import run_agent
from app.agents.paper_agent import run_paper_agent
from app.core.config import LLMConfig
from app.repositories.debug_trace_repository import DebugTraceRepository
from app.repositories.session_event_repository import SessionEventRepository
from app.runtime.tool_registry import ToolRegistry
from app.services.debug_trace_service import DebugTraceService
from app.services import debug_trace_service, llm_service
from app.services.llm_service import AgentLLMDelta, AgentLLMResponse, AgentToolCall, call_llm_with_tools
from app.services.session_model_history_service import project_session_events_to_messages


def _events(repository: DebugTraceRepository, session_id: str, turn_id: str) -> list[dict[str, Any]]:
    return repository.list_by_turn(session_id, turn_id)


def _run_large_tool(database: Path) -> None:
    service = DebugTraceService("s1", "t1", database_path=database, enabled=True)
    service.start()
    registry = ToolRegistry()
    registry.register(
        "debug_large_tool",
        lambda **_args: {"large_text": "X" * 110_000, "operation_id": "TRACE_TEST_7X92", "api_key": "SECRET123", "normal": "visible"},
        project_history_result=lambda _args, _result: {"operation_id": "TRACE_TEST_7X92", "status": "success"},
    )
    calls = [
        AgentLLMResponse("calling", [AgentToolCall("large", "debug_large_tool", '{"api_key":"SECRET123","authorization":"Bearer XXX","normal":"visible"}')]),
        AgentLLMResponse("done", []),
    ]

    def llm(_messages: list[dict[str, Any]], _tools: list[dict[str, Any]]) -> AgentLLMResponse:
        return calls.pop(0)

    result = run_agent(
        AgentContext(messages=[{"role": "system", "content": "system"}, {"role": "user", "content": "run"}], metadata={"message_origins": [{"origin": "system_prompt"}, {"origin": "current_user"}]}),
        tool_registry=registry,
        llm_call=llm,
        debug_trace=service,
    )
    assert result.final_answer == "done"
    service.finish("success")
    events = _events(DebugTraceRepository(database), "s1", "t1")
    assert [event["event_type"] for event in events] == ["turn/debug_start", "llm/input", "llm/output", "tool/call_debug", "tool/result_debug", "llm/input", "llm/output", "turn/debug_end"]
    first_input = events[1]["data"]["messages"]
    assert [(item["role"], item["origin"]) for item in first_input] == [("system", "system_prompt"), ("user", "current_user")]
    second_input = events[5]["data"]["messages"]
    assert second_input[-2]["origin"] == "current_run_assistant"
    assert second_input[-1]["origin"] == "current_run_tool_result"
    tool_result = events[4]["data"]
    assert tool_result["tool_call_id"] == "large" and tool_result["model_call_order_index"] == 0
    assert tool_result["current_run_result"]["truncated"] is True
    assert len(tool_result["current_run_result"]["preview"]) <= 4_001
    assert tool_result["model_result_size"] > 100_000 > tool_result["history_result_size"]
    assert tool_result["history_result"] == {"operation_id": "TRACE_TEST_7X92", "status": "success"}
    assert tool_result["history_projection"]["projector"] == "<lambda>"
    stored = json.dumps(events, ensure_ascii=False)
    assert "SECRET123" not in stored and "Bearer XXX" not in stored and "visible" in stored


def _run_history_origin(database: Path) -> None:
    events = SessionEventRepository(database)
    events.append_event("s2", "user/message", {"content": "我叫 ly"}, turn_id="prior")
    events.append_event("s2", "turn/start", {}, turn_id="prior")
    events.append_event("s2", "tool/call", {"tool_call_id": "profile", "name": "save_user_profile", "arguments": {"identity": "ly"}}, turn_id="prior", step=1)
    events.append_event("s2", "tool/result", {"tool_call_id": "profile", "name": "save_user_profile", "status": "success", "summary": "saved", "history_result": {"tool": "save_user_profile", "status": "success", "updated": {"identity": "ly"}}}, turn_id="prior", step=1)
    events.append_event("s2", "assistant/message", {"content": "已记住。"}, turn_id="prior")
    events.append_event("s2", "turn/end", {"status": "success"}, turn_id="prior")
    history = project_session_events_to_messages("s2", database_path=database)
    service = DebugTraceService("s2", "current", database_path=database, enabled=True)
    service.start()
    result = run_paper_agent(
        "刚才 operation_id 是什么？",
        session_id="s2",
        history=history,
        system_context="## soul.md\nlocal memory",
        llm_call=lambda *_args: AgentLLMResponse("history answer", []),
        debug_trace=service,
    )
    assert result.final_answer == "history answer"
    service.finish("success")
    snapshot = _events(DebugTraceRepository(database), "s2", "current")[1]["data"]["messages"]
    assert snapshot[0]["origin"] == "system_prompt" and snapshot[0]["origin_metadata"]["sources"] == ["PAPER_AGENT_SYSTEM_PROMPT", "soul.md"]
    assert any(item["origin"] == "session_user" for item in snapshot)
    assert any(item["origin"] == "history_tool_call" for item in snapshot)
    tool_message = next(item for item in snapshot if item["origin"] == "history_tool_result")
    assert "ly" in tool_message["content"] and "large_text" not in tool_message["content"]
    assert snapshot[-1]["origin"] == "current_user"


def _run_provider_payload_boundary(database: Path) -> None:
    service = DebugTraceService("s2", "provider", database_path=database, enabled=True)
    service.start()
    captured: list[dict[str, Any]] = []
    original_config, original_request = llm_service.get_llm_config, llm_service._request_chat_completion
    llm_service.get_llm_config = lambda: LLMConfig(provider="deepseek", api_key="test-key", base_url="http://local.invalid", model="test")  # type: ignore[assignment]
    llm_service._request_chat_completion = lambda body, _config: (captured.append(json.loads(json.dumps(body))), {"content": "provider answer"})[1]  # type: ignore[assignment]
    try:
        result = run_agent(
            AgentContext(messages=[{"role": "system", "content": "system"}, {"role": "user", "content": "provider-facing"}], metadata={"message_origins": [{"origin": "system_prompt"}, {"origin": "current_user"}]}),
            llm_call=call_llm_with_tools,
            debug_trace=service,
        )
    finally:
        llm_service.get_llm_config, llm_service._request_chat_completion = original_config, original_request
    assert result.final_answer == "provider answer" and len(captured) == 1
    snapshot = _events(DebugTraceRepository(database), "s2", "provider")[1]["data"]["messages"]
    assert [(item["role"], item["content"]) for item in snapshot] == [
        (item["role"], item["content"]) for item in captured[0]["messages"]
    ]
    service.finish("success")


def _run_concurrency_and_stream(database: Path) -> None:
    service = DebugTraceService("s3", "concurrent", database_path=database, enabled=True)
    service.start()
    registry = ToolRegistry()
    registry.register("one", lambda: {"value": 1}, is_concurrency_safe=lambda _args: True)
    registry.register("two", lambda: {"value": 2}, is_concurrency_safe=lambda _args: True)
    responses = [AgentLLMResponse("", [AgentToolCall("first", "one", "{}"), AgentToolCall("second", "two", "{}")]), AgentLLMResponse("done", [])]
    run_agent(AgentContext(messages=[{"role": "user", "content": "parallel"}]), tool_registry=registry, llm_call=lambda *_args: responses.pop(0), debug_trace=service)
    service.finish("success")
    traces = _events(DebugTraceRepository(database), "s3", "concurrent")
    results = [event["data"] for event in traces if event["event_type"] == "tool/result_debug"]
    assert [(item["tool_call_id"], item["model_call_order_index"]) for item in results] == [("first", 0), ("second", 1)]

    streamed = DebugTraceService("s3", "stream", database_path=database, enabled=True)
    streamed.start()
    run_agent(
        AgentContext(messages=[{"role": "user", "content": "stream"}]),
        llm_stream=lambda *_args: iter([AgentLLMDelta(content="stream "), AgentLLMDelta(content="answer")]),
        debug_trace=streamed,
    )
    streamed.finish("success")
    stream_events = _events(DebugTraceRepository(database), "s3", "stream")
    assert [event["event_type"] for event in stream_events].count("llm/output") == 1
    assert not any(event["event_type"] == "llm/delta" for event in stream_events)


def _run_disabled_and_failure_safe(database: Path) -> None:
    disabled = DebugTraceService("s4", "disabled", database_path=database, enabled=False)
    disabled.start()
    run_agent(AgentContext(messages=[{"role": "user", "content": "disabled"}]), llm_call=lambda *_args: AgentLLMResponse("ok", []), debug_trace=disabled)
    disabled.finish("success")
    assert not _events(DebugTraceRepository(database), "s4", "disabled")

    failed = DebugTraceService("s4", "failure", database_path=database, enabled=True)
    assert failed.repository is not None
    failed.repository.append = lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("disk unavailable"))  # type: ignore[method-assign]
    failed.start()
    result = run_agent(AgentContext(messages=[{"role": "user", "content": "still works"}]), llm_call=lambda *_args: AgentLLMResponse("ok", []), debug_trace=failed)
    failed.finish("success")
    assert result.final_answer == "ok"

    original_repository = debug_trace_service.DebugTraceRepository
    debug_trace_service.DebugTraceRepository = lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("database unavailable"))  # type: ignore[assignment]
    try:
        unavailable = DebugTraceService("s4", "init-failure", database_path=database, enabled=True)
    finally:
        debug_trace_service.DebugTraceRepository = original_repository
    result = run_agent(AgentContext(messages=[{"role": "user", "content": "still works"}]), llm_call=lambda *_args: AgentLLMResponse("ok", []), debug_trace=unavailable)
    assert unavailable.repository is None and result.final_answer == "ok"


def main() -> None:
    with tempfile.TemporaryDirectory() as directory:
        database = Path(directory) / "paperpilot.db"
        _run_large_tool(database)
        _run_history_origin(database)
        _run_provider_payload_boundary(database)
        _run_concurrency_and_stream(database)
        _run_disabled_and_failure_safe(database)
    print("ALL DEBUG TRACE FOUNDATION TESTS PASSED")


if __name__ == "__main__":
    main()
