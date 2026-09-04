"""Pure-local checks for completed SQLite event projection into model messages."""

import json
import tempfile
from pathlib import Path

from app.agents.paper_agent import build_paper_agent_messages
from app.repositories.session_event_repository import SessionEventRepository
from app.services.session_model_history_service import project_session_events_to_messages


def append(events, session, turn, kind, data, step=None):
    events.append_event(session, kind, data, turn_id=turn, step=step)


def main() -> None:
    with tempfile.TemporaryDirectory() as directory:
        database = Path(directory) / "paperpilot.db"
        events = SessionEventRepository(database)
        append(events, "s1", "t1", "user/message", {"content": "我叫 zyh"})
        append(events, "s1", "t1", "turn/start", {})
        append(events, "s1", "t1", "tool/call", {"tool_call_id": "profile", "name": "save_user_profile", "arguments": {"identity": "zyh"}}, 1)
        append(events, "s1", "t1", "tool/result", {"tool_call_id": "profile", "name": "save_user_profile", "status": "success", "summary": "completed", "history_result": {"tool": "save_user_profile", "status": "success", "updated": {"identity": "zyh（人工智能方向本科生）"}}}, 1)
        append(events, "s1", "t1", "assistant/trace", {"content": "hidden from future history"}, 1)
        append(events, "s1", "t1", "assistant/message", {"content": "我已经记下你的称呼了。"})
        append(events, "s1", "t1", "turn/end", {"status": "success"})
        append(events, "s1", "t2", "user/message", {"content": "当前问题"})
        append(events, "s1", "t2", "turn/start", {})
        history = project_session_events_to_messages("s1", before_turn_id="t2", database_path=database)
        assert [item["role"] for item in history] == ["user", "assistant", "tool", "assistant"]
        assert history[1]["tool_calls"][0]["id"] == history[2]["tool_call_id"] == "profile"
        assert json.loads(history[2]["content"])["updated"]["identity"].startswith("zyh")
        messages = build_paper_agent_messages("我之前让你修改了我的长期档案吗？", None, history=history)
        assert [item["role"] for item in messages] == ["system", "user", "assistant", "tool", "assistant", "user"]
        assert sum(item.get("content") == "我之前让你修改了我的长期档案吗？" for item in messages) == 1

        append(events, "s1", "open", "user/message", {"content": "incomplete"})
        append(events, "s1", "open", "turn/start", {})
        assert all(item.get("content") != "incomplete" for item in project_session_events_to_messages("s1", database_path=database))
    print("ALL MODEL HISTORY PROJECTION TESTS PASSED")


if __name__ == "__main__":
    main()
