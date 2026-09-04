"""Project stored semantic and debug events into one read-only turn view."""

from pathlib import Path
from typing import Any

from app.core.config import get_harness_debug_trace_config
from app.repositories.debug_trace_repository import DebugTraceRepository
from app.repositories.session_event_repository import SessionEventRepository
from app.repositories.session_repository import SessionRepository


class TurnTraceNotFoundError(LookupError):
    """Raised when the requested session or turn has no persisted identity."""


class TurnTraceUnavailableError(LookupError):
    """Raised when trace capture was disabled or no D1 trace was stored."""


def _event_data(event: dict[str, Any]) -> dict[str, Any]:
    data = event.get("data")
    return data if isinstance(data, dict) else {}


class TurnTraceService:
    """Read existing facts only; never replays an Agent or reconstructs messages."""

    def __init__(self, *, database_path: Path | None = None) -> None:
        self.database_path = database_path
        self.sessions = SessionRepository(database_path)
        self.session_events = SessionEventRepository(database_path)
        self.debug_events = DebugTraceRepository(database_path)

    def get_turn_trace(self, session_id: str, turn_id: str) -> dict[str, Any]:
        if self.sessions.get(session_id) is None:
            raise TurnTraceNotFoundError("session")
        semantic_events = self.session_events.list_events_by_turn(session_id, turn_id)
        if not semantic_events:
            raise TurnTraceNotFoundError("turn")
        if not get_harness_debug_trace_config().enabled:
            raise TurnTraceUnavailableError("disabled")
        debug_events = self.debug_events.list_by_turn(session_id, turn_id)
        if not debug_events:
            raise TurnTraceUnavailableError("missing")
        return self._project(session_id, turn_id, semantic_events, debug_events)

    @staticmethod
    def _project(
        session_id: str,
        turn_id: str,
        semantic_events: list[dict[str, Any]],
        debug_events: list[dict[str, Any]],
    ) -> dict[str, Any]:
        user_event = next((event for event in semantic_events if event["event_type"] == "user/message"), None)
        final_event = next((event for event in reversed(semantic_events) if event["event_type"] == "assistant/message"), None)
        debug_start = next((event for event in debug_events if event["event_type"] == "turn/debug_start"), None)
        debug_end = next((event for event in reversed(debug_events) if event["event_type"] == "turn/debug_end"), None)
        user_input = _event_data(user_event).get("content") if user_event else _event_data(debug_start).get("user_input")
        final_answer = _event_data(final_event).get("content") if final_event else TurnTraceService._last_llm_content(debug_events)
        steps = TurnTraceService._steps(semantic_events, debug_events)
        return {
            "session_id": session_id,
            "turn_id": turn_id,
            "status": TurnTraceService._status(semantic_events, debug_events),
            "user_input": user_input if isinstance(user_input, str) else None,
            "started_at": (semantic_events[0] if semantic_events else debug_events[0])["created_at"],
            "ended_at": (debug_end or next((event for event in reversed(semantic_events) if event["event_type"] == "turn/end"), None) or debug_events[-1])["created_at"],
            "steps": steps,
            "final_answer": final_answer if isinstance(final_answer, str) else None,
        }

    @staticmethod
    def _status(semantic_events: list[dict[str, Any]], debug_events: list[dict[str, Any]]) -> str:
        debug_end = next((event for event in reversed(debug_events) if event["event_type"] == "turn/debug_end"), None)
        turn_end = next((event for event in reversed(semantic_events) if event["event_type"] == "turn/end"), None)
        if (debug_end and _event_data(debug_end).get("status") != "success") or (turn_end and _event_data(turn_end).get("status") != "success") or any(event["event_type"] == "error" for event in semantic_events):
            return "error"
        if debug_end or turn_end:
            return "success"
        return "incomplete"

    @staticmethod
    def _last_llm_content(debug_events: list[dict[str, Any]]) -> Any:
        output = next((event for event in reversed(debug_events) if event["event_type"] == "llm/output"), None)
        return _event_data(output).get("content") if output else None

    @staticmethod
    def _steps(semantic_events: list[dict[str, Any]], debug_events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        numbers = sorted({event["step"] for event in debug_events if isinstance(event.get("step"), int)} | {event["step"] for event in semantic_events if isinstance(event.get("step"), int)})
        return [TurnTraceService._step(number, semantic_events, [event for event in debug_events if event.get("step") == number]) for number in numbers]

    @staticmethod
    def _step(step: int, semantic_events: list[dict[str, Any]], debug_events: list[dict[str, Any]]) -> dict[str, Any]:
        calls: list[dict[str, Any]] = []
        tool_calls: list[dict[str, Any]] = []
        tool_results: list[dict[str, Any]] = []
        timeline: list[dict[str, Any]] = []
        pending_llm: list[int] = []
        call_refs: dict[str, int] = {}
        for event in debug_events:
            data = _event_data(event)
            event_type, trace_seq = event["event_type"], event["trace_seq"]
            if event_type == "llm/input":
                index = len(calls)
                calls.append({"call_index": index, "input": data, "output": None, "input_trace_seq": trace_seq, "output_trace_seq": None})
                pending_llm.append(index)
                timeline.append({"trace_seq": trace_seq, "type": event_type, "ref": f"llm_calls.{index}.input"})
            elif event_type == "llm/output":
                index = pending_llm.pop(0) if pending_llm else len(calls)
                if index == len(calls):
                    calls.append({"call_index": index, "input": None, "output": data, "input_trace_seq": None, "output_trace_seq": trace_seq})
                else:
                    calls[index]["output"] = data
                    calls[index]["output_trace_seq"] = trace_seq
                timeline.append({"trace_seq": trace_seq, "type": event_type, "ref": f"llm_calls.{index}.output"})
            elif event_type == "tool/call_debug":
                call = {"tool_call_id": data.get("tool_call_id"), "name": data.get("name"), "arguments": data.get("arguments"), "call_index": data.get("model_call_order_index"), "created_at": event["created_at"], "trace_seq": trace_seq, "result_trace_seq": None}
                tool_calls.append(call)
                if isinstance(call["tool_call_id"], str):
                    call_refs[call["tool_call_id"]] = len(tool_calls) - 1
                timeline.append({"trace_seq": trace_seq, "type": event_type, "ref": f"tool_calls.{len(tool_calls) - 1}", "tool_call_id": call["tool_call_id"]})
            elif event_type == "tool/result_debug":
                projection = data.get("history_projection") if isinstance(data.get("history_projection"), dict) else {}
                result = {
                    "tool_call_id": data.get("tool_call_id"),
                    "name": data.get("name"),
                    "status": data.get("status"),
                    "call_index": data.get("model_call_order_index"),
                    "created_at": event["created_at"],
                    "trace_seq": trace_seq,
                    "call_trace_seq": None,
                    "current_run_result": data.get("current_run_result"),
                    "history_result": data.get("history_result"),
                    "history_projection": {
                        "projector": projection.get("projector"),
                        "model_result_size": data.get("model_result_size"),
                        "history_result_size": data.get("history_result_size"),
                        "compression_ratio": data.get("compression_ratio"),
                    },
                }
                tool_results.append(result)
                call_id = result["tool_call_id"]
                if isinstance(call_id, str):
                    call_index = call_refs.get(call_id)
                    if call_index is not None:
                        tool_calls[call_index]["result_trace_seq"] = trace_seq
                        result["call_trace_seq"] = tool_calls[call_index]["trace_seq"]
                timeline.append({"trace_seq": trace_seq, "type": event_type, "ref": f"tool_results.{len(tool_results) - 1}", "tool_call_id": call_id})
        started = next((event for event in semantic_events if event.get("step") == step and event["event_type"] == "step/start"), None)
        ended = next((event for event in reversed(semantic_events) if event.get("step") == step and event["event_type"] == "step/end"), None)
        return {
            "step": step,
            "started_at": started["created_at"] if started else (debug_events[0]["created_at"] if debug_events else None),
            "ended_at": ended["created_at"] if ended else (debug_events[-1]["created_at"] if debug_events else None),
            "llm_calls": calls,
            "tool_calls": tool_calls,
            "tool_results": tool_results,
            "timeline": timeline,
        }
