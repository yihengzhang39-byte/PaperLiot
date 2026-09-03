"""PDF parser selection and fallback service."""

from app.core.config import get_pdf_parser_config
from app.services.parsers.base import BasePDFParser
from app.services.parsers.docling_parser import DoclingParser
from app.services.parsers.grobid_parser import GROBIDParser
from app.services.parsers.marker_parser import MarkerParser
from app.services.parsers.mineru_parser import MinerUParser
from app.services.parsers.pymupdf_parser import PyMuPDFParser
from app.services.parsers.schema import ParsedPaper


PARSER_REGISTRY: dict[str, type[BasePDFParser]] = {
    "pymupdf": PyMuPDFParser,
    "grobid": GROBIDParser,
    "docling": DoclingParser,
    "marker": MarkerParser,
    "mineru": MinerUParser,
}


def _fallback_to_pymupdf(pdf_path: str, warning: str, requested_parser: str) -> ParsedPaper:
    """Parse with PyMuPDF and attach a fallback warning."""
    parsed = PyMuPDFParser().parse(pdf_path)
    parsed.parser_warnings.insert(0, "final_parser=pymupdf")
    parsed.parser_warnings.insert(0, warning)
    parsed.parser_warnings.insert(0, f"requested_parser={requested_parser}")
    parsed.parser_meta["fallback_from"] = warning
    parsed.parser_meta["requested_parser"] = requested_parser
    parsed.parser_meta["final_parser"] = parsed.parser_name
    return parsed


def _is_low_quality_parse(parsed: ParsedPaper) -> bool:
    """Return whether a non-PyMuPDF parse result is too weak to trust."""
    raw_text_length = len(parsed.raw_text.strip())
    section_count = int(parsed.parser_meta.get("section_count", len(parsed.sections)) or 0)
    has_low_quality_warning = any(
        "low quality" in warning.lower() for warning in parsed.parser_warnings
    )
    return raw_text_length < 500 or section_count < 1 or has_low_quality_warning


def parse_pdf(pdf_path: str, parser_name: str | None = None) -> ParsedPaper:
    """Parse a PDF with the configured parser, falling back to PyMuPDF."""
    config = get_pdf_parser_config()
    requested_parser = (parser_name or config.parser or "pymupdf").strip().lower()
    parser_class = PARSER_REGISTRY.get(requested_parser)

    """ 
        如果为空，返回兜底的
    """
    if parser_class is None:
        return _fallback_to_pymupdf(
            pdf_path,
            f"Unknown PDF parser '{requested_parser}', fell back to PyMuPDF.",
            requested_parser,
        )

    parser = parser_class()
    try:
        parsed = parser.parse(pdf_path)
    except Exception as exc:
        if requested_parser == "pymupdf":
            raise
        return _fallback_to_pymupdf(
            pdf_path,
            f"PDF parser '{requested_parser}' failed: {exc}; fell back to PyMuPDF.",
            requested_parser,
        )
    """
        如果解析结果没有文本，返回兜底的
    """
    if requested_parser != "pymupdf" and not parsed.raw_text.strip():
        fallback = _fallback_to_pymupdf(
            pdf_path,
            f"PDF parser '{requested_parser}' returned empty raw_text; fell back to PyMuPDF.",
            requested_parser,
        )
        fallback.parser_warnings = parsed.parser_warnings + fallback.parser_warnings
        fallback.parser_meta["requested_parser_meta"] = parsed.parser_meta
        return fallback

    if requested_parser != "pymupdf" and _is_low_quality_parse(parsed):
        fallback = _fallback_to_pymupdf(
            pdf_path,
            f"PDF parser '{requested_parser}' returned low-quality text; fell back to PyMuPDF.",
            requested_parser,
        )
        fallback.parser_warnings = parsed.parser_warnings + fallback.parser_warnings
        fallback.parser_warnings.insert(
            0,
            f"{requested_parser.upper()} failed or returned low-quality text; fell back to PyMuPDF",
        )
        fallback.parser_meta["requested_parser_meta"] = parsed.parser_meta
        return fallback

    parsed.parser_warnings.insert(0, f"final_parser={parsed.parser_name}")
    parsed.parser_warnings.insert(0, f"requested_parser={requested_parser}")
    parsed.parser_meta["requested_parser"] = requested_parser
    parsed.parser_meta["final_parser"] = parsed.parser_name
    return parsed


def parse_paper_for_language(pdf_path: str, paper_language: str = "zh") -> tuple[ParsedPaper, str, str]:
    """Parse one paper with the project's language-specific parser policy."""
    normalized_language = "en" if paper_language == "en" else "zh"
    requested_parser = "grobid" if normalized_language == "en" else "pymupdf"
    return parse_pdf(pdf_path, parser_name=requested_parser), normalized_language, requested_parser
