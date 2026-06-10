"""PaperPilot 浏览器对话 API。"""

from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.core.config import get_llm_config
from app.services.llm_service import call_llm_text


router = APIRouter()

BACKEND_DIR = Path(__file__).resolve().parents[3]
MEMORY_DIR = BACKEND_DIR / "memory"
MEMORY_FILES = ("soul.md", "memory.md", "user.md")

# 使用内存字典保存当前进程内的多轮对话，服务重启后会清空。
SESSION_HISTORY: dict[str, list[dict[str, str]]] = {}


class ChatRequest(BaseModel):
    """聊天请求体。"""

    message: str = Field(..., min_length=1)
    session_id: str = Field(..., min_length=1)


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


def _load_memory_prompt() -> str:
    """启动时读取长期记忆，拼接为系统提示词。"""
    sections: list[str] = []
    for filename in MEMORY_FILES:
        content = _read_memory_file(filename)
        if content:
            sections.append(f"## {filename}\n{content}")

    memory_text = "\n\n".join(sections)
    if not memory_text:
        memory_text = "暂无可用长期记忆。"

    return (
        "你是 PaperPilot 的论文精读聊天助手。"
        "请用简洁、清晰、严谨的中文回答。"
        "如果用户围绕论文、Agent、LLM、RAG 或工具调用提问，优先关注方法论、创新点、实验设计和可复现性。"
        "不要编造论文内容；不确定时说明需要更多上下文。\n\n"
        f"{memory_text}"
    )


# 模块导入时加载 memory，满足“启动时读取”的要求。
SYSTEM_PROMPT = _load_memory_prompt()


def _build_user_prompt(history: list[dict[str, str]], message: str) -> str:
    """把当前 session 历史压缩成一次 LLM 输入。"""
    recent_history = history[-12:]
    lines = ["下面是当前会话的最近历史："]
    for item in recent_history:
        role = "用户" if item["role"] == "user" else "助手"
        lines.append(f"{role}：{item['content']}")
    lines.append("")
    lines.append(f"用户最新问题：{message}")
    return "\n".join(lines)


def _mock_chat_reply(message: str) -> str:
    """mock 模式下返回稳定占位回复，避免误触真实外部 LLM。"""
    return (
        "我已收到你的问题。当前后端处于 `LLM_PROVIDER=mock` 模式，"
        "所以这里先返回本地占位回复。\n\n"
        f"你的问题是：{message}\n\n"
        "切换到 `deepseek` 或 `openai_compatible` 并配置 API Key 后，"
        "我会结合 `backend/memory/` 中的长期记忆进行真实多轮回答。"
    )


@router.post("", response_model=ChatResponse)
def chat(request: ChatRequest) -> dict[str, str]:
    """处理前端聊天消息，并按 session_id 维护内存历史。"""
    session_id = request.session_id.strip()
    message = request.message.strip()
    if not session_id or not message:
        raise HTTPException(status_code=400, detail="message 和 session_id 不能为空。")

    history = SESSION_HISTORY.setdefault(session_id, [])

    config = get_llm_config()
    if config.provider == "mock":
        reply = _mock_chat_reply(message)
    else:
        user_prompt = _build_user_prompt(history, message)
        reply = call_llm_text(SYSTEM_PROMPT, user_prompt)

    history.append({"role": "user", "content": message})
    history.append({"role": "assistant", "content": reply})
    return {"reply": reply}
