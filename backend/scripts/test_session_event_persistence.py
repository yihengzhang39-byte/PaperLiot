"""Pure-local checks for semantic Agent Runtime event persistence."""

import tempfile
from pathlib import Path
from threading import Event
from types import SimpleNamespace

from app.agents.agent_context import AgentContext
from app.agents.agent_loop import AgentMaxStepsError, run_agent
from app.api.routes import chat
from app.repositories.session_event_repository import SessionEventRepository
from app.runtime.tool_registry import ToolRegistry
from app.services import session_service, sqlite_service
from app.services.llm_service import AgentLLMDelta, AgentLLMResponse, AgentToolCall
from app.services.session_event_service import SessionEventService


def _context() -> AgentContext:
    return AgentContext(messages=[{"role": "user", "content": "question"}])


def _events(database_path: Path, session_id: str, *, message: str = "question") -> tuple[SessionEventService, SessionEventRepository]:
    service = SessionEventService(session_id, database_path=database_path)
    service.start(message, paper_id="paper-1", active_paper_ids=["paper-1"])
    return service, SessionEventRepository(database_path)


def main() -> None:
    with tempfile.TemporaryDirectory() as directory:
        database_path = Path(directory) / "paperpilot.db"

        direct, events = _events(database_path, "direct")
        run_agent(
            _context(),
            llm_call=lambda *_args: AgentLLMResponse("公开回答", []),
            event_sink=direct.persist_runtime_event,
        )
        direct_events = events.list_events("direct")
        assert [event["event_type"] for event in direct_events] == [
            "user/message", "turn/start", "step/start", "assistant/trace", "assistant/message", "step/end", "turn/end"
        ]
        assert all(event["turn_id"] == direct.turn_id for event in direct_events)
        assert direct_events[3]["data"] == {"content": "公开回答"}

        tool_turn, events = _events(database_path, "tool")
        responses = [
            AgentLLMResponse("先读取信息。", [AgentToolCall("info", "echo", '{"query":"method","token":"secret"}')]),
            AgentLLMResponse("最终回答", []),
        ]
        run_agent(
            _context(),
            tools={"echo": lambda query, token: {"chunks": ["x" * 20_000]}},
            llm_call=lambda *_args: responses.pop(0),
            event_sink=tool_turn.persist_runtime_event,
        )
        tool_events = events.list_events("tool")
        assert [event["event_type"] for event in tool_events] == [
            "user/message", "turn/start", "step/start", "assistant/trace", "tool/call", "tool/result", "step/end",
            "step/start", "assistant/trace", "assistant/message", "step/end", "turn/end",
        ]
        call = next(event for event in tool_events if event["event_type"] == "tool/call")
        result = next(event for event in tool_events if event["event_type"] == "tool/result")
        assert call["data"]["arguments"]["token"] == "[redacted]"
        assert result["data"] == {"tool_call_id": "info", "name": "echo", "status": "success", "summary": "retrieved 1 relevant chunks", "history_result": {"tool": "echo", "status": "success", "summary": "retrieved 1 relevant chunks"}}
        assert "chunks" not in result["data"] and [event["step"] for event in tool_events if event["event_type"] == "step/start"] == [1, 2]

        recovered, events = _events(database_path, "recovered")
        responses = [AgentLLMResponse("", [AgentToolCall("broken", "broken", "{}")]), AgentLLMResponse("已恢复", [])]
        run_agent(
            _context(),
            tools={"broken": lambda: (_ for _ in ()).throw(RuntimeError("hidden traceback"))},
            llm_call=lambda *_args: responses.pop(0),
            event_sink=recovered.persist_runtime_event,
        )
        recovered_events = events.list_events("recovered")
        failed = next(event for event in recovered_events if event["event_type"] == "tool/result")
        assert failed["data"]["status"] == "error" and "traceback" not in failed["data"]["summary"]
        assert any(event["event_type"] == "step/start" and event["step"] == 2 for event in recovered_events)

        streamed, events = _events(database_path, "streamed")
        run_agent(
            _context(),
            llm_stream=lambda *_args: iter([AgentLLMDelta("这"), AgentLLMDelta("篇"), AgentLLMDelta("论文")]),
            event_sink=streamed.persist_runtime_event,
        )
        streamed_events = events.list_events("streamed")
        assert [event["event_type"] for event in direct_events] == [event["event_type"] for event in streamed_events]
        assert "final_delta" not in [event["event_type"] for event in streamed_events]
        messages = [event for event in streamed_events if event["event_type"] == "assistant/message"]
        assert len(messages) == 1 and messages[0]["data"]["content"] == "这篇论文"

        first_done, second_done = Event(), Event()
        concurrent, events = _events(database_path, "concurrent")
        registry = ToolRegistry()
        registry.register("first", lambda: (first_done.set(), second_done.wait(1), {"value": "first"})[-1], is_concurrency_safe=lambda _: True)
        registry.register("second", lambda: (first_done.wait(1), second_done.set(), {"value": "second"})[-1], is_concurrency_safe=lambda _: True)
        responses = [
            AgentLLMResponse("", [AgentToolCall("first-call", "first", "{}"), AgentToolCall("second-call", "second", "{}")]),
            AgentLLMResponse("done", []),
        ]
        run_agent(_context(), tool_registry=registry, llm_call=lambda *_args: responses.pop(0), event_sink=concurrent.persist_runtime_event)
        concurrent_events = events.list_events("concurrent")
        assert [event["data"]["tool_call_id"] for event in concurrent_events if event["event_type"] == "tool/result"] == ["first-call", "second-call"]

        first_turn_id = direct.turn_id
        second, events = _events(database_path, "direct")
        run_agent(_context(), llm_call=lambda *_args: AgentLLMResponse("second", []), event_sink=second.persist_runtime_event)
        assert second.turn_id != first_turn_id
        assert [event["seq"] for event in events.list_events("direct")] == list(range(1, 15))

        failed_run, events = _events(database_path, "failure")
        try:
            run_agent(_context(), llm_call=lambda *_args: (_ for _ in ()).throw(RuntimeError("provider secret")), event_sink=failed_run.persist_runtime_event)
        except RuntimeError:
            pass
        else:
            raise AssertionError("Expected provider failure")
        assert [event["event_type"] for event in events.list_events("failure")][-2:] == ["error", "turn/end"]

        maxed, events = _events(database_path, "maxed")
        try:
            run_agent(
                AgentContext(messages=[{"role": "user", "content": "loop"}], max_steps=1),
                tools={"echo": lambda: "ok"},
                llm_call=lambda *_args: AgentLLMResponse("", [AgentToolCall("loop", "echo", "{}")]),
                event_sink=maxed.persist_runtime_event,
            )
        except AgentMaxStepsError:
            pass
        else:
            raise AssertionError("Expected max-step failure")
        maxed_events = events.list_events("maxed")
        assert maxed_events[-2]["data"]["code"] == "agent_max_steps" and maxed_events[-1]["data"] == {"status": "error"}

        original_runner = chat.run_paper_agent
        original_config = chat.get_llm_config
        original_session_dir = session_service.CHAT_SESSIONS_DIR
        original_database_path = sqlite_service.DATABASE_PATH
        route_session_dir = Path(directory) / "route_sessions"
        try:
            chat.SESSION_HISTORY.clear()
            chat.get_llm_config = lambda: SimpleNamespace(provider="deepseek")
            session_service.CHAT_SESSIONS_DIR = route_session_dir
            sqlite_service.DATABASE_PATH = database_path

            def route_runner(message: str, **kwargs):
                return run_agent(
                    AgentContext(messages=[{"role": "user", "content": message}]),
                    llm_call=lambda *_args: AgentLLMResponse("route answer", []),
                    event_sink=kwargs["event_sink"],
                )

            chat.run_paper_agent = route_runner
            chat.chat(SimpleNamespace(message="plain", session_id="route-plain", paper_id=None))
            list(chat._stream_agent_events(session_id="route-stream", message="stream", session=session_service.ChatSessionState(), paper_id=None))
            plain_types = [event["event_type"] for event in events.list_events("route-plain")]
            stream_types = [event["event_type"] for event in events.list_events("route-stream")]
            assert plain_types == stream_types == [
                "user/message", "turn/start", "step/start", "assistant/trace", "assistant/message", "step/end", "turn/end"
            ]
        finally:
            chat.SESSION_HISTORY.clear()
            chat.run_paper_agent = original_runner
            chat.get_llm_config = original_config
            session_service.CHAT_SESSIONS_DIR = original_session_dir
            sqlite_service.DATABASE_PATH = original_database_path

    print("ALL SESSION EVENT PERSISTENCE TESTS PASSED")


if __name__ == "__main__":
    main()
