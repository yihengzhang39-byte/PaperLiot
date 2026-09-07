"""Static regression checks for the event-driven Agent Runtime renderer."""

from pathlib import Path


def main() -> None:
    page = (Path(__file__).resolve().parents[1] / "static" / "index.html").read_text(encoding="utf-8")
    assert "正在思考，请稍候" not in page
    assert "思考过程" not in page
    assert "attachment: options.attachment || null" in page
    assert 'addMessage("user", text, { attachment: sentAttachment })' in page
    assert "message.attachment?.filename" in page
    for text in (
        "Agent 执行过程",
        'case "assistant_trace"',
        'traceTitle.textContent = "LLM:"',
        "successfulToolCalls",
        "text-emerald-600",
        "toolCallId: event.tool_call_id",
        "正在分析请求...",
        "正在尝试恢复...",
        "正在执行",
        "Tool Result:",
        "正在生成回答...",
        'case "tool_start"',
        'case "tool_result"',
        'case "final_start"',
        "updateMessage(messageId, { activityCollapsed: true })",
        "function renderMessages(shouldScroll = true)",
        "}, false));",
        'case "final_delta"',
        'case "agent_done"',
        'case "error"',
        'CURRENT_SESSION_STORAGE_KEY = "paperpilot_current_session_id"',
        "function replayPersistedEvents(events, incompleteTurnIds)",
        "function applyEventToConversationState(messageId, event)",
        'fetch(`/api/chat/sessions/${encodeURIComponent(sessionId)}`)',
        'fetch("/api/chat/sessions")',
        "function applySessionHistory(sessions)",
        "async function refreshSessionHistory()",
        "let isGenerating = false;",
        "const streamSessionId = activeSessionId;",
        'deleteButton.setAttribute("aria-label", "删除对话")',
        "session-delete-button",
        "session-delete-tooltip",
        'viewBox="0 0 24 24"',
        "event.stopPropagation();",
        'confirm("确定删除这个对话吗？")',
        'method: "DELETE"',
        "async function deleteConversation(sessionId)",
        "async function initializeConversation()",
        "initializeConversation();",
        'id="turnInspector"',
        'id="turnInspectorResizeHandle"',
        'id="closeTurnInspectorButton"',
        "function startTurnInspectorResize(event)",
        "function stopTurnInspectorResize()",
        "function resizeTurnInspector(event)",
        "inspectorResizeStart.width + inspectorResizeStart.x - event.clientX",
        'document.removeEventListener("mousemove", resizeTurnInspector)',
        'document.removeEventListener("mouseup", stopTurnInspectorResize)',
        "function openTurnInspector(sessionId, turnId)",
        "function closeTurnInspector()",
        "function renderTurnTrace(trace)",
        "function renderTraceStep(step)",
        "function renderLlmCall(call)",
        "function renderToolCall(call)",
        "function renderToolResult(result)",
        "function formatTraceJson(value)",
        "Current Run Result",
        "History Projection",
        "History / Current:",
        "Reduction:",
        "origin:",
        "pre.textContent =",
        "message.sessionId && message.turnId",
        "openTurnInspector(message.sessionId, message.turnId)",
        "sessionId: options.sessionId || null",
        "turnId: options.turnId || null",
        'addMessage("user", data.content || "", { sessionId: activeSessionId, turnId });',
        'fetch(`/api/chat/sessions/${encodeURIComponent(sessionId)}/turns/${encodeURIComponent(turnId)}/trace`)',
        "if (sessionId !== activeSessionId)",
        "await restoreConversation(streamSessionId);",
        'response.status === 404 ? "该 Turn 没有可用的 Debug Trace。"',
        "执行中或未完整结束；可稍后重新点击轨迹刷新。",
    ):
        assert text in page
    assert "event.button !== 0 || window.innerWidth < 768" not in page
    assert "window.innerWidth < 768 ? window.innerWidth * 0.94" in page
    print("ALL AGENT RUNTIME UI TESTS PASSED")


if __name__ == "__main__":
    main()
