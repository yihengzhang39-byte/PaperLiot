"""Pure-local check for deleting one uploaded paper's local artifacts."""

import json
import sys
import tempfile
from pathlib import Path
try:
    import fastapi  # noqa: F401
except ModuleNotFoundError:
    from types import ModuleType

    fastapi = ModuleType("fastapi")
    fastapi.UploadFile = object
    sys.modules.setdefault("fastapi", fastapi)
from app.services import file_service


def main() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        originals = {
            name: getattr(file_service, name)
            for name in ("PAPERS_DIR", "NOTES_DIR", "PAPER_METADATA_DIR", "PAPER_CHUNKS_DIR", "PAPER_SECTION_JSON_DIR")
        }
        try:
            for name in originals:
                path = root / name.lower()
                path.mkdir()
                setattr(file_service, name, path)

            paper_id, other_id = "a" * 32, "b" * 32
            (file_service.PAPERS_DIR / f"{paper_id}_paper.pdf").write_bytes(b"pdf")
            (file_service.NOTES_DIR / f"{paper_id}.md").write_text("note", encoding="utf-8")
            (file_service.PAPER_METADATA_DIR / f"{paper_id}.json").write_text("{}", encoding="utf-8")
            (file_service.PAPER_CHUNKS_DIR / f"{paper_id}.json").write_text("[]", encoding="utf-8")
            (file_service.PAPER_SECTION_JSON_DIR / "paper.json").write_text(json.dumps({"paper_id": paper_id}), encoding="utf-8")
            (file_service.PAPERS_DIR / f"{other_id}_paper.pdf").write_bytes(b"other")
            (file_service.PAPER_SECTION_JSON_DIR / "other.json").write_text(json.dumps({"paper_id": other_id}), encoding="utf-8")

            assert file_service.delete_paper_data(paper_id)
            assert not list(file_service.PAPERS_DIR.glob(f"{paper_id}*"))
            assert not (file_service.NOTES_DIR / f"{paper_id}.md").exists()
            assert not (file_service.PAPER_METADATA_DIR / f"{paper_id}.json").exists()
            assert not (file_service.PAPER_CHUNKS_DIR / f"{paper_id}.json").exists()
            assert not (file_service.PAPER_SECTION_JSON_DIR / "paper.json").exists()
            assert (file_service.PAPERS_DIR / f"{other_id}_paper.pdf").exists()
            assert (file_service.PAPER_SECTION_JSON_DIR / "other.json").exists()
            try:
                file_service.delete_paper_data("../not-a-paper")
            except ValueError:
                pass
            else:
                raise AssertionError("Expected invalid paper_id rejection")
        finally:
            for name, path in originals.items():
                setattr(file_service, name, path)

    print("ALL PAPER DELETE TESTS PASSED")


if __name__ == "__main__":
    main()
