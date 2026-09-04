"""Pure-local checks for persistent session titles and sidebar history metadata."""

import sqlite3
import tempfile
from pathlib import Path

from app.repositories.session_event_repository import SessionEventRepository
from app.repositories.session_repository import NEW_SESSION_TITLE, SESSION_TITLE_MAX_LENGTH, SessionRepository, build_session_title
from app.services.session_event_service import SessionEventService
from app.services.session_restore_service import backfill_session_titles, list_session_history, load_session_restore
from app.services.sqlite_service import connect, initialize_database


def main() -> None:
    with tempfile.TemporaryDirectory() as directory:
        database_path = Path(directory) / "paperpilot.db"
        sessions = SessionRepository(database_path)
        first = SessionEventService("s1", database_path=database_path)
        first.start("  你好么？\n\n请帮忙  ", paper_id=None, active_paper_ids=[])
        first.finish("success")
        second = SessionEventService("s1", database_path=database_path)
        second.start("再问一个问题", paper_id=None, active_paper_ids=[])
        second.finish("success")
        assert sessions.get("s1")["title"] == "你好么？ 请帮忙"

        long_message = "分析这篇论文的方法和实验设计" * 10
        long = SessionEventService("s2", database_path=database_path)
        long.start(long_message, paper_id="paper-2", active_paper_ids=["paper-2"])
        long.finish("success")
        assert len(sessions.get("s2")["title"]) == SESSION_TITLE_MAX_LENGTH
        assert sessions.get("s2")["title"].endswith("…")

        sessions.get_or_create("s3", updated_at="2026-01-01T00:00:00+00:00")
        assert sessions.get("s3")["title"] == NEW_SESSION_TITLE
        events = SessionEventRepository(database_path)
        sessions.get_or_create("legacy", updated_at="2025-01-01T00:00:00+00:00")
        with connect(database_path) as connection:
            connection.execute("UPDATE sessions SET title = NULL, updated_at = ? WHERE session_id = 'legacy'", ("2025-01-01T00:00:00+00:00",))
        events.append_event("legacy", "user/message", {"content": " 分析   DEIM\n "}, turn_id="legacy-turn")
        before_backfill = sessions.get("legacy")["updated_at"]
        backfill_session_titles(database_path=database_path)
        assert sessions.get("legacy")["title"] == "分析 DEIM"
        assert sessions.get("legacy")["updated_at"] == before_backfill
        backfill_session_titles(database_path=database_path)
        assert sessions.get("legacy")["title"] == "分析 DEIM"

        with connect(database_path) as connection:
            connection.execute("UPDATE sessions SET updated_at = ? WHERE session_id = 's2'", ("2030-01-01T00:00:00+00:00",))
        history = list_session_history(database_path=database_path)
        assert [item["session_id"] for item in history][0] == "s2"
        assert {item["session_id"] for item in history} >= {"s1", "s2", "s3", "legacy"}
        assert all("events" not in item for item in history)

        before_restore = events.list_events("s1")
        restored = load_session_restore("s1", database_path=database_path)
        assert restored is not None and restored["session"]["title"] == "你好么？ 请帮忙"
        assert events.list_events("s1") == before_restore
        events.append_event("s1", "turn/end", {"status": "success"}, turn_id="follow-up")
        assert events.list_events("s1")[-1]["seq"] == len(before_restore) + 1

        legacy_database = Path(directory) / "legacy.db"
        with sqlite3.connect(legacy_database) as connection:
            connection.execute("CREATE TABLE sessions (session_id TEXT PRIMARY KEY, current_paper_id TEXT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)")
        initialize_database(legacy_database)
        with connect(legacy_database) as connection:
            assert "title" in {row["name"] for row in connection.execute("PRAGMA table_info(sessions)")}

    assert build_session_title(" \n ") is None
    print("ALL SESSION HISTORY TESTS PASSED")


if __name__ == "__main__":
    main()
