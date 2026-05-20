"""Docling parser adapter skeleton."""

from app.services.parsers.base import BasePDFParser
from app.services.parsers.schema import ParsedPaper


class DoclingParser(BasePDFParser):
    """Experimental parser adapter for Docling."""

    parser_name = "docling"

    def parse(self, pdf_path: str) -> ParsedPaper:
        """Return a controlled warning until Docling is wired in."""
        try:
            import docling  # noqa: F401
        except ImportError:
            raise RuntimeError("Docling dependency is not installed.")

        return ParsedPaper(
            parser_name=self.parser_name,
            raw_text="",
            parser_warnings=[
                "Docling dependency is installed, but the PaperPilot adapter is not implemented yet."
            ],
            parser_meta={"pdf_path": pdf_path},
        )
