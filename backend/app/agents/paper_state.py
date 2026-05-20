"""State definition for the single-paper reading workflow."""

from typing import TypedDict,Any


class PaperState(TypedDict, total=False):
    """Shared state passed between LangGraph paper-analysis nodes."""

    # 初始输入
    pdf_path: str
    paper_id: str
    paper_language: str

    # PDF 解析结果
    raw_text: str
    parsed_paper: dict[str, Any]
    parser_name: str
    parser_warnings: list[str]
    requested_parser: str
    parser_meta: dict[str, Any]

    # 论文基础信息
    title: str
    authors: list[str]
    year: str
    venue: str
    paper_info_debug: dict[str, object]

    # 章节内容
    abstract: str
    introduction: str
    related_work: str
    method: str
    experiments: str
    conclusion: str
    section_meta: dict[str, dict[str, object]]

    # 分析结果
    problem: str
    motivation: str
    method_summary: str
    innovation_points: list[str]
    experiment_summary: str
    limitations: list[str]
    inspirations: list[str]

    # 最终笔记
    final_note: str
    note_path: str
