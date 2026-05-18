"""PDF parsing node."""

from app.agents.paper_state import PaperState
from app.services.pdf_service import extract_pdf_text


def pdf_parse_node(state: PaperState) -> dict[str, str]:
    """Extract raw text from the PDF path in state."""
    raw_text = extract_pdf_text(state["pdf_path"])
    return {"raw_text": raw_text}
