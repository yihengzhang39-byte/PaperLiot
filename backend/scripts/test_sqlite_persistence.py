"""Pure-local checks for the SQLite persistence foundation."""

import hashlib
import json
import sqlite3
import tempfile
from pathlib import Path

from app.repositories.paper_repository import PaperRepository
from app.repositories.session_event_repository import SessionEventRepository
from app.repositories.session_repository import SessionRepository
from app.services.persistence_migration import migrate_legacy_storage
from app.services.sqlite_service import initialize_database


def main() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        database_path = root / "paperpilot.db"
        initialize_database(database_path)

        papers = PaperRepository(database_path)
        created = papers.create("p1", "hash-a", "a.pdf", "/papers/p1_a.pdf")
        assert created["paper_id"] == "p1" and papers.find_by_hash("hash-a")["paper_id"] == "p1"
        try:
            papers.create("p2", "hash-a", "b.pdf", "/papers/p2_b.pdf")
        except sqlite3.IntegrityError:
            pass
        else:
            raise AssertionError("Expected content_hash UNIQUE constraint")

        sessions = SessionRepository(database_path)
        sessions.get_or_create("session-a")
        sessions.set_current_paper("session-a", "p1")
        sessions.add_paper("session-a", "p1")
        sessions.add_paper("session-a", "p2")
        assert sessions.get("session-a")["current_paper_id"] == "p1"
        assert sessions.get_active_papers("session-a") == ["p1", "p2"]

        events = SessionEventRepository(database_path)
        assert events.append_event("session-a", "turn/start", {"summary": "safe"}, turn_id="turn-1") == 1
        assert events.append_event("session-a", "tool/result", {"summary": "retrieved 1 chunk"}, turn_id="turn-1", step=1) == 2
        assert events.append_event("session-b", "turn/start", {"summary": "safe"}) == 1
        assert [event["seq"] for event in events.list_events_by_turn("session-a", "turn-1")] == [1, 2]

        assert PaperRepository(database_path).get("p1")["content_hash"] == "hash-a"
        assert SessionRepository(database_path).get_active_papers("session-a") == ["p1", "p2"]

        legacy_root = root / "legacy"
        papers_dir, sessions_dir = legacy_root / "papers", legacy_root / "chat_sessions"
        papers_dir.mkdir(parents=True)
        sessions_dir.mkdir()
        old_id, content = "a" * 32, b"legacy-pdf"
        old_path = papers_dir / f"{old_id}_legacy.pdf"
        old_path.write_bytes(content)
        index_path = legacy_root / "paper_index.json"
        index_path.write_text(
            json.dumps({"by_hash": {hashlib.sha256(content).hexdigest(): {"paper_id": old_id, "filename": "legacy.pdf"}}}),
            encoding="utf-8",
        )
        (sessions_dir / "legacy.json").write_text(
            json.dumps({"session_id": "legacy-session", "paper_id": old_id, "active_paper_ids": [old_id, "other"], "updated_at": "2026-01-01T00:00:00+00:00"}),
            encoding="utf-8",
        )
        migration_db = legacy_root / "paperpilot.db"
        migrate_legacy_storage(database_path=migration_db, papers_dir=papers_dir, paper_index_path=index_path, chat_sessions_dir=sessions_dir)
        migrate_legacy_storage(database_path=migration_db, papers_dir=papers_dir, paper_index_path=index_path, chat_sessions_dir=sessions_dir)
        assert PaperRepository(migration_db).find_by_hash(hashlib.sha256(content).hexdigest())["paper_id"] == old_id
        assert SessionRepository(migration_db).get("legacy-session")["current_paper_id"] == old_id
        assert SessionRepository(migration_db).get_active_papers("legacy-session") == [old_id, "other"]

    print("ALL SQLITE PERSISTENCE TESTS PASSED")


if __name__ == "__main__":
    main()
