"""Paper metadata extraction node."""

from app.agents.paper_state import PaperState
from app.services.llm_service import extract_paper_info


def paper_info_node(state: PaperState) -> dict[str, object]:
    """Extract paper title, authors, venue, year, and abstract."""
    text = state.get("raw_text", "")[:8000]
    return extract_paper_info(text)
