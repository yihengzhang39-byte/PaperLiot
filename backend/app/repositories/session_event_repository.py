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

    def append_checkpoint(self, session_id: str, turn_id: str, data: dict[str, Any]) -> int:
        """Strict short transaction: never recreate sessions or hide commit failures."""
        from app.services.session_model_history_service import valid_checkpoint_data

        if not valid_checkpoint_data(data):
            raise ValueError("Invalid checkpoint data")
        with connect(self.database_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            if connection.execute("SELECT 1 FROM sessions WHERE session_id = ?", (session_id,)).fetchone() is None:
                raise ValueError("checkpoint_session_deleted")
            rows = connection.execute("SELECT seq, turn_id, event_type, data_json FROM session_events WHERE session_id = ? ORDER BY seq", (session_id,)).fetchall()
            checkpoints = [row for row in rows if row["event_type"] == "context/checkpoint"]
            previous = checkpoints[-1] if checkpoints else None
            if (previous["seq"] if previous else None) != data["previous_checkpoint_seq"]:
                raise ValueError("checkpoint_conflict")
            previous_covered = json.loads(previous["data_json"])["covered_through_seq"] if previous else 0
            covered = data["covered_through_seq"]
            current = [row for row in rows if row["turn_id"] == turn_id and row["event_type"] != "context/checkpoint"]
            if not current or any(row["event_type"] == "turn/end" for row in current):
                raise ValueError("checkpoint_current_turn_not_active")
            if covered < previous_covered or covered >= current[0]["seq"]:
                raise ValueError("checkpoint_invalid_boundary")
            boundary = next((row for row in rows if row["seq"] == covered), None)
            if boundary is None or boundary["event_type"] != "turn/end":
                raise ValueError("checkpoint_boundary_must_end_old_turn")
            starts = {row["turn_id"] for row in rows if row["turn_id"] and row["seq"] <= covered and row["event_type"] != "context/checkpoint"}
            ends = {row["turn_id"] for row in rows if row["seq"] <= covered and row["event_type"] == "turn/end"}
            if starts - ends:
                raise ValueError("checkpoint_would_cover_incomplete_turn")
            seq = rows[-1]["seq"] + 1 if rows else 1
            connection.execute("INSERT INTO session_events (session_id, seq, turn_id, step, event_type, data_json, created_at) VALUES (?, ?, ?, NULL, 'context/checkpoint', ?, ?)", (session_id, seq, turn_id, json.dumps(data, ensure_ascii=False, allow_nan=False), _now()))
        return seq

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
