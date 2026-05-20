"""Base class for PDF parser adapters."""

from app.services.parsers.schema import ParsedPaper


class BasePDFParser:
    """Base PDF parser adapter."""

    parser_name: str

    def parse(self, pdf_path: str) -> ParsedPaper:
        """Parse a PDF into a unified ParsedPaper object."""
        raise NotImplementedError
