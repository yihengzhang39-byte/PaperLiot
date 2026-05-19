"""Pydantic models for paper APIs."""

from pydantic import BaseModel


class PaperUploadResponse(BaseModel):
    """Response returned after uploading a PDF."""

    paper_id: str
    filename: str
    file_path: str


class PaperAnalyzeResponse(BaseModel):
    """Response returned after analyzing a PDF."""

    paper_id: str
    note_path: str
    final_note: str
    provider: str
    model: str
    success: bool
    error_message: str
    section_meta: dict[str, dict[str, object]]


class PaperNoteResponse(BaseModel):
    """Response returned when reading a generated Markdown note."""

    paper_id: str
    content: str
