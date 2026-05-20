"""Local file storage utilities for PDFs and Markdown notes."""

import json
from pathlib import Path
import re
from typing import Mapping, Any
from uuid import uuid4

from fastapi import UploadFile

from app.core.config import (
    NOTES_DIR,
    PAPER_METADATA_DIR,
    PAPER_SECTION_JSON_DIR,
    PAPERS_DIR,
    ensure_storage_dirs,
)


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


def save_upload_pdf(file: UploadFile, paper_language: str = "zh") -> dict[str, str]:
    """Save an uploaded PDF into local paper storage."""
    ensure_storage_dirs()
    paper_id = generate_paper_id()
    filename = _safe_filename(file.filename or f"{paper_id}.pdf")
    paper_language = normalize_paper_language(paper_language)
    target_path = PAPERS_DIR / f"{paper_id}_{filename}"

    """
        写到本地文件storage文件夹里面，使用分块写入以支持大文件上传
    """
    with target_path.open("wb") as output:
        while chunk := file.file.read(1024 * 1024):
            output.write(chunk)

    save_paper_metadata(
        paper_id,
        {
            "paper_id": paper_id,
            "filename": filename,
            "file_path": str(target_path),
            "paper_language": paper_language,
        },
    )

    return {
        "paper_id": paper_id,
        "filename": filename,
        "file_path": str(target_path),
        "paper_language": paper_language,
    }


def find_paper_pdf(paper_id: str) -> Path | None:
    """Find a saved PDF by paper id."""
    ensure_storage_dirs()
    direct_path = PAPERS_DIR / f"{paper_id}.pdf"
    if direct_path.exists():
        return direct_path

    matches = sorted(PAPERS_DIR.glob(f"{paper_id}_*.pdf"))
    return matches[0] if matches else None


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
