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
    title: str = ""
    authors: list[str] = Field(default_factory=list)
    year: str = ""
    venue: str = ""
    abstract: str = ""
    analysis_plan: dict[str, object] = Field(default_factory=dict)
    paper_type: str = ""
    structure_type: str = ""
    plan_debug: dict[str, object] = Field(default_factory=dict)
    agent_decisions: list[dict[str, object]] = Field(default_factory=list)
    section_meta: dict[str, dict[str, object]]
    section_quality: dict[str, object] = Field(default_factory=dict)
    section_verify_debug: dict[str, object] = Field(default_factory=dict)
    needs_section_repair: bool = False
    section_repair_debug: dict[str, object] = Field(default_factory=dict)
    paper_info_debug: dict[str, object] = Field(default_factory=dict)
    missing_info_fields: list[str] = Field(default_factory=list)
    need_web_search: bool = False
    web_search_debug: dict[str, object] = Field(default_factory=dict)
    web_search_results: list[dict[str, object]] = Field(default_factory=list)


class PaperNoteResponse(BaseModel):
    """Response returned when reading a generated Markdown note."""

    paper_id: str
    content: str
