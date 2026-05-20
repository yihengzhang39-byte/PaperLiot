"""MinerU parser adapter skeleton."""

from app.services.parsers.base import BasePDFParser
from app.services.parsers.schema import ParsedPaper


class MinerUParser(BasePDFParser):
    """Experimental parser adapter for MinerU."""

    parser_name = "mineru"

    def parse(self, pdf_path: str) -> ParsedPaper:
        """Return a controlled warning until MinerU is wired in."""
        try:
            import magic_pdf  # noqa: F401
        except ImportError:
            raise RuntimeError("MinerU dependency is not installed.")

        return ParsedPaper(
            parser_name=self.parser_name,
            raw_text="",
            parser_warnings=[
                "MinerU dependency is installed, but the PaperPilot adapter is not implemented yet."
            ],
            parser_meta={"pdf_path": pdf_path},
        )
