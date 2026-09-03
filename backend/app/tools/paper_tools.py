"""Agent-facing PDF parser and cached-paper Tools."""

import re
from typing import Any

from app.agents.nodes.section_extract_node import (
    SECTION_KEYS,
    extract_sections_by_rules_with_meta,
    extract_sections_from_text,
)
from app.runtime.tool_registry import ToolRegistry
from app.services import retrieval_service
from app.services.file_service import (
    find_paper_pdf,
    read_paper_metadata,
    read_paper_parse_cache,
    save_paper_parse_cache,
)
from app.services.parser_service import parse_pdf
from app.services.parsers.schema import ParsedPaper


PARSER_NAMES = ("grobid", "pymupdf")
_PAGE_MARKER = re.compile(r"^--- Page (\d+) ---\s*$", re.MULTILINE)


def _parser_name(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    normalized = value.strip().lower()
    if normalized not in PARSER_NAMES:
        raise ValueError(f"Unsupported parser: {normalized}")
    return normalized


def _paper_language(paper_id: str) -> str:
    return str(read_paper_metadata(paper_id).get("paper_language", "zh"))


def _load_cached_parse(paper_id: str, parser_name: str) -> ParsedPaper | None:
    payload = read_paper_parse_cache(paper_id, parser_name)
    if payload is None:
        return None
    try:
        parsed = ParsedPaper.model_validate(payload)
    except ValueError:
        return None
    return parsed if parsed.parser_name == parser_name else None


def _parse_with_cache(paper_id: str, parser_name: str) -> tuple[ParsedPaper, bool]:
    """Run exactly the requested parser, or reuse its own cached result."""
    cached = _load_cached_parse(paper_id, parser_name)
    if cached is not None:
        return cached, True
    pdf_path = find_paper_pdf(paper_id)
    if pdf_path is None:
        raise FileNotFoundError(f"Paper not found: {paper_id}")
    parsed = parse_pdf(str(pdf_path), parser_name=parser_name)
    save_paper_parse_cache(paper_id, parser_name, parsed.model_dump())
    return parsed, False


def _info_fields(parsed: Any) -> dict[str, object]:
    meta = getattr(parsed, "parser_meta", {}) or {}
    title = str(getattr(parsed, "title", "") or meta.get("candidate_title", ""))
    authors = list(getattr(parsed, "authors", []) or meta.get("candidate_authors", []))
    abstract = str(getattr(parsed, "abstract", "") or meta.get("candidate_abstract", ""))
    keywords = list(meta.get("keywords", []) or meta.get("candidate_keywords", []))
    return {
        "title": title,
        "authors": authors,
        "abstract": abstract,
        "keywords": keywords,
        "year": str(meta.get("year", "") or ""),
        "venue": str(meta.get("venue", "") or ""),
    }


def _missing_fields(parsed: Any) -> list[str]:
    fields = _info_fields(parsed)
    missing = [name for name in ("title", "authors", "abstract") if not fields[name]]
    if not getattr(parsed, "sections", []):
        missing.append("sections")
    return missing


def _section_summaries(parsed: Any) -> list[dict[str, object]]:
    summaries: list[dict[str, object]] = []
    for section in getattr(parsed, "sections", []) or []:
        text = str(getattr(section, "text", "") or "")
        summaries.append(
            {
                "title": str(getattr(section, "title", "") or ""),
                "level": int(getattr(section, "level", 1) or 1),
                "page_start": getattr(section, "page_start", None),
                "page_end": getattr(section, "page_end", None),
                "source": str(getattr(section, "source", "") or ""),
                "text_preview": text[:1200],
                "text_truncated": len(text) > 1200,
            }
        )
    return summaries


def _reference_summaries(parsed: Any) -> list[dict[str, object]]:
    summaries: list[dict[str, object]] = []
    for reference in getattr(parsed, "references", []) or []:
        text = str(getattr(reference, "text", "") or "")
        summaries.append(
            {
                "text": text[:800],
                "text_truncated": len(text) > 800,
                "title": str(getattr(reference, "title", "") or ""),
                "authors": list(getattr(reference, "authors", []) or []),
                "year": getattr(reference, "year", None),
            }
        )
    return summaries


def _document_summary(parsed: Any, parser_name: str, *, cached: bool) -> dict[str, object]:
    fields = _info_fields(parsed)
    meta = getattr(parsed, "parser_meta", {}) or {}
    return {
        "parser": parser_name,
        "success": True,
        "cached": cached,
        **fields,
        "page_count": int(meta.get("page_count", 0) or 0),
        "sections": _section_summaries(parsed),
        "references": _reference_summaries(parsed),
        "warnings": list(getattr(parsed, "parser_warnings", []) or []),
        "missing_fields": _missing_fields(parsed),
    }


def _parser_failure(paper_id: str, parser_name: str, exc: Exception) -> dict[str, object]:
    error = f"{type(exc).__name__}: {str(exc)[:500]}"
    return {
        "paper_id": paper_id,
        "parser": parser_name,
        "success": False,
        "cached": False,
        "title": "",
        "authors": [],
        "abstract": "",
        "keywords": [],
        "sections": [],
        "references": [],
        "warnings": [error],
        "missing_fields": ["title", "authors", "abstract", "sections"],
        "error": error,
    }


def parse_pdf_with_grobid(paper_id: str = "") -> dict[str, object]:
    """Parse academic structure with GROBID; incomplete fields are still usable."""
    try:
        parsed, cached = _parse_with_cache(paper_id, "grobid")
    except Exception as exc:
        return _parser_failure(paper_id, "grobid", exc)
    result = _document_summary(parsed, "grobid", cached=cached)
    result["paper_id"] = paper_id
    result["paper_language"] = _paper_language(paper_id)
    return result


def _requested_pages(pages: list[int] | None, page_count: int) -> list[int]:
    requested = [1] if pages is None else pages
    if not isinstance(requested, list) or not requested:
        raise ValueError("pages must be a non-empty list of page numbers")
    unique: list[int] = []
    for page in requested:
        if isinstance(page, bool) or not isinstance(page, int) or not 1 <= page <= page_count:
            raise ValueError(f"page must be between 1 and {page_count}")
        if page not in unique:
            unique.append(page)
    return unique


def _pages_from_raw_text(raw_text: str, pages: list[int]) -> list[dict[str, object]]:
    matches = list(_PAGE_MARKER.finditer(raw_text))
    extracted: dict[int, str] = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(raw_text)
        extracted[int(match.group(1))] = raw_text[match.end() : end].strip()
    return [{"page": page, "text": extracted.get(page, ""), "found": page in extracted} for page in pages]


def parse_pdf_with_pymupdf(
    paper_id: str = "",
    pages: list[int] | None = None,
) -> dict[str, object]:
    """Read selected PDF pages with PyMuPDF; defaults to the first page."""
    try:
        parsed, cached = _parse_with_cache(paper_id, "pymupdf")
        meta = getattr(parsed, "parser_meta", {}) or {}
        page_count = int(meta.get("page_count", 0) or 0)
        requested_pages = _requested_pages(pages, page_count)
    except Exception as exc:
        return _parser_failure(paper_id, "pymupdf", exc)
    result = _document_summary(parsed, "pymupdf", cached=cached)
    result.update(
        {
            "paper_id": paper_id,
            "paper_language": _paper_language(paper_id),
            "pages": _pages_from_raw_text(str(getattr(parsed, "raw_text", "") or ""), requested_pages),
        }
    )
    return result


def _cached_documents(paper_id: str, parser_name: str | None = None) -> list[dict[str, object]]:
    documents: list[dict[str, object]] = []
    for name in ((parser_name,) if parser_name else PARSER_NAMES):
        parsed = _load_cached_parse(paper_id, name)
        if parsed is not None:
            documents.append(_document_summary(parsed, name, cached=True))
    return documents


def get_paper_info(paper_id: str = "", parser: str = "") -> dict[str, object]:
    """Read metadata from parser results already chosen by the Agent; never parse."""
    parser_name = _parser_name(parser)
    documents = _cached_documents(paper_id, parser_name)
    primary = documents[0] if len(documents) == 1 else {}
    return {
        "paper_id": paper_id,
        "paper_language": _paper_language(paper_id),
        "success": bool(documents),
        "parser": primary.get("parser", ""),
        "title": primary.get("title", ""),
        "authors": primary.get("authors", []),
        "year": primary.get("year", ""),
        "venue": primary.get("venue", ""),
        "abstract": primary.get("abstract", ""),
        "cached_parsers": [document["parser"] for document in documents],
        "documents": documents,
        "warning": "No cached parser result; call a parser Tool first." if not documents else "",
    }


def _section_result(parsed: Any, parser_name: str) -> dict[str, object]:
    result = extract_sections_from_text(
        str(getattr(parsed, "raw_text", "") or ""),
        str(getattr(parsed, "abstract", "") or ""),
    )
    sections = {name: str(result.get(name, "") or "") for name in SECTION_KEYS}
    return {
        "parser": parser_name,
        "sections": _section_summaries(parsed),
        "section_lengths": {name: len(content) for name, content in sections.items()},
        "section_previews": {name: content[:800] for name, content in sections.items() if content},
        "section_meta": result.get("section_meta", {}),
    }


def extract_sections(paper_id: str = "", parser: str = "") -> dict[str, object]:
    """Read sections from cached parser results; never select or run a parser."""
    parser_name = _parser_name(parser)
    sources = [
        _section_result(parsed, name)
        for name in ((parser_name,) if parser_name else PARSER_NAMES)
        if (parsed := _load_cached_parse(paper_id, name)) is not None
    ]
    primary = sources[0] if len(sources) == 1 else {}
    return {
        "paper_id": paper_id,
        "paper_language": _paper_language(paper_id),
        "success": bool(sources),
        "parser": primary.get("parser", ""),
        "section_lengths": primary.get("section_lengths", {}),
        "section_previews": primary.get("section_previews", {}),
        "section_meta": primary.get("section_meta", {}),
        "sources": sources,
        "warning": "No cached parser result; call a parser Tool first." if not sources else "",
    }


def _retrieval_sections(parsed: Any) -> dict[str, str]:
    """Prefer parser-provided sections, then local rule-only sections."""
    sections = {
        str(getattr(section, "title", "") or "document"): str(getattr(section, "text", "") or "")
        for section in getattr(parsed, "sections", []) or []
        if str(getattr(section, "text", "") or "").strip()
    }
    if sections:
        return sections
    rule_sections, _ = extract_sections_by_rules_with_meta(str(getattr(parsed, "raw_text", "") or ""))
    sections = {name: text for name, text in rule_sections.items() if text}
    return sections or {"document": str(getattr(parsed, "raw_text", "") or "")}


def _retrieve_from_cached_parse(
    paper_id: str,
    parser_name: str,
    parsed: Any,
    query: str,
    top_k: int | None,
) -> dict[str, object]:
    try:
        result = retrieval_service.retrieve_paper_chunks(
            paper_id, query, top_k=top_k, parser_name=parser_name
        )
    except FileNotFoundError:
        chunks = retrieval_service.build_paper_chunks(paper_id, _retrieval_sections(parsed))
        retrieval_service.save_paper_chunks(paper_id, chunks, parser_name=parser_name)
        result = retrieval_service.retrieve_paper_chunks(
            paper_id, query, top_k=top_k, parser_name=parser_name
        )
    for chunk in result["chunks"]:
        chunk["parser"] = parser_name
    return result


def retrieve_paper_context(
    paper_id: str = "",
    query: str = "",
    top_k: int | None = None,
    parser: str = "",
) -> dict[str, object]:
    """Retrieve local context from cached parser sources without reparsing a PDF."""
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query cannot be empty")
    parser_name = _parser_name(parser)
    sources = []
    for name in ((parser_name,) if parser_name else PARSER_NAMES):
        parsed = _load_cached_parse(paper_id, name)
        if parsed is not None:
            sources.append(_retrieve_from_cached_parse(paper_id, name, parsed, query, top_k))
    return {
        "paper_id": paper_id,
        "query": query.strip(),
        "success": bool(sources),
        "chunks": [chunk for source in sources for chunk in source["chunks"]],
        "sources": sources,
        "warning": "No cached parser result; call a parser Tool first." if not sources else "",
    }


PAPER_TOOL_SPECS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "parse_pdf_with_grobid",
            "description": "Parse an uploaded academic PDF with GROBID for title, authors, abstract, section hierarchy, and references. Missing title or authors does not mean failure; inspect missing_fields and call parse_pdf_with_pymupdf if needed.",
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
            "name": "parse_pdf_with_pymupdf",
            "description": "Read selected raw PDF pages with PyMuPDF. Use it for first-page metadata, page-specific questions, unusual layouts, or to supplement an incomplete GROBID result. Omit pages to read page 1.",
            "parameters": {
                "type": "object",
                "properties": {
                    "paper_id": {"type": "string"},
                    "pages": {"type": "array", "items": {"type": "integer", "minimum": 1}, "minItems": 1},
                },
                "required": ["paper_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_paper_info",
            "description": "Read metadata from cached parser results only. If no result is cached, call a parser Tool instead of assuming paper contents.",
            "parameters": {
                "type": "object",
                "properties": {
                    "paper_id": {"type": "string"},
                    "parser": {"type": "string", "enum": list(PARSER_NAMES)},
                },
                "required": ["paper_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "extract_sections",
            "description": "Read section structure from cached parser results only. If no result is cached, call a parser Tool first.",
            "parameters": {
                "type": "object",
                "properties": {
                    "paper_id": {"type": "string"},
                    "parser": {"type": "string", "enum": list(PARSER_NAMES)},
                },
                "required": ["paper_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "retrieve_paper_context",
            "description": "Retrieve bounded context from cached parser results only. If no result is cached, call a parser Tool first.",
            "parameters": {
                "type": "object",
                "properties": {
                    "paper_id": {"type": "string"},
                    "query": {"type": "string"},
                    "top_k": {"type": "integer", "minimum": 1},
                    "parser": {"type": "string", "enum": list(PARSER_NAMES)},
                },
                "required": ["paper_id", "query"],
            },
        },
    },
]


def register_paper_tools(registry: ToolRegistry) -> None:
    """Register the Agent-selectable parser and cached-paper Tools."""
    registry.register("parse_pdf_with_grobid", parse_pdf_with_grobid)
    registry.register("parse_pdf_with_pymupdf", parse_pdf_with_pymupdf)
    registry.register("get_paper_info", get_paper_info)
    registry.register("extract_sections", extract_sections)
    registry.register("retrieve_paper_context", retrieve_paper_context)
