"""PDF parsing utilities."""

from pathlib import Path

import fitz


def extract_pdf_text(pdf_path: str) -> str:
    """Extract text from a PDF file, adding page markers between pages."""
    path = Path(pdf_path)
    if not path.exists():
        raise FileNotFoundError(f"PDF file not found: {pdf_path}")

    try:
        pages: list[str] = []
        with fitz.open(path) as doc:
            for page_index, page in enumerate(doc, start=1):
                text = page.get_text("text")
                pages.append(f"\n\n--- Page {page_index} ---\n\n{text}")
        return "".join(pages).strip()
    except Exception as exc:
        raise RuntimeError(f"Failed to parse PDF '{pdf_path}': {exc}") from exc
