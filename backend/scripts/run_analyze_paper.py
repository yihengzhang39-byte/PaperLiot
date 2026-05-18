"""Command-line entry point for analyzing one PDF paper."""

import argparse
import sys
from pathlib import Path
from uuid import uuid4


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.agents.paper_graph import analyze_paper  # noqa: E402
from app.core.config import ensure_storage_dirs  # noqa: E402


def main() -> None:
    """Run paper analysis from the command line."""
    parser = argparse.ArgumentParser(description="Analyze a PDF paper with PaperPilot.")
    parser.add_argument("--pdf", required=True, help="Path to the PDF file.")
    args = parser.parse_args()

    pdf_path = Path(args.pdf).expanduser().resolve()
    paper_id = uuid4().hex

    ensure_storage_dirs()
    result = analyze_paper(str(pdf_path), paper_id)

    print(f"paper_id: {paper_id}")
    print(f"note_path: {result.get('note_path', '')}")
    print("\nfinal_note preview:")
    print(result.get("final_note", "")[:1000])


if __name__ == "__main__":
    main()
