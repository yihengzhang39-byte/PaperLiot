"""Pure-local checks for safe, bounded JSON chat-session persistence."""

import tempfile
from pathlib import Path

from app.services.session_service import ChatSessionState, load_session, save_session


def main() -> None:
    with tempfile.TemporaryDirectory() as directory:
        storage_dir = Path(directory)
        state = ChatSessionState(
            paper_id="p1",
            active_paper_ids=["p1", "p2", "p1"],
            messages=[
                {"role": "user", "content": "第一问"},
                {"role": "assistant", "content": "第一答"},
                {"role": "tool", "content": "不应保存"},
                {"role": "user", "content": "第二问"},
                {"role": "assistant", "content": "第二答"},
                {"role": "user", "content": "第三问"},
            ],
        )
        path = save_session("session-a", state, storage_dir=storage_dir, max_messages=4)
        assert path.parent == storage_dir and path.name != "session-a.json"
        loaded = load_session("session-a", storage_dir=storage_dir)
        assert loaded is not None and loaded.paper_id == "p1" and loaded.active_paper_ids == ["p1", "p2"] and loaded.updated_at
        assert [message["content"] for message in loaded.messages] == ["第一答", "第二问", "第二答", "第三问"]
        assert all(message["role"] != "tool" for message in loaded.messages)

        save_session("session-b", ChatSessionState(paper_id="p2"), storage_dir=storage_dir)
        assert load_session("session-b", storage_dir=storage_dir).paper_id == "p2"
        unsafe_path = save_session("../../outside", ChatSessionState(paper_id="safe"), storage_dir=storage_dir)
        assert unsafe_path.parent == storage_dir and load_session("../../outside", storage_dir=storage_dir).paper_id == "safe"
        assert len(list(storage_dir.glob("*.json"))) == 3

    print("ALL SESSION PERSISTENCE TESTS PASSED")


if __name__ == "__main__":
    main()
