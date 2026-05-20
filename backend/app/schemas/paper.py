"""Pydantic models for paper APIs."""

from pydantic import BaseModel, Field


class PaperUploadResponse(BaseModel):
    """Response returned after uploading a PDF."""

    paper_id: str
    filename: str
    file_path: str
    paper_language: str = "zh"


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
    paper_info_debug: dict[str, object] = Field(default_factory=dict)
    missing_info_fields: list[str] = Field(default_factory=list)
    need_web_search: bool = False
    web_search_debug: dict[str, object] = Field(default_factory=dict)
    web_search_results: list[dict[str, object]] = Field(default_factory=list)


class PaperNoteResponse(BaseModel):
    """Response returned when reading a generated Markdown note."""

    paper_id: str
    content: str
