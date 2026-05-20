"""PyMuPDF parser adapter."""

from pathlib import Path

import fitz

from app.services.parsers.base import BasePDFParser
from app.services.parsers.schema import ParsedPaper


class PyMuPDFParser(BasePDFParser):
    """Parse PDFs with PyMuPDF while preserving legacy raw_text behavior."""

    parser_name = "pymupdf"

    def parse(self, pdf_path: str) -> ParsedPaper:
        """Extract raw text page by page with PyMuPDF."""
        path = Path(pdf_path)
        if not path.exists():
            raise FileNotFoundError(f"PDF file not found: {pdf_path}")

        try:
            pages: list[str] = []
            with fitz.open(path) as doc:
                page_count = doc.page_count
                for page_index, page in enumerate(doc, start=1):
                    text = page.get_text("text")
                    pages.append(f"\n\n--- Page {page_index} ---\n\n{text}")

            raw_text = "".join(pages).strip()
            return ParsedPaper(
                parser_name=self.parser_name,
                raw_text=raw_text,
                parser_meta={
                    "page_count": page_count,
                    "raw_text_length": len(raw_text),
                },
            )
        except Exception as exc:
            raise RuntimeError(f"Failed to parse PDF with PyMuPDF '{pdf_path}': {exc}") from exc
