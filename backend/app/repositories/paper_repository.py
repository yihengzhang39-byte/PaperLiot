"""Repository for stable paper identity records."""

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.services.sqlite_service import connect, initialize_database


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class PaperRepository:
    def __init__(self, database_path: Path | None = None) -> None:
        self.database_path = database_path
        initialize_database(database_path)

    def get(self, paper_id: str) -> dict[str, Any] | None:
        with connect(self.database_path) as connection:
            row = connection.execute("SELECT * FROM papers WHERE paper_id = ?", (paper_id,)).fetchone()
        return dict(row) if row else None

    def find_by_hash(self, content_hash: str) -> dict[str, Any] | None:
        with connect(self.database_path) as connection:
            row = connection.execute("SELECT * FROM papers WHERE content_hash = ?", (content_hash,)).fetchone()
        return dict(row) if row else None

    def create(
        self,
        paper_id: str,
        content_hash: str,
        filename: str,
        file_path: str,
        *,
        created_at: str | None = None,
        updated_at: str | None = None,
    ) -> dict[str, Any]:
        created_at = created_at or _now()
        updated_at = updated_at or created_at
        with connect(self.database_path) as connection:
            connection.execute(
                "INSERT INTO papers (paper_id, content_hash, filename, file_path, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                (paper_id, content_hash, filename, file_path, created_at, updated_at),
            )
        return self.get(paper_id) or {}

    def exists(self, paper_id: str) -> bool:
        return self.get(paper_id) is not None

    def delete(self, paper_id: str) -> bool:
        with connect(self.database_path) as connection:
            cursor = connection.execute("DELETE FROM papers WHERE paper_id = ?", (paper_id,))
        return cursor.rowcount > 0
