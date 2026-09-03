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


def parse_pdf(pdf_path: str, parser_name: str | None = None) -> ParsedPaper:
    """Parse a PDF with one explicitly requested parser and no fallback."""
    config = get_pdf_parser_config()
    requested_parser = (parser_name or config.parser or "pymupdf").strip().lower()
    parser_class = PARSER_REGISTRY.get(requested_parser)
    if parser_class is None:
        raise ValueError(f"Unknown PDF parser: {requested_parser}")

    parsed = parser_class().parse(pdf_path)
    parsed.parser_warnings.insert(0, f"final_parser={parsed.parser_name}")
    parsed.parser_warnings.insert(0, f"requested_parser={requested_parser}")
    parsed.parser_meta["requested_parser"] = requested_parser
    parsed.parser_meta["final_parser"] = parsed.parser_name
    return parsed
