"""PaperPilot 浏览器对话 API。"""

from collections.abc import Iterator
import json
import logging
from pathlib import Path
from queue import Queue
from threading import Thread

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.agents.agent_loop import AgentMaxStepsError
from app.agents.paper_agent import run_paper_agent
from app.core.config import get_llm_config
from app.services.llm_service import call_llm_with_tools_stream
from app.services.session_service import ChatSessionState, load_session, save_session


router = APIRouter()
logger = logging.getLogger(__name__)

BACKEND_DIR = Path(__file__).resolve().parents[3]
MEMORY_DIR = BACKEND_DIR / "memory"
MEMORY_FILES = ("soul.md", "memory.md", "user.md")

# 使用内存字典缓存已加载 session；JSON 文件在重启后恢复语义历史。
SESSION_HISTORY: dict[str, ChatSessionState] = {}


class ChatRequest(BaseModel):
    """聊天请求体。"""

    message: str = Field(..., min_length=1)
    session_id: str = Field(..., min_length=1, max_length=512)
    paper_id: str | None = Field(default=None, min_length=1)


class ChatResponse(BaseModel):
    """聊天响应体。"""

    reply: str


def _read_memory_file(filename: str) -> str:
    """读取单个 memory 文件；读取失败时静默降级。"""
    try:
        path = MEMORY_DIR / filename
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8").strip()
    except Exception:
        return ""


def _load_memory_context() -> str:
    """读取长期记忆，供每轮 Paper Agent 的 system prompt 使用。"""
    sections: list[str] = []
    for filename in MEMORY_FILES:
        content = _read_memory_file(filename)
        if content:
            sections.append(f"## {filename}\n{content}")

    memory_text = "\n\n".join(sections)
    if not memory_text:
        memory_text = "暂无可用长期记忆。"

    return memory_text


def _mock_chat_reply(message: str) -> str:
    """mock 模式下返回稳定占位回复，避免误触真实外部 LLM。"""
    return (
        "我已收到你的问题。当前后端处于 `LLM_PROVIDER=mock` 模式，"
        "所以这里先返回本地占位回复。\n\n"
        f"你的问题是：{message}\n\n"
        "切换到 `deepseek` 或 `openai_compatible` 并配置 API Key 后，"
        "我会通过 Paper Agent 结合当前论文和长期记忆进行真实多轮回答。"
    )


def _prepare_chat(request: ChatRequest) -> tuple[str, str, ChatSessionState, str | None]:
    """Load one session and apply this request's optional paper binding."""
    session_id = request.session_id.strip()
    message = request.message.strip()
    if not session_id or not message:
        raise HTTPException(status_code=400, detail="message 和 session_id 不能为空。")

    requested_paper_id = request.paper_id.strip() if request.paper_id else None
    if request.paper_id is not None and not requested_paper_id:
        raise HTTPException(status_code=400, detail="paper_id 不能为空。")

    session = SESSION_HISTORY.get(session_id)
    if session is None:
        session = load_session(session_id) or ChatSessionState()
        SESSION_HISTORY[session_id] = session
    if requested_paper_id:
        session.paper_id = requested_paper_id
        if requested_paper_id not in session.active_paper_ids:
            session.active_paper_ids.append(requested_paper_id)
    return session_id, message, session, session.paper_id


def _save_chat_turn(session_id: str, session: ChatSessionState, message: str, reply: str) -> None:
    """Persist semantic history only after a final answer exists."""
    session.messages.append({"role": "user", "content": message})
    session.messages.append({"role": "assistant", "content": reply})
    try:
        save_session(session_id, session)
    except (OSError, ValueError):
        logger.exception("Failed to persist chat session %s", session_id)


def _sse(event: dict[str, object]) -> str:
    """Encode exactly one complete JSON event as one SSE message."""
    return f"event: agent\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"


def _stream_agent_events(
    *,
    session_id: str,
    message: str,
    session: ChatSessionState,
    paper_id: str | None,
) -> Iterator[str]:
    """Bridge one synchronous Agent run into a safe SSE event iterator."""
    queue: Queue[dict[str, object] | None] = Queue()

    def run() -> None:
        try:
            result = run_paper_agent(
                message,
                paper_id=paper_id,
                session_id=session_id,
                history=list(session.messages),
                system_context=_load_memory_context(),
                active_paper_ids=session.active_paper_ids,
                llm_stream=call_llm_with_tools_stream,
                event_sink=queue.put,
            )
            _save_chat_turn(session_id, session, message, result.final_answer)
        except AgentMaxStepsError:
            logger.warning("Paper Agent reached max steps for session %s", session_id)
        except ValueError:
            logger.warning("Invalid Paper Agent request for session %s", session_id)
        except Exception:
            logger.exception("Paper Agent stream failed for session %s", session_id)
        finally:
            queue.put(None)

    worker = Thread(target=run, daemon=True)
    worker.start()
    try:
        while (event := queue.get()) is not None:
            yield _sse(event)
    finally:
        # ponytail: disconnect does not cancel an active provider HTTP call; add
        # request cancellation tokens when stop-generation behavior is required.
        pass


@router.post("", response_model=ChatResponse)
def chat(request: ChatRequest) -> dict[str, str]:
    """处理前端聊天消息，并按 session_id 维护论文上下文和语义历史。"""
    session_id, message, session, paper_id = _prepare_chat(request)

    config = get_llm_config()
    if config.provider == "mock":
        reply = _mock_chat_reply(message)
    else:
        try:
            result = run_paper_agent(
                message,
                paper_id=paper_id,
                session_id=session_id,
                history=list(session.messages),
                system_context=_load_memory_context(),
                active_paper_ids=session.active_paper_ids,
            )
        except AgentMaxStepsError as exc:
            logger.warning("Paper Agent reached max steps for session %s", session_id)
            raise HTTPException(status_code=502, detail="Paper Agent 达到最大推理步数。") from exc
        except ValueError as exc:
            logger.warning("Invalid Paper Agent request for session %s", session_id)
            raise HTTPException(status_code=400, detail="聊天请求无效。") from exc
        except Exception as exc:
            logger.exception("Paper Agent request failed for session %s", session_id)
            raise HTTPException(status_code=502, detail="Paper Agent 请求失败。") from exc
        reply = result.final_answer

    _save_chat_turn(session_id, session, message, reply)
    return {"reply": reply}


@router.post("/stream")
def chat_stream(request: ChatRequest) -> StreamingResponse:
    """Stream safe Agent lifecycle events and final LLM deltas as SSE."""
    session_id, message, session, paper_id = _prepare_chat(request)
    return StreamingResponse(
        _stream_agent_events(
            session_id=session_id,
            message=message,
            session=session,
            paper_id=paper_id,
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
