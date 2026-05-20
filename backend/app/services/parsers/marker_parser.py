"""Marker parser adapter skeleton."""

from app.services.parsers.base import BasePDFParser
from app.services.parsers.schema import ParsedPaper


class MarkerParser(BasePDFParser):
    """Experimental parser adapter for Marker."""

    parser_name = "marker"

    def parse(self, pdf_path: str) -> ParsedPaper:
        """Return a controlled warning until Marker is wired in."""
        try:
            import marker  # noqa: F401
        except ImportError:
            raise RuntimeError("Marker dependency is not installed.")

        return ParsedPaper(
            parser_name=self.parser_name,
            raw_text="",
            parser_warnings=[
                "Marker dependency is installed, but the PaperPilot adapter is not implemented yet."
            ],
            parser_meta={"pdf_path": pdf_path},
        )
