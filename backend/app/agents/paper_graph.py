"""LangGraph workflow for single-paper analysis."""

from langgraph.graph import END, START, StateGraph

from app.agents.nodes.experiment_analyze_node import experiment_analyze_node
from app.agents.nodes.method_analyze_node import method_analyze_node
from app.agents.nodes.paper_info_enrich_node import paper_info_enrich_node
from app.agents.nodes.paper_info_node import paper_info_node
from app.agents.nodes.pdf_parse_node import pdf_parse_node
from app.agents.nodes.section_extract_node import section_extract_node
from app.agents.nodes.summary_write_node import summary_write_node
from app.agents.paper_state import PaperState
from app.services.file_service import save_paper_sections_json


def build_paper_graph():
    """Build and compile the paper-analysis LangGraph workflow."""
    builder = StateGraph(PaperState)

    builder.add_node("pdf_parse_node", pdf_parse_node)
    builder.add_node("paper_info_node", paper_info_node)
    builder.add_node("paper_info_enrich_node", paper_info_enrich_node)
    builder.add_node("section_extract_node", section_extract_node)
    builder.add_node("method_analyze_node", method_analyze_node)
    builder.add_node("experiment_analyze_node", experiment_analyze_node)
    builder.add_node("summary_write_node", summary_write_node)

    builder.add_edge(START, "pdf_parse_node")
    builder.add_edge("pdf_parse_node", "paper_info_node")
    builder.add_edge("paper_info_node", "paper_info_enrich_node")
    builder.add_edge("paper_info_enrich_node", "section_extract_node")
    builder.add_edge("section_extract_node", "method_analyze_node")
    builder.add_edge("method_analyze_node", "experiment_analyze_node")
    builder.add_edge("experiment_analyze_node", "summary_write_node")
    builder.add_edge("summary_write_node", END)

    return builder.compile()


def _initial_state(pdf_path: str, paper_id: str, paper_language: str = "zh") -> PaperState:
    """Create an empty initial state for a paper-analysis run."""
    return {
        "pdf_path": pdf_path,
        "paper_id": paper_id,
        "paper_language": paper_language if paper_language in {"zh", "en"} else "zh",
        "raw_text": "",
        "parsed_paper": {},
        "parser_name": "",
        "parser_warnings": [],
        "requested_parser": "",
        "parser_meta": {},
        "title": "",
        "authors": [],
        "year": "",
        "venue": "",
        "paper_info_debug": {},
        "missing_info_fields": [],
        "need_web_search": False,
        "web_search_results": [],
        "web_search_debug": {},
        "abstract": "",
        "introduction": "",
        "related_work": "",
        "method": "",
        "experiments": "",
        "conclusion": "",
        "section_meta": {},
        "problem": "",
        "motivation": "",
        "method_summary": "",
        "innovation_points": [],
        "experiment_summary": "",
        "limitations": [],
        "inspirations": [],
        "final_note": "",
        "note_path": "",
    }


def analyze_paper(pdf_path: str, paper_id: str, paper_language: str = "zh") -> PaperState:
    """Run the paper-analysis graph for a PDF."""
    graph = build_paper_graph()
    result = graph.invoke(_initial_state(pdf_path, paper_id, paper_language))
    try:
        save_paper_sections_json(result)
    except Exception as exc:
        print(f"[PaperPilot warning] Failed to save section JSON: {exc}")
    return result
