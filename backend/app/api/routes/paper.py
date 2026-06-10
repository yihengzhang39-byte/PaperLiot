"""Paper upload, analysis, and note APIs."""

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile

from app.agents.paper_graph import analyze_paper
from app.core.config import get_llm_config
from app.services.file_service import (
    find_paper_pdf,
    normalize_paper_language,
    read_paper_metadata,
    read_note,
    save_paper_metadata,
    save_upload_pdf,
)
from app.schemas.paper import (
    PaperAnalyzeResponse,
    PaperNoteResponse,
    PaperUploadResponse,
)

router = APIRouter()


"""
    上传PDF文件，保存到本地，并返回paper_id
"""
@router.post("/upload", response_model=PaperUploadResponse)
def upload_paper(
    file: UploadFile = File(...),
    paper_language: str = Form("zh"),
) -> dict[str, str]:
    """Upload a PDF file and save it locally."""
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported.")

    try:
        return save_upload_pdf(file, normalize_paper_language(paper_language))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to save PDF: {exc}") from exc


@router.post("/{paper_id}/analyze", response_model=PaperAnalyzeResponse)
def analyze_uploaded_paper(
    paper_id: str,
    paper_language: str | None = Query(None),
) -> dict[str, object]:
    """Analyze an uploaded paper by paper_id."""
    llm_config = get_llm_config()
    pdf_path = find_paper_pdf(paper_id)
    if pdf_path is None:
        raise HTTPException(status_code=404, detail="PDF not found for this paper_id.")

    metadata = read_paper_metadata(paper_id)
    effective_language = normalize_paper_language(
        paper_language if paper_language is not None else metadata.get("paper_language", "zh")
    )
    if paper_language is not None:
        metadata.update({"paper_language": effective_language})
        save_paper_metadata(paper_id, metadata)

    try:
        result = analyze_paper(str(pdf_path), paper_id, paper_language=effective_language)
    except Exception as exc:
        return {
            "paper_id": paper_id,
            "note_path": "",
            "final_note": "",
            "provider": llm_config.provider,
            "model": llm_config.model,
            "success": False,
            "error_message": str(exc),
            "title": "",
            "authors": [],
            "year": "",
            "venue": "",
            "abstract": "",
            "analysis_plan": {},
            "paper_type": "",
            "structure_type": "",
            "plan_debug": {},
            "agent_decisions": [],
            "section_meta": {},
            "section_quality": {},
            "section_verify_debug": {},
            "needs_section_repair": False,
            "section_repair_debug": {},
            "paper_info_debug": {},
            "missing_info_fields": [],
            "need_web_search": False,
            "web_search_debug": {},
            "web_search_results": [],
        }

    return {
        "paper_id": paper_id,
        "note_path": result.get("note_path", ""),
        "final_note": result.get("final_note", ""),
        "provider": llm_config.provider,
        "model": llm_config.model,
        "success": True,
        "error_message": "",
        "title": result.get("title", ""),
        "authors": result.get("authors", []),
        "year": result.get("year", ""),
        "venue": result.get("venue", ""),
        "abstract": result.get("abstract", ""),
        "analysis_plan": result.get("analysis_plan", {}),
        "paper_type": result.get("paper_type", ""),
        "structure_type": result.get("structure_type", ""),
        "plan_debug": result.get("plan_debug", {}),
        "agent_decisions": result.get("agent_decisions", []),
        "section_meta": result.get("section_meta", {}),
        "section_quality": result.get("section_quality", {}),
        "section_verify_debug": result.get("section_verify_debug", {}),
        "needs_section_repair": result.get("needs_section_repair", False),
        "section_repair_debug": result.get("section_repair_debug", {}),
        "paper_info_debug": result.get("paper_info_debug", {}),
        "missing_info_fields": result.get("missing_info_fields", []),
        "need_web_search": result.get("need_web_search", False),
        "web_search_debug": result.get("web_search_debug", {}),
        "web_search_results": result.get("web_search_results", []),
    }


@router.get("/{paper_id}/note", response_model=PaperNoteResponse)
def get_paper_note(paper_id: str) -> dict[str, str]:
    """Read the generated Markdown note for a paper."""
    content = read_note(paper_id)
    if content is None:
        raise HTTPException(status_code=404, detail="Note not found for this paper_id.")

    return {"paper_id": paper_id, "content": content}
