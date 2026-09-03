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
        "Agent Runtime",
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
    ):
        assert text in page
    print("ALL AGENT RUNTIME UI TESTS PASSED")


if __name__ == "__main__":
    main()
