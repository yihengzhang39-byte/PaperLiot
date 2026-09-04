"""Pure-local checks for SQLite-backed browser session restore."""

import tempfile
from pathlib import Path

from app.repositories.session_event_repository import SessionEventRepository
from app.repositories.session_repository import SessionRepository
from app.services.session_event_service import SessionEventService
from app.services.session_restore_service import create_session_restore, load_session_restore


def _append_turn(events: SessionEventRepository, session_id: str, turn_id: str, question: str, answer: str) -> None:
    events.append_event(session_id, "user/message", {"content": question, "paper_id": "paper-1"}, turn_id=turn_id)
    events.append_event(session_id, "turn/start", {"paper_id": "paper-1", "active_paper_ids": ["paper-1", "paper-2"]}, turn_id=turn_id)
    events.append_event(session_id, "step/start", {}, turn_id=turn_id, step=1)
    events.append_event(session_id, "assistant/trace", {"content": "公开的检查说明"}, turn_id=turn_id, step=1)
    events.append_event(session_id, "tool/call", {"tool_call_id": f"{turn_id}-call", "name": "get_paper_info", "arguments": {"paper_id": "paper-1"}}, turn_id=turn_id, step=1)
    events.append_event(session_id, "tool/result", {"tool_call_id": f"{turn_id}-call", "name": "get_paper_info", "status": "success", "summary": "retrieved paper information"}, turn_id=turn_id, step=1)
    events.append_event(session_id, "step/end", {"outcome": "tool_calls"}, turn_id=turn_id, step=1)
    events.append_event(session_id, "step/start", {}, turn_id=turn_id, step=2)
    events.append_event(session_id, "assistant/message", {"content": answer}, turn_id=turn_id)
    events.append_event(session_id, "step/end", {"outcome": "final"}, turn_id=turn_id, step=2)
    events.append_event(session_id, "turn/end", {"status": "success"}, turn_id=turn_id)


def main() -> None:
    with tempfile.TemporaryDirectory() as directory:
        database_path = Path(directory) / "paperpilot.db"
        sessions = SessionRepository(database_path)
        sessions.get_or_create("restored")
        sessions.set_current_paper("restored", "paper-1")
        sessions.add_paper("restored", "paper-1")
        sessions.add_paper("restored", "paper-2")
        events = SessionEventRepository(database_path)
        _append_turn(events, "restored", "turn-1", "第一问", "第一答")
        _append_turn(events, "restored", "turn-2", "第二问", "第二答")

        before = events.list_events("restored")
        restored = load_session_restore("restored", database_path=database_path)
        assert restored is not None
        assert restored["session"] == {"session_id": "restored", "current_paper_id": "paper-1", "title": "新对话", "active_paper_ids": ["paper-1", "paper-2"]}
        assert [event["seq"] for event in restored["events"]] == list(range(1, len(before) + 1))
        assert [event["turn_id"] for event in restored["events"][:11]] == ["turn-1"] * 11
        assert [event["turn_id"] for event in restored["events"][11:]] == ["turn-2"] * 11
        assert [event["step"] for event in restored["events"] if event["event_type"] == "step/start"] == [1, 2, 1, 2]
        tool_call = next(event for event in restored["events"] if event["event_type"] == "tool/call")
        tool_result = next(event for event in restored["events"] if event["event_type"] == "tool/result")
        assert tool_call["data"]["tool_call_id"] == tool_result["data"]["tool_call_id"]
        assert next(event for event in restored["events"] if event["event_type"] == "assistant/trace")["data"]["content"] == "公开的检查说明"
        assert next(event for event in restored["events"] if event["event_type"] == "assistant/message")["data"]["content"] == "第一答"
        assert restored["incomplete_turn_ids"] == []
        assert events.list_events("restored") == before

        assert load_session_restore("missing", database_path=database_path) is None
        empty = create_session_restore("empty", database_path=database_path)
        assert empty["session"]["session_id"] == "empty" and empty["events"] == []

        sessions.get_or_create("incomplete")
        events.append_event("incomplete", "user/message", {"content": "未完成"}, turn_id="turn-open")
        events.append_event("incomplete", "turn/start", {}, turn_id="turn-open")
        events.append_event("incomplete", "step/start", {}, turn_id="turn-open", step=1)
        incomplete = load_session_restore("incomplete", database_path=database_path)
        assert incomplete is not None and incomplete["incomplete_turn_ids"] == ["turn-open"]

        service = SessionEventService("restored", database_path=database_path)
        service.start("第三问", paper_id="paper-1", active_paper_ids=["paper-1", "paper-2"])
        service.finish("success")
        assert [event["seq"] for event in events.list_events("restored")] == list(range(1, len(before) + 4))

    print("ALL SESSION RESTORE TESTS PASSED")


if __name__ == "__main__":
    main()
