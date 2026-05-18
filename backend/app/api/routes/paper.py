"""Paper upload, analysis, and note APIs."""

from fastapi import APIRouter, File, HTTPException, UploadFile

from app.agents.paper_graph import analyze_paper
from app.core.config import get_llm_config
from app.services.file_service import (
    find_paper_pdf,
    read_note,
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
def upload_paper(file: UploadFile = File(...)) -> dict[str, str]:
    """Upload a PDF file and save it locally."""
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported.")

    try:
        return save_upload_pdf(file)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to save PDF: {exc}") from exc


@router.post("/{paper_id}/analyze", response_model=PaperAnalyzeResponse)
def analyze_uploaded_paper(paper_id: str) -> dict[str, object]:
    """Analyze an uploaded paper by paper_id."""
    llm_config = get_llm_config()
    pdf_path = find_paper_pdf(paper_id)
    if pdf_path is None:
        raise HTTPException(status_code=404, detail="PDF not found for this paper_id.")

    try:
        result = analyze_paper(str(pdf_path), paper_id)
    except Exception as exc:
        return {
            "paper_id": paper_id,
            "note_path": "",
            "final_note": "",
            "provider": llm_config.provider,
            "model": llm_config.model,
            "success": False,
            "error_message": str(exc),
        }

    return {
        "paper_id": paper_id,
        "note_path": result.get("note_path", ""),
        "final_note": result.get("final_note", ""),
        "provider": llm_config.provider,
        "model": llm_config.model,
        "success": True,
        "error_message": "",
    }


@router.get("/{paper_id}/note", response_model=PaperNoteResponse)
def get_paper_note(paper_id: str) -> dict[str, str]:
    """Read the generated Markdown note for a paper."""
    content = read_note(paper_id)
    if content is None:
        raise HTTPException(status_code=404, detail="Note not found for this paper_id.")

    return {"paper_id": paper_id, "content": content}
