"""Bound model-visible Tool data without changing raw execution results or caches."""

import json
from typing import Any

from app.core.config import get_tool_result_config
from app.services.retrieval_service import evidence_ref


_PAPER_TOOLS = {"parse_pdf_with_grobid", "parse_pdf_with_pymupdf", "get_paper_info", "extract_sections", "retrieve_paper_context", "get_multi_paper_context", "read_paper_chunk"}
_IDENTITY_FIELDS = {"paper_id", "parser", "cache_version", "chunk_id"}
_FACT_FIELDS = {"paper_id", "parser", "ok", "success", "status", "error", "warning", "warnings", "cached", "saved", "missing_fields"}


def serialize_model_result(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _compact(value: Any, text_chars: int, items: int, depth: int = 0) -> Any:
    """Trim fields before serialization; reference identifiers are indivisible."""
    if isinstance(value, str):
        return value[:text_chars]
    if depth >= 5 and isinstance(value, (dict, list)):
        return "[omitted nested value]"
    if isinstance(value, list):
        return [_compact(item, text_chars, items, depth + 1) for item in value[:items]]
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            result[key] = item if key in _IDENTITY_FIELDS else _compact(item, text_chars, items, depth + 1)
            if key not in _IDENTITY_FIELDS and isinstance(item, str) and len(item) > text_chars:
                result[f"{key}_truncated"] = True
            if isinstance(item, list) and len(item) > items:
                result[f"{key}_omitted_count"] = len(item) - items
        return result
    return value


def _unique_chunks(chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen = set()
    unique = []
    for chunk in chunks:
        key = serialize_model_result(evidence_ref(chunk) or {key: chunk.get(key) for key in ("paper_id", "parser", "chunk_id", "section", "text")})
        if key not in seen:
            seen.add(key)
            unique.append(chunk)
    return unique


def _paper_view(payload: dict[str, Any], text_chars: int, items: int) -> dict[str, Any]:
    chunks = _unique_chunks(payload.get("chunks", []))
    # sources carry parser/cache facts, never a second copy of top-level chunks.
    metadata = {key: value for key, value in payload.items() if key != "chunks"}
    if isinstance(metadata.get("sources"), list):
        metadata["sources"] = [{key: value for key, value in source.items() if key != "chunks"} for source in metadata["sources"]]
    view = _compact(metadata, min(text_chars, 500), max(items, 1))
    if isinstance(payload.get("text"), str):
        view["text"] = payload["text"][:text_chars]
        view["text_truncated"] = len(view["text"]) < len(payload["text"])
        if view["text_truncated"]:
            view["next_offset"] = payload.get("offset", 0) + len(view["text"])
    omitted = payload.get("omitted", False) or any(view.get(key) != value for key, value in metadata.items())
    if "chunks" in payload:
        view["chunks"] = [_compact(chunk, text_chars, 8) for chunk in chunks[:items]]
        for chunk in view["chunks"]:
            if chunk.get("text_truncated"):
                chunk["next_offset"] = chunk.get("offset", 0) + len(chunk["text"])
        view["omitted_chunks"] = len(chunks) - len(view["chunks"])
        view["omitted_chunk_range"] = [len(view["chunks"]), len(chunks)] if view["omitted_chunks"] else None
        view["coverage"] = "none" if not view["chunks"] else "partial" if view["omitted_chunks"] or any(chunk.get("text_truncated") for chunk in view["chunks"]) else "complete_returned_chunks"
        omitted = omitted or view["chunks"] != chunks
    view["status"] = payload.get("status", "error" if payload.get("success") is False else "success")
    view["omitted"] = omitted
    return view


def project_tool_result(tool_name: str, raw_content: str) -> str:
    """One serialized total cap per Tool result, shared fairly across all papers."""
    limit = get_tool_result_config().max_chars
    try:
        payload = json.loads(raw_content)
    except json.JSONDecodeError:
        if len(raw_content) <= limit:
            return raw_content
        view = {"status": "success", "text": raw_content[:limit - 300], "omitted": True, "readback": None, "note": "No replay source exists for this Tool output."}
        while len(serialize_model_result(view)) > limit:
            view["text"] = view["text"][:len(view["text"]) // 2]
        return serialize_model_result(view)
    if tool_name not in _PAPER_TOOLS and len(raw_content) <= limit:
        return raw_content
    if not isinstance(payload, dict):
        return serialize_model_result({"status": "success", "omitted": True, "readback": None, "note": "Oversized unstructured result; no replay source."})
    readback = (
        {"tool": "read_paper_chunk", "arguments": ["paper_id", "parser", "cache_version", "chunk_id"], "optional": ["offset", "max_chars"], "note": "Only shown text is read. For omitted chunks, retrieve_paper_context with paper_id/parser/query; no PDF reparse."}
        if tool_name in _PAPER_TOOLS else None
    )
    # Equal text/chunk allowances apply to every paper, never just the first one.
    for items in (8, 4, 2, 1, 0):
        for text_chars in ((2000, 1000, 500, 250) if items > 1 else (2000, 1000, 500, 250, 100, 32)):
            if tool_name in _PAPER_TOOLS and isinstance(payload.get("papers"), list):
                view = _compact({key: value for key, value in payload.items() if key != "papers"}, 300, 8)
                view["papers"] = [_paper_view(paper, text_chars, items) for paper in payload["papers"]]
            elif tool_name in _PAPER_TOOLS:
                view = _paper_view(payload, text_chars, items)
            else:
                view = _compact(payload, text_chars, max(items, 1))
                view["omitted"] = view != payload
                view["status"] = payload.get("status", "error" if payload.get("success") is False or payload.get("ok") is False else "success")
            view["readback"] = readback
            if tool_name in _PAPER_TOOLS and isinstance(view.get("papers"), list):
                view["omitted"] = any(paper.get("omitted") for paper in view["papers"]) or view.get("omitted", False)
            encoded = serialize_model_result(view)
            if len(encoded) <= limit:
                return encoded
    # Bounded fallback for metadata-heavy parser/memory results, not a spill store.
    def facts(value: dict[str, Any]) -> dict[str, Any]:
        return {**_compact({key: item for key, item in value.items() if key in _FACT_FIELDS}, 64, 1), "omitted": True, "coverage": "none"}
    view = facts(payload)
    if tool_name in _PAPER_TOOLS and isinstance(payload.get("papers"), list):
        view["papers"] = [facts(paper) for paper in payload["papers"]]
    view.update(readback=readback, note="Evidence omitted by total result budget; do not claim it was read.")
    encoded = serialize_model_result(view)
    if len(encoded) > limit:
        raise ValueError("Tool result identifiers/status cannot fit TOOL_RESULT_MAX_CHARS")
    return encoded


def model_evidence_refs(model_content: str) -> list[dict[str, Any]]:
    """History keeps exact refs plus the actually shown range, never full text."""
    try:
        payload = json.loads(model_content)
    except json.JSONDecodeError:
        return []
    if not isinstance(payload, dict):
        return []
    refs = []
    papers = payload["papers"] if isinstance(payload.get("papers"), list) else [payload]
    for paper in papers:
        if not isinstance(paper, dict):
            continue
        chunks = paper["chunks"] if isinstance(paper.get("chunks"), list) else [paper]
        for chunk in chunks:
            if not isinstance(chunk, dict):
                continue
            reference = evidence_ref(chunk)
            if reference is not None and isinstance(chunk.get("text"), str) and chunk["text"]:
                refs.append({**reference, "offset": chunk.get("offset", 0), "shown_chars": len(chunk["text"])})
    return refs


def shrink_readable_result(tool_name: str, content: str) -> str:
    """Overflow-only reduction of versioned evidence text, preserving facts and IDs."""
    if tool_name not in _PAPER_TOOLS:
        return content
    try:
        payload = json.loads(content)
    except (ValueError, TypeError):
        return content
    if not isinstance(payload, dict):
        return content
    changed = False
    papers = payload["papers"] if isinstance(payload.get("papers"), list) else [payload]
    for paper in papers:
        if not isinstance(paper, dict):
            continue
        chunks = paper["chunks"] if isinstance(paper.get("chunks"), list) else [paper]
        paper_changed = False
        for chunk in chunks:
            if not isinstance(chunk, dict) or evidence_ref(chunk) is None or not isinstance(chunk.get("text"), str) or len(chunk["text"]) <= 256:
                continue
            chunk["text"] = chunk["text"][:256]
            chunk.update(text_truncated=True, omitted=True, next_offset=chunk.get("offset", 0) + 256)
            if "shown_chars" in chunk:
                chunk["shown_chars"] = 256
            paper_changed = changed = True
        if paper_changed:
            paper.update(omitted=True, coverage="partial")
    if not changed:
        return content
    payload.update(omitted=True, recovery_note="仅展示片段前256字符；省略正文可用 read_paper_chunk 及完整版本引用、next_offset 回读，不代表全文已读取。")
    candidate = serialize_model_result(payload)
    return candidate if len(candidate) < len(content) else content
