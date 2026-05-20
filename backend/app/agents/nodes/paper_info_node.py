"""Paper metadata extraction node."""

import re
from typing import Any

from app.agents.paper_state import PaperState
from app.services.llm_service import extract_paper_info


UNCLEAR_VALUES = {"", "未明确提及", "未知", "unknown", "Unknown", "N/A", "无", "暂无", "待识别论文标题"}


def _contains_chinese(text: str) -> bool:
    """Return whether text contains Chinese characters."""
    return bool(re.search(r"[\u4e00-\u9fff]", text or ""))


def _is_clear_value(value: object, field_name: str | None = None) -> bool:
    """Return whether a parser/LLM value is explicit enough to trust."""
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
    }


def _missing_fields(parsed_info: dict[str, object]) -> list[str]:
    """Return metadata fields that still need LLM extraction."""
    fields = ["title", "authors", "year", "venue", "abstract"]
    return [
        field
        for field in fields
        if not _is_clear_value(parsed_info.get(field), field_name=field)
    ]


"""
    # 功能：找缺失的论文必填字段
"""
def _missing_final_fields(info: dict[str, object]) -> list[str]:
    """Return final metadata fields still missing after parser/LLM extraction."""
    fields = ["title", "authors", "year", "venue", "abstract"]
    return [
        field
        for field in fields
        if not _is_clear_value(info.get(field), field_name=field)
    ]


def _normalize_authors(value: object) -> list[str]:
    """Normalize authors to a list of strings."""
    if isinstance(value, list):
        return [str(author) for author in value if str(author).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _resolve_field(
    field_name: str,
    parser_value: object = None,
    parser_meta_value: object = None,
    llm_value: object = None,
    default_value: object = "未明确提及",
    prefer_parser: bool = True,
) -> tuple[object, dict[str, object]]:
    """Resolve one metadata field and return its debug record."""
    parser_value_clear = _is_clear_value(parser_value, field_name)
    parser_meta_value_clear = _is_clear_value(parser_meta_value, field_name)
    llm_value_clear = _is_clear_value(llm_value, field_name)

    if prefer_parser and parser_value_clear:
        final_value = parser_value
        source = "parser"
        reason = f"parsed_paper.{field_name} 是明确值，因此使用 parser 结果"
    elif parser_meta_value_clear:
        final_value = parser_meta_value
        source = "parser_meta"
        reason = f"parser_meta.{field_name} 是明确值，因此使用 parser_meta 结果"
    elif llm_value_clear:
        final_value = llm_value
        source = "llm"
        if parser_meta_value is not None:
            reason = f"parser_meta.{field_name} 缺失，因此使用 LLM 提取结果"
        else:
            reason = f"parsed_paper.{field_name} 缺失，因此使用 LLM 提取结果"
    else:
        final_value = default_value
        source = "default"
        reason = f"parser 和 LLM 都没有明确 {field_name}，因此使用默认值"

    debug = {
        "final_value": final_value,
        "source": source,
        "parser_value": parser_value,
        "parser_meta_value": parser_meta_value,
        "llm_value": llm_value,
        "default_value": default_value,
        "used_parser": source == "parser",
        "used_parser_meta": source == "parser_meta",
        "used_llm": source == "llm",
        "used_default": source == "default",
        "parser_value_clear": parser_value_clear,
        "parser_meta_value_clear": parser_meta_value_clear,
        "llm_value_clear": llm_value_clear,
        "reason": reason,
    }
    return final_value, debug


def _resolve_abstract(
    parser_value: object,
    llm_value: object,
    paper_language: str,
) -> tuple[str, dict[str, object]]:
    """Resolve abstract with extra protection for parser-provided abstracts."""
    final_value, debug = _resolve_field(
        "abstract",
        parser_value=parser_value,
        llm_value=llm_value,
        default_value="未明确提及",
        prefer_parser=True,
    )
    parser_text = str(parser_value or "")
    if _is_clear_value(parser_text, "abstract"):
        debug["reason"] = "parsed_paper.abstract 是明确值，因此使用 parser 结果；LLM 不允许覆盖 parser 摘要"
        if paper_language == "zh" and _contains_chinese(parser_text):
            debug["reason"] = "中文论文 parser 摘要包含中文，因此不允许 LLM 英文摘要覆盖"
        debug["source"] = "parser"
        debug["used_parser"] = True
        debug["used_llm"] = False
        debug["used_default"] = False
        final_value = parser_text

    return str(final_value), debug


def _build_debug_summary(
    state: PaperState,
    llm_called: bool,
    llm_requested_fields: list[str],
    llm_raw_result: dict[str, object],
    raw_text_preview_length: int,
) -> dict[str, object]:
    """Build summary-level debug information."""
    return {
        "parser_name": state.get("parser_name", ""),
        "paper_language": state.get("paper_language", "zh"),
        "llm_called": llm_called,
        "llm_requested_fields": llm_requested_fields,
        "llm_raw_result": llm_raw_result,
        "raw_text_preview_length": raw_text_preview_length,
    }


def paper_info_node(state: PaperState) -> dict[str, object]:
    """Extract paper title, authors, venue, year, and abstract."""
    parsed_info = _get_parsed_paper_info(state)
    missing_fields = _missing_fields(parsed_info)
    text = state.get("raw_text", "")[:8000]
    paper_language = state.get("paper_language", "zh")

    llm_info: dict[str, object] = {}
    llm_called = False
    if missing_fields:
        llm_called = True
        llm_info = extract_paper_info(
            text,
            missing_fields=missing_fields,
            paper_language=paper_language,
            parser_info={
                key: value
                for key, value in parsed_info.items()
                if _is_clear_value(value, field_name=key)
            },
        )

    final_title, title_debug = _resolve_field(
        "title",
        parser_value=parsed_info.get("title"),
        llm_value=llm_info.get("title"),
        default_value="未明确提及",
    )
    final_authors, authors_debug = _resolve_field(
        "authors",
        parser_value=parsed_info.get("authors"),
        llm_value=llm_info.get("authors"),
        default_value=[],
    )
    final_abstract, abstract_debug = _resolve_abstract(
        parsed_info.get("abstract"),
        llm_info.get("abstract"),
        paper_language,
    )
    final_year, year_debug = _resolve_field(
        "year",
        parser_meta_value=parsed_info.get("year"),
        llm_value=llm_info.get("year"),
        default_value="未明确提及",
        prefer_parser=False,
    )
    final_venue, venue_debug = _resolve_field(
        "venue",
        parser_meta_value=parsed_info.get("venue"),
        llm_value=llm_info.get("venue"),
        default_value="未明确提及",
        prefer_parser=False,
    )

    final_authors = _normalize_authors(final_authors)
    authors_debug["final_value"] = final_authors

    paper_info_debug = {
        "title": title_debug,
        "authors": authors_debug,
        "abstract": abstract_debug,
        "year": year_debug,
        "venue": venue_debug,
        "_summary": _build_debug_summary(
            state,
            llm_called,
            missing_fields,
            llm_info,
            len(text),
        ),
    }

    final_info = {
        "title": str(final_title),
        "authors": final_authors,
        "year": str(final_year),
        "venue": str(final_venue),
        "abstract": final_abstract,
    }
    missing_info_fields = _missing_final_fields(final_info)
    need_web_search = any(
        field in missing_info_fields for field in ["title", "authors", "year", "venue"]
    )

    return {
        **final_info,
        "paper_info_debug": paper_info_debug,
        "missing_info_fields": missing_info_fields,
        "need_web_search": need_web_search,
    }
