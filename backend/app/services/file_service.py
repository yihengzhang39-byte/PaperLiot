"""Local file storage utilities for PDFs and Markdown notes."""

from pathlib import Path
from uuid import uuid4

from fastapi import UploadFile

from app.core.config import NOTES_DIR, PAPERS_DIR, ensure_storage_dirs


def generate_paper_id() -> str:
    """Generate a unique paper id."""
    return uuid4().hex


def _safe_filename(filename: str) -> str:
    """Keep only a safe basename for local storage."""
    return Path(filename).name.replace(" ", "_")


def save_upload_pdf(file: UploadFile) -> dict[str, str]:
    """Save an uploaded PDF into local paper storage."""
    ensure_storage_dirs()
    paper_id = generate_paper_id()
    filename = _safe_filename(file.filename or f"{paper_id}.pdf")
    target_path = PAPERS_DIR / f"{paper_id}_{filename}"

    """
        写到本地文件storage文件夹里面，使用分块写入以支持大文件上传
    """
    with target_path.open("wb") as output:
        while chunk := file.file.read(1024 * 1024):
            output.write(chunk)

    return {
        "paper_id": paper_id,
        "filename": filename,
        "file_path": str(target_path),
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
