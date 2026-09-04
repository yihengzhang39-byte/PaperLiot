"""Pure-local checks for transactional session deletion without paper deletion."""

import tempfile
from pathlib import Path

from app.repositories.paper_repository import PaperRepository
from app.repositories.session_event_repository import SessionEventRepository
from app.repositories.session_repository import SessionRepository
from app.services.session_restore_service import delete_session_restore, list_session_history, load_session_restore


def _session_with_event(sessions: SessionRepository, events: SessionEventRepository, session_id: str, paper_id: str) -> None:
    sessions.get_or_create(session_id)
    sessions.set_current_paper(session_id, paper_id)
    sessions.add_paper(session_id, paper_id)
    events.append_event(session_id, "user/message", {"content": session_id}, turn_id=f"{session_id}-turn")


def main() -> None:
    with tempfile.TemporaryDirectory() as directory:
        database_path = Path(directory) / "paperpilot.db"
        papers = PaperRepository(database_path)
        papers.create("paper-1", "hash-1", "shared.pdf", "/papers/shared.pdf")
        sessions = SessionRepository(database_path)
        events = SessionEventRepository(database_path)
        _session_with_event(sessions, events, "s1", "paper-1")
        _session_with_event(sessions, events, "s2", "paper-1")
        sessions.get_or_create("empty")
        s2_events = events.list_events("s2")

        assert delete_session_restore("s1", database_path=database_path) is True
        assert sessions.get("s1") is None
        assert events.list_events("s1") == []
        assert sessions.get_active_papers("s1") == []
        assert papers.get("paper-1") is not None
        assert sessions.get("s2") is not None and sessions.get_active_papers("s2") == ["paper-1"]
        assert events.list_events("s2") == s2_events
        assert "s1" not in {session["session_id"] for session in list_session_history(database_path=database_path)}
        assert load_session_restore("s1", database_path=database_path) is None

        assert delete_session_restore("empty", database_path=database_path) is True
        assert sessions.get("empty") is None
        assert delete_session_restore("missing", database_path=database_path) is False
        assert papers.get("paper-1") is not None

    print("ALL SESSION DELETE TESTS PASSED")


if __name__ == "__main__":
    main()
