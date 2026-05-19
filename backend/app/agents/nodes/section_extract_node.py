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


SectionMeta = dict[str, object]

"""
    规则提取的标题
"""
SECTION_ALIASES = {
    "abstract": [
        "abstract",
    ],
    "introduction": [
        "introduction",
    ],
    "related_work": [
        "related work",
        "background",
    ],
    "method": [
        "method",
        "methods",
        "methodology",
        "approach",
        "proposed method",
        "proposed approach",
        "our method",
        "our approach",
    ],
    "experiments": [
        "experiments",
        "experiment",
        "experimental results",
        "evaluation",
        "results",
        "results and discussion",
        "ablation study",
    ],
    "conclusion": [
        "conclusion",
        "conclusions",
    ],
    "_references": [
        "references",
        "bibliography",
    ],
}

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
            "length": 0,
            "start_char": -1,
            "end_char": -1,
            "warning": "section not found",
        }
        for key in SECTION_KEYS
    }

"""
    正则表达式匹配标题，支持阿拉伯数字和罗马数字编号
"""
def _heading_pattern(alias: str) -> re.Pattern[str]:
    """Build a heading regex that supports Arabic and Roman numbering."""
    escaped_alias = re.escape(alias).replace(r"\ ", r"\s+")
    return re.compile(
        rf"(?im)^[ \t]*"
        rf"(?:(?:\d+(?:\.\d+)*\.?|[IVXLCDM]+\.?)\s+)?"
        rf"{escaped_alias}"
        rf"[ \t]*[:.\-]?[ \t]*$",
    )


"""
    找到匹配的标题，记录标题对应的section和在文本中的位置
    对找到的标题进行排序，按照在文本中出现的先后顺序
"""
def _find_headings(raw_text: str) -> list[HeadingMatch]:
    """Find all supported section headings in raw paper text."""
    matches: list[HeadingMatch] = []
    for section, aliases in SECTION_ALIASES.items():
        for alias in aliases:
            for match in _heading_pattern(alias).finditer(raw_text):
                matches.append(
                    {
                        "section": section,
                        "title": match.group(0).strip(),
                        "start": match.start(),
                        "end": match.end(),
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
        "source": "rule",
        "matched_title": heading["title"],
        "length": len(content),
        "start_char": heading["end"],
        "end_char": end_char,
        "warning": warning,
    }


"""    如果规则提取的内容过短，LLM修正可能会有帮助
"""
def _is_obviously_short(section: str, content: str) -> bool:
    """Return whether a rule-extracted major section is too short to trust."""
    return section in SHORT_SECTION_KEYS and 0 < len(content.strip()) < MIN_SECTION_LENGTH


def extract_sections_by_rules_with_meta(
    raw_text: str,
) -> tuple[dict[str, str], dict[str, SectionMeta]]:
    """Extract paper sections and debugging metadata by heading rules."""
    sections = _empty_sections()
    section_meta = _empty_section_meta()
    if not raw_text.strip():
        return sections, section_meta

    headings = _find_headings(raw_text)
    if not headings:
        return sections, section_meta

    for index, heading in enumerate(headings):
        section = heading["section"]
        if section == "_references":
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


def _llm_meta(raw_text: str, content: str, warning: str = "") -> SectionMeta:
    """Build metadata for an LLM-filled or LLM-corrected section."""
    start, end = _locate_text(raw_text, content)
    return {
        "source": "llm",
        "matched_title": "",
        "length": len(content),
        "start_char": start,
        "end_char": end,
        "warning": warning,
    }


def _missing_meta(warning: str = "section not found") -> SectionMeta:
    """Build metadata for a missing section."""
    return {
        "source": "missing",
        "matched_title": "",
        "length": 0,
        "start_char": -1,
        "end_char": -1,
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
                "rule section was very short and replaced by LLM correction",
            )
        elif rule_value:
            merged[key] = rule_value
            section_meta[key] = rule_meta.get(key, _missing_meta())
        elif llm_value:
            merged[key] = llm_value
            section_meta[key] = _llm_meta(raw_text, llm_value, "filled by LLM fallback")
        else:
            section_meta[key] = _missing_meta()

    if not merged["abstract"]:
        merged["abstract"] = existing_abstract
        if existing_abstract:
            section_meta["abstract"] = _llm_meta(
                raw_text,
                existing_abstract,
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
            f"warning={meta.get('warning', '')}"
        )
    return "\n".join(lines)


"""
    最终执行的主提取节点逻辑：先用规则提取，
    如果主要部分缺失或过短，或者没有提取到method和experiments，再用LLM提取，
    最后合并结果并生成metadata
"""
def section_extract_node(state: PaperState) -> dict[str, object]:
    """Extract major paper sections with rules first and LLM fallback if needed."""
    raw_text = state.get("raw_text", "")
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
        state.get("abstract", ""),
    )
    if llm_warning:
        for key in SECTION_KEYS:
            if section_meta[key]["source"] == "missing" or _is_obviously_short(key, merged[key]):
                section_meta[key]["warning"] = llm_warning

    return {
        **merged,
        "section_meta": section_meta,
    }
