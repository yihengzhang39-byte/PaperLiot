"""Local section-aware chunking and lexical retrieval for uploaded papers."""

from collections import Counter
from dataclasses import asdict, dataclass
import json
from pathlib import Path
import re
from typing import Iterable, Mapping

from app.core.config import PAPER_CHUNKS_DIR, get_rag_config


_TOKEN_PATTERN = re.compile(r"[a-z0-9_]+|[\u4e00-\u9fff]", re.IGNORECASE)


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
    if top_k < 1:
        raise ValueError("top_k must be at least 1")

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
    config = get_rag_config()
    effective_top_k = config.top_k if top_k is None else top_k
    if isinstance(effective_top_k, bool) or not isinstance(effective_top_k, int):
        raise ValueError("top_k must be an integer")
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
