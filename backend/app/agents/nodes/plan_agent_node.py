"""Controlled planning agent node for paper analysis strategy."""

from copy import deepcopy
import json
import re
from typing import Any

from app.agents.paper_state import PaperState
from app.core.config import get_plan_agent_config, get_section_repair_config
from app.services.llm_service import call_llm_json


PAPER_TYPE_SECTIONS = {
    "algorithm_paper": {
        "required": ["abstract", "introduction", "method", "experiments", "conclusion"],
        "optional": ["related_work", "discussion"],
        "structure_type": "algorithm_research_paper",
        "focus": ["problem_formulation", "model_architecture", "training_strategy", "experiments", "ablation"],
    },
    "experimental_research": {
        "required": ["abstract", "introduction", "method", "experiments", "conclusion"],
        "optional": ["related_work", "discussion"],
        "structure_type": "zh_experimental_paper",
        "focus": ["materials_and_methods", "experimental_design", "result_analysis", "limitations"],
    },
    "engineering_system": {
        "required": ["introduction", "method", "experiments", "conclusion"],
        "optional": ["abstract", "related_work", "discussion"],
        "structure_type": "engineering_system_paper",
        "focus": ["requirements", "system_design", "implementation", "testing", "deployment"],
    },
    "review": {
        "required": ["introduction", "related_work", "discussion", "conclusion"],
        "optional": ["abstract", "method", "experiments"],
        "structure_type": "review_or_survey",
        "focus": ["taxonomy", "research_progress", "challenges", "future_directions"],
    },
    "thesis_or_report": {
        "required": ["introduction", "method", "experiments", "conclusion"],
        "optional": ["abstract", "related_work", "discussion"],
        "structure_type": "thesis_or_report",
        "focus": ["chapter_structure", "theoretical_basis", "system_or_method", "experiments", "summary"],
    },
    "generic_research": {
        "required": ["abstract", "introduction", "method", "experiments", "conclusion"],
        "optional": ["related_work", "discussion"],
        "structure_type": "generic_research_paper",
        "focus": ["research_problem", "method", "experiments", "conclusion"],
    },
}

SECTION_ALIASES = {
    "abstract": ["Abstract", "摘要", "内容摘要", "中文摘要"],
    "introduction": ["Introduction", "引言", "绪论", "前言", "研究背景"],
    "related_work": ["Related Work", "Literature Review", "相关工作", "研究现状", "文献综述", "理论基础"],
    "method": ["Method", "Methodology", "Approach", "材料与方法", "研究方法", "模型构建", "算法设计", "系统设计"],
    "experiments": ["Experiments", "Experimental Results", "Evaluation", "实验", "结果与分析", "对比实验", "系统测试"],
    "discussion": ["Discussion", "Limitations", "讨论", "分析与讨论", "挑战", "展望"],
    "conclusion": ["Conclusion", "Future Work", "结论", "总结", "总结与展望", "结论与展望"],
}

ALLOWED_PAPER_TYPES = set(PAPER_TYPE_SECTIONS)
ALLOWED_SECTION_NAMES = {
    "abstract",
    "introduction",
    "related_work",
    "method",
    "experiments",
    "discussion",
    "conclusion",
}


def _contains_chinese(text: str) -> bool:
    """Return whether text contains Chinese characters."""
    return bool(re.search(r"[\u4e00-\u9fff]", text or ""))


def _normalize_text(text: str) -> str:
    """Normalize text for simple keyword matching."""
    return re.sub(r"\s+", " ", text or "").strip()


def _safe_int(value: object) -> int | None:
    """Convert a value to int when possible."""
    try:
        if value in {None, ""}:
            return None
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _safe_float(value: object, default: float = 0.0) -> float:
    """Convert a value to float with a default."""
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _get_parser_meta(parsed_paper: dict[str, Any]) -> dict[str, Any]:
    """Return parser_meta from ParsedPaper dict."""
    parser_meta = parsed_paper.get("parser_meta", {}) if isinstance(parsed_paper, dict) else {}
    return parser_meta if isinstance(parser_meta, dict) else {}


def _extract_section_titles_from_parsed(parsed_paper: dict[str, Any]) -> list[str]:
    """Extract section titles already found by parser adapters."""
    titles: list[str] = []
    sections = parsed_paper.get("sections", []) if isinstance(parsed_paper, dict) else []
    if isinstance(sections, list):
        for section in sections:
            if isinstance(section, dict):
                title = str(section.get("title", "") or "").strip()
                if title:
                    titles.append(title)

    parser_meta = _get_parser_meta(parsed_paper)
    meta_titles = parser_meta.get("section_titles", [])
    if isinstance(meta_titles, list):
        titles.extend(str(title).strip() for title in meta_titles if str(title).strip())
    return _dedupe_keep_order(titles)


def _extract_heading_candidates(raw_text: str, limit: int = 40) -> list[str]:
    """Extract early heading-like lines from raw_text without deep parsing."""
    heading_pattern = re.compile(
        r"^\s*(?:"
        r"(?:\d+(?:[.．]\d+)*(?:[.．、])?|[IVXLCDM]+\.?|[一二三四五六七八九十百]+[、.．]|第[一二三四五六七八九十百\d]+[章节])\s*)?"
        r"([A-Za-z][A-Za-z \-]{2,80}|[\u4e00-\u9fff][\u4e00-\u9fffA-Za-z0-9、与及和\-—— ]{1,50})"
        r"\s*$",
        re.IGNORECASE,
    )
    titles: list[str] = []
    for line in (raw_text or "")[:60000].splitlines():
        cleaned = _normalize_text(line)
        if not cleaned or cleaned.startswith("--- Page"):
            continue
        if len(cleaned) > 120 or re.search(r"[。！？]", cleaned):
            continue
        match = heading_pattern.match(cleaned)
        if match:
            titles.append(cleaned)
        if len(titles) >= limit:
            break
    return _dedupe_keep_order(titles)


def _dedupe_keep_order(values: list[str]) -> list[str]:
    """Deduplicate strings while preserving order."""
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        key = value.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(value)
    return result


def _score_paper_types(
    section_titles: list[str],
    text_preview: str,
    parsed_paper: dict[str, Any],
    *,
    raw_text_length: int | None = None,
    page_count: int | None = None,
) -> tuple[str, float, dict[str, int]]:
    """Classify paper type with transparent keyword scores."""
    combined = "\n".join(
        section_titles
        + [
            text_preview[:8000],
            str(parsed_paper.get("title", "")),
            str(parsed_paper.get("abstract", "")),
        ]
    )
    lower = combined.lower()
    scores = {
        "algorithm_paper": 0,
        "experimental_research": 0,
        "engineering_system": 0,
        "review": 0,
        "thesis_or_report": 0,
        "generic_research": 1,
    }

    if re.search(r"综述|研究进展|现状|挑战|展望|\breview\b|\bsurvey\b", combined, re.IGNORECASE):
        scores["review"] += 4
    if re.search(r"材料与方法|实验材料与方法|结果与分析|分析与讨论|讨论", combined):
        scores["experimental_research"] += 5
    if re.search(r"系统设计|系统实现|需求分析|系统测试|功能测试|平台|软件系统", combined):
        scores["engineering_system"] += 5
    if re.search(r"related work|method|methodology|experiments|ablation|evaluation|dataset|architecture", lower):
        scores["algorithm_paper"] += 5
    if re.search(r"目标检测|语义分割|深度学习|神经网络|transformer|yolo|llm|model|algorithm", lower):
        scores["algorithm_paper"] += 3
    if re.search(r"第一章|第二章|第三章|第四章|第五章|绪论|理论基础", combined):
        scores["thesis_or_report"] += 4

    parser_meta = _get_parser_meta(parsed_paper)
    page_count = page_count if page_count is not None else _safe_int(parser_meta.get("page_count"))
    length_signal = raw_text_length if raw_text_length is not None else len(text_preview or "")
    if length_signal > 120000 or (page_count is not None and page_count > 50):
        scores["thesis_or_report"] += 2

    paper_type = max(scores, key=lambda key: scores[key])
    best_score = scores[paper_type]
    total_signal = max(sum(score for key, score in scores.items() if key != "generic_research"), 1)
    confidence = 0.45 if paper_type == "generic_research" else min(0.95, 0.55 + best_score / (total_signal + 4))
    return paper_type, round(confidence, 2), scores


def _document_size_strategy_from_length(raw_text_length: int, page_count: int | None) -> dict[str, object]:
    """Plan for large document handling without implementing chunk/RAG."""
    if raw_text_length >= 120000 or (page_count is not None and page_count > 50):
        size_level = "huge"
    elif raw_text_length >= 40000:
        size_level = "large"
    else:
        size_level = "normal"

    use_chunking = size_level in {"large", "huge"}
    return {
        "size_level": size_level,
        "raw_text_length": raw_text_length,
        "page_count": page_count,
        "use_chunking": use_chunking,
        "use_rag": use_chunking,
        "reason": "Large document strategy is planned only; chunking/RAG is not implemented in this round."
        if use_chunking
        else "Document size is normal; current direct workflow is acceptable.",
    }


def _has_english_title_signal(title: str) -> bool:
    """Return whether a title looks like a strong English-title lookup signal."""
    return bool(title and not _contains_chinese(title) and re.search(r"[A-Za-z]{8,}", title))


def _extract_identifier_flags(parser_meta: dict[str, Any], raw_text_preview: str) -> tuple[bool, bool]:
    """Detect DOI and arXiv identifiers from metadata or preview text."""
    has_doi = bool(parser_meta.get("doi")) or bool(
        re.search(r"\b10\.\d{4,9}/[-._;()/:A-Z0-9]+\b", raw_text_preview[:5000], re.IGNORECASE)
    )
    has_arxiv = bool(parser_meta.get("arxiv_id")) or bool(
        re.search(r"\b\d{4}\.\d{4,5}(?:v\d+)?\b", raw_text_preview[:5000])
    )
    return has_doi, has_arxiv


def build_plan_evidence(state: PaperState) -> dict[str, object]:
    """Build bounded planning evidence without exposing full raw_text."""
    config = get_plan_agent_config()
    paper_language = state.get("paper_language", "zh")
    parser_name = state.get("parser_name", "")
    raw_text = state.get("raw_text", "") or ""
    raw_text_length = len(raw_text)
    raw_text_preview = raw_text[: max(config.max_input_chars, 0)]
    parsed_paper = state.get("parsed_paper", {}) or {}
    parser_meta = _get_parser_meta(parsed_paper)
    page_count = _safe_int(parser_meta.get("page_count"))

    parsed_titles = _extract_section_titles_from_parsed(parsed_paper)
    raw_heading_titles = _extract_heading_candidates(raw_text)
    section_titles = _dedupe_keep_order(parsed_titles + raw_heading_titles)[:50]

    candidate_title = str(
        parsed_paper.get("title", "")
        or parser_meta.get("candidate_title", "")
        or state.get("title", "")
        or ""
    ).strip()
    candidate_abstract = str(
        parsed_paper.get("abstract", "")
        or parser_meta.get("candidate_abstract", "")
        or state.get("abstract", "")
        or ""
    ).strip()[:1500]
    first_page_text = str(parser_meta.get("first_page_text", "") or "")[:2000]
    has_doi, has_arxiv = _extract_identifier_flags(parser_meta, raw_text_preview)

    score_parsed = {
        "title": candidate_title,
        "abstract": candidate_abstract,
        "parser_meta": {"page_count": page_count},
    }
    _, _, type_scores = _score_paper_types(
        section_titles,
        raw_text_preview,
        score_parsed,
        raw_text_length=raw_text_length,
        page_count=page_count,
    )
    max_score = max(type_scores.values()) or 1
    rule_signals = {key: round(value / max_score, 3) for key, value in type_scores.items()}

    return {
        "paper_language": paper_language,
        "parser_name": parser_name,
        "raw_text_length": raw_text_length,
        "raw_text_preview": raw_text_preview,
        "page_count": page_count,
        "section_titles": section_titles,
        "candidate_title": candidate_title,
        "candidate_abstract": candidate_abstract,
        "first_page_text": first_page_text,
        "parser_warnings": state.get("parser_warnings", []) or [],
        "has_doi": has_doi,
        "has_arxiv_id": has_arxiv,
        "has_english_title_signal": _has_english_title_signal(candidate_title),
        "rule_signals": rule_signals,
    }


def _metadata_strategy_from_evidence(evidence: dict[str, object]) -> dict[str, object]:
    """Recommend metadata enrichment strategy without changing paper_info_node behavior."""
    paper_language = str(evidence.get("paper_language", "zh") or "zh")
    has_doi = bool(evidence.get("has_doi"))
    has_arxiv = bool(evidence.get("has_arxiv_id"))
    has_english_title = bool(evidence.get("has_english_title_signal"))
    title = str(evidence.get("candidate_title", "") or "")

    if paper_language == "zh" and not (has_doi or has_arxiv or has_english_title):
        return {
            "use_external_lookup": False,
            "allowed_tools": [],
            "reason": "Chinese paper without DOI/arXiv ID/English title",
        }
    if paper_language == "en" and title:
        return {
            "use_external_lookup": True,
            "allowed_tools": ["arxiv", "crossref", "openalex"],
            "reason": "English paper has a title; external metadata lookup may help fill venue/year",
        }
    return {
        "use_external_lookup": bool(has_doi or has_arxiv),
        "allowed_tools": ["arxiv", "crossref", "openalex"] if has_arxiv else ["crossref", "openalex"] if has_doi else [],
        "reason": "External lookup is recommended only when strong identifiers are present",
    }


def build_rule_plan(evidence: dict[str, object]) -> dict[str, object]:
    """Build the deterministic rule plan from bounded evidence."""
    paper_language = str(evidence.get("paper_language", "zh") or "zh")
    raw_text_preview = str(evidence.get("raw_text_preview", "") or "")
    section_titles = [str(title) for title in evidence.get("section_titles", []) if str(title).strip()]  # type: ignore[union-attr]
    raw_text_length = int(evidence.get("raw_text_length", 0) or 0)
    page_count = _safe_int(evidence.get("page_count"))
    parsed_paper = {
        "title": evidence.get("candidate_title", ""),
        "abstract": evidence.get("candidate_abstract", ""),
        "parser_meta": {"page_count": page_count},
    }

    paper_type, confidence, _ = _score_paper_types(
        section_titles,
        raw_text_preview,
        parsed_paper,
        raw_text_length=raw_text_length,
        page_count=page_count,
    )
    type_config = PAPER_TYPE_SECTIONS.get(paper_type, PAPER_TYPE_SECTIONS["generic_research"])
    repair_config = get_section_repair_config()

    analysis_plan = {
        "paper_language": paper_language,
        "paper_type": paper_type,
        "structure_type": type_config["structure_type"],
        "confidence": confidence,
        "metadata_strategy": _metadata_strategy_from_evidence(evidence),
        "section_strategy": {
            "preserve_original_sections": True,
            "use_canonical_mapping": True,
            "required_sections": list(type_config["required"]),
            "optional_sections": list(type_config["optional"]),
            "section_aliases": SECTION_ALIASES,
        },
        "repair_strategy": {
            "verify_sections": True,
            "repair_if_needed": True,
            "llm_repair_enabled": repair_config.llm_enabled,
            "max_repair_rounds": repair_config.max_rounds,
        },
        "document_size_strategy": _document_size_strategy_from_length(raw_text_length, page_count),
        "analysis_focus": list(type_config["focus"]),
        "risks": [],
    }
    if analysis_plan["document_size_strategy"]["size_level"] in {"large", "huge"}:  # type: ignore[index]
        analysis_plan["risks"].append("large_document_truncation_risk")  # type: ignore[union-attr]
    if confidence < 0.65:
        analysis_plan["risks"].append("weak_paper_type_signal")  # type: ignore[union-attr]
    return analysis_plan


def _build_decisions(
    paper_type: str,
    confidence: float,
    section_titles: list[str],
    document_strategy: dict[str, object],
) -> list[dict[str, object]]:
    """Build agent decision records."""
    decisions = [
        {
            "node": "plan_agent_node",
            "decision": f"classified_as_{paper_type}",
            "reason": f"Detected section titles: {', '.join(section_titles[:8])}" if section_titles else "No strong section titles detected; used bounded text keyword signals",
            "confidence": confidence,
        }
    ]
    if document_strategy.get("size_level") in {"large", "huge"}:
        decisions.append(
            {
                "node": "plan_agent_node",
                "decision": "enable_large_pdf_strategy",
                "reason": f"raw_text_length={document_strategy.get('raw_text_length')}, page_count={document_strategy.get('page_count')}",
                "confidence": 1.0,
            }
        )
    return decisions


def _evidence_summary(evidence: dict[str, object]) -> dict[str, object]:
    """Build a compact evidence summary for debug output."""
    section_titles = evidence.get("section_titles", [])
    return {
        "raw_text_length": evidence.get("raw_text_length", 0),
        "section_title_count": len(section_titles) if isinstance(section_titles, list) else 0,
        "paper_language": evidence.get("paper_language", ""),
        "parser_name": evidence.get("parser_name", ""),
        "page_count": evidence.get("page_count"),
        "has_doi": evidence.get("has_doi", False),
        "has_arxiv_id": evidence.get("has_arxiv_id", False),
        "has_english_title_signal": evidence.get("has_english_title_signal", False),
    }


def _sanitize_sections(values: object) -> list[str]:
    """Keep only valid canonical section names."""
    if not isinstance(values, list):
        return []
    cleaned: list[str] = []
    for value in values:
        section = str(value).strip()
        if section in ALLOWED_SECTION_NAMES and section not in cleaned:
            cleaned.append(section)
    return cleaned


def _sanitize_document_strategy(evidence: dict[str, object]) -> dict[str, object]:
    """Recompute document-size strategy from trusted evidence."""
    return _document_size_strategy_from_length(
        int(evidence.get("raw_text_length", 0) or 0),
        _safe_int(evidence.get("page_count")),
    )


def call_llm_planner(evidence: dict[str, object], rule_plan: dict[str, object]) -> dict[str, object]:
    """Call the optional LLM planner with bounded evidence and rule_plan."""
    safe_evidence = {
        "paper_language": evidence.get("paper_language", ""),
        "parser_name": evidence.get("parser_name", ""),
        "raw_text_length": evidence.get("raw_text_length", 0),
        "raw_text_preview": str(evidence.get("raw_text_preview", "") or "")[: get_plan_agent_config().max_input_chars],
        "page_count": evidence.get("page_count"),
        "section_titles": (evidence.get("section_titles", []) or [])[:50] if isinstance(evidence.get("section_titles", []), list) else [],
        "candidate_title": evidence.get("candidate_title", ""),
        "candidate_abstract": str(evidence.get("candidate_abstract", "") or "")[:1500],
        "first_page_text": str(evidence.get("first_page_text", "") or "")[:2000],
        "parser_warnings": evidence.get("parser_warnings", []),
        "has_doi": evidence.get("has_doi", False),
        "has_arxiv_id": evidence.get("has_arxiv_id", False),
        "has_english_title_signal": evidence.get("has_english_title_signal", False),
        "rule_signals": evidence.get("rule_signals", {}),
    }
    system_prompt = (
        "你是论文解析 Plan Agent。你的任务不是分析论文内容，而是根据给定 evidence 和 rule_plan，"
        "生成受控的 analysis_plan。必须只输出 JSON，不要输出 Markdown。你不能调用工具，不能输出代码，"
        "不能决定或改变 LangGraph 路由。"
    )
    user_prompt = f"""请根据 evidence 和 rule_plan 输出受控 JSON 计划。

允许的 paper_type:
- algorithm_paper
- experimental_research
- engineering_system
- review
- thesis_or_report
- generic_research

允许的 section 名：
- abstract
- introduction
- related_work
- method
- experiments
- discussion
- conclusion

输出 JSON schema：
{{
  "paper_type": "algorithm_paper | experimental_research | engineering_system | review | thesis_or_report | generic_research",
  "structure_type": "",
  "confidence": 0.0,
  "required_sections": [],
  "optional_sections": [],
  "metadata_strategy": {{
    "use_external_lookup": false,
    "allowed_tools": [],
    "reason": ""
  }},
  "section_strategy": {{
    "preserve_original_sections": true,
    "use_canonical_mapping": true,
    "section_aliases": {{}}
  }},
  "repair_strategy": {{
    "verify_sections": true,
    "repair_if_needed": true,
    "llm_repair_enabled": false,
    "max_repair_rounds": 1
  }},
  "document_size_strategy": {{
    "size_level": "normal | large | huge",
    "use_chunking": false,
    "use_rag": false,
    "reason": ""
  }},
  "analysis_focus": [],
  "risks": [],
  "reason": ""
}}

规则：
1. 只能从允许的 paper_type 中选择。
2. required_sections / optional_sections 只能使用合法 section 名。
3. required_sections 和 optional_sections 不要重复。
4. 不要编造不存在的章节标题。
5. 如果无法判断，使用 generic_research。
6. 如果是 review，不要把 experiments 设为 required。
7. 如果是中文论文且没有 DOI / arXiv ID / 明确英文标题，不建议 external_lookup。
8. 如果 document 很大，只能建议 use_chunking/use_rag=true，但不要执行。
9. 不要要求改变 graph 路由。
10. 不要生成最终论文分析。
11. 不要总结论文内容。

evidence:
{json.dumps(safe_evidence, ensure_ascii=False)}

rule_plan:
{json.dumps(rule_plan, ensure_ascii=False)}
"""
    return call_llm_json(system_prompt, user_prompt)


def validate_and_merge_plan(
    evidence: dict[str, object],
    rule_plan: dict[str, object],
    llm_plan: dict[str, object],
) -> tuple[dict[str, object], list[str], bool]:
    """Validate LLM planner output and merge it with trusted rule_plan."""
    warnings: list[str] = []
    config = get_plan_agent_config()
    if not isinstance(llm_plan, dict):
        return deepcopy(rule_plan), ["LLM plan is not a JSON object; fallback to rule_plan"], False

    llm_confidence = _safe_float(llm_plan.get("confidence"), 0.0)
    if llm_confidence < config.confidence_threshold:
        warnings.append(
            f"LLM confidence {llm_confidence:.2f} is below threshold {config.confidence_threshold:.2f}; fallback to rule_plan"
        )
        return deepcopy(rule_plan), warnings, False

    validated = deepcopy(rule_plan)
    requested_type = str(llm_plan.get("paper_type", "") or "")
    if requested_type not in ALLOWED_PAPER_TYPES:
        warnings.append(f"Invalid paper_type from LLM: {requested_type}; kept rule_plan paper_type")
        requested_type = str(rule_plan.get("paper_type", "generic_research"))

    type_config = PAPER_TYPE_SECTIONS.get(requested_type, PAPER_TYPE_SECTIONS["generic_research"])
    required_sections = _sanitize_sections(llm_plan.get("required_sections"))
    optional_sections = _sanitize_sections(llm_plan.get("optional_sections"))
    if not required_sections:
        required_sections = list(type_config["required"])
        warnings.append("LLM required_sections missing or invalid; filled from paper_type defaults")
    optional_sections = [section for section in optional_sections if section not in required_sections]
    if not optional_sections:
        optional_sections = [section for section in type_config["optional"] if section not in required_sections]

    validated["paper_type"] = requested_type
    validated["structure_type"] = str(llm_plan.get("structure_type") or type_config["structure_type"])
    validated["confidence"] = llm_confidence
    validated["analysis_focus"] = llm_plan.get("analysis_focus") if isinstance(llm_plan.get("analysis_focus"), list) else list(type_config["focus"])
    validated["risks"] = llm_plan.get("risks") if isinstance(llm_plan.get("risks"), list) else list(rule_plan.get("risks", []))

    metadata_strategy = deepcopy(rule_plan.get("metadata_strategy", {}))
    if isinstance(llm_plan.get("metadata_strategy"), dict):
        metadata_strategy.update(llm_plan["metadata_strategy"])  # type: ignore[index]
    if (
        str(evidence.get("paper_language", "zh")) == "zh"
        and not evidence.get("has_doi")
        and not evidence.get("has_arxiv_id")
        and not evidence.get("has_english_title_signal")
    ):
        metadata_strategy["use_external_lookup"] = False
        metadata_strategy["allowed_tools"] = []
        warnings.append("Forced metadata_strategy.use_external_lookup=false for Chinese paper without DOI/arXiv ID/English title")
    validated["metadata_strategy"] = metadata_strategy

    llm_section_strategy = llm_plan.get("section_strategy") if isinstance(llm_plan.get("section_strategy"), dict) else {}
    validated["section_strategy"] = {
        "preserve_original_sections": bool(llm_section_strategy.get("preserve_original_sections", True)),
        "use_canonical_mapping": bool(llm_section_strategy.get("use_canonical_mapping", True)),
        "required_sections": required_sections,
        "optional_sections": optional_sections,
        "section_aliases": SECTION_ALIASES,
    }

    repair_config = get_section_repair_config()
    llm_repair_strategy = llm_plan.get("repair_strategy") if isinstance(llm_plan.get("repair_strategy"), dict) else {}
    llm_repair_requested = bool(llm_repair_strategy.get("llm_repair_enabled", False))
    if llm_repair_requested and not repair_config.llm_enabled:
        warnings.append("LLM requested repair_strategy.llm_repair_enabled=true, but SECTION_REPAIR_LLM_ENABLED=false")
    max_rounds = _safe_int(llm_repair_strategy.get("max_repair_rounds")) or repair_config.max_rounds
    validated["repair_strategy"] = {
        "verify_sections": bool(llm_repair_strategy.get("verify_sections", True)),
        "repair_if_needed": bool(llm_repair_strategy.get("repair_if_needed", True)),
        "llm_repair_enabled": bool(llm_repair_requested and repair_config.llm_enabled),
        "max_repair_rounds": min(max_rounds, repair_config.max_rounds),
    }

    validated["document_size_strategy"] = _sanitize_document_strategy(evidence)
    return validated, warnings, True


def _make_plan_debug(
    *,
    planner_mode: str,
    llm_enabled: bool,
    evidence: dict[str, object],
    rule_plan: dict[str, object],
    llm_plan: dict[str, object] | None,
    validated_plan: dict[str, object],
    validation_warnings: list[str],
) -> dict[str, object]:
    """Create compact debug output without full raw_text."""
    return {
        "rule_version": "plan_agent_rule_v2",
        "planner_mode": planner_mode,
        "llm_enabled": llm_enabled,
        "llm_called": llm_enabled and planner_mode != "rule_only",
        "rule_plan": rule_plan,
        "llm_plan": llm_plan or {},
        "validated_plan": validated_plan,
        "validation_warnings": validation_warnings,
        "evidence_summary": _evidence_summary(evidence),
    }


def _plan_state_update(
    state: PaperState,
    analysis_plan: dict[str, object],
    plan_debug: dict[str, object],
    decisions: list[dict[str, object]],
) -> dict[str, object]:
    """Build the state update for downstream nodes."""
    section_strategy = analysis_plan.get("section_strategy", {}) if isinstance(analysis_plan.get("section_strategy"), dict) else {}
    required_sections = _sanitize_sections(section_strategy.get("required_sections"))
    optional_sections = _sanitize_sections(section_strategy.get("optional_sections"))
    paper_type = str(analysis_plan.get("paper_type", "generic_research") or "generic_research")
    structure_type = str(analysis_plan.get("structure_type", "") or "")
    existing_decisions = state.get("agent_decisions", []) or []
    return {
        "analysis_plan": analysis_plan,
        "paper_type": paper_type,
        "structure_type": structure_type,
        "required_sections": required_sections,
        "optional_sections": optional_sections,
        "plan_debug": plan_debug,
        "agent_decisions": [*existing_decisions, *decisions],
    }


def finalize_rule_plan(rule_plan: dict[str, object], evidence: dict[str, object], state: PaperState) -> dict[str, object]:
    """Finalize a rule-only plan."""
    document_strategy = rule_plan.get("document_size_strategy", {}) if isinstance(rule_plan.get("document_size_strategy"), dict) else {}
    section_titles = [str(title) for title in evidence.get("section_titles", []) if str(title).strip()]  # type: ignore[union-attr]
    confidence = _safe_float(rule_plan.get("confidence"), 0.45)
    decisions = _build_decisions(str(rule_plan.get("paper_type", "generic_research")), confidence, section_titles, document_strategy)
    decisions.append(
        {
            "node": "plan_agent_node",
            "decision": "used_rule_plan",
            "reason": "PLAN_AGENT_LLM_ENABLED=false",
            "confidence": 1.0,
        }
    )
    plan_debug = _make_plan_debug(
        planner_mode="rule_only",
        llm_enabled=False,
        evidence=evidence,
        rule_plan=rule_plan,
        llm_plan=None,
        validated_plan=rule_plan,
        validation_warnings=[],
    )
    return _plan_state_update(state, rule_plan, plan_debug, decisions)


def plan_agent_node(state: PaperState) -> dict[str, object]:
    """Generate a controlled analysis plan without changing graph routing."""
    config = get_plan_agent_config()
    evidence = build_plan_evidence(state)
    rule_plan = build_rule_plan(evidence)

    if not config.llm_enabled:
        return finalize_rule_plan(rule_plan, evidence, state)

    llm_plan: dict[str, object] | None = None
    validation_warnings: list[str] = []
    try:
        llm_plan = call_llm_planner(evidence, rule_plan)
        validated_plan, validation_warnings, accepted = validate_and_merge_plan(evidence, rule_plan, llm_plan)
        planner_mode = "llm_accepted" if accepted else "llm_rejected"
    except Exception as exc:  # noqa: BLE001 - planner failures must not break the paper workflow.
        validated_plan = deepcopy(rule_plan)
        validation_warnings = [f"LLM planner failed or returned invalid JSON: {exc}"]
        planner_mode = "llm_failed"

    confidence = _safe_float((llm_plan or {}).get("confidence"), _safe_float(rule_plan.get("confidence"), 0.45))
    if planner_mode == "llm_accepted":
        decision = {
            "node": "plan_agent_node",
            "decision": "accepted_llm_plan",
            "reason": "LLM planner confidence >= threshold and validation passed",
            "confidence": confidence,
        }
    elif planner_mode == "llm_rejected":
        decision = {
            "node": "plan_agent_node",
            "decision": "rejected_llm_plan",
            "reason": "LLM confidence below threshold or validation failed; fallback to rule_plan",
            "confidence": confidence,
        }
    else:
        decision = {
            "node": "plan_agent_node",
            "decision": "fallback_to_rule_plan",
            "reason": "LLM planner failed or returned invalid JSON",
            "confidence": 1.0,
        }

    section_titles = [str(title) for title in evidence.get("section_titles", []) if str(title).strip()]  # type: ignore[union-attr]
    decisions = _build_decisions(
        str(validated_plan.get("paper_type", "generic_research")),
        _safe_float(validated_plan.get("confidence"), 0.45),
        section_titles,
        validated_plan.get("document_size_strategy", {}) if isinstance(validated_plan.get("document_size_strategy"), dict) else {},
    )
    decisions.append(decision)
    plan_debug = _make_plan_debug(
        planner_mode=planner_mode,
        llm_enabled=True,
        evidence=evidence,
        rule_plan=rule_plan,
        llm_plan=llm_plan,
        validated_plan=validated_plan,
        validation_warnings=validation_warnings,
    )
    return _plan_state_update(state, validated_plan, plan_debug, decisions)
