"""Offline admission tests: normal/SSE share ownership until the worker exits."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
from threading import Event
from types import SimpleNamespace
from unittest.mock import patch
import time

from fastapi import HTTPException
from app.api.routes import chat
from app.repositories.session_event_repository import SessionEventRepository
from app.services import session_service, sqlite_service
from app.services.session_run_service import acquire_session_run, SessionBusyError


def request(name, paper_id=None):
    return SimpleNamespace(session_id=name, message="question", paper_id=paper_id)


def busy(fn):
    try:
        fn()
    except HTTPException as exc:
        assert exc.status_code == 409
    else:
        raise AssertionError("Expected busy before creating a turn")


def wait_released(name):
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        try:
            lease = acquire_session_run(name)
        except SessionBusyError:
            time.sleep(.005)
        else:
            lease.release()
            return
    raise AssertionError("Session lease leaked")


def main():
    with tempfile.TemporaryDirectory() as directory:
        db = Path(directory) / "events.db"
        entered, finish = Event(), Event()
        def runner(message, **kwargs):
            if kwargs["session_id"] == "locked":
                entered.set()
                assert finish.wait(3)
            kwargs["event_sink"]({"type": "agent_done", "finish_reason": "final_answer", "steps": 1})
            return SimpleNamespace(final_answer="done")
        with patch.object(sqlite_service, "DATABASE_PATH", db), patch.object(session_service, "CHAT_SESSIONS_DIR", Path(directory) / "legacy"), patch.object(chat, "_load_memory_context", return_value=""), patch.object(chat, "run_paper_agent", side_effect=runner):
            chat.SESSION_HISTORY.clear()
            try:
                with ThreadPoolExecutor(max_workers=1) as executor:
                    first = executor.submit(chat.chat, request("locked", "p1"))
                    assert entered.wait(2)
                    before = SessionEventRepository(db).list_events("locked")
                    busy(lambda: chat.chat(request("locked", "p2")))
                    busy(lambda: chat.chat_stream(request(" locked ", "p2")))
                    busy(lambda: chat.delete_chat_session("locked"))
                    assert chat.SESSION_HISTORY["locked"].paper_id == "p1"
                    assert SessionEventRepository(db).list_events("locked") == before
                    assert chat.chat(request("other"))["reply"] == "done"
                    finish.set()
                    assert first.result(timeout=2)["reply"] == "done"
                wait_released("locked")

                entered.clear(); finish.clear()
                def stream_runner(message, **kwargs):
                    kwargs["event_sink"]({"type": "agent_start", "max_steps": 1})
                    entered.set()
                    assert finish.wait(3)
                    raise RuntimeError("fake stream failure")
                with patch.object(chat, "run_paper_agent", side_effect=stream_runner):
                    iterator = chat._stream_agent_events(session_id="disconnect", message="stream", session=session_service.ChatSessionState(), paper_id=None)
                    assert entered.wait(2)
                    assert "agent_start" in next(iterator)
                    iterator.close()  # Disconnect must not release while the worker is running.
                    busy(lambda: chat.chat(request("disconnect")))
                    assert chat.chat is not None
                    finish.set()
                    wait_released("disconnect")
                ends = [event for event in SessionEventRepository(db).list_events("disconnect") if event["event_type"] == "turn/end"]
                assert len(ends) == 1 and ends[0]["data"]["status"] == "error"

                with patch.object(chat, "run_paper_agent", side_effect=RuntimeError("fake plain failure")):
                    try:
                        chat.chat(request("plain-error"))
                    except HTTPException as exc:
                        assert exc.status_code == 502
                    else:
                        raise AssertionError("Expected error")
                wait_released("plain-error")
                for route in (chat.chat, chat.chat_stream):
                    with patch.object(chat, "_prepare_chat", side_effect=ValueError("setup failed")):
                        try:
                            route(request("setup-error"))
                        except ValueError:
                            pass
                    wait_released("setup-error")
                with patch.object(chat, "Thread") as thread:
                    thread.return_value.start.side_effect = RuntimeError("cannot start")
                    try:
                        chat.chat_stream(request("thread-error"))
                    except RuntimeError:
                        pass
                    else:
                        raise AssertionError("Expected startup error")
                wait_released("thread-error")
                # Idempotent release cannot accidentally release a subsequent owner's lease.
                first = acquire_session_run("idempotent")
                first.release()
                second = acquire_session_run("idempotent")
                first.release()
                busy(lambda: chat.chat(request("idempotent")))
                second.release()
            finally:
                finish.set()
                chat.SESSION_HISTORY.clear()
    print("ALL SESSION RUN ADMISSION TESTS PASSED")


if __name__ == "__main__":
    main()
