"""Local file storage utilities for PDFs and Markdown notes."""

import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
from threading import Lock
from typing import Mapping, Any
from uuid import uuid4

from fastapi import UploadFile

from app.repositories.paper_repository import PaperRepository
from app.core.config import (
    NOTES_DIR,
    PAPER_CHUNKS_DIR,
    PAPER_METADATA_DIR,
    PAPER_INDEX_PATH,
    PAPER_PARSE_CACHE_DIR,
    PAPER_SECTION_JSON_DIR,
    PAPERS_DIR,
    ensure_storage_dirs,
)
from app.services.persistence_migration import migrate_legacy_papers


UPLOAD_CHUNK_SIZE = 1024 * 1024
# ponytail: process-local lock reduces duplicate file work; SQLite UNIQUE resolves cross-process hash races.
_PAPER_UPLOAD_LOCK = Lock()


def generate_paper_id() -> str:
    """Generate a unique paper id."""
    return uuid4().hex


def _safe_filename(filename: str) -> str:
    """Keep only a safe basename for local storage."""
    return Path(filename).name.replace(" ", "_")


def _safe_json_filename(filename: str) -> str:
    """Build a filesystem-safe JSON filename from the original PDF name."""
    stem = Path(filename).stem or "paper_sections"
    safe_stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", stem).strip(" ._")
    return f"{safe_stem or 'paper_sections'}.json"


def _original_pdf_filename(pdf_path: str, paper_id: str) -> str:
    """Recover the original upload filename when storage added a paper_id prefix."""
    filename = Path(pdf_path).name
    prefix = f"{paper_id}_"
    if filename.startswith(prefix):
        return filename[len(prefix) :]
    return filename


def normalize_paper_language(paper_language: str | None) -> str:
    """Normalize paper language without breaking old callers."""
    value = (paper_language or "zh").strip().lower()
    return value if value in {"zh", "en"} else "zh"


def _paper_metadata_path(paper_id: str) -> Path:
    """Return metadata path for an uploaded paper."""
    return PAPER_METADATA_DIR / f"{paper_id}.json"


def save_paper_metadata(paper_id: str, metadata: Mapping[str, Any]) -> None:
    """Save lightweight paper metadata locally."""
    ensure_storage_dirs()
    _paper_metadata_path(paper_id).write_text(
        json.dumps(dict(metadata), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def read_paper_metadata(paper_id: str) -> dict[str, Any]:
    """Read lightweight paper metadata if present."""
    path = _paper_metadata_path(paper_id)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _parse_cache_path(paper_id: str, parser_name: str) -> Path:
    """Return a safe per-paper, per-parser cache path."""
    if not re.fullmatch(r"[A-Za-z0-9_-]+", paper_id):
        raise ValueError("paper_id must contain only letters, numbers, underscores, or hyphens")
    if not re.fullmatch(r"[a-z0-9_]+", parser_name):
        raise ValueError("parser_name must contain only lowercase letters, numbers, or underscores")
    return PAPER_PARSE_CACHE_DIR / f"{paper_id}_{parser_name}.json"


def save_paper_parse_cache(paper_id: str, parser_name: str, payload: Mapping[str, Any]) -> None:
    """Persist one parser result without merging it with another parser's data."""
    ensure_storage_dirs()
    _parse_cache_path(paper_id, parser_name).write_text(
        json.dumps(dict(payload), ensure_ascii=False), encoding="utf-8"
    )


def read_paper_parse_cache(paper_id: str, parser_name: str) -> dict[str, Any] | None:
    """Read one parser-specific cached result, if available."""
    path = _parse_cache_path(paper_id, parser_name)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def save_upload_pdf(file: UploadFile, paper_language: str = "zh") -> dict[str, object]:
    """Stream one upload, then reuse or create its canonical paper id by SHA-256."""
    ensure_storage_dirs()
    filename = _safe_filename(file.filename or "paper.pdf")
    paper_language = normalize_paper_language(paper_language)
    temp_path = PAPERS_DIR / f".upload-{uuid4().hex}.tmp"
    hasher = hashlib.sha256()
    moved_path: Path | None = None
    paper_id: str | None = None
    try:
        with temp_path.open("wb") as output:
            while chunk := file.file.read(UPLOAD_CHUNK_SIZE):
                hasher.update(chunk)
                output.write(chunk)
        content_hash = hasher.hexdigest()

        migrate_legacy_papers(papers_dir=PAPERS_DIR, paper_index_path=PAPER_INDEX_PATH)
        repository = PaperRepository()
        with _PAPER_UPLOAD_LOCK:
            existing = repository.find_by_hash(content_hash)
            existing_path = find_paper_pdf(str(existing["paper_id"])) if existing else None
            if existing is not None and existing_path is not None:
                metadata = read_paper_metadata(str(existing["paper_id"]))
                return {
                    "paper_id": str(existing["paper_id"]),
                    "filename": str(metadata.get("filename") or existing["filename"]),
                    "file_path": str(existing_path),
                    "paper_language": str(metadata.get("paper_language") or paper_language),
                    "reused": True,
                }
            if existing is not None:
                repository.delete(str(existing["paper_id"]))

            paper_id = generate_paper_id()
            moved_path = PAPERS_DIR / f"{paper_id}_{filename}"
            os.replace(temp_path, moved_path)
            save_paper_metadata(
                paper_id,
                {
                    "paper_id": paper_id,
                    "filename": filename,
                    "file_path": str(moved_path),
                    "paper_language": paper_language,
                },
            )
            try:
                repository.create(paper_id, content_hash, filename, str(moved_path))
            except sqlite3.IntegrityError:
                moved_path.unlink(missing_ok=True)
                moved_path = None
                _paper_metadata_path(paper_id).unlink(missing_ok=True)
                existing = repository.find_by_hash(content_hash)
                if existing is None or find_paper_pdf(str(existing["paper_id"])) is None:
                    raise
                return {
                    "paper_id": str(existing["paper_id"]),
                    "filename": str(existing["filename"]),
                    "file_path": str(find_paper_pdf(str(existing["paper_id"]))),
                    "paper_language": paper_language,
                    "reused": True,
                }
            return {
                "paper_id": paper_id,
                "filename": filename,
                "file_path": str(moved_path),
                "paper_language": paper_language,
                "reused": False,
            }
    except Exception:
        if moved_path is not None and moved_path.exists():
            moved_path.unlink()
        if paper_id is not None:
            _paper_metadata_path(paper_id).unlink(missing_ok=True)
        raise
    finally:
        if temp_path.exists():
            temp_path.unlink()


def find_paper_pdf(paper_id: str) -> Path | None:
    """Find a saved PDF by paper id."""
    ensure_storage_dirs()
    direct_path = PAPERS_DIR / f"{paper_id}.pdf"
    if direct_path.exists():
        return direct_path

    matches = sorted(PAPERS_DIR.glob(f"{paper_id}_*.pdf"))
    return matches[0] if matches else None


def delete_paper_data(paper_id: str) -> bool:
    """Delete local files owned by one generated paper id."""
    if not re.fullmatch(r"[0-9a-f]{32}", paper_id):
        raise ValueError("paper_id must be a generated 32-character hexadecimal id")

    paths = [
        PAPERS_DIR / f"{paper_id}.pdf",
        *PAPERS_DIR.glob(f"{paper_id}_*.pdf"),
        NOTES_DIR / f"{paper_id}.md",
        PAPER_METADATA_DIR / f"{paper_id}.json",
        PAPER_CHUNKS_DIR / f"{paper_id}.json",
        *PAPER_CHUNKS_DIR.glob(f"{paper_id}_*.json"),
        *PAPER_PARSE_CACHE_DIR.glob(f"{paper_id}_*.json"),
    ]
    deleted = False
    for path in paths:
        if path.is_file():
            path.unlink()
            deleted = True

    for path in PAPER_SECTION_JSON_DIR.glob("*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict) and payload.get("paper_id") == paper_id:
            path.unlink()
            deleted = True
    if deleted:
        PaperRepository().delete(paper_id)
    return deleted


def write_text_file(path: Path, content: str) -> None:
    """Write UTF-8 text to a file, creating parent directories if needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def read_note(paper_id: str) -> str | None:
    """Read a generated Markdown note by paper id."""
    note_path = NOTES_DIR / f"{paper_id}.md"
    if not note_path.exists():
        return None
    return note_path.read_text(encoding="utf-8")


def save_paper_sections_json(state: Mapping[str, Any]) -> Path:
    """Save extracted paper sections and metadata as a UTF-8 JSON file."""
    ensure_storage_dirs()
    paper_id = str(state.get("paper_id", ""))
    filename = _original_pdf_filename(str(state.get("pdf_path", "")), paper_id)
    output_path = PAPER_SECTION_JSON_DIR / _safe_json_filename(filename)

    payload = {
        "paper_id": paper_id,
        "filename": filename,
        "abstract": state.get("abstract", ""),
        "introduction": state.get("introduction", ""),
        "related_work": state.get("related_work", ""),
        "method": state.get("method", ""),
        "experiments": state.get("experiments", ""),
        "conclusion": state.get("conclusion", ""),
        "section_meta": state.get("section_meta", {}),
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return output_path
