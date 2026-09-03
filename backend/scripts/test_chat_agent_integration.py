"""Pure-local checks for Chat Route -> Paper Agent integration."""

import importlib
import sys
import tempfile
from pathlib import Path
from types import ModuleType, SimpleNamespace


def _load_chat_route():
    """Import the route without installing the optional FastAPI/Pydantic packages."""
    fastapi = ModuleType("fastapi")

    class HTTPException(Exception):
        def __init__(self, status_code: int, detail: str) -> None:
            super().__init__(detail)
            self.status_code = status_code
            self.detail = detail

    class APIRouter:
        def post(self, *_args, **_kwargs):
            return lambda function: function

    fastapi.APIRouter = APIRouter
    fastapi.HTTPException = HTTPException
    responses = ModuleType("fastapi.responses")
    responses.StreamingResponse = type("StreamingResponse", (), {})
    fastapi.responses = responses
    pydantic = ModuleType("pydantic")
    pydantic.BaseModel = type("BaseModel", (), {})
    pydantic.Field = lambda default=None, **_kwargs: default
    sys.modules["fastapi"] = fastapi
    sys.modules["fastapi.responses"] = responses
    sys.modules["pydantic"] = pydantic
    return importlib.import_module("app.api.routes.chat")


def _parsed_paper() -> SimpleNamespace:
    section_text = "内容。" * 100
    return SimpleNamespace(
        parser_name="pymupdf",
        title="Chat Agent Test Paper",
        authors=["Test Author"],
        abstract="用于验证 Chat 与 Paper Agent 集成。",
        parser_warnings=[],
        parser_meta={"year": "2026", "venue": "Local Venue"},
        raw_text="\n".join(
            [
                "摘要",
                "用于章节提取测试的摘要。",
                "1 引言",
                section_text,
                "2 相关工作",
                section_text,
                "3 方法",
                section_text,
                "4 实验",
                section_text,
                "5 结论",
                section_text,
            ]
        ),
    )


def _agent_runner(response_batches, records):
    from app.agents.paper_agent import run_paper_agent

    def runner(message, **kwargs):
        responses = response_batches.pop(0)
        llm_calls = []

        def llm(messages, tools):
            llm_calls.append((list(messages), list(tools)))
            return responses[len(llm_calls) - 1]

        result = run_paper_agent(message, llm_call=llm, **kwargs)
        records.append(
            {
                "message": message,
                "paper_id": kwargs["paper_id"],
                "history": list(kwargs["history"]),
                "system_context": kwargs["system_context"],
                "llm_calls": llm_calls,
                "result": result,
            }
        )
        return result

    return runner


def _request(message: str, session_id: str, paper_id: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(message=message, session_id=session_id, paper_id=paper_id)


def _load_test_memory(chat) -> str:
    original_memory_dir = chat.MEMORY_DIR
    try:
        with tempfile.TemporaryDirectory() as directory:
            memory_dir = Path(directory)
            (memory_dir / "soul.md").write_text("Soul test context", encoding="utf-8")
            (memory_dir / "user.md").write_text("User test context", encoding="utf-8")
            (memory_dir / "memory.md").write_text("Memory test context", encoding="utf-8")
            chat.MEMORY_DIR = memory_dir
            context = chat._load_memory_context()
            assert all(text in context for text in ("Soul test", "User test", "Memory test"))

            chat.MEMORY_DIR = memory_dir / "missing"
            assert chat._load_memory_context() == "暂无可用长期记忆。"
            return context
    finally:
        chat.MEMORY_DIR = original_memory_dir


def main() -> None:
    chat = _load_chat_route()
    from app.agents.agent_loop import AgentMaxStepsError
    from app.services import session_service
    from app.services.llm_service import AgentLLMResponse, AgentToolCall
    from app.tools import paper_tools

    original_config = chat.get_llm_config
    original_runner = chat.run_paper_agent
    original_memory_loader = chat._load_memory_context
    original_loader = paper_tools._load_paper
    original_session_dir = session_service.CHAT_SESSIONS_DIR
    session_directory = tempfile.TemporaryDirectory()
    chat.get_llm_config = lambda: SimpleNamespace(provider="deepseek")
    memory_context = _load_test_memory(chat)
    chat._load_memory_context = lambda: memory_context
    paper_tools._load_paper = lambda _paper_id: (_parsed_paper(), "zh", "pymupdf")
    session_service.CHAT_SESSIONS_DIR = Path(session_directory.name)
    try:
        chat.SESSION_HISTORY.clear()
        records = []
        chat.run_paper_agent = _agent_runner([[AgentLLMResponse("我可以帮你阅读论文。", [])]], records)
        response = chat.chat(_request("你能做什么？", "plain"))
        assert response["reply"] == "我可以帮你阅读论文。"
        assert records[0]["paper_id"] is None

        chat.SESSION_HISTORY.clear()
        records = []
        chat.run_paper_agent = _agent_runner(
            [
                [
                    AgentLLMResponse("", [AgentToolCall("info", "get_paper_info", '{"paper_id":"p1"}')]),
                    AgentLLMResponse("标题是 Chat Agent Test Paper。", []),
                ],
                [
                    AgentLLMResponse("", [AgentToolCall("sections", "extract_sections", '{"paper_id":"p1"}')]),
                    AgentLLMResponse("主要章节已整理。", []),
                ],
            ],
            records,
        )
        first = chat.chat(_request("这篇论文标题是什么？", "history-s1", "p1"))
        second = chat.chat(_request("它有哪些主要章节？", "history-s1"))
        assert first["reply"] == "标题是 Chat Agent Test Paper。"
        assert second["reply"] == "主要章节已整理。"
        assert [record["paper_id"] for record in records] == ["p1", "p1"]
        assert chat.SESSION_HISTORY["history-s1"].paper_id == "p1"
        assert [item["role"] for item in chat.SESSION_HISTORY["history-s1"].messages] == [
            "user",
            "assistant",
            "user",
            "assistant",
        ]
        assert all(item["role"] != "tool" for item in chat.SESSION_HISTORY["history-s1"].messages)
        history_messages = records[1]["llm_calls"][0][0]
        assert [item["role"] for item in history_messages] == ["system", "user", "assistant", "user"]
        assert sum(item["content"] == "它有哪些主要章节？" for item in history_messages) == 1
        assert all(text in history_messages[0]["content"] for text in ("Soul test", "User test", "Memory test"))
        assert any(
            event.get("event") == "tool_succeeded" for event in records[1]["result"].context.metadata["agent_trace"]
        )

        chat.SESSION_HISTORY.clear()
        records = []
        chat.run_paper_agent = _agent_runner(
            [[AgentLLMResponse("ok", [])] for _ in range(7)], records
        )
        chat.chat(_request("a", "isolate-s1", "p1"))
        chat.chat(_request("b", "isolate-s2", "p2"))
        chat.chat(_request("c", "isolate-s1"))
        chat.chat(_request("d", "isolate-s2"))
        chat.chat(_request("e", "switch", "p1"))
        chat.chat(_request("f", "switch", "p2"))
        chat.chat(_request("g", "switch"))
        assert [record["paper_id"] for record in records] == ["p1", "p2", "p1", "p2", "p1", "p2", "p2"]

        chat.run_paper_agent = lambda *_args, **_kwargs: (_ for _ in ()).throw(AgentMaxStepsError(8, 8))
        try:
            chat.chat(_request("失败场景", "error"))
        except chat.HTTPException as exc:
            assert exc.status_code == 502 and "最大推理步数" in exc.detail
        else:
            raise AssertionError("Expected a safe AgentMaxStepsError response")
    finally:
        chat.get_llm_config = original_config
        chat.run_paper_agent = original_runner
        chat._load_memory_context = original_memory_loader
        paper_tools._load_paper = original_loader
        session_service.CHAT_SESSIONS_DIR = original_session_dir
        session_directory.cleanup()

    print("ALL CHAT AGENT INTEGRATION TESTS PASSED")


if __name__ == "__main__":
    main()
