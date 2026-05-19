"""State definition for the single-paper reading workflow."""

from typing import TypedDict


class PaperState(TypedDict):
    """Shared state passed between LangGraph paper-analysis nodes."""

    pdf_path: str
    paper_id: str
    raw_text: str

    title: str
    authors: list[str]
    year: str
    venue: str

    abstract: str
    introduction: str
    related_work: str
    method: str
    experiments: str
    conclusion: str
    section_meta: dict[str, dict[str, object]]

    problem: str
    motivation: str
    method_summary: str
    innovation_points: list[str]
    experiment_summary: str
    limitations: list[str]
    inspirations: list[str]

    final_note: str
    note_path: str
