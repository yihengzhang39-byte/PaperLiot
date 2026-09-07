"""Agent-facing PDF parser and cached-paper Tools."""

import re
import json
from typing import Any

from app.runtime.tool_executor import ToolExecutionResult
from app.agents.nodes.section_extract_node import (
    SECTION_KEYS,
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
    if value is not None and not isinstance(value, str):
        raise retrieval_service.ToolInputError("parser must be a string")
    if value is None or not value.strip():
        return None
    normalized = value.strip().lower()
    if normalized not in PARSER_NAMES:
        raise retrieval_service.ToolInputError("Unsupported parser; expected grobid or pymupdf")
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
    retrieval_service.validate_paper_id(paper_id)
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


def _requested_pages(pages: list[int] | None, page_count: int | None) -> list[int]:
    requested = [1] if pages is None else pages
    if not isinstance(requested, list) or not 1 <= len(requested) <= 8:
        raise ValueError("pages must contain 1 to 8 page numbers")
    unique: list[int] = []
    for page in requested:
        if type(page) is not int or page < 1 or (page_count is not None and page > page_count):
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
        _requested_pages(pages, None)
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
    retrieval_service.validate_paper_id(paper_id)
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
    retrieval_service.validate_paper_id(paper_id)
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


def retrieve_paper_context(
    paper_id: str = "",
    query: str = "",
    top_k: int | None = None,
    parser: str = "",
) -> dict[str, object]:
    """Retrieve local context from cached parser sources without reparsing a PDF."""
    retrieval_service.validate_paper_id(paper_id)
    top_k = retrieval_service.validate_top_k(top_k)
    if not isinstance(query, str) or not 1 <= len(query.strip()) <= 2000:
        raise retrieval_service.ToolInputError("query must contain 1 to 2000 characters")
    parser_name = _parser_name(parser)
    sources = []
    for name in ((parser_name,) if parser_name else PARSER_NAMES):
        parsed = _load_cached_parse(paper_id, name)
        if parsed is not None:
            source = retrieval_service.retrieve_cached_paper_chunks(paper_id, name, parsed, query.strip(), top_k)
            source["warnings"] = list(getattr(parsed, "parser_warnings", []) or [])
            sources.append(source)
    return {
        "paper_id": paper_id,
        "query": query.strip(),
        "success": bool(sources),
        "chunks": [chunk for source in sources for chunk in source["chunks"]],
        "sources": sources,
        "warning": "No cached parser result; call a parser Tool first." if not sources else "",
    }


def read_paper_chunk(paper_id: str = "", parser: str = "", chunk_id: str = "", cache_version: str = "", offset: int = 0, max_chars: int | None = None) -> dict[str, object]:
    """Read a versioned local chunk, without parsing, writing or following paths."""
    retrieval_service.validate_paper_id(paper_id)
    parser_name = _parser_name(parser)
    if parser_name is None:
        raise retrieval_service.ToolInputError("An explicit parser is required for evidence readback")
    parsed = _load_cached_parse(paper_id, parser_name)
    if parsed is None:
        raise retrieval_service.ToolInputError("evidence_cache_missing: no valid existing parser cache; no parse was attempted")
    result = retrieval_service.read_cached_paper_chunk(paper_id, parser_name, parsed, chunk_id, cache_version, offset=offset, max_chars=max_chars)
    warnings = list(getattr(parsed, "parser_warnings", []) or [])
    result.update(warnings=[str(warning)[:160] for warning in warnings[:3]], warnings_omitted_count=max(0, len(warnings) - 3), warnings_truncated=any(len(str(warning)) > 160 for warning in warnings[:3]))
    # The read Tool itself is bounded, including escaped JSON and metadata.
    from app.services.tool_result_service import project_tool_result
    return json.loads(project_tool_result("read_paper_chunk", json.dumps(result, ensure_ascii=False)))


def _history_payload(result: ToolExecutionResult) -> dict[str, Any]:
    try:
        value = json.loads(result.content)
    except (TypeError, json.JSONDecodeError):
        value = {}
    return value if isinstance(value, dict) else {}


def _paper_history_failure(result: ToolExecutionResult, payload: dict[str, Any]) -> dict[str, Any]:
    return {"tool": result.tool_name, "status": "error", "paper_id": payload.get("paper_id", ""), "parser": payload.get("parser", ""), "summary": str(payload.get("error") or payload.get("warning") or result.error or "Tool execution failed.")[:300]}


def project_paper_history(_args: dict[str, Any], result: ToolExecutionResult) -> dict[str, Any]:
    payload = _history_payload(result)
    if not result.ok or payload.get("success") is False:
        return _paper_history_failure(result, payload)
    name = result.tool_name
    if name.startswith("parse_pdf_with_"):
        return {"tool": name, "status": "success", "paper_id": payload.get("paper_id", ""), "parser": payload.get("parser", ""), "cache_created": payload.get("cached") is False}
    if name == "get_paper_info":
        return {"tool": name, "status": "success", "paper_id": payload.get("paper_id", ""), "title": payload.get("title", "")[:300], "parsed": True, "parser": payload.get("parser", ""), "cache_status": "cached" if payload.get("cached_parsers") else "missing"}
    if name == "extract_sections":
        section_lengths = payload.get("section_lengths")
        return {"tool": name, "status": "success", "paper_id": payload.get("paper_id", ""), "parser": payload.get("parser", ""), "sections": list(section_lengths)[:20] if isinstance(section_lengths, dict) else [], "section_count": len(section_lengths) if isinstance(section_lengths, dict) else 0}
    chunks = payload.get("chunks") if isinstance(payload.get("chunks"), list) else []
    sections = list(dict.fromkeys(str(chunk.get("section")) for chunk in chunks if isinstance(chunk, dict) and chunk.get("section")))[:20]
    return {"tool": name, "status": "success", "paper_id": payload.get("paper_id", ""), "query": str(payload.get("query", ""))[:300], "chunk_count": len(chunks), "sections": sections}


PAPER_TOOL_SPECS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "parse_pdf_with_grobid",
            "description": "Run a new GROBID parse of an uploaded academic PDF for title, authors, abstract, section hierarchy, and references; this may create or update parser cache. Missing title or authors does not mean failure; inspect missing_fields and call parse_pdf_with_pymupdf if needed. Do not use this Tool merely to determine whether the paper was previously parsed, cached, processed, or analyzed; inspect existing paper/cache state first.",
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
            "description": "Run a new PyMuPDF parse and read selected raw PDF pages; this may create or update parser cache. Use it for first-page metadata, page-specific questions, unusual layouts, or to supplement an incomplete GROBID result. Omit pages to read page 1. Do not use this Tool merely to determine whether the paper was previously parsed, cached, processed, or analyzed; inspect existing paper/cache state first.",
            "parameters": {
                "type": "object",
                "properties": {
                    "paper_id": {"type": "string"},
                    "pages": {"type": "array", "items": {"type": "integer", "minimum": 1}, "minItems": 1, "maxItems": 8},
                },
                "required": ["paper_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_paper_info",
            "description": "Read existing cached parser metadata for paper_id without parsing a PDF or creating/updating parser cache. Use this Tool first when the user asks whether the current paper was previously parsed, cached, processed, or has existing paper information. If no cached parser result exists, report that current cache state; do not parse merely to answer that state question. This Tool reports parser-cache evidence, not whether a full paper analysis was completed.",
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
            "description": "Retrieve actual evidence from existing parser cache only. Each shown chunk has paper_id/parser/cache_version/chunk_id for read_paper_chunk. Check omitted/coverage: omitted text was not read. No reparsing. If no result is cached, call a parser Tool first.",
            "parameters": {
                "type": "object",
                "properties": {
                    "paper_id": {"type": "string"},
                    "query": {"type": "string", "minLength": 1, "maxLength": 2000},
                    "top_k": {"type": "integer", "minimum": 1, "maximum": retrieval_service.MAX_TOP_K},
                    "parser": {"type": "string", "enum": list(PARSER_NAMES)},
                },
                "required": ["paper_id", "query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_paper_chunk",
            "description": "Read a specified versioned evidence chunk from local parser cache. Copy paper_id, parser, cache_version and chunk_id exactly from evidence_refs or retrieved chunks. Use offset and next_offset to read further; max_chars is bounded by TOOL_READ_MAX_CHARS (default 2000). Only current/active papers are accessible. Stale or missing references fail without reparsing; retrieve again to obtain a new reference.",
            "parameters": {
                "type": "object",
                "properties": {
                    "paper_id": {"type": "string", "maxLength": 128},
                    "parser": {"type": "string", "enum": list(PARSER_NAMES)},
                    "chunk_id": {"type": "string", "pattern": "^c_[0-9a-f]{64}$"},
                    "cache_version": {"type": "string", "pattern": "^sha256:[0-9a-f]{64}$"},
                    "offset": {"type": "integer", "minimum": 0},
                    "max_chars": {"type": "integer", "minimum": 1, "maximum": 4000},
                },
                "required": ["paper_id", "parser", "chunk_id", "cache_version"],
            },
        },
    },
]


def register_paper_tools(registry: ToolRegistry, *, allowed_paper_ids: list[str] | None = None) -> None:
    """Register the Agent-selectable parser and cached-paper Tools."""
    registry.register(
        "parse_pdf_with_grobid",
        parse_pdf_with_grobid,
        requires=("paper_id",),
        produces=("parsed_pdf", "paper_metadata", "sections"),
        project_history_result=project_paper_history,
    )
    registry.register(
        "parse_pdf_with_pymupdf",
        parse_pdf_with_pymupdf,
        requires=("paper_id",),
        produces=("parsed_pdf", "paper_metadata", "sections"),
        project_history_result=project_paper_history,
    )
    registry.register(
        "get_paper_info",
        get_paper_info,
        requires=("paper_id",),
        produces=("parsed_pdf", "paper_metadata", "cache_status"),
        is_concurrency_safe=lambda _args: True,
        project_history_result=project_paper_history,
    )
    registry.register(
        "extract_sections",
        extract_sections,
        requires=("paper_id", "parsed_pdf"),
        produces=("sections",),
        is_concurrency_safe=lambda _args: True,
        project_history_result=project_paper_history,
    )
    registry.register(
        "retrieve_paper_context",
        retrieve_paper_context,
        requires=("paper_id", "parsed_pdf"),
        produces=("retrieved_paper_context",),
        project_history_result=project_paper_history,
    )
    def scoped_read(**arguments: Any) -> dict[str, object]:
        if allowed_paper_ids is not None and arguments.get("paper_id") not in allowed_paper_ids:
            raise retrieval_service.ToolInputError("evidence_access_denied: paper_id is outside current/active papers")
        return read_paper_chunk(**arguments)

    registry.register("read_paper_chunk", scoped_read, is_concurrency_safe=lambda _args: True, project_history_result=project_paper_history)
