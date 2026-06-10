"""Rule-based section quality verification node."""

from difflib import SequenceMatcher
import re

from app.agents.paper_state import PaperState


VERIFY_SECTION_KEYS = [
    "abstract",
    "introduction",
    "related_work",
    "method",
    "experiments",
    "conclusion",
]

DEFAULT_REQUIRED_SECTIONS = ["abstract", "introduction", "method", "experiments", "conclusion"]
DEFAULT_OPTIONAL_SECTIONS = ["related_work", "discussion"]

UNCLEAR_SECTION_VALUES = {"", "未明确提及", "unknown", "Unknown", "N/A", "无"}

ZH_MIN_LENGTHS = {
    "abstract": 30,
    "introduction": 100,
    "related_work": 80,
    "method": 150,
    "experiments": 150,
    "conclusion": 50,
}

EN_MIN_LENGTHS = {
    "abstract": 250,
    "introduction": 750,
    "related_work": 600,
    "method": 1000,
    "experiments": 1000,
    "conclusion": 400,
}

REFERENCE_PATTERNS = [
    r"参考文献",
    r"\bReferences\b",
    r"\bBibliography\b",
    r"\[\s*\d+\s*\]",
    r"\bdoi\b",
]

ABSTRACT_BOUNDARY_PATTERNS = [
    r"关键词",
    r"关键\s*词",
    r"\bKeywords\b",
    r"引言",
    r"\bIntroduction\b",
]

SECTION_HEADING_PATTERNS = [
    r"^\s*(?:\d+(?:[.．]\d+)*(?:[.．、])?|第[一二三四五六七八九十百\d]+[章节]|[一二三四五六七八九十百]+[、.．])?\s*(?:引言|绪论|相关工作|材料与方法|研究方法|实验|结果与分析|结论|参考文献)\s*$",
    r"^\s*(?:\d+(?:\.\d+)*\.?|[IVXLCDM]+\.?)?\s*(?:Introduction|Related Work|Methodology|Method|Experiments|Experimental Results|Conclusion|References)\s*$",
]


def _is_missing(text: object) -> bool:
    """Return whether a section value is missing."""
    if text is None:
        return True
    cleaned = str(text).strip()
    return cleaned in UNCLEAR_SECTION_VALUES


def _is_too_short(text: str, section_name: str, paper_language: str) -> bool:
    """Return whether section content is shorter than expected."""
    cleaned = str(text or "").strip()
    if not cleaned:
        return True
    thresholds = ZH_MIN_LENGTHS if paper_language == "zh" else EN_MIN_LENGTHS
    return len(cleaned) < thresholds.get(section_name, 100)


def _contains_references_pollution(text: str) -> bool:
    """Detect references-like content in the latter half of a section."""
    cleaned = text or ""
    if len(cleaned) < 80:
        return False
    latter_half = cleaned[len(cleaned) // 2 :]
    hits = sum(1 for pattern in REFERENCE_PATTERNS if re.search(pattern, latter_half, re.IGNORECASE))
    bracket_hits = len(re.findall(r"\[\s*\d+\s*\]", latter_half))
    return hits >= 2 or bracket_hits >= 2


def _contains_keywords_pollution(text: str) -> bool:
    """Return whether abstract still contains keywords markers."""
    return bool(re.search(r"关键词|关键\s*词|\bKeywords\b|\bIndex Terms\b", text or "", re.IGNORECASE))


def _contains_abstract_pollution(text: str) -> bool:
    """Return whether abstract likely includes following section content."""
    cleaned = text or ""
    if len(cleaned) < 300:
        return False
    return bool(re.search(r"引言|绪论|\bIntroduction\b", cleaned, re.IGNORECASE))


def _contains_multiple_sections(text: str) -> bool:
    """Return whether one section contains multiple section headings."""
    cleaned = text or ""
    count = 0
    for pattern in SECTION_HEADING_PATTERNS:
        count += len(re.findall(pattern, cleaned, re.IGNORECASE | re.MULTILINE))
    return count >= 2


def _contains_result_terms(text: str) -> bool:
    """Return whether text looks like experiment/result content."""
    return bool(
        re.search(
            r"实验结果|结果与分析|对比实验|消融实验|性能分析|\bExperimental Results\b|\bResults\b|\bEvaluation\b|\bAblation\b",
            text or "",
            re.IGNORECASE,
        )
    )


def _contains_method_terms(text: str) -> bool:
    """Return whether text looks like method/model content."""
    return bool(
        re.search(
            r"方法|算法|模型结构|网络结构|模型构建|\bMethod\b|\bAlgorithm\b|\bArchitecture\b|\bFramework\b|\bModel\b",
            text or "",
            re.IGNORECASE,
        )
    )


def _text_similarity(left: str, right: str) -> float:
    """Compute similarity for the first part of two sections."""
    left_head = re.sub(r"\s+", " ", (left or "")[:500]).strip().lower()
    right_head = re.sub(r"\s+", " ", (right or "")[:500]).strip().lower()
    if not left_head or not right_head:
        return 0.0
    return SequenceMatcher(None, left_head, right_head).ratio()


def _score_section(
    section_name: str,
    text: str,
    meta: dict[str, object],
    paper_language: str,
) -> dict[str, object]:
    """Score one extracted section and list quality issues."""
    issues: list[str] = []
    warnings: list[str] = []
    cleaned = str(text or "").strip()
    score = 1.0

    if _is_missing(cleaned):
        issues.append("missing")
        score = 0.0
    elif _is_too_short(cleaned, section_name, paper_language):
        issues.append("missing_or_too_short")
        score -= 0.45

    if section_name in {"method", "experiments", "conclusion"} and _contains_references_pollution(cleaned):
        issues.append("references_pollution")
        score -= 0.35

    if section_name == "abstract":
        if _contains_keywords_pollution(cleaned):
            issues.append("abstract_boundary_issue")
            score -= 0.25
        if _contains_abstract_pollution(cleaned):
            issues.append("abstract_boundary_issue")
            score -= 0.35

    if section_name == "conclusion" and re.search(
        r"参考文献|\bReferences\b|致谢|\bAcknowledgements?\b|附录|\bAppendix\b",
        cleaned,
        re.IGNORECASE,
    ):
        issues.append("conclusion_pollution")
        score -= 0.35

    if section_name == "method" and _contains_result_terms(cleaned):
        warnings.append("possible_experiment_content")
        score -= 0.1

    if section_name == "experiments" and _contains_method_terms(cleaned) and not _contains_result_terms(cleaned):
        warnings.append("possible_method_content")
        score -= 0.15

    if _contains_multiple_sections(cleaned):
        warnings.append("contains_multiple_section_headings")
        score -= 0.1

    source = str(meta.get("source", "") or "")
    if section_name in {"method", "experiments", "conclusion"} and source in {"", "missing"}:
        issues.append("meta_source_missing")
        score = min(score, 0.4)

    score = round(max(0.0, min(1.0, score)), 2)
    return {
        "score": score,
        "length": len(cleaned),
        "issues": sorted(set(issues)),
        "warnings": sorted(set(warnings)),
        "source": source,
        "matched_title": str(meta.get("matched_title", "") or ""),
    }


def _normalize_section_list(value: object) -> list[str]:
    """Normalize section list values from state or analysis_plan."""
    if not isinstance(value, list):
        return []
    normalized = []
    for item in value:
        section = str(item or "").strip()
        if section and section in VERIFY_SECTION_KEYS and section not in normalized:
            normalized.append(section)
    return normalized


def _resolve_section_plan(state: PaperState) -> tuple[list[str], list[str], bool]:
    """Resolve required/optional sections from state or analysis_plan."""
    required_sections = _normalize_section_list(state.get("required_sections", []))
    optional_sections = _normalize_section_list(state.get("optional_sections", []))
    used_plan = bool(required_sections or optional_sections)

    analysis_plan = state.get("analysis_plan", {}) or {}
    section_strategy = analysis_plan.get("section_strategy", {}) if isinstance(analysis_plan, dict) else {}
    if not required_sections:
        required_sections = _normalize_section_list(section_strategy.get("required_sections", []))
    if not optional_sections:
        optional_sections = _normalize_section_list(section_strategy.get("optional_sections", []))
    if required_sections or optional_sections:
        used_plan = True

    if not required_sections:
        required_sections = list(DEFAULT_REQUIRED_SECTIONS)
        used_plan = False
    if not optional_sections:
        optional_sections = [section for section in DEFAULT_OPTIONAL_SECTIONS if section not in required_sections]

    optional_sections = [section for section in optional_sections if section not in required_sections]
    return required_sections, optional_sections, used_plan


def _quality_status(section_name: str, required_sections: list[str], optional_sections: list[str]) -> str:
    """Return required/optional/ignored for a section."""
    if section_name in required_sections:
        return "required"
    if section_name in optional_sections:
        return "optional"
    return "ignored"


def _required_threshold(section_name: str) -> float:
    """Return repair threshold for a required section."""
    if section_name == "conclusion":
        return 0.4
    return 0.45


def _blocking_issues_for_required(section_name: str, quality: dict[str, object], threshold: float) -> list[str]:
    """Return repair-blocking issues for required sections."""
    issues = set(quality.get("issues", []) or [])
    blocking: list[str] = []
    if float(quality.get("score", 0.0) or 0.0) < threshold:
        blocking.append("low_quality")
    if "missing" in issues or "missing_or_too_short" in issues:
        blocking.append("missing_or_too_short")
    if "meta_source_missing" in issues:
        blocking.append("meta_source_missing")
    if "references_pollution" in issues:
        blocking.append("references_pollution")
    if section_name == "abstract" and "abstract_boundary_issue" in issues:
        blocking.append("abstract_boundary_issue")
    if section_name == "conclusion" and "conclusion_pollution" in issues:
        blocking.append("conclusion_pollution")
    return sorted(set(blocking))


def evaluate_section_quality(state: PaperState) -> tuple[dict[str, object], dict[str, object], bool]:
    """Evaluate all major sections and decide whether repair is needed."""
    paper_language = state.get("paper_language", "zh")
    paper_type = state.get("paper_type", "generic_research") or "generic_research"
    structure_type = state.get("structure_type", "") or ""
    required_sections, optional_sections, used_analysis_plan = _resolve_section_plan(state)
    section_meta = state.get("section_meta", {}) or {}
    section_quality: dict[str, object] = {}
    repair_reasons: list[str] = []
    optional_warnings: list[str] = []

    for section_name in VERIFY_SECTION_KEYS:
        quality = _score_section(
            section_name,
            str(state.get(section_name, "") or ""),
            section_meta.get(section_name, {}) if isinstance(section_meta, dict) else {},
            paper_language,
        )
        status = _quality_status(section_name, required_sections, optional_sections)
        quality["required"] = status == "required"
        quality["optional"] = status == "optional"
        quality["status"] = status
        quality["repair_blocking"] = False
        if status == "optional" and "missing" in quality.get("issues", []):
            quality["warnings"].append("optional section missing")
            optional_warnings.append(f"{section_name} optional_missing")
        elif status == "optional" and quality.get("issues"):
            optional_warnings.append(f"{section_name} optional_quality_issue")
        section_quality[section_name] = quality

    for name in required_sections:
        quality = section_quality[name]
        blocking_issues = _blocking_issues_for_required(name, quality, _required_threshold(name))
        if blocking_issues:
            quality["repair_blocking"] = True
            for issue in blocking_issues:
                repair_reasons.append(f"{name} {issue}")

    duplicate_similarity = _text_similarity(str(state.get("method", "")), str(state.get("experiments", "")))
    if duplicate_similarity >= 0.88 and "method" in required_sections and "experiments" in required_sections:
        section_quality["method"]["warnings"].append("duplicate_sections")
        section_quality["experiments"]["warnings"].append("duplicate_sections")
        section_quality["method"]["repair_blocking"] = True
        section_quality["experiments"]["repair_blocking"] = True
        repair_reasons.append("method experiments duplicate_sections")
    elif duplicate_similarity >= 0.88:
        if section_quality["method"]["status"] == "optional" or section_quality["experiments"]["status"] == "optional":
            optional_warnings.append("method experiments duplicate_sections_but_optional")

    needs_repair = bool(repair_reasons)
    debug = {
        "paper_language": paper_language,
        "paper_type": paper_type,
        "structure_type": structure_type,
        "parser_name": state.get("parser_name", ""),
        "needs_section_repair": needs_repair,
        "repair_reasons": sorted(set(repair_reasons)),
        "optional_warnings": sorted(set(optional_warnings)),
        "required_sections": required_sections,
        "optional_sections": optional_sections,
        "used_analysis_plan": used_analysis_plan,
        "checked_sections": VERIFY_SECTION_KEYS,
        "duplicate_method_experiments_similarity": round(duplicate_similarity, 4),
        "rule_version": "section_verify_plan_aware_v1",
    }
    return section_quality, debug, needs_repair


def _build_verify_decisions(state: PaperState, debug: dict[str, object], section_quality: dict[str, object]) -> list[dict[str, object]]:
    """Append plan-aware section verification decisions."""
    decisions = list(state.get("agent_decisions", []) or [])
    paper_type = str(debug.get("paper_type", "generic_research"))
    for section_name, quality in section_quality.items():
        if quality.get("optional") and "missing" in quality.get("issues", []):
            decisions.append(
                {
                    "node": "section_verify_node",
                    "decision": f"{section_name}_missing_but_optional",
                    "reason": f"paper_type={paper_type}, {section_name} is optional according to analysis_plan",
                    "confidence": 0.9,
                }
            )
    for reason in debug.get("repair_reasons", []) or []:
        decisions.append(
            {
                "node": "section_verify_node",
                "decision": "needs_section_repair",
                "reason": f"required section issue: {reason}",
                "confidence": 0.85,
            }
        )
    return decisions


def section_verify_node(state: PaperState) -> dict[str, object]:
    """Verify extracted section quality without calling an LLM."""
    section_quality, debug, needs_repair = evaluate_section_quality(state)
    return {
        "section_quality": section_quality,
        "section_verify_debug": debug,
        "needs_section_repair": needs_repair,
        "agent_decisions": _build_verify_decisions(state, debug, section_quality),
    }
