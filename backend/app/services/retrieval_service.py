"""Local section-aware chunking and lexical retrieval for uploaded papers."""

from collections import Counter
from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Iterable, Mapping

from app.core.config import PAPER_CHUNKS_DIR, get_rag_config


_TOKEN_PATTERN = re.compile(r"[a-z0-9_]+|[\u4e00-\u9fff]", re.IGNORECASE)
MAX_TOP_K = 8
MAX_PAPERS = 8


class ToolInputError(ValueError):
    """Safe validation/evidence errors that may be shown to the model."""


def validate_paper_id(paper_id: str) -> None:
    if not isinstance(paper_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", paper_id):
        raise ToolInputError("paper_id must be a plain identifier of 1 to 128 letters, numbers, underscores or hyphens")


def validate_top_k(top_k: int | None) -> int:
    value = get_rag_config().top_k if top_k is None else top_k
    if type(value) is not int or not 1 <= value <= MAX_TOP_K:
        raise ToolInputError(f"top_k must be an integer between 1 and {MAX_TOP_K}")
    return value


def valid_evidence_ref(value: Any) -> bool:
    """Small fixed identifiers are safe to persist without display truncation."""
    return isinstance(value, dict) and all(isinstance(value.get(key), str) and re.fullmatch(pattern, value[key]) for key, pattern in (
        ("paper_id", r"[A-Za-z0-9_-]{1,128}"), ("parser", r"grobid|pymupdf"),
        ("cache_version", r"sha256:[0-9a-f]{64}"), ("chunk_id", r"c_[0-9a-f]{64}"),
    ))


def evidence_ref(chunk: dict[str, Any]) -> dict[str, str] | None:
    return {key: chunk[key] for key in ("paper_id", "parser", "cache_version", "chunk_id")} if valid_evidence_ref(chunk) else None


@dataclass(frozen=True)
class PaperChunk:
    """A source-traceable section fragment stored in the local index."""

    chunk_id: str
    paper_id: str
    section: str
    text: str
    start_char: int
    end_char: int


def _chunk_path(
    paper_id: str,
    storage_dir: Path | None = None,
    parser_name: str | None = None,
) -> Path:
    if (
        not paper_id.strip()
        or paper_id in {".", ".."}
        or any(character in paper_id for character in "/\\")
        or Path(paper_id).name != paper_id
    ):
        raise ValueError("paper_id must be a plain non-empty identifier")
    if parser_name is not None and not re.fullmatch(r"[a-z0-9_]+", parser_name):
        raise ValueError("parser_name must contain only lowercase letters, numbers, or underscores")
    suffix = f"_{parser_name}" if parser_name else ""
    return (storage_dir or PAPER_CHUNKS_DIR) / f"{paper_id}{suffix}.json"


def _split_section(text: str, chunk_size: int, overlap: int) -> Iterable[tuple[int, int, str]]:
    """Split one section only, preserving a small character overlap."""
    start = 0
    text = text.strip()
    while start < len(text):
        end = min(start + chunk_size, len(text))
        if end < len(text):
            boundary = max(text.rfind("\n", start + chunk_size // 2, end), text.rfind(" ", start + chunk_size // 2, end))
            if boundary > start:
                end = boundary
        fragment = text[start:end].strip()
        if fragment:
            yield start, end, fragment
        if end >= len(text):
            return
        start = max(end - overlap, start + 1)


def build_paper_chunks(
    paper_id: str,
    sections: Mapping[str, str],
    *,
    chunk_size: int | None = None,
    overlap: int | None = None,
) -> list[PaperChunk]:
    """Build source-aware chunks without joining text from different sections."""
    config = get_rag_config()
    chunk_size = chunk_size or config.chunk_size
    overlap = config.chunk_overlap if overlap is None else overlap
    if chunk_size < 1 or overlap < 0 or overlap >= chunk_size:
        raise ValueError("chunk_size must be positive and overlap must be smaller than chunk_size")

    chunks: list[PaperChunk] = []
    for section, text in sections.items():
        if not isinstance(text, str) or not text.strip():
            continue
        for index, (start, end, fragment) in enumerate(_split_section(text, chunk_size, overlap)):
            chunks.append(
                PaperChunk(
                    chunk_id=f"{paper_id}:{section}:{index}",
                    paper_id=paper_id,
                    section=section,
                    text=fragment,
                    start_char=start,
                    end_char=end,
                )
            )
    return chunks


def save_paper_chunks(
    paper_id: str,
    chunks: list[PaperChunk],
    *,
    storage_dir: Path | None = None,
    parser_name: str | None = None,
) -> Path:
    """Persist one paper's local index as UTF-8 JSON."""
    path = _chunk_path(paper_id, storage_dir, parser_name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([asdict(chunk) for chunk in chunks], ensure_ascii=False), encoding="utf-8")
    return path


def load_paper_chunks(
    paper_id: str,
    *,
    storage_dir: Path | None = None,
    parser_name: str | None = None,
) -> list[PaperChunk]:
    """Load a previously built local index."""
    path = _chunk_path(paper_id, storage_dir, parser_name)
    if not path.exists():
        raise FileNotFoundError(f"Paper chunk index not found: {paper_id}")
    try:
        values = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid paper chunk index: {paper_id}") from exc
    if not isinstance(values, list):
        raise ValueError(f"Invalid paper chunk index: {paper_id}")
    return [PaperChunk(**value) for value in values if isinstance(value, dict)]


def _tokens(text: str) -> Counter[str]:
    return Counter(_TOKEN_PATTERN.findall(text.lower()))


def retrieve_chunks(chunks: list[PaperChunk], query: str, top_k: int) -> list[dict[str, object]]:
    """Rank chunks with a dependency-free token-overlap score."""
    query_tokens = _tokens(query)
    if not query_tokens:
        raise ValueError("query cannot be empty")
    top_k = validate_top_k(top_k)

    ranked: list[tuple[float, PaperChunk]] = []
    for chunk in chunks:
        chunk_tokens = _tokens(chunk.text)
        overlap = sum(min(count, chunk_tokens[token]) for token, count in query_tokens.items())
        if overlap:
            score = overlap / sum(query_tokens.values())
            if query.lower() in chunk.text.lower():
                score += 1.0
            ranked.append((score, chunk))
    ranked.sort(key=lambda item: (-item[0], item[1].chunk_id))
    return [
        {
            "chunk_id": chunk.chunk_id,
            "paper_id": chunk.paper_id,
            "section": chunk.section,
            "text": chunk.text,
            "score": round(score, 4),
        }
        for score, chunk in ranked[:top_k]
    ]


def retrieve_paper_chunks(
    paper_id: str,
    query: str,
    *,
    top_k: int | None = None,
    storage_dir: Path | None = None,
    parser_name: str | None = None,
) -> dict[str, object]:
    """Retrieve bounded relevant context from a persisted paper index."""
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query cannot be empty")
    effective_top_k = validate_top_k(top_k)
    return {
        "paper_id": paper_id,
        "parser": parser_name or "",
        "query": query.strip(),
        "chunks": retrieve_chunks(
            load_paper_chunks(paper_id, storage_dir=storage_dir, parser_name=parser_name),
            query.strip(),
            effective_top_k,
        ),
    }


def cached_paper_chunks(paper_id: str, parser: str, parsed: Any) -> tuple[str, list[PaperChunk]]:
    """Derive versioned evidence from the existing parse; never write a second copy."""
    from app.agents.nodes.section_extract_node import extract_sections_by_rules_with_meta

    validate_paper_id(paper_id)
    if parser not in {"grobid", "pymupdf"}:
        raise ValueError("parser must be grobid or pymupdf")
    raw_text = str(getattr(parsed, "raw_text", "") or "")
    sections = {}
    for index, section in enumerate(getattr(parsed, "sections", []) or []):
        title = str(getattr(section, "title", "") or "document")
        # Keep repeated section headings distinct, with a stable source position.
        sections[f"{index + 1}: {title}"] = str(getattr(section, "text", "") or "")
    if not any(text.strip() for text in sections.values()):
        sections, _ = extract_sections_by_rules_with_meta(raw_text)
        sections = {name: text for name, text in sections.items() if text} or {"document": raw_text}
    config = get_rag_config()
    identity = json.dumps({"format": 1, "paper_id": paper_id, "parser": parser, "raw_text": raw_text, "sections": sections, "chunk_size": config.chunk_size, "overlap": config.chunk_overlap}, ensure_ascii=False, sort_keys=True)
    version = "sha256:" + hashlib.sha256(identity.encode()).hexdigest()
    # ponytail: rebuild in memory from the canonical cache; add a version-keyed
    # offset index if profiling shows this scan matters, never duplicate full text.
    chunks = []
    for chunk in build_paper_chunks(paper_id, sections):
        identity = json.dumps([chunk.section, chunk.start_char, chunk.end_char, chunk.text], ensure_ascii=False)
        chunks.append(PaperChunk("c_" + hashlib.sha256(identity.encode()).hexdigest(), paper_id, chunk.section, chunk.text, chunk.start_char, chunk.end_char))
    return version, chunks


def retrieve_cached_paper_chunks(paper_id: str, parser: str, parsed: Any, query: str, top_k: int | None = None) -> dict[str, object]:
    top_k = validate_top_k(top_k)
    version, chunks = cached_paper_chunks(paper_id, parser, parsed)
    ranked = retrieve_chunks(chunks, query, top_k)
    positions = {chunk.chunk_id: chunk for chunk in chunks}
    for item in ranked:
        chunk = positions[item["chunk_id"]]
        item.update(parser=parser, cache_version=version, start_char=chunk.start_char, end_char=chunk.end_char)
    return {"paper_id": paper_id, "parser": parser, "cache_version": version, "query": query, "success": True, "chunks": ranked}


def read_cached_paper_chunk(paper_id: str, parser: str, parsed: Any, chunk_id: str, cache_version: str, *, offset: int = 0, max_chars: int | None = None) -> dict[str, object]:
    from app.core.config import get_tool_result_config

    reference = {"paper_id": paper_id, "parser": parser, "chunk_id": chunk_id, "cache_version": cache_version}
    if not valid_evidence_ref(reference):
        raise ToolInputError("Invalid evidence reference; retrieve a new versioned reference first")
    limit = get_tool_result_config().read_chars
    max_chars = limit if max_chars is None else max_chars
    if type(max_chars) is not int or not 1 <= max_chars <= limit:
        raise ToolInputError(f"max_chars must be an integer between 1 and {limit}")
    if type(offset) is not int or offset < 0:
        raise ToolInputError("offset must be a non-negative integer")
    version, chunks = cached_paper_chunks(paper_id, parser, parsed)
    if version != cache_version:
        raise ToolInputError("stale_evidence_reference: parser cache content or chunking configuration changed; retrieve again")
    chunk = next((item for item in chunks if item.chunk_id == chunk_id), None)
    if chunk is None:
        raise ToolInputError("evidence_not_found: chunk does not exist in the specified cache version")
    if offset >= len(chunk.text):
        raise ToolInputError("offset is outside the referenced chunk")
    end = min(offset + max_chars, len(chunk.text))
    return {**reference, "success": True, "section": chunk.section, "start_char": chunk.start_char, "end_char": chunk.end_char, "text": chunk.text[offset:end], "offset": offset, "next_offset": end if end < len(chunk.text) else None, "total_chars": len(chunk.text), "omitted": end < len(chunk.text) or offset > 0}
