"""Pure-local read-only API checks for the D2 turn trace endpoint."""

import os
import tempfile
from pathlib import Path

from fastapi import HTTPException

from app.api.routes import chat as chat_route
from app.repositories.debug_trace_repository import DebugTraceRepository
from app.repositories.session_event_repository import SessionEventRepository
from app.repositories.session_repository import SessionRepository
from app.services.turn_trace_service import TurnTraceService


def main() -> None:
    with tempfile.TemporaryDirectory() as directory:
        database = Path(directory) / "paperpilot.db"
        SessionRepository(database).get_or_create("s1")
        events, debug = SessionEventRepository(database), DebugTraceRepository(database)
        events.append_event("s1", "user/message", {"content": "hello"}, turn_id="t1")
        events.append_event("s1", "turn/start", {}, turn_id="t1")
        events.append_event("s1", "assistant/message", {"content": "semantic answer"}, turn_id="t1")
        events.append_event("s1", "turn/end", {"status": "success"}, turn_id="t1")
        debug.append("s1", "t1", "turn/debug_start", {})
        debug.append("s1", "t1", "llm/input", {"message_count": 1, "messages": [{"index": 0, "role": "user", "origin": "current_user", "content": "hello", "content_length": 5}]}, step=1)
        debug.append("s1", "t1", "llm/output", {"content": "semantic answer", "content_length": 15, "tool_calls": []}, step=1)
        debug.append("s1", "t1", "turn/debug_end", {"status": "success"})
        service = TurnTraceService(database_path=database)
        before = (len(events.list_events("s1")), len(debug.list_by_turn("s1", "t1")))
        original_service = chat_route.TurnTraceService
        chat_route.TurnTraceService = lambda: service  # type: ignore[assignment]
        try:
            response = chat_route.get_chat_turn_trace("s1", "t1")
            assert response["session_id"] == "s1" and response["turn_id"] == "t1" and response["final_answer"] == "semantic answer"
            assert before == (len(events.list_events("s1")), len(debug.list_by_turn("s1", "t1")))
            original_flag = os.environ.get("HARNESS_DEBUG_TRACE")
            os.environ["HARNESS_DEBUG_TRACE"] = "false"
            try:
                chat_route.get_chat_turn_trace("s1", "t1")
            except HTTPException as exc:
                assert exc.status_code == 404 and "已禁用" in str(exc.detail)
            else:
                raise AssertionError("disabled trace must not look empty")
            finally:
                if original_flag is None:
                    os.environ.pop("HARNESS_DEBUG_TRACE", None)
                else:
                    os.environ["HARNESS_DEBUG_TRACE"] = original_flag
            try:
                chat_route.get_chat_turn_trace("s1", "missing")
            except HTTPException as exc:
                assert exc.status_code == 404 and "Turn" in str(exc.detail)
            else:
                raise AssertionError("missing turn must return 404")
        finally:
            chat_route.TurnTraceService = original_service
    print("ALL TURN TRACE API TESTS PASSED")


if __name__ == "__main__":
    main()
