"""Controlled write-back for PaperPilot's single writable research-memory file."""

from pathlib import Path
import re


MEMORY_FILE = Path(__file__).resolve().parents[2] / "memory" / "memory.md"
MEMORY_HEADINGS = {
    "paper": "## 已分析论文列表",
    "theme": "## 用户关注的研究主题",
    "finding": "## 跨论文发现的规律",
}


def load_research_memory(*, memory_file: Path | None = None) -> str:
    """Read the only writable memory file; missing data is an empty memory."""
    path = memory_file or MEMORY_FILE
    try:
        return path.read_text(encoding="utf-8") if path.exists() else ""
    except OSError:
        return ""


def _normalize_entry(entry: str) -> str:
    normalized = re.sub(r"\s+", " ", entry or "").strip()
    if not normalized or len(normalized) > 500:
        raise ValueError("memory entry must contain 1 to 500 characters")
    return normalized


def _empty_memory() -> str:
    return "# PaperPilot Memory\n\n" + "\n\n".join(MEMORY_HEADINGS.values()) + "\n"


def append_memory_entry(
    category: str,
    entry: str,
    *,
    memory_file: Path | None = None,
) -> dict[str, object]:
    """Append one normalized, de-duplicated bullet to a fixed memory section."""
    if category not in MEMORY_HEADINGS:
        raise ValueError(f"unknown memory category: {category}")
    entry = _normalize_entry(entry)
    path = memory_file or MEMORY_FILE
    content = load_research_memory(memory_file=path) or _empty_memory()
    normalized_entry = entry.casefold()
    existing_entries = {
        re.sub(r"\s+", " ", line[2:]).strip().casefold()
        for line in content.splitlines()
        if line.startswith("- ")
    }
    if normalized_entry in existing_entries:
        return {"saved": False, "reason": "duplicate", "category": category, "entry": entry}

    lines = content.splitlines()
    heading = MEMORY_HEADINGS[category]
    try:
        insert_at = lines.index(heading) + 1
    except ValueError:
        lines.extend(["", heading])
        insert_at = len(lines)
    while insert_at < len(lines) and lines[insert_at].strip().startswith("<!--"):
        while insert_at < len(lines):
            closing = "-->" in lines[insert_at]
            insert_at += 1
            if closing:
                break
    lines.insert(insert_at, f"- {entry}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return {"saved": True, "reason": "added", "category": category, "entry": entry}
