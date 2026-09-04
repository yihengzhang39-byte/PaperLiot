"""Idempotent one-time import of legacy filesystem identity and session headers."""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re

from app.core.config import CHAT_SESSIONS_DIR, PAPER_INDEX_PATH, PAPERS_DIR
from app.repositories.paper_repository import PaperRepository
from app.repositories.session_repository import SessionRepository
from app.services.sqlite_service import connect, initialize_database


def _timestamp(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()


def _file_hash(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            hasher.update(chunk)
    return hasher.hexdigest()


def _paper_id(path: Path) -> str | None:
    match = re.fullmatch(r"([0-9a-f]{32})(?:_.*)?\.pdf", path.name)
    return match.group(1) if match else None


def _done(database_path: Path | None, key: str) -> bool:
    with connect(database_path) as connection:
        return connection.execute("SELECT 1 FROM persistence_meta WHERE key = ?", (key,)).fetchone() is not None


def _mark_done(database_path: Path | None, key: str) -> None:
    with connect(database_path) as connection:
        connection.execute("INSERT OR IGNORE INTO persistence_meta (key, value) VALUES (?, ?)", (key, "done"))


def migrate_legacy_papers(
    *,
    database_path: Path | None = None,
    papers_dir: Path | None = None,
    paper_index_path: Path | None = None,
) -> None:
    """Import existing canonical PDFs once; legacy JSON remains untouched."""
    initialize_database(database_path)
    key = "legacy_papers_v1"
    if _done(database_path, key):
        return
    repository = PaperRepository(database_path)
    papers_dir = papers_dir or PAPERS_DIR
    index_path = paper_index_path or PAPER_INDEX_PATH
    indexed_hashes: dict[str, str] = {}
    try:
        payload = json.loads(index_path.read_text(encoding="utf-8"))
        for content_hash, entry in payload.get("by_hash", {}).items():
            if isinstance(content_hash, str) and isinstance(entry, dict) and isinstance(entry.get("paper_id"), str):
                indexed_hashes[entry["paper_id"]] = content_hash
    except (OSError, json.JSONDecodeError, AttributeError):
        pass
    for path in sorted(papers_dir.glob("*.pdf")):
        paper_id = _paper_id(path)
        if paper_id is None:
            continue
        content_hash = indexed_hashes.get(paper_id) or _file_hash(path)
        prefix = f"{paper_id}_"
        filename = path.name[len(prefix) :] if path.name.startswith(prefix) else path.name
        timestamp = _timestamp(path)
        if repository.find_by_hash(content_hash) is None and not repository.exists(paper_id):
            repository.create(paper_id, content_hash, filename, str(path), created_at=timestamp, updated_at=timestamp)
    _mark_done(database_path, key)


def migrate_legacy_sessions(
    *,
    database_path: Path | None = None,
    chat_sessions_dir: Path | None = None,
) -> None:
    """Import JSON session headers and paper relations once, never their messages."""
    initialize_database(database_path)
    key = "legacy_sessions_v1"
    if _done(database_path, key):
        return
    repository = SessionRepository(database_path)
    for path in sorted((chat_sessions_dir or CHAT_SESSIONS_DIR).glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        session_id = payload.get("session_id") if isinstance(payload, dict) else None
        if not isinstance(session_id, str) or not session_id.strip():
            continue
        timestamp = str(payload.get("updated_at") or _timestamp(path))
        repository.get_or_create(session_id.strip(), created_at=_timestamp(path), updated_at=timestamp)
        paper_id = payload.get("paper_id")
        if isinstance(paper_id, str) and paper_id.strip():
            repository.set_current_paper(session_id.strip(), paper_id.strip())
        for active_id in payload.get("active_paper_ids", []):
            if isinstance(active_id, str) and active_id.strip():
                repository.add_paper(session_id.strip(), active_id.strip())
        if isinstance(paper_id, str) and paper_id.strip():
            repository.add_paper(session_id.strip(), paper_id.strip())
    _mark_done(database_path, key)


def migrate_legacy_storage(
    *,
    database_path: Path | None = None,
    papers_dir: Path | None = None,
    paper_index_path: Path | None = None,
    chat_sessions_dir: Path | None = None,
) -> None:
    """Run both idempotent imports for explicit migrations and local startup use."""
    migrate_legacy_papers(
        database_path=database_path,
        papers_dir=papers_dir,
        paper_index_path=paper_index_path,
    )
    migrate_legacy_sessions(
        database_path=database_path,
        chat_sessions_dir=chat_sessions_dir,
    )
