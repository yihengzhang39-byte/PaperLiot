"""Paper metadata extraction node with tool-calling enrichment."""

from concurrent.futures import ThreadPoolExecutor
from difflib import SequenceMatcher
import json
import re
import time
from typing import Any

import requests

from app.agents.paper_state import PaperState
from app.core.config import (
    get_llm_config,
    get_paper_lookup_config,
    is_paper_info_profile_enabled,
)
from app.tools.paper_lookup_tools import (
    search_arxiv_paper,
    search_crossref_paper,
    search_openalex_paper,
)
from app.services.llm_service import call_llm_json


UNCLEAR_VALUES = {"", "未明确提及", "未知", "unknown", "Unknown", "N/A", "无", "暂无", "待识别论文标题"}
DEFAULT_TEXT = "未明确提及"
CONFIDENCE_THRESHOLD = 0.75
ZH_TITLE_HIGH_CONFIDENCE = 0.75
ZH_TITLE_LLM_MIN_CONFIDENCE = 0.45
MAX_TOOL_ROUNDS = 2 # 最多 2 次 LLM 调用：工具决策 + 最终汇总


def _duration_sec(start: float) -> float:
    """Return elapsed seconds rounded for profile output."""
    return round(time.perf_counter() - start, 4)


def _new_profile(enabled: bool) -> dict[str, object]:
    """Create a paper_info_node profile record."""
    if not enabled:
        return {}
    return {
        "enabled": True,
        "paper_info_node_total_sec": 0.0,
        "parsed_info_extract_sec": 0.0,
        "missing_fields_check_sec": 0.0,
        "tool_agent_total_sec": 0.0,
        "bind_tools_sec": 0.0,
        "bind_tools_note": "Current implementation uses manual OpenAI-compatible tool schemas; model.bind_tools is not called.",
        "llm_call_count": 0,
        "llm_total_sec": 0.0,
        "llm_rounds": [],
        "tool_execution_total_sec": 0.0,
        "tool_calls": [],
        "final_json_parse_sec": 0.0,
        "field_merge_sec": 0.0,
        "debug_build_sec": 0.0,
        "raw_text_length": 0,
        "raw_text_preview_length": 0,
        "missing_fields": [],
        "final_missing_fields": [],
        "need_web_search": False,
        "tool_schema_build_sec": 0.0,
        "tool_execution_mode": "parallel",
        "tool_parallel_batch_count": 0,
        "tool_parallel_batches": [],
    }


def _summarize_tool_args(args: dict[str, object]) -> dict[str, object]:
    """Summarize tool arguments without logging raw query text."""
    return {
        key: {
            "present": bool(value),
            "length": len(str(value or "")),
        }
        for key, value in args.items()
    }


def _contains_chinese(text: str) -> bool:
    """Return whether text contains Chinese characters."""
    return bool(re.search(r"[\u4e00-\u9fff]", text or ""))


def _is_clear_value(value: object, field_name: str | None = None) -> bool:
    """Return whether a parser/tool value is explicit enough to trust."""
    if value is None:
        return False
    if isinstance(value, list):
        return bool(value) and any(_is_clear_value(item) for item in value)
    if not isinstance(value, str):
        return True

    cleaned = value.strip()
    if cleaned in UNCLEAR_VALUES:
        return False
    if cleaned.lower() in {"unknown", "n/a", "none", "null"}:
        return False
    if field_name == "abstract" and len(cleaned) < 20:
        return False
    return True


def _normalize_authors(value: object) -> list[str]:
    """Normalize authors to a list of strings."""
    if isinstance(value, list):
        return [str(author).strip() for author in value if str(author).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _get_parsed_paper_info(state: PaperState) -> dict[str, object]:
    """Read structured metadata already extracted by the PDF parser."""
    parsed_paper = state.get("parsed_paper", {}) or {}
    parser_meta = parsed_paper.get("parser_meta", {}) or {}
    return {
        "title": parsed_paper.get("title", ""),
        "authors": parsed_paper.get("authors", []),
        "abstract": parsed_paper.get("abstract", ""),
        "year": parser_meta.get("year", ""),
        "venue": parser_meta.get("venue", ""),
        "doi": parser_meta.get("doi", ""),
        "arxiv_id": parser_meta.get("arxiv_id", ""),
        "url": parser_meta.get("url", ""),
        "candidate_title": parser_meta.get("candidate_title", ""),
        "candidate_title_confidence": parser_meta.get("candidate_title_confidence", 0.0),
        "candidate_title_candidates": parser_meta.get("candidate_title_candidates", []),
        "candidate_title_source": parser_meta.get("candidate_title_source", ""),
        "candidate_authors": parser_meta.get("candidate_authors", []),
        "candidate_abstract": parser_meta.get("candidate_abstract", ""),
        "candidate_keywords": parser_meta.get("candidate_keywords", []),
        "first_page_text": parser_meta.get("first_page_text", ""),
        "zh_metadata_extract_debug": parser_meta.get("zh_metadata_extract_debug", {}),
    }


"""
    # 功能：找缺失的论文必填字段
"""
def _missing_final_fields(info: dict[str, object]) -> list[str]:
    """Return final metadata fields still missing."""
    fields = ["title", "authors", "year", "venue", "abstract"]
    return [
        field
        for field in fields
        if not _is_clear_value(info.get(field), field_name=field)
    ]


def _similarity(left: str, right: str) -> float:
    """Compute simple normalized similarity."""
    left = re.sub(r"\s+", " ", (left or "").lower()).strip()
    right = re.sub(r"\s+", " ", (right or "").lower()).strip()
    if not left or not right:
        return 0.0
    return SequenceMatcher(None, left, right).ratio()


def _extract_doi(text: str) -> str:
    """Extract a DOI from local text hints if present."""
    match = re.search(r"\b10\.\d{4,9}/[-._;()/:A-Z0-9]+\b", text or "", re.IGNORECASE)
    return match.group(0) if match else ""


def _extract_arxiv_id(text: str) -> str:
    """Extract an arXiv id from local text hints if present."""
    match = re.search(r"(?:arXiv[:\s]*)?(\d{4}\.\d{4,5}(?:v\d+)?)", text or "", re.IGNORECASE)
    if match:
        return match.group(1)
    old_match = re.search(r"(?:arXiv[:\s]*)?([a-z-]+(?:\.[A-Z]{2})?/\d{7})", text or "", re.IGNORECASE)
    return old_match.group(1) if old_match else ""


def _candidate_title_from_raw_text(raw_text: str) -> str:
    """Guess a possible title line without asking the LLM to extract metadata from raw text."""
    for line in (raw_text or "")[:2000].splitlines():
        cleaned = line.strip()
        if not cleaned or cleaned.startswith("--- Page"):
            continue
        if 8 <= len(cleaned) <= 220:
            return cleaned
    return ""


def _first_author(authors: object) -> str:
    """Return the first author from a parser author list."""
    normalized = _normalize_authors(authors)
    return normalized[0] if normalized else ""


def _tool_name(tool_obj: object) -> str:
    """Return a LangChain tool name without depending on tool internals."""
    return str(getattr(tool_obj, "name", "") or getattr(tool_obj, "__name__", ""))


def _select_tools_for_missing_fields(
    missing_fields: list[str],
    lookup_hints: dict[str, object],
    paper_language: str,
) -> list[object]:
    """Select the minimal lookup tools exposed to the first LLM decision round."""
    if not missing_fields:
        return []

    arxiv_id = str(lookup_hints.get("arxiv_id", "") or "").strip()
    has_arxiv_hint = bool(arxiv_id)
    missing = set(missing_fields)

    if missing == {"abstract"}:
        if has_arxiv_hint:
            return [search_arxiv_paper]
        return []

    tools: list[object] = [search_crossref_paper, search_openalex_paper]
    if has_arxiv_hint:
        tools.insert(0, search_arxiv_paper)
    return tools


def _is_tool_value_usable(
    field_name: str,
    value: object,
    confidence: float,
    parser_title: str = "",
    tool_title: str = "",
) -> bool:
    """Validate whether a tool-provided value is safe to use."""
    if confidence < CONFIDENCE_THRESHOLD:
        return False
    if not _is_clear_value(value, field_name=field_name):
        return False
    if field_name == "title" and len(str(value).strip()) < 8:
        return False
    if field_name == "authors" and not _normalize_authors(value):
        return False
    if field_name == "year" and not re.fullmatch(r"(19|20)\d{2}", str(value).strip()):
        return False
    if field_name == "venue" and not str(value).strip():
        return False
    if field_name == "abstract" and len(str(value).strip()) < 20:
        return False
    if parser_title and tool_title and _similarity(parser_title, tool_title) < 0.4:
        return False
    return True


def _normalize_match_text(text: object) -> str:
    """Normalize title/venue text for local candidate matching."""
    normalized = str(text or "").lower()
    normalized = normalized.replace("：", ":").replace("\n", " ")
    normalized = re.sub(r"\s+", " ", normalized)
    normalized = re.sub(r"\s*:\s*", ": ", normalized)
    normalized = normalized.replace("16x16", "16x16")
    normalized = re.sub(r"[\"'“”‘’`]+", "", normalized)
    normalized = re.sub(r"[\s\.,;:，。；：!\?？!]+$", "", normalized)
    return normalized.strip()


def _normalize_doi(value: object) -> str:
    """Normalize DOI strings for exact matching."""
    return str(value or "").strip().lower().replace("https://doi.org/", "").replace("http://doi.org/", "")


def _normalize_arxiv(value: object) -> str:
    """Extract and normalize an arXiv id from a string."""
    text = str(value or "").strip()
    return _extract_arxiv_id(text).lower()


def _author_match_score(parser_authors: list[str], candidate_authors: object) -> float:
    """Score author overlap, emphasizing first-author match."""
    parser_list = [_normalize_match_text(author) for author in parser_authors if str(author).strip()]
    candidate_list = [_normalize_match_text(author) for author in _normalize_authors(candidate_authors)]
    if not parser_list or not candidate_list:
        return 0.0

    first_score = 0.0
    if parser_list[0] == candidate_list[0] or _similarity(parser_list[0], candidate_list[0]) >= 0.82:
        first_score = 0.6

    parser_set = set(parser_list)
    candidate_set = set(candidate_list)
    exact_overlap = len(parser_set & candidate_set) / max(len(parser_set), 1)
    fuzzy_hits = 0
    for parser_author in parser_list:
        if any(_similarity(parser_author, candidate_author) >= 0.82 for candidate_author in candidate_list):
            fuzzy_hits += 1
    fuzzy_overlap = fuzzy_hits / max(len(parser_list), 1)
    return round(min(1.0, first_score + max(exact_overlap, fuzzy_overlap) * 0.4), 4)


def _venue_match_score(parser_venue: object, candidate_venue: object) -> float:
    """Score venue similarity when parser venue exists."""
    parser_text = _normalize_match_text(parser_venue)
    candidate_text = _normalize_match_text(candidate_venue)
    if not parser_text or not candidate_text:
        return 0.0
    if parser_text == candidate_text:
        return 1.0
    if parser_text in candidate_text or candidate_text in parser_text:
        return 0.85
    return round(_similarity(parser_text, candidate_text), 4)


def _candidate_rerank_reason(candidate: dict[str, object]) -> str:
    """Build a concise explanation for local candidate score."""
    reasons = []
    if candidate.get("doi_match"):
        reasons.append("doi_match")
    if candidate.get("arxiv_match"):
        reasons.append("arxiv_match")
    if float(candidate.get("title_similarity", 0.0) or 0.0) >= 0.9:
        reasons.append(f"title_similarity={candidate.get('title_similarity')}")
    if float(candidate.get("author_match_score", 0.0) or 0.0) >= 0.6:
        reasons.append(f"author_match_score={candidate.get('author_match_score')}")
    if candidate.get("year_match"):
        reasons.append("year_match")
    if float(candidate.get("venue_match_score", 0.0) or 0.0) >= 0.8:
        reasons.append(f"venue_match_score={candidate.get('venue_match_score')}")
    return ", ".join(reasons) if reasons else "weak local match"


def rerank_metadata_candidates(
    candidates: list[dict],
    parser_title: str,
    parser_authors: list[str],
    parser_year: str | None,
    parser_venue: str | None,
    doi: str | None = None,
    arxiv_id: str | None = None,
) -> list[dict]:
    """Rerank metadata candidates across providers with local matching signals."""
    provider_counts: dict[str, int] = {}
    normalized_parser_title = _normalize_match_text(parser_title)
    normalized_doi = _normalize_doi(doi)
    normalized_arxiv = _normalize_arxiv(arxiv_id)
    reranked: list[dict] = []

    for index, candidate in enumerate(candidates):
        if not isinstance(candidate, dict):
            continue
        source = str(candidate.get("source", "") or candidate.get("provider", "") or "unknown")
        provider_counts[source] = provider_counts.get(source, 0) + 1
        provider_rank = provider_counts[source]
        candidate_title = _normalize_match_text(candidate.get("title", ""))
        title_similarity = round(_similarity(normalized_parser_title, candidate_title), 4) if normalized_parser_title and candidate_title else 0.0
        author_score = _author_match_score(parser_authors, candidate.get("authors", []))
        year_match = bool(parser_year and str(candidate.get("year", "")).strip() == str(parser_year).strip())
        venue_score = _venue_match_score(parser_venue, candidate.get("venue", ""))
        candidate_doi = _normalize_doi(candidate.get("doi", ""))
        candidate_arxiv = _normalize_arxiv(" ".join([str(candidate.get("url", "")), str(candidate.get("doi", "")), str(candidate.get("title", ""))]))
        doi_match = bool(normalized_doi and candidate_doi and normalized_doi == candidate_doi)
        arxiv_match = bool(normalized_arxiv and candidate_arxiv and normalized_arxiv == candidate_arxiv)

        score = 0.0
        if doi_match:
            score = max(score, 0.98)
        if arxiv_match:
            score = max(score, 0.97)
        score = max(
            score,
            title_similarity * 0.62
            + author_score * 0.23
            + (0.08 if year_match else 0.0)
            + min(venue_score, 1.0) * 0.07,
        )
        if title_similarity >= 0.96 and author_score >= 0.5:
            score = max(score, 0.88)
        if title_similarity >= 0.98 and (candidate.get("year") or candidate.get("venue")):
            score = max(score, 0.82)

        enriched = {
            **candidate,
            "provider_rank": provider_rank,
            "provider_global_rank": index + 1,
            "local_match_score": round(min(1.0, score), 4),
            "title_similarity": title_similarity,
            "author_match_score": author_score,
            "year_match": year_match,
            "venue_match_score": venue_score,
            "doi_match": doi_match,
            "arxiv_match": arxiv_match,
        }
        enriched["rerank_reason"] = _candidate_rerank_reason(enriched)
        reranked.append(enriched)

    reranked.sort(
        key=lambda item: (
            float(item.get("local_match_score", 0.0) or 0.0),
            float(item.get("title_similarity", 0.0) or 0.0),
            float(item.get("author_match_score", 0.0) or 0.0),
            -int(item.get("provider_rank", 999) or 999),
        ),
        reverse=True,
    )
    for local_rank, candidate in enumerate(reranked, start=1):
        candidate["local_rank"] = local_rank
    return reranked


def _base_field_debug(
    field_name: str,
    final_value: object,
    source: str,
    parser_value: object = None,
    parser_meta_value: object = None,
    tool_value: object = None,
    default_value: object = DEFAULT_TEXT,
    reason: str = "",
) -> dict[str, object]:
    """Build debug record for one paper info field."""
    return {
        "final_value": final_value,
        "source": source,
        "parser_value": parser_value,
        "parser_meta_value": parser_meta_value,
        "tool_value": tool_value,
        "default_value": default_value,
        "used_parser": source == "parser",
        "used_parser_meta": source == "parser_meta",
        "used_tool": source == "tool",
        "used_default": source == "default",
        "parser_value_clear": _is_clear_value(parser_value, field_name),
        "parser_meta_value_clear": _is_clear_value(parser_meta_value, field_name),
        "tool_value_clear": _is_clear_value(tool_value, field_name),
        "reason": reason,
    }


def _resolve_initial_info(parsed_info: dict[str, object], paper_language: str) -> tuple[dict[str, object], dict[str, object]]:
    """Resolve parser/parser_meta/default fields before tool calling."""
    title = parsed_info.get("title")
    authors = parsed_info.get("authors")
    abstract = parsed_info.get("abstract")
    year = parsed_info.get("year")
    venue = parsed_info.get("venue")

    final: dict[str, object] = {}
    debug: dict[str, object] = {}

    if _is_clear_value(title, "title"):
        final["title"] = str(title)
        debug["title"] = _base_field_debug("title", title, "parser", parser_value=title, reason="parsed_paper.title 是明确值，因此使用 parser 结果")
    else:
        final["title"] = DEFAULT_TEXT
        debug["title"] = _base_field_debug("title", DEFAULT_TEXT, "default", parser_value=title, reason="parser 未提供明确 title，等待工具补充或使用默认值")

    if _is_clear_value(authors, "authors"):
        final["authors"] = _normalize_authors(authors)
        debug["authors"] = _base_field_debug("authors", final["authors"], "parser", parser_value=authors, default_value=[], reason="parsed_paper.authors 非空，因此使用 parser 结果")
    else:
        final["authors"] = []
        debug["authors"] = _base_field_debug("authors", [], "default", parser_value=authors, default_value=[], reason="parser 未提供明确 authors，等待工具补充或使用默认值")

    if _is_clear_value(abstract, "abstract"):
        final["abstract"] = str(abstract)
        reason = "parsed_paper.abstract 是明确值，因此使用 parser 结果；工具和 LLM 不允许覆盖 parser 摘要"
        if paper_language == "zh" and _contains_chinese(str(abstract)):
            reason = "中文论文 parser 摘要包含中文，因此不允许工具或 LLM 英文摘要覆盖"
        debug["abstract"] = _base_field_debug("abstract", final["abstract"], "parser", parser_value=abstract, reason=reason)
    else:
        final["abstract"] = DEFAULT_TEXT
        debug["abstract"] = _base_field_debug("abstract", DEFAULT_TEXT, "default", parser_value=abstract, reason="parser 未提供明确 abstract，等待工具补充或使用默认值")

    if _is_clear_value(year, "year"):
        final["year"] = str(year)
        debug["year"] = _base_field_debug("year", final["year"], "parser_meta", parser_meta_value=year, reason="parser_meta.year 是明确值，因此使用 parser_meta 结果")
    else:
        final["year"] = DEFAULT_TEXT
        debug["year"] = _base_field_debug("year", DEFAULT_TEXT, "default", parser_meta_value=year, reason="parser_meta 未提供明确 year，等待工具补充或使用默认值")

    if _is_clear_value(venue, "venue"):
        final["venue"] = str(venue)
        debug["venue"] = _base_field_debug("venue", final["venue"], "parser_meta", parser_meta_value=venue, reason="parser_meta.venue 是明确值，因此使用 parser_meta 结果")
    else:
        final["venue"] = DEFAULT_TEXT
        debug["venue"] = _base_field_debug("venue", DEFAULT_TEXT, "default", parser_meta_value=venue, reason="parser_meta 未提供明确 venue，等待工具补充或使用默认值")

    return final, debug


def _resolve_zh_initial_info(parsed_info: dict[str, object], paper_language: str) -> tuple[dict[str, object], dict[str, object]]:
    """Resolve Chinese paper metadata from parser and PyMuPDF candidate fields."""
    final, debug = _resolve_initial_info(parsed_info, paper_language)

    candidate_title = parsed_info.get("candidate_title", "")
    candidate_title_confidence = float(parsed_info.get("candidate_title_confidence", 0.0) or 0.0)
    if (
        not _is_clear_value(final.get("title"), "title")
        and _is_clear_value(candidate_title, "title")
        and candidate_title_confidence >= ZH_TITLE_HIGH_CONFIDENCE
    ):
        final["title"] = str(candidate_title)
        debug["title"] = _base_field_debug(
            "title",
            final["title"],
            "parser_meta",
            parser_value=parsed_info.get("title"),
            parser_meta_value=candidate_title,
            reason="中文论文 parsed_paper.title 缺失，PyMuPDF layout 标题候选置信度较高，因此使用 candidate_title",
        )
        debug["title"]["parser_meta_confidence"] = candidate_title_confidence
    elif not _is_clear_value(final.get("title"), "title"):
        debug["title"]["parser_meta_value"] = candidate_title
        debug["title"]["parser_meta_confidence"] = candidate_title_confidence
        debug["title"]["reason"] = "中文论文 parsed_paper.title 缺失，layout 候选置信度不足，暂不直接使用 candidate_title"

    candidate_authors = parsed_info.get("candidate_authors", [])
    if not _is_clear_value(final.get("authors"), "authors") and _is_clear_value(candidate_authors, "authors"):
        final["authors"] = _normalize_authors(candidate_authors)
        debug["authors"] = _base_field_debug(
            "authors",
            final["authors"],
            "parser_meta",
            parser_value=parsed_info.get("authors"),
            parser_meta_value=candidate_authors,
            default_value=[],
            reason="中文论文 parsed_paper.authors 缺失，因此使用 PyMuPDF 首页规则抽取的 candidate_authors",
        )

    candidate_abstract = parsed_info.get("candidate_abstract", "")
    if not _is_clear_value(final.get("abstract"), "abstract") and _is_clear_value(candidate_abstract, "abstract"):
        final["abstract"] = str(candidate_abstract)
        debug["abstract"] = _base_field_debug(
            "abstract",
            final["abstract"],
            "parser_meta",
            parser_value=parsed_info.get("abstract"),
            parser_meta_value=candidate_abstract,
            reason="中文论文 parsed_paper.abstract 缺失，因此使用 PyMuPDF 摘要规则抽取的 candidate_abstract",
        )

    return final, debug


def _missing_zh_core_fields(info: dict[str, object]) -> list[str]:
    """Return missing Chinese metadata fields that can be corrected from first page."""
    return [
        field
        for field in ["authors", "abstract"]
        if not _is_clear_value(info.get(field), field_name=field)
    ]


def _select_zh_title_with_llm(
    candidate_title_candidates: object,
    first_page_text: str,
) -> tuple[dict[str, object], dict[str, object]]:
    """Ask a lightweight LLM to choose one Chinese title from layout candidates only."""
    candidates = candidate_title_candidates if isinstance(candidate_title_candidates, list) else []
    candidates = [item for item in candidates[:5] if isinstance(item, dict) and str(item.get("text", "")).strip()]
    debug: dict[str, object] = {
        "called": False,
        "skipped": False,
        "reason": "",
        "candidate_count": len(candidates),
    }
    if not candidates:
        debug.update({"skipped": True, "reason": "No layout title candidates"})
        return {}, debug
    if get_llm_config().provider == "mock":
        debug.update({"skipped": True, "reason": "LLM_PROVIDER=mock; skip title candidate selection"})
        return {}, debug

    slim_candidates = [
        {
            "index": index,
            "text": str(candidate.get("text", "")),
            "score": float(candidate.get("score", 0.0) or 0.0),
            "font_size": candidate.get("font_size", 0.0),
            "bbox": candidate.get("bbox", []),
            "center_offset": candidate.get("center_offset", 0.0),
            "reasons": candidate.get("reasons", []),
        }
        for index, candidate in enumerate(candidates)
    ]
    system_prompt = (
        "你是中文论文标题候选判别助手。你只能从给定候选标题中选择一个，"
        "不允许改写、扩写、翻译或编造标题。如果候选都不像论文标题，返回空字符串。只返回 JSON。"
    )
    user_prompt = f"""请从候选列表中选择最像中文论文标题的一项。输出严格 JSON：

{{
  "title": "",
  "selected_index": null,
  "confidence": 0.0,
  "reason": ""
}}

规则：
- 只能返回候选列表中原封不动的 text
- 不允许从首页文本自由抽取新标题
- 不允许改写、扩写、翻译或编造
- 候选不可靠时 title 返回空字符串，selected_index 返回 null

候选标题：
{json.dumps(slim_candidates, ensure_ascii=False)}

首页文本辅助信息，最多 1500 字：
{first_page_text[:1500]}
"""
    try:
        data = call_llm_json(system_prompt, user_prompt)
    except Exception as exc:
        debug.update({"called": True, "reason": f"title candidate LLM failed: {exc}"})
        return {}, debug

    title = str(data.get("title", "") or "").strip()
    selected_index = data.get("selected_index")
    try:
        selected_index_int = int(selected_index) if selected_index is not None else None
    except (TypeError, ValueError):
        selected_index_int = None
    candidate_texts = {str(candidate.get("text", "")) for candidate in candidates}
    if selected_index_int is not None and 0 <= selected_index_int < len(candidates):
        selected_text = str(candidates[selected_index_int].get("text", ""))
        if title and title != selected_text:
            title = ""
            debug["reason"] = "LLM returned a title different from selected candidate text"
    elif title and title not in candidate_texts:
        title = ""
        debug["reason"] = "LLM returned a title outside candidate list"

    result = {
        "title": title,
        "selected_index": selected_index_int,
        "confidence": float(data.get("confidence", 0.0) or 0.0),
        "reason": str(data.get("reason", "") or debug.get("reason", "")),
    }
    debug.update({"called": True, "reason": result["reason"]})
    return result, debug


def _apply_zh_title_selection_result(
    final_info: dict[str, object],
    field_debug: dict[str, object],
    parsed_info: dict[str, object],
    title_result: dict[str, object],
) -> tuple[dict[str, object], dict[str, object]]:
    """Fill title only when the LLM selected one of the layout candidates."""
    if _is_clear_value(final_info.get("title"), "title"):
        return final_info, field_debug

    title = str(title_result.get("title", "") or "").strip()
    confidence = float(title_result.get("confidence", 0.0) or 0.0)
    if not _is_clear_value(title, "title") or confidence < ZH_TITLE_LLM_MIN_CONFIDENCE:
        field_debug["title"] = {
            **field_debug.get("title", {}),
            "first_page_llm_value": title,
            "first_page_llm_confidence": confidence,
            "reason": "layout 候选未达到直接使用阈值，且 LLM 未选出足够可靠的候选标题，因此保持默认值",
        }
        return final_info, field_debug

    final_info["title"] = title
    field_debug["title"] = {
        **field_debug.get("title", {}),
        "final_value": title,
        "source": "first_page_llm",
        "parser_value": parsed_info.get("title", ""),
        "parser_meta_value": parsed_info.get("candidate_title", ""),
        "parser_meta_confidence": float(parsed_info.get("candidate_title_confidence", 0.0) or 0.0),
        "first_page_llm_value": title,
        "first_page_llm_confidence": confidence,
        "used_parser": False,
        "used_parser_meta": False,
        "used_tool": False,
        "used_default": False,
        "used_first_page_llm": True,
        "reason": "layout 标题候选置信度不足以直接使用，因此由首页轻量 LLM 仅从候选列表中选择该标题",
    }
    return final_info, field_debug


def _run_zh_first_page_llm(
    first_page_text: str,
    missing_fields: list[str],
) -> tuple[dict[str, object], dict[str, object]]:
    """Use a lightweight LLM call to extract Chinese authors/abstract from first page only."""
    debug: dict[str, object] = {
        "called": False,
        "skipped": False,
        "reason": "",
        "missing_fields": missing_fields,
    }
    if not missing_fields:
        debug.update({"skipped": True, "reason": "No authors/abstract fields missing"})
        return {}, debug
    if not first_page_text.strip():
        debug.update({"skipped": True, "reason": "first_page_text is empty"})
        return {}, debug
    if get_llm_config().provider == "mock":
        debug.update({"skipped": True, "reason": "LLM_PROVIDER=mock; skip first-page LLM"})
        return {}, debug

    system_prompt = (
        "你是中文论文首页元数据抽取助手。只从给定首页文本抽取 authors/abstract。"
        "不要抽取或编造 title，不要总结论文内容，不要翻译，不要抽取 year/venue。只返回 JSON。"
    )
    user_prompt = f"""请只从下面中文论文首页文本中抽取缺失字段，输出严格 JSON：

{{
  "authors": [],
  "abstract": "",
  "reason": ""
}}

需要补充的字段：{missing_fields}

规则：
- 只抽取 authors/abstract，不要抽取 title
- 作者输出字符串数组
- 不确定就返回空字符串或空数组
- 不要翻译，不要总结，不要编造

首页文本：
{first_page_text[:3000]}
"""
    try:
        data = call_llm_json(system_prompt, user_prompt)
    except Exception as exc:
        debug.update({"called": True, "reason": f"first-page LLM failed: {exc}"})
        return {}, debug

    result = {
        "authors": _normalize_authors(data.get("authors", [])),
        "abstract": str(data.get("abstract", "") or ""),
        "reason": str(data.get("reason", "") or ""),
    }
    debug.update({"called": True, "reason": result["reason"]})
    return result, debug


def _apply_first_page_llm_result(
    final_info: dict[str, object],
    field_debug: dict[str, object],
    llm_result: dict[str, object],
) -> tuple[dict[str, object], dict[str, object]]:
    """Fill missing Chinese authors/abstract from first-page LLM result."""
    for field, default_value in [("authors", []), ("abstract", DEFAULT_TEXT)]:
        if _is_clear_value(final_info.get(field), field):
            continue
        llm_value = llm_result.get(field)
        if not _is_clear_value(llm_value, field):
            continue
        final_info[field] = _normalize_authors(llm_value) if field == "authors" else str(llm_value)
        field_debug[field] = {
            **field_debug.get(field, {}),
            "final_value": final_info[field],
            "source": "first_page_llm",
            "tool_value": "",
            "default_value": default_value,
            "used_tool": False,
            "used_default": False,
            "used_first_page_llm": True,
            "reason": "中文论文 parser/parser_meta 未提取到该字段，因此使用首页轻量 LLM 抽取结果",
        }
    return final_info, field_debug


def _looks_like_english_title(text: str) -> bool:
    """Return whether text looks like an English paper title."""
    cleaned = str(text or "").strip()
    if not cleaned or _contains_chinese(cleaned):
        return False
    letter_count = len(re.findall(r"[A-Za-z]", cleaned))
    return letter_count >= 8 and len(cleaned) >= 12


def _zh_external_lookup_decision(
    parsed_info: dict[str, object],
    final_info: dict[str, object],
    raw_text: str,
) -> tuple[bool, str]:
    """Decide whether a Chinese paper has strong enough hints for external lookup."""
    text_head = "\n".join(
        part
        for part in [
            str(parsed_info.get("doi", "") or ""),
            str(parsed_info.get("arxiv_id", "") or ""),
            str(parsed_info.get("url", "") or ""),
            str(final_info.get("title", "") or ""),
            raw_text[:3000],
        ]
        if part
    )
    if str(parsed_info.get("doi", "") or "").strip() or _extract_doi(text_head):
        return True, "Chinese paper has DOI"
    if str(parsed_info.get("arxiv_id", "") or "").strip() or _extract_arxiv_id(text_head):
        return True, "Chinese paper has arXiv ID"
    if _looks_like_english_title(str(final_info.get("title", "") or "")):
        return True, "Chinese paper has clear English title"
    if _is_clear_value(parsed_info.get("venue"), "venue") and _is_clear_value(parsed_info.get("year"), "year"):
        return True, "Chinese paper has parser_meta venue and year"
    return False, "Chinese paper without DOI/arXiv ID/English title; skip external metadata lookup"


def _build_zh_metadata_debug(
    parsed_info: dict[str, object],
    title_llm_result: dict[str, object],
    title_llm_debug: dict[str, object],
    llm_result: dict[str, object],
    llm_debug: dict[str, object],
    external_allowed: bool,
    external_reason: str,
    final_info: dict[str, object],
    field_debug: dict[str, object],
) -> dict[str, object]:
    """Build Chinese metadata debug details."""
    return {
        "enabled": True,
        "candidate_title": parsed_info.get("candidate_title", ""),
        "candidate_title_confidence": float(parsed_info.get("candidate_title_confidence", 0.0) or 0.0),
        "candidate_title_candidates": parsed_info.get("candidate_title_candidates", []),
        "candidate_title_source": parsed_info.get("candidate_title_source", ""),
        "used_title_llm_selection": bool(title_llm_debug.get("called") and title_llm_result),
        "title_llm_selection_result": title_llm_result,
        "title_llm_selection_debug": title_llm_debug,
        "final_title_source": field_debug.get("title", {}).get("source", "default"),
        "title_selection_reason": field_debug.get("title", {}).get("reason", ""),
        "candidate_authors": parsed_info.get("candidate_authors", []),
        "candidate_abstract_length": len(str(parsed_info.get("candidate_abstract", "") or "")),
        "candidate_keywords": parsed_info.get("candidate_keywords", []),
        "used_first_page_llm": bool(llm_debug.get("called") and llm_result),
        "first_page_llm_result": llm_result,
        "first_page_llm_debug": llm_debug,
        "external_lookup_allowed": external_allowed,
        "external_lookup_skip_reason": "" if external_allowed else external_reason,
        "reason": external_reason,
        "final_title": final_info.get("title", DEFAULT_TEXT),
        "rule_debug": parsed_info.get("zh_metadata_extract_debug", {}),
    }


def _tool_specs(selected_tools: list[object] | None = None) -> list[dict[str, object]]:
    """Return OpenAI-compatible tool schemas."""
    providers = set(get_paper_lookup_config().providers or ["arxiv", "crossref", "openalex"])
    selected_names = None if selected_tools is None else {_tool_name(tool_obj) for tool_obj in selected_tools}
    common_props = {
        "title": {"type": "string"},
        "first_author": {"type": "string"},
        "year": {"type": "string"},
        "doi": {"type": "string"},
    }
    specs: list[dict[str, object]] = []
    if "crossref" in providers and (selected_names is None or "search_crossref_paper" in selected_names):
        specs.append(
            {
                "type": "function",
                "function": {
                    "name": "search_crossref_paper",
                    "description": "Search Crossref for paper metadata candidates.",
                    "parameters": {"type": "object", "properties": common_props},
                },
            }
        )
    if "openalex" in providers and (selected_names is None or "search_openalex_paper" in selected_names):
        specs.append(
            {
                "type": "function",
                "function": {
                    "name": "search_openalex_paper",
                    "description": "Search OpenAlex for paper metadata candidates.",
                    "parameters": {"type": "object", "properties": common_props},
                },
            }
        )
    if "arxiv" in providers and (selected_names is None or "search_arxiv_paper" in selected_names):
        specs.append(
            {
                "type": "function",
                "function": {
                    "name": "search_arxiv_paper",
                    "description": "Search arXiv by title or arXiv id.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "title": {"type": "string"},
                            "arxiv_id": {"type": "string"},
                        },
                    },
                },
            },
        )
    return specs


def _execute_tool(name: str, args: dict[str, object]) -> dict[str, object]:
    """Execute one metadata lookup tool by name."""
    tool_map = {
        "search_crossref_paper": search_crossref_paper,
        "search_openalex_paper": search_openalex_paper,
        "search_arxiv_paper": search_arxiv_paper,
    }
    tool_obj = tool_map.get(name)
    if tool_obj is None:
        return {"provider": name, "query": "", "candidates": [], "error": f"Unknown tool: {name}"}
    result = tool_obj.invoke(args)
    return result if isinstance(result, dict) else {"provider": name, "query": "", "candidates": [], "error": str(result)}


def _execute_one_tool_call(tool_call: dict[str, object]) -> dict[str, object]:
    """Execute one tool call and return an ordered, profile-friendly result."""
    function = tool_call.get("function", {}) or {}
    name = str(function.get("name", ""))
    raw_arguments = function.get("arguments") or {}
    try:
        args = raw_arguments if isinstance(raw_arguments, dict) else json.loads(str(raw_arguments))
    except (TypeError, json.JSONDecodeError):
        args = {}

    start = time.perf_counter()
    error = ""
    try:
        result = _execute_tool(name, args)
    except Exception as exc:
        error = str(exc)
        result = {"provider": name, "query": "", "candidates": [], "error": error}
    duration = _duration_sec(start)
    if result.get("error") and not error:
        error = str(result.get("error", ""))

    return {
        "tool_call_id": tool_call.get("id", ""),
        "name": name,
        "args_summary": _summarize_tool_args(args),
        "result": result,
        "duration_sec": duration,
        "error": error,
    }


def _execute_tool_calls_concurrently(
    tool_calls: list[dict[str, object]],
    profile: dict[str, object] | None = None,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    """Execute one LLM round of tool calls concurrently while preserving order."""
    if not tool_calls:
        return [], []

    batch_index = 1
    if profile is not None:
        batch_index = int(profile.get("tool_parallel_batch_count", 0)) + 1
    batch_start = time.perf_counter()
    with ThreadPoolExecutor(max_workers=len(tool_calls)) as executor:
        tool_results = list(executor.map(_execute_one_tool_call, tool_calls))
    batch_duration = _duration_sec(batch_start)

    tool_messages = [
        {
            "role": "tool",
            "tool_call_id": tool_result.get("tool_call_id", ""),
            "name": tool_result.get("name", ""),
            "content": json.dumps(tool_result.get("result", {}), ensure_ascii=False),
        }
        for tool_result in tool_results
    ]

    if profile is not None:
        individual_sum = round(sum(float(item.get("duration_sec", 0.0)) for item in tool_results), 4)
        profile["tool_execution_mode"] = "parallel"
        profile["tool_execution_total_sec"] = round(
            float(profile.get("tool_execution_total_sec", 0.0)) + batch_duration,
            4,
        )
        profile["tool_parallel_batch_count"] = batch_index
        profile.setdefault("tool_parallel_batches", []).append(
            {
                "batch_index": batch_index,
                "tool_count": len(tool_results),
                "tool_names": [str(item.get("name", "")) for item in tool_results],
                "duration_sec": batch_duration,
                "sum_individual_duration_sec": individual_sum,
            }
        )
        for tool_result in tool_results:
            result = tool_result.get("result", {}) if isinstance(tool_result.get("result"), dict) else {}
            profile.setdefault("tool_calls", []).append(
                {
                    "name": tool_result.get("name", ""),
                    "duration_sec": tool_result.get("duration_sec", 0.0),
                    "arg_summary": tool_result.get("args_summary", {}),
                    "candidate_count": len(result.get("candidates", []) or []),
                    "has_error": bool(tool_result.get("error") or result.get("error")),
                }
            )

    return tool_messages, tool_results


def _slim_tool_result_for_llm(result: dict[str, object], missing_fields: list[str]) -> dict[str, object]:
    """Trim tool output before sending it to the final summary LLM call."""
    slim_candidates = []
    include_abstract = "abstract" in set(missing_fields)
    for candidate in (result.get("candidates", []) or [])[:3]:
        if not isinstance(candidate, dict):
            continue
        slim_candidate = {
            "title": candidate.get("title", ""),
            "authors": candidate.get("authors", []),
            "year": candidate.get("year", ""),
            "venue": candidate.get("venue", ""),
            "doi": candidate.get("doi", ""),
            "url": candidate.get("url", ""),
            "source": candidate.get("source", ""),
            "confidence": candidate.get("confidence", 0.0),
            "provider_rank": candidate.get("provider_rank", ""),
            "local_rank": candidate.get("local_rank", ""),
            "local_match_score": candidate.get("local_match_score", 0.0),
            "title_similarity": candidate.get("title_similarity", 0.0),
            "author_match_score": candidate.get("author_match_score", 0.0),
            "year_match": candidate.get("year_match", False),
            "venue_match_score": candidate.get("venue_match_score", 0.0),
            "doi_match": candidate.get("doi_match", False),
            "arxiv_match": candidate.get("arxiv_match", False),
            "rerank_reason": candidate.get("rerank_reason", ""),
        }
        if include_abstract:
            slim_candidate["abstract"] = str(candidate.get("abstract", "") or "")[:500]
        slim_candidates.append(slim_candidate)

    return {
        "provider": result.get("provider", ""),
        "error": result.get("error", ""),
        "candidates": slim_candidates,
    }


def _parse_agent_json(content: str) -> dict[str, object]:
    """Parse final JSON returned by the tool-calling agent."""
    cleaned = content.strip()
    fence_match = re.search(r"```(?:json)?\s*(.*?)```", cleaned, re.DOTALL | re.IGNORECASE)
    if fence_match:
        cleaned = fence_match.group(1).strip()
    if not cleaned.startswith("{"):
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start != -1 and end != -1 and end > start:
            cleaned = cleaned[start : end + 1]
    parsed = json.loads(cleaned)
    return parsed if isinstance(parsed, dict) else {}


def _candidate_debug_view(candidate: dict[str, object] | None) -> dict[str, object]:
    """Return a compact candidate view for debug/API output."""
    if not candidate:
        return {}
    return {
        "title": candidate.get("title", ""),
        "authors": candidate.get("authors", []),
        "year": candidate.get("year", ""),
        "venue": candidate.get("venue", ""),
        "doi": candidate.get("doi", ""),
        "url": candidate.get("url", ""),
        "source": candidate.get("source", ""),
        "provider_rank": candidate.get("provider_rank", ""),
        "local_rank": candidate.get("local_rank", ""),
        "local_match_score": candidate.get("local_match_score", 0.0),
        "title_similarity": candidate.get("title_similarity", 0.0),
        "author_match_score": candidate.get("author_match_score", 0.0),
        "year_match": candidate.get("year_match", False),
        "venue_match_score": candidate.get("venue_match_score", 0.0),
        "doi_match": candidate.get("doi_match", False),
        "arxiv_match": candidate.get("arxiv_match", False),
        "rerank_reason": candidate.get("rerank_reason", ""),
    }


def _augment_agent_json_with_selected_candidate(
    agent_json: dict[str, object],
    selected_candidate: dict[str, object] | None,
    missing_fields: list[str],
) -> dict[str, object]:
    """Use a high-confidence reranked candidate as conservative fallback for missing fields."""
    if not selected_candidate:
        return agent_json
    score = float(selected_candidate.get("local_match_score", 0.0) or 0.0)
    if score < CONFIDENCE_THRESHOLD:
        return agent_json

    augmented = dict(agent_json or {})
    confidence = augmented.get("confidence", {}) if isinstance(augmented.get("confidence"), dict) else {}
    field_sources = augmented.get("field_sources", {}) if isinstance(augmented.get("field_sources"), dict) else {}
    for field in missing_fields:
        if field not in {"title", "authors", "year", "venue", "abstract"}:
            continue
        if _is_clear_value(augmented.get(field), field) and field_sources.get(field) == "tool":
            continue
        value = selected_candidate.get(field)
        if _is_clear_value(value, field):
            augmented[field] = _normalize_authors(value) if field == "authors" else str(value)
            confidence[field] = max(float(confidence.get(field, 0.0) or 0.0), score)
            field_sources[field] = "tool"

    augmented["confidence"] = confidence
    augmented["field_sources"] = field_sources
    if selected_candidate:
        reason = str(augmented.get("reason", "") or "")
        selected_reason = (
            f"Selected {selected_candidate.get('source', '')} candidate local_rank={selected_candidate.get('local_rank')} "
            f"with local_match_score={selected_candidate.get('local_match_score')}."
        )
        augmented["reason"] = f"{reason} {selected_reason}".strip()
    return augmented


def _tool_calling_supported() -> tuple[bool, str]:
    """Return whether the current LLM provider can use OpenAI-style tool calls."""
    config = get_llm_config()
    if config.provider not in {"deepseek", "openai_compatible"}:
        return False, "Current LLM provider does not support tool calling"
    if not config.api_key or not config.base_url or not config.model:
        return False, "LLM_API_KEY, LLM_BASE_URL, and LLM_MODEL are required for tool calling"
    if not _tool_specs():
        return False, "PAPER_LOOKUP_PROVIDERS does not enable any supported lookup tools"
    return True, ""


def _call_llm_chat(
    messages: list[dict[str, object]],
    tools: list[dict[str, object]] | None = None,
    tool_choice: str | None = None,
) -> dict[str, object]:
    """Call an OpenAI-compatible chat completion endpoint."""
    config = get_llm_config()
    body = {
        "model": config.model,
        "messages": messages,
        "temperature": config.temperature,
    }
    if tools is not None:
        body["tools"] = tools
    if tool_choice is not None:
        body["tool_choice"] = tool_choice

    response = requests.post(
        f"{config.base_url}/chat/completions",
        json=body,
        headers={"Authorization": f"Bearer {config.api_key}", "Content-Type": "application/json"},
        timeout=config.timeout,
    )
    response.raise_for_status()
    payload = response.json()
    return payload["choices"][0]["message"]


def _call_llm_with_tools(messages: list[dict[str, object]], tools: list[dict[str, object]]) -> dict[str, object]:
    """Call an OpenAI-compatible chat completion endpoint with tools."""
    return _call_llm_chat(messages, tools=tools, tool_choice="auto")


def _call_llm_without_tools(messages: list[dict[str, object]]) -> dict[str, object]:
    """Call an OpenAI-compatible chat completion endpoint without tools."""
    return _call_llm_chat(messages, tools=None, tool_choice=None)


def _run_tool_agent(
    state: PaperState,
    parsed_info: dict[str, object],
    final_info: dict[str, object],
    missing_fields: list[str],
    profile: dict[str, object] | None = None,
) -> dict[str, object]:
    """Let the LLM decide whether to call metadata tools and return final JSON."""
    profile = profile if profile and profile.get("enabled") else None
    supported, unsupported_reason = _tool_calling_supported()
    tool_debug: dict[str, object] = {
        "enabled": get_paper_lookup_config().enabled,
        "called": False,
        "tool_calling_supported": supported,
        "missing_fields": missing_fields,
        "used_tools": [],
        "tool_rounds": 0,
        "tool_results": [],
        "final_agent_json": {},
        "errors": [],
        "raw_text_length": len(state.get("raw_text", "")),
        "raw_text_preview_length": min(len(state.get("raw_text", "")), 2000),
        "parser_name": state.get("parser_name", ""),
        "paper_language": state.get("paper_language", "zh"),
        "candidate_rerank_enabled": True,
        "candidate_rank_before": None,
        "candidate_rank_after": None,
        "selected_candidate": {},
        "top_candidates_after_rerank": [],
        "local_match_score": 0.0,
        "title_similarity": 0.0,
        "author_match_score": 0.0,
        "selected_reason": "",
    }
    if not supported:
        tool_debug["reason"] = unsupported_reason
        return {"agent_json": {}, "candidates": [], "debug": tool_debug}

    tool_debug["called"] = True
    raw_text_head = state.get("raw_text", "")[:2000]
    text_for_ids = "\n".join(
        part
        for part in [
            str(parsed_info.get("title", "") or ""),
            str(parsed_info.get("doi", "") or ""),
            str(parsed_info.get("arxiv_id", "") or ""),
            raw_text_head,
        ]
        if part
    )
    lookup_hints = {
        "candidate_title": (
            parsed_info.get("title")
            if _is_clear_value(parsed_info.get("title"), "title")
            else final_info.get("title")
            if _is_clear_value(final_info.get("title"), "title")
            else _candidate_title_from_raw_text(raw_text_head)
        ),
        "first_author": _first_author(parsed_info.get("authors") or final_info.get("authors")),
        "doi": parsed_info.get("doi") or _extract_doi(text_for_ids),
        "arxiv_id": parsed_info.get("arxiv_id") or _extract_arxiv_id(text_for_ids),
        "url": parsed_info.get("url", ""),
    }
    parser_context = {
        "parser_info": parsed_info,
        "current_final_info": final_info,
        "missing_fields": missing_fields,
        "paper_language": state.get("paper_language", "zh"),
        "parser_name": state.get("parser_name", ""),
        "lookup_hints": lookup_hints,
    }
    schema_start = time.perf_counter()
    selected_tools = _select_tools_for_missing_fields(
        missing_fields,
        lookup_hints,
        state.get("paper_language", "zh"),
    )
    tool_schemas = _tool_specs(selected_tools)
    if profile is not None:
        profile["tool_schema_build_sec"] = _duration_sec(schema_start)
    if not tool_schemas:
        tool_debug["reason"] = "No lookup tools selected for current missing fields"
        return {"agent_json": {}, "candidates": [], "debug": tool_debug}

    messages: list[dict[str, object]] = [
        {
            "role": "system",
            "content": (
                "你是论文元数据补全 Agent。你会收到 parser 已提取的信息和缺失字段。"
                "你可以调用论文检索工具查找元数据。必须遵守：只补充 missing_fields 中的字段；"
                "不覆盖 parser/parser_meta 已有明确字段；工具结果置信度不足时不要使用；"
                "不确定就返回默认值；不要翻译摘要；中文论文优先使用中文元数据源或中文摘要；"
                "英文论文优先使用 Crossref/OpenAlex；只有存在 arXiv ID 或明确 arXiv 线索时才使用 arXiv。"
                "你最多只有一次工具调用机会；如果需要工具，必须在第一轮一次性调用所有必要工具；"
                "不要分多轮逐步试探。最终必须输出 JSON。"
            ),
        },
        {
            "role": "user",
            "content": (
                "请根据以下上下文决定是否调用工具，并只补充缺失字段。"
                "上下文中的 lookup_hints 只用于构造工具查询，不要直接从原文猜测最终字段。"
                "如果只缺 year/venue，优先调用 Crossref 和 OpenAlex；没有 arXiv ID 时不要调用 arXiv。"
                "如果 parser/parser_meta 已经有明确字段，不要为该字段调用工具。"
                "最终 JSON 格式："
                '{"title":"","authors":[],"year":"","venue":"","abstract":"","used_tools":[],'
                '"field_sources":{"title":"tool/default","authors":"tool/default","year":"tool/default","venue":"tool/default","abstract":"tool/default"},'
                '"confidence":{"title":0.0,"authors":0.0,"year":0.0,"venue":0.0,"abstract":0.0},"reason":""}\n'
                f"{json.dumps(parser_context, ensure_ascii=False)}"
            ),
        },
    ]
    candidates: list[dict[str, object]] = []
    final_agent_json: dict[str, object] = {}

    llm_start = time.perf_counter()
    llm_round: dict[str, object] = {
        "round": 1,
        "duration_sec": 0.0,
        "has_tool_calls": False,
        "tool_call_count": 0,
        "tool_names": [],
    }
    try:
        message = _call_llm_with_tools(messages, tool_schemas)
    except Exception as exc:
        llm_round["duration_sec"] = _duration_sec(llm_start)
        llm_round["error"] = str(exc)
        if profile is not None:
            profile["llm_call_count"] = int(profile.get("llm_call_count", 0)) + 1
            profile["llm_total_sec"] = round(float(profile.get("llm_total_sec", 0.0)) + float(llm_round["duration_sec"]), 4)
            profile.setdefault("llm_rounds", []).append(llm_round)
        tool_debug["tool_rounds"] = 1
        tool_debug["errors"].append(str(exc))
        return {"agent_json": {}, "candidates": [], "debug": tool_debug}

    llm_round["duration_sec"] = _duration_sec(llm_start)
    tool_calls = message.get("tool_calls") or []
    tool_names = [
        str((tool_call.get("function", {}) or {}).get("name", ""))
        for tool_call in tool_calls
    ]
    llm_round.update(
        {
            "has_tool_calls": bool(tool_calls),
            "tool_call_count": len(tool_calls),
            "tool_names": [name for name in tool_names if name],
        }
    )
    if profile is not None:
        profile["llm_call_count"] = int(profile.get("llm_call_count", 0)) + 1
        profile["llm_total_sec"] = round(float(profile.get("llm_total_sec", 0.0)) + float(llm_round["duration_sec"]), 4)
        profile.setdefault("llm_rounds", []).append(llm_round)

    if not tool_calls:
        parse_start = time.perf_counter()
        try:
            final_agent_json = _parse_agent_json(str(message.get("content", "") or "{}"))
        except Exception as exc:
            tool_debug["errors"].append(f"Failed to parse final agent JSON: {exc}")
        if profile is not None:
            profile["final_json_parse_sec"] = round(
                float(profile.get("final_json_parse_sec", 0.0)) + _duration_sec(parse_start),
                4,
            )
        tool_debug["tool_rounds"] = 1
        tool_debug["final_agent_json"] = final_agent_json
        return {"agent_json": final_agent_json, "candidates": candidates, "debug": tool_debug}

    messages.append(message)
    tool_messages, tool_results = _execute_tool_calls_concurrently(tool_calls, profile)
    slim_tool_messages = []
    for tool_message, tool_result in zip(tool_messages, tool_results):
        name = str(tool_result.get("name", ""))
        result = tool_result.get("result", {}) if isinstance(tool_result.get("result"), dict) else {}
        tool_debug["used_tools"].append(name)
        tool_debug["tool_results"].append(result)
        candidates.extend(result.get("candidates", []) or [])
        slim_tool_messages.append(
            {
                **tool_message,
                "content": json.dumps(_slim_tool_result_for_llm(result, missing_fields), ensure_ascii=False),
            }
        )

    reranked_candidates = rerank_metadata_candidates(
        candidates,
        str(final_info.get("title", "") if _is_clear_value(final_info.get("title"), "title") else parsed_info.get("title", "") or ""),
        _normalize_authors(final_info.get("authors") or parsed_info.get("authors") or []),
        str(parsed_info.get("year", "") or final_info.get("year", "") or ""),
        str(parsed_info.get("venue", "") or final_info.get("venue", "") or ""),
        str(lookup_hints.get("doi", "") or ""),
        str(lookup_hints.get("arxiv_id", "") or ""),
    )
    selected_candidate = reranked_candidates[0] if reranked_candidates else {}
    top_candidates_debug = [_candidate_debug_view(candidate) for candidate in reranked_candidates[:5]]
    tool_debug["top_candidates_after_rerank"] = top_candidates_debug
    tool_debug["selected_candidate"] = _candidate_debug_view(selected_candidate)
    if selected_candidate:
        tool_debug["candidate_rank_before"] = selected_candidate.get("provider_rank")
        tool_debug["candidate_rank_after"] = selected_candidate.get("local_rank")
        tool_debug["local_match_score"] = selected_candidate.get("local_match_score", 0.0)
        tool_debug["title_similarity"] = selected_candidate.get("title_similarity", 0.0)
        tool_debug["author_match_score"] = selected_candidate.get("author_match_score", 0.0)
        tool_debug["selected_reason"] = selected_candidate.get("rerank_reason", "")
        if float(selected_candidate.get("local_match_score", 0.0) or 0.0) < 0.6:
            tool_debug["low_confidence_candidate_rejected"] = True
            tool_debug["low_confidence_reason"] = "Top local_match_score is below 0.60"
    candidates = reranked_candidates
    messages.extend(slim_tool_messages)
    messages.append(
        {
            "role": "user",
            "content": (
                "下面是工具候选的本地重排结果。provider_rank 是原 provider 内排序，local_rank 是 PaperPilot 本地重排后的排序。"
                "优先参考 local_rank=1 且 local_match_score>=0.75 的候选；0.60 到 0.75 只能作为弱参考；低于 0.60 不要用于补字段。"
                f"{json.dumps({'top_candidates_after_rerank': top_candidates_debug, 'missing_fields': missing_fields}, ensure_ascii=False)}"
            ),
        }
    )
    messages.append(
        {
            "role": "user",
            "content": (
                "工具调用已经结束，不允许继续调用工具。"
                "请只根据 parser 信息、missing_fields 和上面的工具结果输出最终 JSON。"
                "只补充 missing_fields 中的字段，不覆盖 parser/parser_meta 已有明确值。"
                "不在 missing_fields 中的 title/authors/abstract/year/venue 必须返回空字符串或空数组，不要复述 parser 已有字段。"
                "工具结果不可信或信息不足时返回默认值。不要翻译摘要。"
                "reason 中说明 selected candidate 的 provider、local_rank 和 local_match_score。"
                "最终只能输出 JSON，不要 Markdown，不要解释。"
            ),
        }
    )

    llm_start = time.perf_counter()
    llm_round = {
        "round": 2,
        "duration_sec": 0.0,
        "has_tool_calls": False,
        "tool_call_count": 0,
        "tool_names": [],
    }
    try:
        message = _call_llm_without_tools(messages)
    except Exception as exc:
        llm_round["duration_sec"] = _duration_sec(llm_start)
        llm_round["error"] = str(exc)
        if profile is not None:
            profile["llm_call_count"] = int(profile.get("llm_call_count", 0)) + 1
            profile["llm_total_sec"] = round(float(profile.get("llm_total_sec", 0.0)) + float(llm_round["duration_sec"]), 4)
            profile.setdefault("llm_rounds", []).append(llm_round)
        tool_debug["errors"].append(str(exc))
        tool_debug["tool_rounds"] = 2
        return {"agent_json": {}, "candidates": candidates, "debug": tool_debug}

    llm_round["duration_sec"] = _duration_sec(llm_start)
    unexpected_tool_calls = message.get("tool_calls") or []
    if unexpected_tool_calls:
        llm_round.update(
            {
                "has_tool_calls": True,
                "tool_call_count": len(unexpected_tool_calls),
                "tool_names": [
                    str((tool_call.get("function", {}) or {}).get("name", ""))
                    for tool_call in unexpected_tool_calls
                ],
            }
        )
        tool_debug["errors"].append("Final summary LLM returned unexpected tool_calls; ignored them.")
    if profile is not None:
        profile["llm_call_count"] = int(profile.get("llm_call_count", 0)) + 1
        profile["llm_total_sec"] = round(float(profile.get("llm_total_sec", 0.0)) + float(llm_round["duration_sec"]), 4)
        profile.setdefault("llm_rounds", []).append(llm_round)

    parse_start = time.perf_counter()
    try:
        final_agent_json = _parse_agent_json(str(message.get("content", "") or "{}"))
    except Exception as exc:
        tool_debug["errors"].append(f"Failed to parse final agent JSON: {exc}")
    final_agent_json = _augment_agent_json_with_selected_candidate(final_agent_json, selected_candidate, missing_fields)
    if profile is not None:
        profile["final_json_parse_sec"] = round(
            float(profile.get("final_json_parse_sec", 0.0)) + _duration_sec(parse_start),
            4,
        )
    tool_debug["tool_rounds"] = 2

    tool_debug["final_agent_json"] = final_agent_json
    return {"agent_json": final_agent_json, "candidates": candidates, "debug": tool_debug}


def _merge_tool_agent_result(
    final_info: dict[str, object],
    field_debug: dict[str, object],
    agent_json: dict[str, object],
    parser_title: str,
    missing_fields: list[str],
) -> tuple[dict[str, object], dict[str, object]]:
    """Fill missing fields from the tool agent JSON without overwriting clear values."""
    confidence = agent_json.get("confidence", {}) if isinstance(agent_json.get("confidence"), dict) else {}
    field_sources = agent_json.get("field_sources", {}) if isinstance(agent_json.get("field_sources"), dict) else {}
    allowed_fields = set(missing_fields)
    for field in ["title", "authors", "year", "venue", "abstract"]:
        if field not in allowed_fields:
            field_debug[field]["tool_checked"] = True
            field_debug[field]["tool_not_used_reason"] = "Field was not in missing_fields; tool result ignored"
            continue
        if _is_clear_value(final_info.get(field), field):
            field_debug[field]["tool_checked"] = True
            field_debug[field]["tool_not_used_reason"] = "Existing parser/parser_meta value is clear; tool result not used"
            continue
        tool_value = agent_json.get(field)
        field_confidence = float(confidence.get(field, 0.0) or 0.0)
        tool_title = str(agent_json.get("title", "") or "")
        if field_sources.get(field) == "tool" and _is_tool_value_usable(field, tool_value, field_confidence, parser_title, tool_title):
            final_info[field] = _normalize_authors(tool_value) if field == "authors" else str(tool_value)
            field_debug[field] = {
                **field_debug.get(field, {}),
                "final_value": final_info[field],
                "source": "tool",
                "tool_value": tool_value,
                "confidence": field_confidence,
                "used_tool": True,
                "used_default": False,
                "tool_value_clear": _is_clear_value(tool_value, field),
                "reason": "parser/parser_meta 未提取到该字段，工具 Agent 或本地 rerank 命中高置信结果，因此使用工具结果",
            }
        else:
            field_debug[field]["tool_checked"] = True
            field_debug[field]["tool_not_used_reason"] = "Tool result missing, source is not tool, or confidence is below threshold"
    return final_info, field_debug


def paper_info_node(state: PaperState) -> dict[str, object]:
    """Extract and enrich paper title, authors, venue, year, and abstract."""
    node_start = time.perf_counter()
    profile_enabled = is_paper_info_profile_enabled()
    profile = _new_profile(profile_enabled)

    parsed_start = time.perf_counter()
    parsed_info = _get_parsed_paper_info(state)
    if profile_enabled:
        profile["parsed_info_extract_sec"] = _duration_sec(parsed_start)
        profile["raw_text_length"] = len(state.get("raw_text", ""))
        profile["raw_text_preview_length"] = min(len(state.get("raw_text", "")), 2000)

    paper_language = state.get("paper_language", "zh")
    zh_metadata_debug: dict[str, object] = {"enabled": False}
    if paper_language == "zh":
        final_info, field_debug = _resolve_zh_initial_info(parsed_info, paper_language)
        title_llm_result: dict[str, object] = {}
        title_llm_debug: dict[str, object] = {
            "called": False,
            "skipped": True,
            "reason": "Title already resolved or no layout candidate selection needed",
        }
        if not _is_clear_value(final_info.get("title"), "title"):
            candidate_confidence = float(parsed_info.get("candidate_title_confidence", 0.0) or 0.0)
            candidate_title = parsed_info.get("candidate_title", "")
            if _is_clear_value(candidate_title, "title"):
                title_llm_result, title_llm_debug = _select_zh_title_with_llm(
                    parsed_info.get("candidate_title_candidates", []),
                    str(parsed_info.get("first_page_text", "") or ""),
                )
                final_info, field_debug = _apply_zh_title_selection_result(
                    final_info,
                    field_debug,
                    parsed_info,
                    title_llm_result,
                )
                if not _is_clear_value(final_info.get("title"), "title"):
                    field_debug["title"]["reason"] = (
                        "PyMuPDF layout candidate_title 置信度不足"
                        f"({candidate_confidence:.2f})，且 LLM 未从候选中选出可靠标题，因此返回默认值"
                    )
            else:
                field_debug["title"]["reason"] = "PyMuPDF 未生成明确 layout 标题候选，因此返回默认值"
        zh_core_missing = _missing_zh_core_fields(final_info)
        first_page_llm_result, first_page_llm_debug = _run_zh_first_page_llm(
            str(parsed_info.get("first_page_text", "") or ""),
            zh_core_missing,
        )
        final_info, field_debug = _apply_first_page_llm_result(
            final_info,
            field_debug,
            first_page_llm_result,
        )
        external_lookup_allowed, external_lookup_reason = _zh_external_lookup_decision(
            parsed_info,
            final_info,
            state.get("raw_text", ""),
        )
        zh_metadata_debug = _build_zh_metadata_debug(
            parsed_info,
            title_llm_result,
            title_llm_debug,
            first_page_llm_result,
            first_page_llm_debug,
            external_lookup_allowed,
            external_lookup_reason,
            final_info,
            field_debug,
        )
    else:
        final_info, field_debug = _resolve_initial_info(parsed_info, paper_language)
        external_lookup_allowed = True
        external_lookup_reason = ""

    missing_start = time.perf_counter()
    missing_fields = _missing_final_fields(final_info)
    config = get_paper_lookup_config()
    if profile_enabled:
        profile["missing_fields_check_sec"] = _duration_sec(missing_start)
        profile["missing_fields"] = missing_fields

    web_search_results: list[dict[str, object]] = []
    if not missing_fields:
        tool_debug = {
            "enabled": config.enabled,
            "called": False,
            "tool_calling_supported": False,
            "missing_fields": [],
            "used_tools": [],
            "tool_rounds": 0,
            "tool_results": [],
            "final_agent_json": {},
            "errors": [],
            "skipped": True,
            "reason": "No missing fields",
            "parser_name": state.get("parser_name", ""),
            "paper_language": paper_language,
        }
    elif paper_language == "zh" and not external_lookup_allowed:
        tool_debug = {
            "enabled": config.enabled,
            "called": False,
            "tool_calling_supported": False,
            "missing_fields": missing_fields,
            "used_tools": [],
            "tool_rounds": 0,
            "tool_results": [],
            "final_agent_json": {},
            "errors": [],
            "skipped": True,
            "reason": external_lookup_reason,
            "parser_name": state.get("parser_name", ""),
            "paper_language": paper_language,
        }
    elif not config.enabled:
        tool_debug = {
            "enabled": False,
            "called": False,
            "tool_calling_supported": False,
            "missing_fields": missing_fields,
            "used_tools": [],
            "tool_rounds": 0,
            "tool_results": [],
            "final_agent_json": {},
            "errors": [],
            "skipped": True,
            "reason": "PAPER_INFO_TOOL_AGENT_ENABLED is false",
            "parser_name": state.get("parser_name", ""),
            "paper_language": paper_language,
        }
    else:
        tool_agent_start = time.perf_counter()
        agent_result = _run_tool_agent(state, parsed_info, final_info, missing_fields, profile)
        if profile_enabled:
            profile["tool_agent_total_sec"] = _duration_sec(tool_agent_start)
        web_search_results = agent_result.get("candidates", []) or []
        tool_debug = agent_result.get("debug", {}) or {}
        merge_start = time.perf_counter()
        final_info, field_debug = _merge_tool_agent_result(
            final_info,
            field_debug,
            agent_result.get("agent_json", {}) or {},
            str(parsed_info.get("title", "") or ""),
            missing_fields,
        )
        if profile_enabled:
            profile["field_merge_sec"] = _duration_sec(merge_start)

    final_missing_start = time.perf_counter()
    missing_info_fields = _missing_final_fields(final_info)
    need_web_search = bool(
        missing_info_fields
        and config.enabled
        and (paper_language != "zh" or external_lookup_allowed)
    )
    if profile_enabled:
        profile["missing_fields_check_sec"] = round(
            float(profile.get("missing_fields_check_sec", 0.0)) + _duration_sec(final_missing_start),
            4,
        )
        profile["final_missing_fields"] = missing_info_fields
        profile["need_web_search"] = need_web_search

    debug_start = time.perf_counter()
    paper_info_debug = {
        **field_debug,
        "_summary": {
            "parser_name": state.get("parser_name", ""),
            "paper_language": paper_language,
            "raw_text_length": len(state.get("raw_text", "")),
            "raw_text_preview_length": min(len(state.get("raw_text", "")), 2000),
        },
        "_tool_agent": tool_debug,
        "_zh_metadata": zh_metadata_debug,
    }
    if profile_enabled:
        profile["debug_build_sec"] = _duration_sec(debug_start)
        profile["paper_info_node_total_sec"] = _duration_sec(node_start)
        paper_info_debug["_profile"] = profile
        print(
            "[paper_info_profile] "
            f"total={profile['paper_info_node_total_sec']}s "
            f"llm_calls={profile['llm_call_count']} "
            f"llm_total={profile['llm_total_sec']}s "
            f"tools_total={profile['tool_execution_total_sec']}s "
            f"tools={len(profile['tool_calls'])}"
        )

    return {
        "title": str(final_info.get("title", DEFAULT_TEXT)),
        "authors": _normalize_authors(final_info.get("authors", [])),
        "year": str(final_info.get("year", DEFAULT_TEXT)),
        "venue": str(final_info.get("venue", DEFAULT_TEXT)),
        "abstract": str(final_info.get("abstract", DEFAULT_TEXT)),
        "paper_info_debug": paper_info_debug,
        "missing_info_fields": missing_info_fields,
        "need_web_search": need_web_search,
        "web_search_results": web_search_results,
        "web_search_debug": tool_debug,
    }
