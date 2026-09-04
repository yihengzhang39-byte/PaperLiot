"""Append-only repository for future safe Agent event persistence."""

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from app.services.sqlite_service import connect, initialize_database


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SessionEventRepository:
    def __init__(self, database_path: Path | None = None) -> None:
        self.database_path = database_path
        initialize_database(database_path)

    def next_seq(self, session_id: str) -> int:
        with connect(self.database_path) as connection:
            row = connection.execute("SELECT COALESCE(MAX(seq), 0) + 1 AS next_seq FROM session_events WHERE session_id = ?", (session_id,)).fetchone()
        return int(row["next_seq"])

    def append_event(
        self,
        session_id: str,
        event_type: str,
        data: dict[str, Any],
        *,
        turn_id: str | None = None,
        step: int | None = None,
    ) -> int:
        with connect(self.database_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            seq = int(connection.execute("SELECT COALESCE(MAX(seq), 0) + 1 FROM session_events WHERE session_id = ?", (session_id,)).fetchone()[0])
            connection.execute(
                "INSERT INTO session_events (session_id, seq, turn_id, step, event_type, data_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (session_id, seq, turn_id, step, event_type, json.dumps(data, ensure_ascii=False), _now()),
            )
        return seq

    def list_events(self, session_id: str) -> list[dict[str, Any]]:
        with connect(self.database_path) as connection:
            rows = connection.execute("SELECT * FROM session_events WHERE session_id = ? ORDER BY seq", (session_id,)).fetchall()
        return [{**dict(row), "data": json.loads(str(row["data_json"]))} for row in rows]

    def list_events_by_turn(self, session_id: str, turn_id: str) -> list[dict[str, Any]]:
        return [event for event in self.list_events(session_id) if event["turn_id"] == turn_id]

    def first_user_message(self, session_id: str) -> str | None:
        with connect(self.database_path) as connection:
            row = connection.execute(
                "SELECT data_json FROM session_events WHERE session_id = ? AND event_type = 'user/message' ORDER BY seq LIMIT 1",
                (session_id,),
            ).fetchone()
        if row is None:
            return None
        try:
            data = json.loads(str(row["data_json"]))
        except json.JSONDecodeError:
            return None
        content = data.get("content") if isinstance(data, dict) else None
        return content if isinstance(content, str) else None
