"""Controlled write-back for PaperPilot's research and user-profile memory."""

from pathlib import Path
import re


MEMORY_FILE = Path(__file__).resolve().parents[2] / "memory" / "memory.md"
USER_PROFILE_FILE = Path(__file__).resolve().parents[2] / "memory" / "user.md"
MEMORY_HEADINGS = {
    "paper": "## 已分析论文列表",
    "theme": "## 用户关注的研究主题",
    "finding": "## 跨论文发现的规律",
}
USER_PROFILE_FIELDS = (
    "university",
    "education",
    "identity",
    "research_interest",
    "technical_background",
    "preferences",
)
USER_PROFILE_LIST_FIELDS = {"research_interest", "technical_background", "preferences"}


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


def _empty_user_profile() -> dict[str, str | list[str]]:
    return {field: [] if field in USER_PROFILE_LIST_FIELDS else "" for field in USER_PROFILE_FIELDS}


def _read_user_profile(path: Path) -> dict[str, str | list[str]]:
    profile = _empty_user_profile()
    current_list: str | None = None
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return profile
    for line in lines:
        key, separator, value = line.partition(":")
        if separator and key in USER_PROFILE_FIELDS:
            current_list = key if key in USER_PROFILE_LIST_FIELDS else None
            if current_list is None:
                profile[key] = value.strip()
            continue
        if current_list and line.strip().startswith("- "):
            profile[current_list].append(line.strip()[2:].strip())  # type: ignore[union-attr]
    return profile


def _profile_list(value: list[str] | None, field: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"{field} must be a list of strings")
    entries: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise ValueError(f"{field} must be a list of strings")
        normalized = _normalize_entry(item)
        if normalized.casefold() not in {entry.casefold() for entry in entries}:
            entries.append(normalized)
    return entries


def _render_user_profile(profile: dict[str, str | list[str]]) -> str:
    lines = ["# User Profile"]
    for field in USER_PROFILE_FIELDS:
        value = profile[field]
        if field in USER_PROFILE_LIST_FIELDS:
            lines.append(f"{field}:")
            lines.extend(f"  - {item}" for item in value)
        else:
            lines.append(f"{field}: {value}")
    return "\n".join(lines) + "\n"


def save_user_profile(
    *,
    university: str = "",
    education: str = "",
    identity: str = "",
    research_interest: list[str] | None = None,
    technical_background: list[str] | None = None,
    preferences: list[str] | None = None,
    user_file: Path | None = None,
) -> dict[str, object]:
    """Upsert explicit, stable user-profile fields into the fixed profile file."""
    path = user_file or USER_PROFILE_FILE
    profile = _read_user_profile(path)
    updates: dict[str, str | list[str]] = {}
    for field, value in (("university", university), ("education", education), ("identity", identity)):
        if value:
            updates[field] = _normalize_entry(value)
    for field, value in (
        ("research_interest", research_interest),
        ("technical_background", technical_background),
        ("preferences", preferences),
    ):
        entries = _profile_list(value, field)
        if entries:
            updates[field] = entries
    if not updates:
        raise ValueError("at least one user profile field is required")

    changed: dict[str, str | list[str]] = {}
    for field, value in updates.items():
        if field not in USER_PROFILE_LIST_FIELDS:
            if profile[field] != value:
                profile[field] = value
                changed[field] = value
            continue
        if profile[field] != value:
            profile[field] = value
            changed[field] = value
    if not changed:
        return {"saved": False, "reason": "unchanged", "profile": profile}

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_render_user_profile(profile), encoding="utf-8")
    return {"saved": True, "reason": "updated", "updated_fields": changed, "profile": profile}
