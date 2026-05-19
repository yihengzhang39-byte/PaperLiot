"""Rule-first section extraction node with optional LLM fallback."""

import re
from typing import TypedDict

from app.agents.paper_state import PaperState
from app.services.llm_service import extract_sections_with_llm


class HeadingMatch(TypedDict):
    """A detected section heading and its position in raw text."""

    section: str
    start: int
    end: int


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
    ],
    "experiments": [
        "experiments",
        "experiment",
        "experimental results",
        "evaluation",
        "results",
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

SECTION_KEYS = [
    "abstract",
    "introduction",
    "related_work",
    "method",
    "experiments",
    "conclusion",
]


def _empty_sections() -> dict[str, str]:
    """Return an empty section mapping."""
    return {key: "" for key in SECTION_KEYS}


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


def extract_sections_by_rules(raw_text: str) -> dict[str, str]:
    """Extract paper sections by matching common section headings."""
    sections = _empty_sections()
    if not raw_text.strip():
        return sections

    headings = _find_headings(raw_text)
    if not headings:
        return sections

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
        else:
            sections[section] = content

    return sections


def _merge_sections(
    rule_sections: dict[str, str],
    llm_sections: dict[str, str],
    existing_abstract: str,
) -> dict[str, str]:
    """Merge section extraction results, preferring rule-based content."""
    merged = _empty_sections()
    for key in SECTION_KEYS:
        merged[key] = rule_sections.get(key, "").strip() or llm_sections.get(key, "").strip()

    if not merged["abstract"]:
        merged["abstract"] = existing_abstract
    return merged
def section_extract_node(state: PaperState) -> dict[str, str]:
    """Extract major paper sections with rules first and LLM fallback if needed."""
    raw_text = state.get("raw_text", "")
    rule_sections = extract_sections_by_rules(raw_text)

    if rule_sections.get("method") and rule_sections.get("experiments"):
        return _merge_sections(rule_sections, {}, state.get("abstract", ""))

    llm_sections = extract_sections_with_llm(raw_text[:40000])
    return _merge_sections(rule_sections, llm_sections, state.get("abstract", ""))
