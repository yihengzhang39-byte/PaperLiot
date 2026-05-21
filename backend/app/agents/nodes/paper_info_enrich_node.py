"""Deprecated manual paper metadata enrichment node.

This node is intentionally not connected to the LangGraph workflow. Paper
metadata tool calling now lives in paper_info_node.
"""

from copy import deepcopy
from typing import Any

from app.agents.nodes.paper_info_node import _is_clear_value
from app.agents.paper_state import PaperState
from app.core.config import get_paper_lookup_config
from app.services.paper_lookup_service import lookup_paper_metadata


CONFIDENCE_THRESHOLD = 0.75


def _missing_fields(state: PaperState) -> list[str]:
    """Return metadata fields still missing in state."""
    fields = ["title", "authors", "year", "venue", "abstract"]
    return [
        field
        for field in fields
        if not _is_clear_value(state.get(field), field_name=field)
    ]


"""
    判断网络搜到的信息能不能用来填充缺失字段：
"""
def _can_use_web_value(field: str, value: object, confidence: float) -> bool:
    """Return whether a web candidate can fill a missing field."""
    return confidence >= CONFIDENCE_THRESHOLD and _is_clear_value(value, field_name=field)


def _update_existing_debug(
    paper_info_debug: dict[str, Any],
    field: str,
    reason: str,
) -> None:
    """Mark a field debug record as checked but not replaced by web."""
    field_debug = paper_info_debug.setdefault(field, {})
    field_debug["web_checked"] = True
    field_debug["web_not_used_reason"] = reason


"""
    用网络搜到的最佳匹配结果，补全论文缺失的信息"""
def _fill_from_web(
    state: PaperState,
    paper_info_debug: dict[str, Any],
    best_match: dict[str, Any],
    confidence: float,
) -> dict[str, object]:
    """Fill missing metadata fields from a high-confidence web candidate."""
    updates: dict[str, object] = {}
    for field in ["title", "authors", "year", "venue", "abstract"]:
        current_value = state.get(field)
        web_value = best_match.get(field)
        if _is_clear_value(current_value, field_name=field):
            _update_existing_debug(
                paper_info_debug,
                field,
                "Existing value is clear; web result not used",
            )
            continue
        if not _can_use_web_value(field, web_value, confidence):
            _update_existing_debug(
                paper_info_debug,
                field,
                "Web result missing or confidence is below threshold",
            )
            continue

        updates[field] = web_value
        paper_info_debug[field] = {
            **paper_info_debug.get(field, {}),
            "source": "web",
            "final_value": web_value,
            "web_value": web_value,
            "web_source": best_match.get("source", ""),
            "confidence": confidence,
            "used_parser": False,
            "used_parser_meta": False,
            "used_llm": False,
            "used_default": False,
            "used_web": True,
            "reason": "parser 和 LLM 均未提取到该字段，工具查询命中高置信候选，因此使用 web 结果",
        }
    return updates


def paper_info_enrich_node(state: PaperState) -> dict[str, object]:
    """Enrich missing paper metadata with manual paper lookup tools."""
    config = get_paper_lookup_config()
    need_web_search = bool(state.get("need_web_search", False))
    if not need_web_search:
        return {
            "web_search_results": [],
            "web_search_debug": {
                "enabled": config.enabled,
                "skipped": True,
                "reason": "No missing fields requiring web enrichment",
            },
        }
    """
        如果系统管理员关闭了「网络搜索补全论文信息」这个功能，
        那就直接不做任何网络搜索，返回空结果 + 关闭原因。
    """
    if not config.enabled:
        return {
            "web_search_results": [],
            "web_search_debug": {
                "enabled": False,
                "skipped": True,
                "reason": "PAPER_INFO_WEB_ENRICH_ENABLED is false",
            },
        }

    lookup_result = lookup_paper_metadata(
        title=str(state.get("title", "") or ""),
        authors=state.get("authors", []),
        raw_text=state.get("raw_text", ""),
        paper_language=state.get("paper_language", "zh"),
        year=str(state.get("year", "") or ""),
    )

    best_match = lookup_result.get("best_match", {}) or {}
    candidates = lookup_result.get("candidates", []) or []
    lookup_debug = lookup_result.get("debug", {}) or {}
    confidence = float(best_match.get("confidence", 0.0) or 0.0)

    paper_info_debug = deepcopy(state.get("paper_info_debug", {}) or {})
    updates = _fill_from_web(state, paper_info_debug, best_match, confidence)
    next_state = {**state, **updates}
    missing_info_fields = _missing_fields(next_state)
    """
    # 四个核心字段（标题、作者、年份、发表场所），有没有还缺失的？
    # 只要缺一个 → need_web_search_after = True（还要继续搜）
    # 全都不缺 → False（不用搜了）
    """
    need_web_search_after = any(
        field in missing_info_fields for field in ["title", "authors", "year", "venue"]
    )

    return {
        **updates,
        "paper_info_debug": paper_info_debug,
        "missing_info_fields": missing_info_fields,
        "need_web_search": need_web_search_after,
        "web_search_results": candidates,
        "web_search_debug": {
            "enabled": True,
            "skipped": False,
            "reason": "",
            "providers_tried": lookup_debug.get("providers_tried", []),
            "queries": lookup_debug.get("queries", []),
            "candidate_count": len(candidates),
            "best_match": best_match,
            "confidence": confidence,
            "errors": lookup_debug.get("errors", []),
        },
    }
