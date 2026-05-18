"""Rule-based section extraction node."""

import re

from app.agents.paper_state import PaperState


SECTION_PATTERNS = {
    "introduction": [
        r"introduction",
        r"1\s+introduction",
    ],
    "method": [
        r"method",
        r"methodology",
        r"approach",
        r"proposed\s+method",
        r"model",
    ],
    "experiments": [
        r"experiment",
        r"experiments",
        r"experimental\s+results",
        r"evaluation",
        r"results",
    ],
    "conclusion": [
        r"conclusion",
        r"conclusions",
        r"discussion",
    ],
}


def _find_section_starts(text: str) -> list[tuple[str, int]]:
    """Find likely section heading positions in raw paper text."""
    starts: list[tuple[str, int]] = []
    for section, patterns in SECTION_PATTERNS.items():
        for pattern in patterns:
            match = re.search(
                rf"(?im)^\s*(?:\d+(?:\.\d+)*\.?\s*)?{pattern}\s*$",
                text,
            )
            if match:
                starts.append((section, match.start()))
                break
    return sorted(starts, key=lambda item: item[1])


def _slice_sections(text: str, starts: list[tuple[str, int]]) -> dict[str, str]:
    """Slice raw text by detected section starts."""
    sections = {
        "introduction": "",
        "method": "",
        "experiments": "",
        "conclusion": "",
    }

    for index, (section, start) in enumerate(starts):
        end = starts[index + 1][1] if index + 1 < len(starts) else len(text)
        sections[section] = text[start:end].strip()

    return sections


def section_extract_node(state: PaperState) -> dict[str, str]:
    """Extract major paper sections with simple heading rules."""
    raw_text = state.get("raw_text", "")
    if not raw_text:
        return {
            "introduction": "",
            "method": "",
            "experiments": "",
            "conclusion": "",
        }

    starts = _find_section_starts(raw_text)
    return _slice_sections(raw_text, starts)
