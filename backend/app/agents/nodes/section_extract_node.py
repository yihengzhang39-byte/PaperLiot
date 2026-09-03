"""Rule-first section extraction node with optional LLM fallback."""

import re
from typing import TypedDict

from app.agents.paper_state import PaperState
from app.services.llm_service import extract_sections_with_llm


class HeadingMatch(TypedDict):
    """A detected section heading and its position in raw text."""

    section: str
    title: str
    start: int
    end: int
    source: str
    confidence: float


SectionMeta = dict[str, object]

"""
    规则提取的标题
"""
ZH_SECTION_ALIASES = {
    "abstract": ["摘要", "摘 要", "内容摘要", "中文摘要"],
    "keywords": ["关键词", "关键 词", "关键字"],
    "introduction": ["引言", "绪论", "前言", "研究背景", "课题背景", "问题提出"],
    "related_work": ["相关工作", "国内外研究现状", "研究现状", "文献综述", "理论基础", "背景与相关工作", "相关研究"],
    "method": [
        "方法",
        "方法设计",
        "研究方法",
        "材料与方法",
        "实验材料与方法",
        "模型与方法",
        "算法设计",
        "算法原理",
        "模型构建",
        "模型设计",
        "网络结构",
        "系统设计",
        "方案设计",
        "技术路线",
        "本文方法",
        "方法实现",
    ],
    "experiments": [
        "实验",
        "实验设计",
        "实验设置",
        "实验过程",
        "实验结果",
        "结果",
        "结果与分析",
        "实验结果与分析",
        "性能分析",
        "对比实验",
        "消融实验",
        "评价指标",
        "数据集",
        "模型训练",
        "测试结果",
        "验证实验",
        "应用验证",
    ],
    "discussion": ["讨论", "分析与讨论", "结果讨论", "误差分析", "局限性分析"],
    "conclusion": ["结论", "结语", "总结", "结束语", "结论与展望", "总结与展望", "研究结论", "展望"],
    "_references": ["参考文献", "文献"],
    "_acknowledgements": ["致谢", "鸣谢"],
    "_appendix": ["附录"],
}

EN_SECTION_ALIASES = {
    "abstract": ["abstract"],
    "keywords": ["keywords", "index terms"],
    "introduction": ["introduction", "background"],
    "related_work": [
        "related work",
        "related works",
        "literature review",
        "background and related work",
        "prior work",
    ],
    "method": [
        "method",
        "methods",
        "methodology",
        "proposed method",
        "approach",
        "our approach",
        "model",
        "framework",
        "architecture",
        "system design",
        "network architecture",
    ],
    "experiments": [
        "experiments",
        "experiment",
        "experimental setup",
        "experimental results",
        "results",
        "results and analysis",
        "evaluation",
        "ablation study",
        "implementation details",
        "dataset",
        "datasets",
    ],
    "discussion": ["discussion", "analysis", "limitations"],
    "conclusion": ["conclusion", "conclusions", "future work", "conclusion and future work"],
    "_references": ["references", "bibliography"],
    "_acknowledgements": ["acknowledgements", "acknowledgments"],
    "_appendix": ["appendix", "supplementary material"],
}

BOUNDARY_SECTIONS = {"keywords", "discussion", "_references", "_acknowledgements", "_appendix"}

"""    需要重点关注的标题"""
SECTION_KEYS = [
    "abstract",
    "introduction",
    "related_work",
    "method",
    "experiments",
    "conclusion",
]

"""
    这些标题长度过短，LLM修正可能会有帮助
"""
SHORT_SECTION_KEYS = {
    "introduction",
    "related_work",
    "method",
    "experiments",
    "conclusion",
}
MIN_SECTION_LENGTH = 150

"""
    初始化SECTION_KEYS
"""
def _empty_sections() -> dict[str, str]:
    """Return an empty section mapping."""
    return {key: "" for key in SECTION_KEYS}

"""
    初始化SECTION_KEYS的metadata
"""
def _empty_section_meta() -> dict[str, SectionMeta]:
    """Return default metadata for all tracked sections."""
    return {
        key: {
            "source": "missing",
            "matched_title": "",
            "normalized_section": key,
            "length": 0,
            "start_char": -1,
            "end_char": -1,
            "confidence": 0.0,
            "warning": "section not found",
        }
        for key in SECTION_KEYS
    }

"""
    正则表达式匹配标题，支持中英文常见编号
"""
def _heading_pattern(alias: str, source: str) -> re.Pattern[str]:
    """Build a heading regex that supports Arabic, Roman, and Chinese numbering."""
    escaped_alias = re.escape(alias).replace(r"\ ", r"\s+")
    flags = re.MULTILINE if source == "rule_zh" else re.IGNORECASE | re.MULTILINE
    number_prefix = (
        r"(?:"
        r"\d+(?:[.．]\d+)*(?:[.．、])?"
        r"|[IVXLCDM]+\.?"
        r"|[一二三四五六七八九十百]+[、.．]"
        r"|第[一二三四五六七八九十百\d]+[章节]"
        r"|[（(][一二三四五六七八九十百\d]+[）)]"
        r"|[一二三四五六七八九十百\d]+[）)]"
        r")"
    )
    return re.compile(
        rf"^[ \t　]*"
        rf"(?:{number_prefix}[ \t　]*)?"
        rf"{escaped_alias}"
        rf"[ \t　]*(?:[:：.。．\-])?[ \t　]*$",
        flags,
    )


def _heading_confidence(title: str, section: str, source: str) -> float:
    """Return a conservative confidence score for a heading line."""
    cleaned = re.sub(r"\s+", "", title or "")
    score = 0.9 if source == "rule_zh" else 0.88
    if section in {"abstract", "keywords", "_references", "_acknowledgements", "_appendix"}:
        score += 0.03
    if re.match(r"^\s*(?:\d+|[一二三四五六七八九十百]+|第)", title):
        score += 0.04
    if len(cleaned) > 60 and source == "rule_zh":
        score -= 0.25
    if len(cleaned) > 120 and source == "rule_en":
        score -= 0.25
    if title.strip().endswith(("。", "！", "？", ".", "!", "?")) and section not in {"abstract", "keywords"}:
        score -= 0.08
    return round(max(0.0, min(1.0, score)), 2)


def _is_plausible_heading(title: str, source: str) -> bool:
    """Avoid matching ordinary body sentences as headings."""
    cleaned = re.sub(r"\s+", "", title or "")
    if not cleaned:
        return False
    if source == "rule_zh" and len(cleaned) > 80:
        return False
    if source == "rule_en" and len(cleaned) > 140:
        return False
    if re.search(r"[。！？]", cleaned) and len(cleaned) > 12:
        return False
    if len(re.findall(r"[，,；;]", cleaned)) >= 2:
        return False
    return True


"""
    找到匹配的标题，记录标题对应的section和在文本中的位置
    对找到的标题进行排序，按照在文本中出现的先后顺序
"""
def _find_headings(raw_text: str) -> list[HeadingMatch]:
    """Find all supported section headings in raw paper text."""
    matches: list[HeadingMatch] = []
    for source, alias_map in [("rule_zh", ZH_SECTION_ALIASES), ("rule_en", EN_SECTION_ALIASES)]:
        for section, aliases in alias_map.items():
            for alias in sorted(aliases, key=len, reverse=True):
                for match in _heading_pattern(alias, source).finditer(raw_text):
                    title = match.group(0).strip()
                    if not _is_plausible_heading(title, source):
                        continue
                    matches.append(
                        {
                            "section": section,
                            "title": title,
                            "start": match.start(),
                            "end": match.end(),
                            "source": source,
                            "confidence": _heading_confidence(title, section, source),
                        }
                    )

    matches.sort(key=lambda item: (item["start"], item["end"]))
    return _dedupe_headings(matches)


def _dedupe_headings(matches: list[HeadingMatch]) -> list[HeadingMatch]:
    """Remove duplicate matches that point to the same heading line."""
    deduped: list[HeadingMatch] = []
    seen_positions: set[tuple[int, int]] = set()
    for match in matches:
        position = (match["start"], match["end"])
        if position in seen_positions:
            continue
        seen_positions.add(position)
        deduped.append(match)
    return deduped


def _new_rule_meta(heading: HeadingMatch, content: str, end_char: int) -> SectionMeta:
    """Build metadata for a rule-extracted section."""
    warning = ""
    if _is_obviously_short(heading["section"], content):
        warning = "rule section is very short; LLM correction may be useful"
    return {
        "source": heading.get("source", "rule"),
        "matched_title": heading["title"],
        "normalized_section": heading["section"],
        "length": len(content),
        "start_char": heading["end"],
        "end_char": end_char,
        "confidence": heading.get("confidence", 0.0),
        "warning": warning,
    }


def _new_inline_meta(
    source: str,
    matched_title: str,
    normalized_section: str,
    content: str,
    start_char: int,
    end_char: int,
    confidence: float,
    warning: str = "",
) -> SectionMeta:
    """Build metadata for a rule-extracted inline section."""
    return {
        "source": source,
        "matched_title": matched_title,
        "normalized_section": normalized_section,
        "length": len(content),
        "start_char": start_char,
        "end_char": end_char,
        "confidence": confidence,
        "warning": warning,
    }


"""    如果规则提取的内容过短，LLM修正可能会有帮助
"""
def _is_obviously_short(section: str, content: str) -> bool:
    """Return whether a rule-extracted major section is too short to trust."""
    return section in SHORT_SECTION_KEYS and 0 < len(content.strip()) < MIN_SECTION_LENGTH


def _extract_zh_inline_abstract(raw_text: str) -> tuple[str, SectionMeta] | None:
    """Extract Chinese inline abstract text without mixing keywords/introduction."""
    abstract_start = re.search(r"(?m)^[ \t　]*(?P<title>摘\s*要|内容摘要|中文摘要)[ \t　]*[:：]?[ \t　]*", raw_text)
    if not abstract_start:
        return None

    content_start = abstract_start.end()
    boundary_pattern = re.compile(
        r"(?m)^[ \t　]*(?:"
        r"关\s*键\s*词|关键字|Abstract|ABSTRACT|"
        r"(?:(?:\d+(?:[.．]\d+)*(?:[.．、])?|[一二三四五六七八九十百]+[、.．]|第[一二三四五六七八九十百\d]+[章节])?[ \t　]*)"
        r"(?:引言|绪论|前言|研究背景|课题背景|问题提出)"
        r")[ \t　]*[:：.。．\-]?"
    )
    boundary = boundary_pattern.search(raw_text, content_start)
    content_end = boundary.start() if boundary else min(len(raw_text), content_start + 2000)
    content = raw_text[content_start:content_end].strip()

    warning = ""
    if len(content) < 30:
        warning = "abstract is very short; rule confidence is low"
    elif len(content) > 2000:
        content = content[:2000]
        content_end = content_start + 2000
        warning = "abstract is longer than 2000 chars and was truncated"

    if not content:
        return None

    confidence = 0.65 if len(content) < 30 else 0.95
    meta = _new_inline_meta(
        "rule_zh",
        abstract_start.group("title").strip(),
        "abstract",
        content,
        content_start,
        content_end,
        confidence,
        warning,
    )
    return content, meta


def extract_sections_by_rules_with_meta(
    raw_text: str,
) -> tuple[dict[str, str], dict[str, SectionMeta]]:
    """Extract paper sections and debugging metadata by heading rules."""
    sections = _empty_sections()
    section_meta = _empty_section_meta()
    if not raw_text.strip():
        return sections, section_meta

    headings = _find_headings(raw_text)
    inline_abstract = _extract_zh_inline_abstract(raw_text)
    if inline_abstract:
        sections["abstract"], section_meta["abstract"] = inline_abstract

    if not headings:
        return sections, section_meta

    for index, heading in enumerate(headings):
        section = heading["section"]
        if section in BOUNDARY_SECTIONS:
            continue

        next_start = headings[index + 1]["start"] if index + 1 < len(headings) else len(raw_text)
        content = raw_text[heading["end"] : next_start].strip()
        if not content:
            continue

        if sections[section]:
            sections[section] = f"{sections[section]}\n\n{content}"
            section_meta[section]["length"] = len(sections[section])
            section_meta[section]["end_char"] = next_start
        else:
            sections[section] = content
            section_meta[section] = _new_rule_meta(heading, content, next_start)

    return sections, section_meta


def extract_sections_by_rules(raw_text: str) -> dict[str, str]:
    """Extract paper sections by matching common section headings."""
    sections, _ = extract_sections_by_rules_with_meta(raw_text)
    return sections


def _locate_text(raw_text: str, content: str) -> tuple[int, int]:
    """Find a section content span in raw text when possible."""
    if not content:
        return -1, -1
    start = raw_text.find(content)
    if start == -1:
        compact_content = re.sub(r"\s+", " ", content).strip()
        compact_text = re.sub(r"\s+", " ", raw_text).strip()
        compact_start = compact_text.find(compact_content)
        if compact_start == -1:
            return -1, -1
        return -1, -1
    return start, start + len(content)


def _llm_meta(raw_text: str, content: str, section: str = "", warning: str = "") -> SectionMeta:
    """Build metadata for an LLM-filled or LLM-corrected section."""
    start, end = _locate_text(raw_text, content)
    return {
        "source": "llm",
        "matched_title": "",
        "normalized_section": section,
        "length": len(content),
        "start_char": start,
        "end_char": end,
        "confidence": 0.0,
        "warning": warning,
    }


def _missing_meta(warning: str = "section not found") -> SectionMeta:
    """Build metadata for a missing section."""
    return {
        "source": "missing",
        "matched_title": "",
        "normalized_section": "",
        "length": 0,
        "start_char": -1,
        "end_char": -1,
        "confidence": 0.0,
        "warning": warning,
    }


"""
    合并规则提取和LLM提取的结果，优先使用规则提取，除非规则提取的内容明显过短且LLM修正后更长
"""
def _merge_sections(
    raw_text: str,
    rule_sections: dict[str, str],
    rule_meta: dict[str, SectionMeta],
    llm_sections: dict[str, str],
    existing_abstract: str,
) -> tuple[dict[str, str], dict[str, SectionMeta]]:
    """Merge section results and metadata, preferring rules unless correction helps."""
    merged = _empty_sections()
    section_meta = _empty_section_meta()
    for key in SECTION_KEYS:
        rule_value = rule_sections.get(key, "").strip()
        llm_value = llm_sections.get(key, "").strip()

        if rule_value and _is_obviously_short(key, rule_value) and len(llm_value) > len(rule_value):
            merged[key] = llm_value
            section_meta[key] = _llm_meta(
                raw_text,
                llm_value,
                key,
                "rule section was very short and replaced by LLM correction",
            )
        elif rule_value:
            merged[key] = rule_value
            section_meta[key] = rule_meta.get(key, _missing_meta())
        elif llm_value:
            merged[key] = llm_value
            section_meta[key] = _llm_meta(raw_text, llm_value, key, "filled by LLM fallback")
        else:
            section_meta[key] = _missing_meta()

    if not merged["abstract"]:
        merged["abstract"] = existing_abstract
        if existing_abstract:
            section_meta["abstract"] = _llm_meta(
                raw_text,
                existing_abstract,
                "abstract",
                "filled from paper_info abstract",
            )

    for key in SECTION_KEYS:
        section_meta[key]["length"] = len(merged[key])
        if not merged[key]:
            section_meta[key] = _missing_meta()
    return merged, section_meta


def format_section_debug_info(section_meta: dict[str, SectionMeta]) -> str:
    """Render section extraction metadata for CLI/debug output."""
    lines = ["section debug:"]
    for key in SECTION_KEYS:
        meta = section_meta.get(key, _missing_meta())
        lines.append(
            "- "
            f"{key}: source={meta.get('source', 'missing')}, "
            f"matched_title={meta.get('matched_title', '')!r}, "
            f"length={meta.get('length', 0)}, "
            f"confidence={meta.get('confidence', 0.0)}, "
            f"warning={meta.get('warning', '')}"
        )
    return "\n".join(lines)


"""
    最终执行的主提取节点逻辑：先用规则提取，
    如果主要部分缺失或过短，或者没有提取到method和experiments，再用LLM提取，
    最后合并结果并生成metadata
"""
def extract_sections_from_text(raw_text: str, existing_abstract: str = "") -> dict[str, object]:
    """Extract major paper sections from text with rules first and LLM fallback."""
    rule_sections, rule_meta = extract_sections_by_rules_with_meta(raw_text)
    needs_llm = (
        not rule_sections.get("method")
        or not rule_sections.get("experiments")
        or any(_is_obviously_short(key, rule_sections.get(key, "")) for key in SHORT_SECTION_KEYS)
    )

    llm_sections: dict[str, str] = {}
    llm_warning = ""
    if needs_llm:
        try:
            llm_sections = extract_sections_with_llm(raw_text[:40000])
        except Exception as exc:
            llm_warning = f"LLM fallback failed: {exc}"

    merged, section_meta = _merge_sections(
        raw_text,
        rule_sections,
        rule_meta,
        llm_sections,
        existing_abstract,
    )
    if llm_warning:
        for key in SECTION_KEYS:
            if section_meta[key]["source"] == "missing" or _is_obviously_short(key, merged[key]):
                section_meta[key]["warning"] = llm_warning

    return {
        **merged,
        "section_meta": section_meta,
    }


def section_extract_node(state: PaperState) -> dict[str, object]:
    """Adapt shared section extraction capability to PaperState."""
    return extract_sections_from_text(state.get("raw_text", ""), state.get("abstract", ""))
