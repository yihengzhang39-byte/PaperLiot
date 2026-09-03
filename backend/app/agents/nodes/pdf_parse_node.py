"""PDF parsing node."""

from app.agents.paper_state import PaperState
from app.services.parser_service import parse_pdf


def pdf_parse_node(state: PaperState) -> dict[str, object]:
    """Extract raw text from the PDF path in state."""
    parsed = parse_pdf(state["pdf_path"])
    return {
        "raw_text": parsed.raw_text,
        "parsed_paper": parsed.model_dump(),
        "parser_name": parsed.parser_name,
        "parser_warnings": parsed.parser_warnings,
        "parser_meta": parsed.parser_meta,
        "requested_parser": str(parsed.parser_meta.get("requested_parser", "")),
        "paper_language": state.get("paper_language", "zh"),
    }
