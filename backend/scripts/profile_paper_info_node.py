"""Profile paper_info_node during a normal PaperPilot analysis run."""

import argparse
import json
import os
import sys
from pathlib import Path
from uuid import uuid4


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


def main() -> None:
    """Run analysis with paper_info_node profiling enabled and print the profile."""
    parser = argparse.ArgumentParser(description="Profile PaperPilot paper_info_node.")
    parser.add_argument("--pdf", required=True, help="Path to the PDF file.")
    parser.add_argument(
        "--paper-language",
        choices=["zh", "en"],
        default="zh",
        help="Paper language metadata for downstream analysis; parser comes from PDF_PARSER.",
    )
    args = parser.parse_args()

    os.environ["PAPER_INFO_PROFILE_ENABLED"] = "true"

    from app.agents.paper_graph import analyze_paper  # noqa: WPS433
    from app.core.config import ensure_storage_dirs  # noqa: WPS433

    pdf_path = Path(args.pdf).expanduser().resolve()
    paper_id = uuid4().hex

    ensure_storage_dirs()
    result = analyze_paper(str(pdf_path), paper_id, paper_language=args.paper_language)
    profile = (result.get("paper_info_debug", {}) or {}).get("_profile", {})

    print(f"paper_id: {paper_id}")
    print(f"paper_language: {result.get('paper_language', args.paper_language)}")
    print(f"parser_name: {result.get('parser_name', '')}")
    print("paper_info_profile:")
    print(json.dumps(profile, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
