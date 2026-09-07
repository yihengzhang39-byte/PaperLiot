"""Configurable LLM service for paper reading tasks."""

import json
import logging
import re
from time import perf_counter
from contextvars import ContextVar, Token
from dataclasses import dataclass
from collections.abc import Iterator
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.core.config import LLMConfig, get_llm_config
from app.services.debug_trace_service import DebugTraceService


logger = logging.getLogger(__name__)
_LLM_INPUT_DIAG_SESSION_ID: ContextVar[str | None] = ContextVar("llm_input_diag_session_id", default=None)
_LLM_DEBUG_TRACE: ContextVar[tuple[DebugTraceService, int, list[dict[str, Any]]] | None] = ContextVar("llm_debug_trace", default=None)
_DIAG_SECRET_VALUE = re.compile(
    r"(?i)\b(api[_ -]?key|authorization|secret|token|password|credential)\b\s*[:=]\s*(?:\"[^\"]*\"|'[^']*'|\S+)"
)
_DIAG_BEARER_TOKEN = re.compile(r"(?i)\bbearer\s+[a-z0-9._-]+")
_DIAG_API_KEY = re.compile(r"\bsk-[a-zA-Z0-9_-]{8,}\b")


def set_llm_input_diagnostic_session(session_id: str) -> Token[str | None]:
    """Bind a chat session to temporary provider-input diagnostics."""
    return _LLM_INPUT_DIAG_SESSION_ID.set(session_id)


def reset_llm_input_diagnostic_session(token: Token[str | None]) -> None:
    """Clear the temporary provider-input diagnostic binding."""
    _LLM_INPUT_DIAG_SESSION_ID.reset(token)


def set_llm_debug_trace(trace: DebugTraceService, step: int, origins: list[dict[str, Any]]) -> Token[Any]:
    """Bind sidecar origins until the provider-facing request body is ready."""
    return _LLM_DEBUG_TRACE.set((trace, step, origins))


def reset_llm_debug_trace(token: Token[Any]) -> None:
    _LLM_DEBUG_TRACE.reset(token)


def _capture_provider_input(messages: list[dict[str, Any]]) -> None:
    binding = _LLM_DEBUG_TRACE.get()
    if binding is not None:
        trace, step, origins = binding
        trace.record_llm_input(messages, origins, step=step)


def diagnostic_summary(value: object, limit: int = 180) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    text = _DIAG_SECRET_VALUE.sub(r"\1=<redacted>", text)
    text = _DIAG_BEARER_TOKEN.sub("Bearer <redacted>", text)
    text = _DIAG_API_KEY.sub("<redacted-api-key>", text)
    return text[:limit] + ("…" if len(text) > limit else "")


def _log_llm_input_diagnostic(messages: list[dict[str, Any]]) -> None:
    lines: list[str] = []
    for index, message in enumerate(messages):
        role = str(message.get("role", "unknown"))
        if role == "tool":
            lines.append(
                f"{index} tool: tool_call_id={message.get('tool_call_id', '')} "
                f"name={message.get('name', '')} content_length={len(str(message.get('content', '') or ''))}"
            )
            continue
        lines.append(f"{index} {role}: {diagnostic_summary(message.get('content', ''))!r}")
    logger.warning(
        "[LLM INPUT DIAG]\nsession_id=%s\nmessage_count=%d\n%s",
        _LLM_INPUT_DIAG_SESSION_ID.get() or "unavailable",
        len(messages),
        "\n".join(lines),
    )


@dataclass(frozen=True)
class AgentToolCall:
    """Provider-neutral function call returned by an LLM."""

    id: str
    name: str
    arguments: str | dict[str, Any]


@dataclass(frozen=True)
class AgentLLMResponse:
    """Minimal response shape used by the generic agent loop."""

    content: str
    tool_calls: list[AgentToolCall]
    usage: dict[str, Any] | None = None
    finish_reason: str | None = None
    elapsed_ms: float | None = None


@dataclass(frozen=True)
class AgentToolCallDelta:
    """One OpenAI-compatible streamed tool-call fragment."""

    index: int
    id: str = ""
    name: str = ""
    arguments_delta: str = ""


@dataclass(frozen=True)
class AgentLLMDelta:
    """Provider-neutral streamed LLM content and tool-call fragments."""

    content: str = ""
    tool_calls: list[AgentToolCallDelta] | None = None
    usage: dict[str, Any] | None = None
    finish_reason: str | None = None


class LLMProviderError(RuntimeError):
    """Retain provider evidence without putting raw response bodies in public errors."""

    def __init__(self, provider: str, status_code: int | None, body: str) -> None:
        self.provider = provider
        self.status_code = status_code
        self.body = body
        try:
            payload = json.loads(body)
        except (ValueError, TypeError):
            payload = {}
        error = payload.get("error", payload) if isinstance(payload, dict) else {}
        error = error if isinstance(error, dict) else {}
        self.error_code = error.get("code")
        self.error_type = error.get("type")
        self.param = error.get("param")
        self.provider_message = str(error.get("message", ""))
        explicit_code = str(self.error_code) in {
            "context_length_exceeded", "context_window_exceeded", "input_tokens_exceeded",
        } or str(self.error_type) in {"context_length_exceeded", "context_window_exceeded"}
        explicit_message = bool(re.search(
            r"maximum context length.{0,100}(?:exceed|requested|resulted)|"
            r"(?:exceed\w*.{0,60}(?:context (?:length|window)|input token limit))|"
            r"(?:context (?:length|window)|input token limit).{0,60}exceed",
            self.provider_message, re.IGNORECASE,
        ))
        self.is_context_overflow = provider in {"deepseek", "openai_compatible"} and status_code in {None, 400, 413, 422} and (explicit_code or explicit_message)
        self.code = "provider_context_exceeded" if self.is_context_overflow else "llm_provider_error"
        super().__init__(
            "Provider 拒绝了超出上下文容量的请求。"
            if self.is_context_overflow else f"LLM provider HTTP error {status_code}." if status_code is not None else "LLM provider stream error."
        )


class LLMStreamInterruptedError(RuntimeError):
    """Partial public content/tool fragments cannot be safely replayed."""

    code = "llm_stream_interrupted"

    def __init__(self) -> None:
        super().__init__("回答流中断，已收到正文或部分工具调用，未自动重放；部分工具调用不会执行。")


class LLMOutputTruncatedError(RuntimeError):
    """A length-limited answer or tool call must not be treated as completed."""

    code = "llm_output_truncated"

    def __init__(self, response: AgentLLMResponse) -> None:
        self.response = response
        super().__init__("LLM 输出达到生成上限而被截断；不会自动重试已截断的输出。")


"""
找论文文本里第一行有效内容
"""
def _first_non_empty_line(text: str) -> str:
    """Return the first meaningful line from text."""
    for line in text.splitlines():
        cleaned = line.strip()
        if cleaned and not cleaned.startswith("--- Page"):
            return cleaned
    return ""

"""把 AI 输出转成标准列表，防止格式错乱，方便后续处理"""
def _ensure_list(value: Any) -> list[str]:
    """Normalize a model output value to a list of strings."""
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []

"""
AI 经常返回带 json 标记的内容，这个函数删掉标记，只留纯 JSON，增加解析成功率
"""
def _clean_json_content(content: str) -> str:
    """Remove common Markdown code fences before JSON parsing."""
    cleaned = content.strip()
    fence_match = re.search(r"```(?:json)?\s*(.*?)```", cleaned, re.DOTALL | re.IGNORECASE)
    if fence_match:
        cleaned = fence_match.group(1).strip()

    if not cleaned.startswith("{"):
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start != -1 and end != -1 and end > start:
            cleaned = cleaned[start : end + 1]

    return cleaned

"""
解析 JSON，解析失败就报错，告诉你 AI 返回了无效数据
"""
def _parse_json_response(content: str) -> dict[str, Any]:
    """Parse a JSON object from LLM response text."""
    cleaned = _clean_json_content(content)
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        preview = content[:500].replace("\n", "\\n")
        raise ValueError(f"LLM returned invalid JSON: {exc}. Response preview: {preview}") from exc

    if not isinstance(parsed, dict):
        raise ValueError("LLM JSON response must be an object.")
    return parsed


def _chat_completion(system_prompt: str, user_prompt: str, *, expect_json: bool) -> str:
    """Call an OpenAI-compatible chat completion endpoint."""
    config = get_llm_config()
    _validate_real_llm_config(config)

    body: dict[str, Any] = {
        "model": config.model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": config.temperature,
    }
    if expect_json:
        body["response_format"] = {"type": "json_object"}

    message = _request_chat_completion(body, config)
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError("LLM returned an empty message.")
    return content


def _request_chat_completion(body: dict[str, Any], config: LLMConfig) -> dict[str, Any]:
    """Send one OpenAI-compatible chat completion request and return its message."""

    request = Request(
        url=f"{config.base_url}/chat/completions",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {config.api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urlopen(request, timeout=config.timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="ignore")
        raise LLMProviderError(config.provider, exc.code, error_body) from exc
    except URLError as exc:
        raise RuntimeError(f"Failed to connect to LLM provider: {exc.reason}") from exc
    except TimeoutError as exc:
        raise RuntimeError(f"LLM request timed out after {config.timeout} seconds.") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"LLM provider returned invalid response JSON: {exc}") from exc

    try:
        message = payload["choices"][0]["message"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError(f"Unexpected LLM response shape: {payload}") from exc
    if not isinstance(message, dict):
        raise RuntimeError(f"Unexpected LLM message shape: {message}")
    return {**message, "usage": payload.get("usage"), "finish_reason": payload["choices"][0].get("finish_reason")}


def _validate_real_llm_config(config: LLMConfig) -> None:
    """Validate required settings before calling a real LLM provider."""
    if config.provider not in {"deepseek", "openai_compatible"}:
        raise ValueError(f"Real LLM call is not available for provider: {config.provider}")
    if not config.api_key:
        raise ValueError("LLM_API_KEY is required for real LLM providers.")
    if not config.base_url:
        raise ValueError("LLM_BASE_URL is required for real LLM providers.")
    if not config.model:
        raise ValueError("LLM_MODEL is required for real LLM providers.")


def call_llm_text(system_prompt: str, user_prompt: str) -> str:
    """Call the configured real LLM and return raw text."""
    return _chat_completion(system_prompt, user_prompt, expect_json=False)


def call_llm_json(system_prompt: str, user_prompt: str) -> dict[str, Any]:
    """Call the configured real LLM and parse a JSON object response."""
    content = _chat_completion(system_prompt, user_prompt, expect_json=True)
    return _parse_json_response(content)


def call_llm_summary(messages: list[dict[str, Any]], *, config: LLMConfig, budget_config: Any) -> AgentLLMResponse:
    """One non-streaming, tool-free auxiliary request with its own output reserve."""
    from app.services.context_service import check_request

    check_request(messages, [], budget_config, model=config.model).require_sendable()
    started = perf_counter()
    if config.provider == "mock":
        # No fabricated summary in mock mode; callers retain their last valid context.
        return AgentLLMResponse("", [], finish_reason="stop", elapsed_ms=0)
    _validate_real_llm_config(config)
    body = {"model": config.model, "messages": messages, "temperature": config.temperature}
    _set_output_limit(body, budget_config.max_output_tokens, budget_config.output_token_parameter)
    message = _request_chat_completion(body, config)
    if message.get("tool_calls"):
        raise ValueError("summary_unexpected_tool_calls")
    return AgentLLMResponse(message.get("content") or "", [], usage=message.get("usage"), finish_reason=message.get("finish_reason"), elapsed_ms=(perf_counter() - started) * 1000)


def call_llm_with_tools(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    *,
    max_output_tokens: int | None = None,
    output_token_parameter: str = "max_tokens",
    config: LLMConfig | None = None,
) -> AgentLLMResponse:
    """Call the configured provider with OpenAI-compatible tool definitions."""
    config = config or get_llm_config()
    if config.provider == "mock":
        _capture_provider_input(messages)
        return AgentLLMResponse(
            content="当前处于 mock 模式，未执行工具调用。",
            tool_calls=[],
            finish_reason="stop",
        )

    _validate_real_llm_config(config)
    _log_llm_input_diagnostic(messages)
    body = {
        "model": config.model,
        "messages": messages,
        "tools": tools,
        "tool_choice": "auto",
        "temperature": config.temperature,
    }
    _set_output_limit(body, max_output_tokens, output_token_parameter)
    _capture_provider_input(body["messages"])
    message = _request_chat_completion(body, config)
    tool_calls: list[AgentToolCall] = []
    for index, tool_call in enumerate(message.get("tool_calls") or []):
        if not isinstance(tool_call, dict):
            continue
        function = tool_call.get("function")
        if not isinstance(function, dict):
            continue
        name = function.get("name")
        if not isinstance(name, str) or not name:
            continue
        arguments = function.get("arguments", "{}")
        if not isinstance(arguments, (str, dict)):
            arguments = "{}"
        tool_calls.append(
            AgentToolCall(
                id=str(tool_call.get("id") or f"call_{index + 1}"),
                name=name,
                arguments=arguments,
            )
        )

    content = message.get("content")
    return AgentLLMResponse(
        content=content if isinstance(content, str) else "", tool_calls=tool_calls,
        usage=message.get("usage"), finish_reason=message.get("finish_reason"),
    )


def _set_output_limit(body: dict[str, Any], limit: int | None, parameter: str) -> None:
    if parameter not in {"max_tokens", "max_completion_tokens"}:
        raise ValueError("Unsupported output token parameter.")
    if limit is not None:
        if type(limit) is not int or limit <= 0:
            raise ValueError("max_output_tokens must be a positive integer.")
        body[parameter] = limit


def _tool_call_deltas(value: Any) -> list[AgentToolCallDelta]:
    """Normalize streamed OpenAI-compatible tool-call fragments."""
    if not isinstance(value, list):
        return []
    deltas: list[AgentToolCallDelta] = []
    for position, item in enumerate(value):
        if not isinstance(item, dict):
            continue
        index = item.get("index", position)
        if not isinstance(index, int):
            continue
        function = item.get("function")
        function = function if isinstance(function, dict) else {}
        name = function.get("name", "")
        arguments = function.get("arguments", "")
        deltas.append(
            AgentToolCallDelta(
                index=index,
                id=str(item.get("id") or ""),
                name=name if isinstance(name, str) else "",
                arguments_delta=arguments if isinstance(arguments, str) else "",
            )
        )
    return deltas


def _stream_chat_completion(body: dict[str, Any], config: LLMConfig) -> Iterator[AgentLLMDelta]:
    """Yield real OpenAI-compatible SSE deltas without buffering the answer."""
    body = {**body, "stream": True, "stream_options": {"include_usage": True}}
    request = Request(
        url=f"{config.base_url}/chat/completions",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {config.api_key}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=config.timeout) as response:
            completed = False
            for raw_line in response:
                line = raw_line.decode("utf-8").strip()
                if not line.startswith("data:"):
                    continue
                payload_text = line[5:].strip()
                if payload_text == "[DONE]":
                    return
                try:
                    payload = json.loads(payload_text)
                    if not isinstance(payload, dict):
                        raise TypeError("SSE payload must be an object")
                    if "error" in payload:
                        raise LLMProviderError(config.provider, None, payload_text)
                    usage = payload.get("usage")
                    choices = payload.get("choices", [] if isinstance(usage, dict) else None)
                    if not isinstance(choices, list):
                        raise TypeError("SSE choices must be a list")
                    if choices == []:
                        if isinstance(usage, dict):
                            yield AgentLLMDelta(usage=usage)
                        continue
                    choice = choices[0]
                    if not isinstance(choice, dict):
                        raise TypeError("SSE choice must be an object")
                    delta = choice.get("delta") or {}
                    finish_reason = choice.get("finish_reason")
                    completed = completed or finish_reason is not None
                except (json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
                    raise RuntimeError("LLM streaming response contained an invalid SSE event.") from exc
                if not isinstance(delta, dict):
                    continue
                content = delta.get("content")
                tool_calls = _tool_call_deltas(delta.get("tool_calls"))
                if isinstance(content, str) or tool_calls or finish_reason is not None or isinstance(usage, dict):
                    yield AgentLLMDelta(
                        content=content if isinstance(content, str) else "", tool_calls=tool_calls,
                        usage=usage if isinstance(usage, dict) else None, finish_reason=finish_reason,
                    )
            if not completed:
                raise RuntimeError("LLM stream ended without a completion event.")
    except HTTPError as exc:
        raise LLMProviderError(config.provider, exc.code, exc.read().decode("utf-8", errors="replace")) from exc
    except URLError as exc:
        raise RuntimeError(f"Failed to connect to LLM provider: {exc.reason}") from exc
    except TimeoutError as exc:
        raise RuntimeError(f"LLM request timed out after {config.timeout} seconds.") from exc


def call_llm_with_tools_stream(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    *,
    max_output_tokens: int | None = None,
    output_token_parameter: str = "max_tokens",
    config: LLMConfig | None = None,
) -> Iterator[AgentLLMDelta]:
    """Yield native provider deltas for one tool-capable LLM turn."""
    config = config or get_llm_config()
    if config.provider == "mock":
        _capture_provider_input(messages)
        yield AgentLLMDelta(content="当前处于 mock 模式，未执行工具调用。", finish_reason="stop")
        return

    _validate_real_llm_config(config)
    _log_llm_input_diagnostic(messages)
    body = {
        "model": config.model,
        "messages": messages,
        "tools": tools,
        "tool_choice": "auto",
        "temperature": config.temperature,
    }
    _set_output_limit(body, max_output_tokens, output_token_parameter)
    _capture_provider_input(body["messages"])
    yield from _stream_chat_completion(body, config)


def mock_extract_paper_info(text: str) -> dict[str, object]:
    """Mock paper metadata extraction from the first part of raw text."""
    title = _first_non_empty_line(text) or "待识别论文标题"
    year_match = re.search(r"\b(19|20)\d{2}\b", text)

    abstract = ""
    abstract_match = re.search(
        r"(?is)\babstract\b\s*[:.\-]?\s*(.*?)(?:\n\s*(?:1\s+)?introduction\b|\n\s*keywords\b)",
        text,
    )
    if abstract_match:
        abstract = re.sub(r"\s+", " ", abstract_match.group(1)).strip()

    return {
        "title": title,
        "authors": ["Mock Author"],
        "year": year_match.group(0) if year_match else "未知",
        "venue": "Mock Venue",
        "abstract": abstract or "这里是 mock 摘要。后续可替换为真实 LLM 抽取结果。",
    }

def mock_analyze_method(text: str) -> dict[str, object]:
    """Mock method analysis with a stable structured response."""
    has_content = bool(text.strip())
    return {
        "problem": "本文关注的核心问题将在接入真实 LLM 后从论文中自动归纳。"
        if has_content
        else "暂无可分析内容。",
        "motivation": "研究动机将在后续由真实模型结合摘要、引言和方法章节生成。",
        "method_summary": "当前为 mock 方法总结：系统已完成章节解析与工作流串联，可替换为真实模型分析。",
        "innovation_points": [
            "提供从 PDF 到中文精读笔记的最小闭环。",
            "使用 LangGraph 将解析、抽取、分析和写作拆成可替换节点。",
        ],
        "limitations": [
            "当前章节识别依赖关键词规则，复杂论文版式下可能不稳定。",
            "当前分析内容为 mock，占位文本不代表真实论文结论。",
        ],
        "inspirations": [
            "后续可以把各节点升级为真实 LLM 调用。",
            "可以增加多论文对比、Related Work 生成和知识库检索。",
        ],
    }

def mock_analyze_experiment(text: str) -> dict[str, str]:
    """Mock experiment analysis with a stable structured response."""
    if not text.strip():
        summary = "未识别到实验章节，后续可用 LLM 进行更稳健的章节定位。"
    else:
        summary = "当前为 mock 实验分析：已捕获实验章节文本，后续可归纳数据集、指标、基线和主要结果。"

    return {"experiment_summary": summary}


def extract_sections_with_llm(raw_text: str) -> dict[str, str]:
    """Extract paper sections with the configured LLM provider."""
    section_keys = [
        "abstract",
        "introduction",
        "related_work",
        "method",
        "experiments",
        "conclusion",
    ]
    if get_llm_config().provider == "mock":
        return {key: "" for key in section_keys}

    system_prompt = (
        "你是一个严谨的论文结构识别助手。"
        "请只返回 JSON，不要返回 Markdown、解释或额外文本。"
        "你的任务是把原文内容归类到章节字段中，不要改写，不要总结，不要翻译。"
        "如果没有明确章节内容，对应字段返回空字符串。"
    )
    user_prompt = f"""请从下面论文文本中识别并归类章节内容，输出 JSON：

{{
  "abstract": "",
  "introduction": "",
  "related_work": "",
  "method": "",
  "experiments": "",
  "conclusion": ""
}}

要求：
- 只复制原文中对应章节的内容，不要总结或改写
- Related Work 和 Background 都可归入 related_work
- Method、Methods、Methodology、Approach、Proposed Method、Proposed Approach、Our Method、Our Approach 都可归入 method
- Experiments、Experimental Results、Evaluation、Results、Results and Discussion、Ablation Study 都可归入 experiments
- References 及其之后的内容不要放入 conclusion
- 没有明确章节时返回空字符串

论文文本：
{raw_text[:40000]}
"""
    data = call_llm_json(system_prompt, user_prompt)
    return {key: str(data.get(key, "") or "") for key in section_keys}


def extract_paper_info(
    text: str,
    missing_fields: list[str] | None = None,
    paper_language: str = "zh",
    parser_info: dict[str, object] | None = None,
) -> dict[str, object]:
    """Extract paper metadata with the configured provider."""
    if get_llm_config().provider == "mock":
        return mock_extract_paper_info(text)

    missing_fields = missing_fields or ["title", "authors", "year", "venue", "abstract"]
    parser_info = parser_info or {}
    language_note = "中文论文，摘要必须返回中文，不要翻译成英文。" if paper_language == "zh" else "英文论文，摘要返回英文。"
    system_prompt = (
        "你是一个严谨的论文阅读助手。"
        "请只返回 JSON，不要返回 Markdown、解释或额外文本。"
        "如果论文中信息不足，对应字段写“未明确提及”，不要编造。"
        "只能根据给定 raw_text 提取信息，不要覆盖 parser 已经给出的明确信息。"
    )
    user_prompt = f"""请从下面论文文本中补充缺失的论文基本信息，输出 JSON：

{{
  "title": "",
  "authors": [],
  "year": "",
  "venue": "",
  "abstract": ""
}}

需要补充的字段：
{missing_fields}

parser 已经明确提取的信息，不要覆盖：
{json.dumps(parser_info, ensure_ascii=False)}

要求：
- 只根据给定 raw_text 提取
- 不要翻译摘要
- {language_note}
- 不确定就返回“未明确提及”
- 输出 JSON

论文文本：
{text}
"""
    data = call_llm_json(system_prompt, user_prompt)
    return {
        "title": str(data.get("title", "未明确提及")),
        "authors": _ensure_list(data.get("authors", [])),
        "year": str(data.get("year", "未明确提及")),
        "venue": str(data.get("venue", "未明确提及")),
        "abstract": str(data.get("abstract", "未明确提及")),
    }


def analyze_method(text: str) -> dict[str, object]:
    """Analyze method-related content with the configured provider."""
    if get_llm_config().provider == "mock":
        return mock_analyze_method(text)

    system_prompt = (
        "你是一个资深计算机视觉/目标检测论文精读助手。"
        "请面向中文科研笔记写作，只返回 JSON。"
        "如果信息不足，字段写“未明确提及”，不要编造。"
    )
    user_prompt = f"""请分析下面论文的摘要、引言、相关工作和方法内容，输出 JSON：

{{
  "problem": "",
  "motivation": "",
  "method_summary": "",
  "innovation_points": [],
  "limitations": [],
  "inspirations": []
}}

要求：
- 用中文回答
- 重点关注计算机视觉/目标检测论文中的问题定义、核心方法、创新点、局限和研究启发
- 列表字段输出字符串数组

论文内容：
{text}
"""
    data = call_llm_json(system_prompt, user_prompt)
    return {
        "problem": str(data.get("problem", "未明确提及")),
        "motivation": str(data.get("motivation", "未明确提及")),
        "method_summary": str(data.get("method_summary", "未明确提及")),
        "innovation_points": _ensure_list(data.get("innovation_points", [])),
        "limitations": _ensure_list(data.get("limitations", [])),
        "inspirations": _ensure_list(data.get("inspirations", [])),
    }
def analyze_experiment(text: str) -> dict[str, str]:
    """Analyze experiment-related content with the configured provider."""
    if get_llm_config().provider == "mock":
        return mock_analyze_experiment(text)

    system_prompt = (
        "你是一个资深计算机视觉/目标检测论文实验分析助手。"
        "请只返回 JSON，不要返回 Markdown、解释或额外文本。"
        "如果信息不足，字段写“未明确提及”，不要编造。"
    )
    user_prompt = f"""请分析下面论文实验章节，输出 JSON：

{{
  "experiment_summary": ""
}}

要求：
- 用中文总结实验设计与结果
- 优先覆盖数据集、评价指标、对比基线、消融实验和主要结论

实验内容：
{text}
"""
    data = call_llm_json(system_prompt, user_prompt)
    return {"experiment_summary": str(data.get("experiment_summary", "未明确提及"))}
