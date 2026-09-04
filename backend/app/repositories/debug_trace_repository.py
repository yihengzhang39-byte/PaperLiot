"""Append-only local persistence for non-semantic debug execution facts."""

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from app.services.sqlite_service import connect, initialize_database


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class DebugTraceRepository:
    """Keep debug-only events separate from conversation replay events."""

    def __init__(self, database_path: Path | None = None) -> None:
        self.database_path = database_path
        initialize_database(database_path)

    def append(
        self,
        session_id: str,
        turn_id: str,
        event_type: str,
        data: dict[str, Any],
        *,
        step: int | None = None,
    ) -> int:
        with connect(self.database_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            trace_seq = int(
                connection.execute(
                    "SELECT COALESCE(MAX(trace_seq), 0) + 1 FROM debug_trace_events WHERE session_id = ? AND turn_id = ?",
                    (session_id, turn_id),
                ).fetchone()[0]
            )
            connection.execute(
                "INSERT INTO debug_trace_events (session_id, turn_id, step, trace_seq, event_type, data_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (session_id, turn_id, step, trace_seq, event_type, json.dumps(data, ensure_ascii=False), _now()),
            )
        return trace_seq

    def list_by_turn(self, session_id: str, turn_id: str) -> list[dict[str, Any]]:
        with connect(self.database_path) as connection:
            rows = connection.execute(
                "SELECT * FROM debug_trace_events WHERE session_id = ? AND turn_id = ? ORDER BY trace_seq",
                (session_id, turn_id),
            ).fetchall()
        return [{**dict(row), "data": json.loads(str(row["data_json"]))} for row in rows]

    def list_by_step(self, session_id: str, turn_id: str, step: int) -> list[dict[str, Any]]:
        return [event for event in self.list_by_turn(session_id, turn_id) if event["step"] == step]
