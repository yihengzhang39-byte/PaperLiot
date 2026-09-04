"""Read-only session event-log snapshots for browser restore."""

from pathlib import Path
from typing import Any

from app.repositories.session_event_repository import SessionEventRepository
from app.repositories.session_repository import NEW_SESSION_TITLE, SessionRepository, build_session_title
from app.services.sqlite_service import connect, initialize_database


def _session_id(value: str) -> str:
    session_id = value.strip() if isinstance(value, str) else ""
    if not session_id or len(session_id) > 512:
        raise ValueError("session_id must be a non-empty string up to 512 characters")
    return session_id


def load_session_restore(session_id: str, *, database_path: Path | None = None) -> dict[str, Any] | None:
    """Return one SQLite-backed session snapshot without writing events or history."""
    session_id = _session_id(session_id)
    session = SessionRepository(database_path).get(session_id)
    if session is None:
        return None
    events = SessionEventRepository(database_path).list_events(session_id)
    started = {event["turn_id"] for event in events if event["event_type"] == "turn/start" and event["turn_id"]}
    ended = {event["turn_id"] for event in events if event["event_type"] == "turn/end" and event["turn_id"]}
    return {
        "session": {
            "session_id": session_id,
            "current_paper_id": session["current_paper_id"],
            "title": session["title"] or NEW_SESSION_TITLE,
            "active_paper_ids": SessionRepository(database_path).get_active_papers(session_id),
        },
        "events": [
            {
                "seq": event["seq"],
                "turn_id": event["turn_id"],
                "step": event["step"],
                "event_type": event["event_type"],
                "data": event["data"],
                "created_at": event["created_at"],
            }
            for event in events
        ],
        "incomplete_turn_ids": sorted(started - ended),
    }


def create_session_restore(session_id: str, *, database_path: Path | None = None) -> dict[str, Any]:
    """Create an empty session header so an untouched new conversation can survive F5."""
    session_id = _session_id(session_id)
    SessionRepository(database_path).get_or_create(session_id)
    return load_session_restore(session_id, database_path=database_path) or {}


def delete_session_restore(session_id: str, *, database_path: Path | None = None) -> bool:
    """Delete only the selected session's SQLite-owned history and relationships."""
    return SessionRepository(database_path).delete(_session_id(session_id))


def _backfill_done(database_path: Path | None) -> bool:
    with connect(database_path) as connection:
        return connection.execute("SELECT 1 FROM persistence_meta WHERE key = 'session_titles_v1'").fetchone() is not None


def _mark_backfill_done(database_path: Path | None) -> None:
    with connect(database_path) as connection:
        connection.execute("INSERT OR IGNORE INTO persistence_meta (key, value) VALUES ('session_titles_v1', 'done')")


def backfill_session_titles(*, database_path: Path | None = None) -> None:
    """One-time repair for C1-C3 sessions created before the title column existed."""
    initialize_database(database_path)
    if _backfill_done(database_path):
        return
    sessions = SessionRepository(database_path)
    events = SessionEventRepository(database_path)
    for session in sessions.list_sessions():
        if session["title"] is not None:
            continue
        title = build_session_title(events.first_user_message(str(session["session_id"])) or "") or NEW_SESSION_TITLE
        sessions.set_title_if_empty(str(session["session_id"]), title, update_timestamp=False)
    _mark_backfill_done(database_path)


def list_session_history(*, database_path: Path | None = None) -> list[dict[str, Any]]:
    """Return sidebar metadata only; detailed event logs stay on the restore endpoint."""
    backfill_session_titles(database_path=database_path)
    return [
        {**session, "title": session["title"] or NEW_SESSION_TITLE}
        for session in SessionRepository(database_path).list_sessions()
    ]
