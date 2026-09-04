"""Pure-local checks that D2 projects only stored D1 and semantic facts."""

import json
import tempfile
from pathlib import Path
from typing import Any

from app.repositories.debug_trace_repository import DebugTraceRepository
from app.repositories.session_event_repository import SessionEventRepository
from app.repositories.session_repository import SessionRepository
from app.services.turn_trace_service import TurnTraceNotFoundError, TurnTraceService, TurnTraceUnavailableError


def _input(messages: list[dict[str, Any]]) -> dict[str, Any]:
    return {"message_count": len(messages), "messages": messages}


def _message(index: int, role: str, origin: str, content: str, **extra: Any) -> dict[str, Any]:
    return {"index": index, "role": role, "origin": origin, "origin_metadata": extra.pop("origin_metadata", {}), "content": content, "content_length": len(content), "tool_call_id": extra.pop("tool_call_id", None), "name": extra.pop("name", None), "tool_calls": extra.pop("tool_calls", None), **extra}


def _seed_session(database: Path, session_id: str) -> tuple[SessionEventRepository, DebugTraceRepository]:
    SessionRepository(database).get_or_create(session_id)
    return SessionEventRepository(database), DebugTraceRepository(database)


def _semantic(events: SessionEventRepository, session: str, turn: str, user: str, final: str | None, *, complete: bool = True) -> None:
    events.append_event(session, "user/message", {"content": user}, turn_id=turn)
    events.append_event(session, "turn/start", {}, turn_id=turn)
    events.append_event(session, "step/start", {}, turn_id=turn, step=1)
    if final is not None:
        events.append_event(session, "assistant/message", {"content": final}, turn_id=turn)
    if complete:
        events.append_event(session, "turn/end", {"status": "success"}, turn_id=turn)


def _start(debug: DebugTraceRepository, session: str, turn: str) -> None:
    debug.append(session, turn, "turn/debug_start", {"user_message_ref": {"event_type": "user/message", "turn_id": turn}})


def _tool_result(call_id: str, name: str, index: int, *, preview: str = "safe preview") -> dict[str, Any]:
    return {
        "tool_call_id": call_id,
        "name": name,
        "model_call_order_index": index,
        "status": "success",
        "current_run_result": {"type": "text", "size_chars": 18_420, "preview": preview, "truncated": True},
        "history_result": {"tool": name, "status": "success", "identity": "ly"},
        "model_result_size": 18_420,
        "history_result_size": 214,
        "compression_ratio": 214 / 18_420,
        "history_projection": {"projector": "project_save_user_profile_history"},
    }


def _direct_case(database: Path) -> None:
    events, debug = _seed_session(database, "s1")
    _semantic(events, "s1", "direct", "semantic user", "semantic final")
    _start(debug, "s1", "direct")
    debug.append("s1", "direct", "llm/input", _input([_message(0, "system", "system_prompt", "system", origin_metadata={"sources": ["PAPER_AGENT_SYSTEM_PROMPT", "soul.md"]}), _message(1, "user", "current_user", "semantic user")]), step=1)
    debug.append("s1", "direct", "llm/output", {"content": "debug fallback only", "content_length": 19, "tool_calls": []}, step=1)
    debug.append("s1", "direct", "turn/debug_end", {"status": "success"})
    trace = TurnTraceService(database_path=database).get_turn_trace("s1", "direct")
    assert trace["status"] == "success" and trace["user_input"] == "semantic user" and trace["final_answer"] == "semantic final"
    step = trace["steps"][0]
    assert step["llm_calls"][0]["input"]["messages"][0]["origin"] == "system_prompt"
    assert step["llm_calls"][0]["input"]["messages"][0]["origin_metadata"]["sources"] == ["PAPER_AGENT_SYSTEM_PROMPT", "soul.md"]
    assert [item["trace_seq"] for item in step["timeline"]] == sorted(item["trace_seq"] for item in step["timeline"])


def _tool_and_history_cases(database: Path) -> None:
    events, debug = _seed_session(database, "s2")
    _semantic(events, "s2", "tool", "run tool", "final answer")
    events.append_event("s2", "step/start", {}, turn_id="tool", step=2)
    events.append_event("s2", "step/end", {"outcome": "tool_calls"}, turn_id="tool", step=1)
    events.append_event("s2", "step/end", {"outcome": "final"}, turn_id="tool", step=2)
    _start(debug, "s2", "tool")
    debug.append("s2", "tool", "llm/input", _input([_message(0, "user", "current_user", "run tool")]), step=1)
    debug.append("s2", "tool", "llm/output", {"content": "", "content_length": 0, "tool_calls": [{"tool_call_id": "a", "name": "save_user_profile", "arguments": {"identity": "ly"}}]}, step=1)
    debug.append("s2", "tool", "tool/call_debug", {"tool_call_id": "a", "name": "save_user_profile", "arguments": {"identity": "ly"}, "model_call_order_index": 0}, step=1)
    debug.append("s2", "tool", "tool/call_debug", {"tool_call_id": "b", "name": "retrieve_paper_context", "arguments": {"query": "method"}, "model_call_order_index": 1}, step=1)
    debug.append("s2", "tool", "tool/result_debug", _tool_result("b", "retrieve_paper_context", 1, preview="safe preview only"), step=1)
    debug.append("s2", "tool", "tool/result_debug", _tool_result("a", "save_user_profile", 0), step=1)
    debug.append("s2", "tool", "llm/input", _input([_message(0, "assistant", "current_run_assistant", "", tool_calls=[{"id": "a"}]), _message(1, "tool", "current_run_tool_result", "safe preview", tool_call_id="a", name="save_user_profile")]), step=2)
    debug.append("s2", "tool", "llm/output", {"content": "final answer", "content_length": 12, "tool_calls": []}, step=2)
    debug.append("s2", "tool", "turn/debug_end", {"status": "success"})
    trace = TurnTraceService(database_path=database).get_turn_trace("s2", "tool")
    first = trace["steps"][0]
    calls = {item["tool_call_id"]: item for item in first["tool_calls"]}
    results = {item["tool_call_id"]: item for item in first["tool_results"]}
    assert calls["a"]["result_trace_seq"] == results["a"]["trace_seq"] and calls["b"]["result_trace_seq"] == results["b"]["trace_seq"]
    assert results["a"]["current_run_result"]["preview"] != results["a"]["history_result"]
    assert results["a"]["history_projection"]["model_result_size"] == 18_420
    assert results["a"]["history_projection"]["compression_ratio"] == 214 / 18_420
    assert "X" * 100 not in json.dumps(trace)
    second = trace["steps"][1]
    assert second["llm_calls"][0]["input"]["messages"][-1]["origin"] == "current_run_tool_result"

    _semantic(events, "s2", "turn2", "上一轮做了什么？", "已保存身份。")
    _start(debug, "s2", "turn2")
    debug.append("s2", "turn2", "llm/input", _input([_message(0, "assistant", "history_tool_call", "", tool_calls=[{"id": "a"}]), _message(1, "tool", "history_tool_result", '{"identity":"ly"}', tool_call_id="a", name="save_user_profile"), _message(2, "user", "current_user", "上一轮做了什么？")]), step=1)
    debug.append("s2", "turn2", "llm/output", {"content": "已保存身份。", "content_length": 6, "tool_calls": []}, step=1)
    debug.append("s2", "turn2", "turn/debug_end", {"status": "success"})
    second_turn = TurnTraceService(database_path=database).get_turn_trace("s2", "turn2")
    history_messages = second_turn["steps"][0]["llm_calls"][0]["input"]["messages"]
    assert [item["origin"] for item in history_messages[:2]] == ["history_tool_call", "history_tool_result"]
    assert "ly" in history_messages[1]["content"] and "safe preview only" not in history_messages[1]["content"]


def _incomplete_and_absent_cases(database: Path) -> None:
    events, debug = _seed_session(database, "s3")
    _semantic(events, "s3", "partial", "partial", None, complete=False)
    _start(debug, "s3", "partial")
    debug.append("s3", "partial", "llm/input", _input([_message(0, "user", "current_user", "partial")]), step=1)
    trace = TurnTraceService(database_path=database).get_turn_trace("s3", "partial")
    assert trace["status"] == "incomplete" and trace["final_answer"] is None
    try:
        TurnTraceService(database_path=database).get_turn_trace("s3", "missing")
    except TurnTraceNotFoundError as exc:
        assert str(exc) == "turn"
    else:
        raise AssertionError("missing turn must fail")
    try:
        TurnTraceService(database_path=database).get_turn_trace("missing", "anything")
    except TurnTraceNotFoundError as exc:
        assert str(exc) == "session"
    else:
        raise AssertionError("missing session must fail")
    events.append_event("s3", "user/message", {"content": "old"}, turn_id="no-trace")
    try:
        TurnTraceService(database_path=database).get_turn_trace("s3", "no-trace")
    except TurnTraceUnavailableError as exc:
        assert str(exc) == "missing"
    else:
        raise AssertionError("missing trace must be explicit")


def main() -> None:
    with tempfile.TemporaryDirectory() as directory:
        database = Path(directory) / "paperpilot.db"
        _direct_case(database)
        _tool_and_history_cases(database)
        _incomplete_and_absent_cases(database)
    print("ALL TURN TRACE PROJECTION TESTS PASSED")


if __name__ == "__main__":
    main()
