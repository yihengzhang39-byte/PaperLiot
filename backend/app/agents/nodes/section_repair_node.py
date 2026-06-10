"""Rule-based section repair node."""

import re
from copy import deepcopy

from app.agents.nodes.section_extract_node import (
    SectionMeta,
    extract_sections_by_rules_with_meta,
)
from app.agents.nodes.section_verify_node import evaluate_section_quality
from app.agents.paper_state import PaperState
from app.core.config import get_section_repair_config


REPAIR_SECTION_KEYS = ["abstract", "method", "experiments", "conclusion"]
REFERENCE_BOUNDARY_RE = re.compile(r"参考文献|\bReferences\b|\bBibliography\b", re.IGNORECASE)
ABSTRACT_START_RE = re.compile(r"(?im)^[ \t　]*(?P<title>摘\s*要|内容摘要|中文摘要|Abstract)[ \t　]*[:：]?[ \t　]*")
ABSTRACT_END_RE = re.compile(
    r"(?im)^[ \t　]*(?:关\s*键\s*词|关键字|Keywords|Index Terms|引言|绪论|Introduction)[ \t　]*[:：.。．\-]?"
)


def _truncate_references_pollution(text: str) -> tuple[str, bool]:
    """Trim references content from a section when it appears."""
    match = REFERENCE_BOUNDARY_RE.search(text or "")
    if not match:
        return text, False
    trimmed = text[: match.start()].strip()
    return trimmed, bool(trimmed and len(trimmed) < len(text.strip()))


def _repair_abstract_from_raw_text(raw_text: str) -> tuple[str, SectionMeta] | None:
    """Repair abstract by copying text between abstract and the next boundary."""
    start_match = ABSTRACT_START_RE.search(raw_text or "")
    if not start_match:
        return None
    content_start = start_match.end()
    end_match = ABSTRACT_END_RE.search(raw_text, content_start)
    content_end = end_match.start() if end_match else min(len(raw_text), content_start + 2000)
    content = raw_text[content_start:content_end].strip()
    if len(content) < 20:
        return None
    if len(content) > 2000:
        content = content[:2000]
        content_end = content_start + 2000
    return content, _rule_repair_meta(
        "abstract",
        start_match.group("title").strip(),
        content,
        content_start,
        content_end,
        "repaired abstract boundary after section_verify_node",
    )


def _rule_repair_meta(
    section: str,
    matched_title: str,
    content: str,
    start_char: int,
    end_char: int,
    warning: str,
) -> SectionMeta:
    """Build section_meta for a repaired section."""
    return {
        "source": "rule_repair",
        "matched_title": matched_title,
        "normalized_section": section,
        "start_char": start_char,
        "end_char": end_char,
        "length": len(content),
        "confidence": 0.72,
        "warning": warning,
    }


def _quality_has_issue(quality: dict[str, object], issue_names: set[str]) -> bool:
    """Return whether a quality record contains any given issue."""
    issues = set(quality.get("issues", []) or [])
    warnings = set(quality.get("warnings", []) or [])
    return bool((issues | warnings) & issue_names)


def _should_repair_section(section: str, quality: dict[str, object]) -> bool:
    """Return whether a section should be repaired in this pass."""
    score = float(quality.get("score", 0.0) or 0.0)
    if section in {"method", "experiments"}:
        return score < 0.45 or _quality_has_issue(quality, {"missing", "missing_or_too_short", "meta_source_missing"})
    if section == "conclusion":
        return score < 0.4 or _quality_has_issue(
            quality,
            {"missing", "missing_or_too_short", "meta_source_missing", "references_pollution", "conclusion_pollution"},
        )
    if section == "abstract":
        return _quality_has_issue(quality, {"abstract_boundary_issue"})
    return False


def _copy_rule_section(
    raw_text: str,
    section: str,
    rule_sections: dict[str, str],
    rule_meta: dict[str, SectionMeta],
) -> tuple[str, SectionMeta] | None:
    """Copy a section found by rules and mark it as a repair result."""
    content = (rule_sections.get(section, "") or "").strip()
    meta = rule_meta.get(section, {})
    if not content:
        return None
    return content, _rule_repair_meta(
        section,
        str(meta.get("matched_title", "") or ""),
        content,
        int(meta.get("start_char", -1) or -1),
        int(meta.get("end_char", -1) or -1),
        "repaired after section_verify_node",
    )


def _repair_sections_by_rules(state: PaperState, section_quality: dict[str, object]) -> tuple[dict[str, object], dict[str, object]]:
    """Repair low-quality sections by copying raw text spans with rules."""
    raw_text = state.get("raw_text", "")
    repaired_state: dict[str, object] = {}
    repaired_sections: list[str] = []
    errors: list[str] = []
    section_meta = deepcopy(state.get("section_meta", {}) or {})

    rule_sections, rule_meta = extract_sections_by_rules_with_meta(raw_text)

    for section in ["method", "experiments", "conclusion"]:
        current_text = str(state.get(section, "") or "")
        quality = section_quality.get(section, {}) if isinstance(section_quality, dict) else {}
        if _quality_has_issue(quality, {"references_pollution", "conclusion_pollution"}):
            trimmed, changed = _truncate_references_pollution(current_text)
            if changed:
                repaired_state[section] = trimmed
                meta = deepcopy(section_meta.get(section, {}) or {})
                meta.update(
                    {
                        "source": "rule_repair",
                        "normalized_section": section,
                        "length": len(trimmed),
                        "end_char": int(meta.get("start_char", 0) or 0) + len(trimmed),
                        "confidence": 0.72,
                        "warning": "truncated references pollution",
                    }
                )
                section_meta[section] = meta
                repaired_sections.append(section)
                continue

        if _should_repair_section(section, quality):
            repaired = _copy_rule_section(raw_text, section, rule_sections, rule_meta)
            if repaired is not None:
                content, meta = repaired
                repaired_state[section] = content
                section_meta[section] = meta
                repaired_sections.append(section)

    abstract_quality = section_quality.get("abstract", {}) if isinstance(section_quality, dict) else {}
    if _should_repair_section("abstract", abstract_quality):
        repaired = _repair_abstract_from_raw_text(raw_text)
        if repaired is not None:
            content, meta = repaired
            repaired_state["abstract"] = content
            section_meta["abstract"] = meta
            repaired_sections.append("abstract")

    repaired_state["section_meta"] = section_meta
    debug = {
        "repaired_sections": sorted(set(repaired_sections)),
        "errors": errors,
    }
    return repaired_state, debug


def section_repair_node(state: PaperState) -> dict[str, object]:
    """Repair sections once when verification reports quality issues."""
    config = get_section_repair_config()
    if not state.get("needs_section_repair", False):
        return {
            "section_repair_debug": {
                "skipped": True,
                "reason": "section_verify_node reports no repair needed",
                "llm_repair_enabled": config.llm_enabled,
            },
            "section_repair_rounds": 0,
        }

    before_quality = state.get("section_quality", {}) or {}
    repaired_state, repair_rule_debug = _repair_sections_by_rules(state, before_quality)
    next_state: PaperState = {**state, **repaired_state}
    after_quality, verify_debug, needs_repair = evaluate_section_quality(next_state)
    repaired_sections = repair_rule_debug.get("repaired_sections", []) or []
    unresolved_sections = [
        section
        for section, quality in after_quality.items()
        if section in {"method", "experiments", "conclusion"}
        and float(quality.get("score", 0.0) or 0.0) < (0.4 if section == "conclusion" else 0.45)
    ]

    debug = {
        "skipped": False,
        "strategy": "rule_repair",
        "before_quality": before_quality,
        "after_quality": after_quality,
        "repaired_sections": repaired_sections,
        "unresolved_sections": unresolved_sections,
        "errors": repair_rule_debug.get("errors", []),
        "llm_repair_enabled": config.llm_enabled,
        "llm_repair_note": "LLM repair is reserved but not executed in this default rule-repair pass.",
        "verify_debug_after_repair": verify_debug,
    }

    return {
        **repaired_state,
        "section_quality": after_quality,
        "needs_section_repair": needs_repair,
        "section_repair_debug": debug,
        "section_repair_rounds": 1,
    }
