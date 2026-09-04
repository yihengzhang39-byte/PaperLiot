"""Repository for session headers and current paper relationships."""

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.services.sqlite_service import connect, initialize_database


NEW_SESSION_TITLE = "新对话"
SESSION_TITLE_MAX_LENGTH = 40


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_session_title(message: str) -> str | None:
    """Normalize the first real user message into one compact sidebar title."""
    title = " ".join(message.split()) if isinstance(message, str) else ""
    if not title:
        return None
    return title if len(title) <= SESSION_TITLE_MAX_LENGTH else f"{title[:SESSION_TITLE_MAX_LENGTH - 1]}…"


class SessionRepository:
    def __init__(self, database_path: Path | None = None) -> None:
        self.database_path = database_path
        initialize_database(database_path)

    def get(self, session_id: str) -> dict[str, Any] | None:
        with connect(self.database_path) as connection:
            row = connection.execute("SELECT * FROM sessions WHERE session_id = ?", (session_id,)).fetchone()
        return dict(row) if row else None

    def get_or_create(self, session_id: str, *, created_at: str | None = None, updated_at: str | None = None) -> dict[str, Any]:
        timestamp = updated_at or created_at or _now()
        with connect(self.database_path) as connection:
            connection.execute(
                "INSERT OR IGNORE INTO sessions (session_id, current_paper_id, title, created_at, updated_at) VALUES (?, NULL, ?, ?, ?)",
                (session_id, NEW_SESSION_TITLE, created_at or timestamp, timestamp),
            )
        return self.get(session_id) or {}

    def touch(self, session_id: str) -> None:
        self.get_or_create(session_id)
        with connect(self.database_path) as connection:
            connection.execute("UPDATE sessions SET updated_at = ? WHERE session_id = ?", (_now(), session_id))

    def set_title_if_empty(self, session_id: str, title: str, *, update_timestamp: bool = True) -> bool:
        """Set a first-message title once, retaining it across later turns."""
        self.get_or_create(session_id)
        if not title:
            return False
        with connect(self.database_path) as connection:
            if update_timestamp:
                cursor = connection.execute(
                    "UPDATE sessions SET title = ?, updated_at = ? WHERE session_id = ? AND (title IS NULL OR title = ?)",
                    (title, _now(), session_id, NEW_SESSION_TITLE),
                )
            else:
                cursor = connection.execute(
                    "UPDATE sessions SET title = ? WHERE session_id = ? AND title IS NULL",
                    (title, session_id),
                )
        return cursor.rowcount > 0

    def set_current_paper(self, session_id: str, paper_id: str | None) -> None:
        self.get_or_create(session_id)
        with connect(self.database_path) as connection:
            connection.execute(
                "UPDATE sessions SET current_paper_id = ?, updated_at = ? WHERE session_id = ?",
                (paper_id, _now(), session_id),
            )

    def add_paper(self, session_id: str, paper_id: str, *, added_at: str | None = None) -> None:
        self.get_or_create(session_id)
        with connect(self.database_path) as connection:
            connection.execute(
                "INSERT OR IGNORE INTO session_papers (session_id, paper_id, added_at) VALUES (?, ?, ?)",
                (session_id, paper_id, added_at or _now()),
            )

    def get_active_papers(self, session_id: str) -> list[str]:
        with connect(self.database_path) as connection:
            rows = connection.execute(
                "SELECT paper_id FROM session_papers WHERE session_id = ? ORDER BY added_at, paper_id", (session_id,)
            ).fetchall()
        return [str(row["paper_id"]) for row in rows]

    def list_sessions(self) -> list[dict[str, Any]]:
        with connect(self.database_path) as connection:
            rows = connection.execute(
                "SELECT session_id, title, current_paper_id, created_at, updated_at FROM sessions ORDER BY updated_at DESC, session_id DESC"
            ).fetchall()
        return [dict(row) for row in rows]

    def delete(self, session_id: str) -> bool:
        """Delete one session and its owned event/relationship rows, never papers."""
        with connect(self.database_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            if connection.execute("SELECT 1 FROM sessions WHERE session_id = ?", (session_id,)).fetchone() is None:
                return False
            connection.execute("DELETE FROM session_events WHERE session_id = ?", (session_id,))
            connection.execute("DELETE FROM session_papers WHERE session_id = ?", (session_id,))
            connection.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))
        return True
