"""PaperPilot domain tools backed by existing parser and section capabilities."""

from typing import Any

from app.agents.nodes.section_extract_node import (
    SECTION_KEYS,
    extract_sections_by_rules_with_meta,
    extract_sections_from_text,
)
from app.runtime.tool_registry import ToolRegistry
from app.services import retrieval_service


def _load_paper(paper_id: str) -> tuple[Any, str, str]:
    """Load and parse one uploaded paper by its stable storage identifier."""
    if not paper_id.strip():
        raise ValueError("paper_id is required")
    from app.services.file_service import find_paper_pdf, normalize_paper_language, read_paper_metadata
    from app.services.parser_service import parse_paper_for_language

    pdf_path = find_paper_pdf(paper_id)
    if pdf_path is None:
        raise FileNotFoundError(f"Paper not found: {paper_id}")
    metadata = read_paper_metadata(paper_id)
    return parse_paper_for_language(
        str(pdf_path),
        normalize_paper_language(str(metadata.get("paper_language", "zh"))),
    )


def get_paper_info(paper_id: str = "") -> dict[str, object]:
    """Get parser-extracted metadata for an uploaded paper without external lookup."""
    parsed, paper_language, requested_parser = _load_paper(paper_id)
    parser_meta = parsed.parser_meta
    return {
        "paper_id": paper_id,
        "paper_language": paper_language,
        "requested_parser": requested_parser,
        "parser_name": parsed.parser_name,
        "parser_warnings": parsed.parser_warnings,
        "title": parsed.title,
        "authors": parsed.authors,
        "year": parser_meta.get("year", ""),
        "venue": parser_meta.get("venue", ""),
        "abstract": parsed.abstract,
    }


def extract_sections(paper_id: str = "") -> dict[str, object]:
    """Extract major section previews and metadata for an uploaded paper."""
    parsed, paper_language, requested_parser = _load_paper(paper_id)
    result = extract_sections_from_text(parsed.raw_text, parsed.abstract)
    sections = {name: str(result.get(name, "") or "") for name in SECTION_KEYS}
    return {
        "paper_id": paper_id,
        "paper_language": paper_language,
        "requested_parser": requested_parser,
        "parser_name": parsed.parser_name,
        "section_lengths": {name: len(content) for name, content in sections.items()},
        "section_previews": {name: content[:800] for name, content in sections.items() if content},
        "section_meta": result.get("section_meta", {}),
    }


def _retrieval_sections(parsed: Any) -> dict[str, str]:
    """Prefer parser sections, then use rule-only extraction without an LLM fallback."""
    sections = {
        str(getattr(section, "title", "") or "document"): str(getattr(section, "text", "") or "")
        for section in getattr(parsed, "sections", []) or []
        if str(getattr(section, "text", "") or "").strip()
    }
    if sections:
        return sections
    rule_sections, _ = extract_sections_by_rules_with_meta(parsed.raw_text)
    sections = {name: text for name, text in rule_sections.items() if text}
    return sections or {"document": parsed.raw_text}


def retrieve_paper_context(
    paper_id: str = "",
    query: str = "",
    top_k: int | None = None,
) -> dict[str, object]:
    """Return bounded lexical context for a paper, building its local index once."""
    try:
        return retrieval_service.retrieve_paper_chunks(paper_id, query, top_k=top_k)
    except FileNotFoundError:
        parsed, _, _ = _load_paper(paper_id)
        chunks = retrieval_service.build_paper_chunks(paper_id, _retrieval_sections(parsed))
        retrieval_service.save_paper_chunks(paper_id, chunks)
        return retrieval_service.retrieve_paper_chunks(paper_id, query, top_k=top_k)


PAPER_TOOL_SPECS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "get_paper_info",
            "description": "Get parser-extracted metadata for an uploaded paper identified by paper_id.",
            "parameters": {
                "type": "object",
                "properties": {"paper_id": {"type": "string"}},
                "required": ["paper_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "extract_sections",
            "description": "Extract major section previews and metadata from an uploaded paper identified by paper_id.",
            "parameters": {
                "type": "object",
                "properties": {"paper_id": {"type": "string"}},
                "required": ["paper_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "retrieve_paper_context",
            "description": "Retrieve bounded, relevant paper chunks when section previews are insufficient.",
            "parameters": {
                "type": "object",
                "properties": {
                    "paper_id": {"type": "string"},
                    "query": {"type": "string"},
                    "top_k": {"type": "integer", "minimum": 1},
                },
                "required": ["paper_id", "query"],
            },
        },
    },
]


def register_paper_tools(registry: ToolRegistry) -> None:
    """Register the Phase 3 PaperPilot tools with a runtime registry."""
    registry.register("get_paper_info", get_paper_info)
    registry.register("extract_sections", extract_sections)
    registry.register("retrieve_paper_context", retrieve_paper_context)
