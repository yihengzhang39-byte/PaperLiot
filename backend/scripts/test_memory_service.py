"""Pure-local checks for controlled research and user-profile memory write-back."""

import json
import tempfile
from pathlib import Path

from app.agents.paper_agent import run_paper_agent
from app.runtime.tool_executor import execute_tool_call
from app.runtime.tool_registry import ToolRegistry
from app.services import memory_service
from app.services.llm_service import AgentLLMResponse, AgentToolCall
from app.tools.memory_tools import register_memory_tools


def main() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        memory_file = root / "memory.md"
        soul_file = root / "soul.md"
        user_file = root / "user.md"
        soul_file.write_text("immutable soul", encoding="utf-8")
        user_file.write_text("# User Profile\nuniversity: 江南大学\nresearch_interest:\n  - RAG\n", encoding="utf-8")
        memory_file.write_text(
            "# PaperPilot Memory\n\n## 已分析论文列表\n\n## 用户关注的研究主题\n\n## 跨论文发现的规律\n",
            encoding="utf-8",
        )
        assert "PaperPilot" in memory_service.load_research_memory(memory_file=memory_file)
        first = memory_service.append_memory_entry("theme", "RAG", memory_file=memory_file)
        duplicate = memory_service.append_memory_entry("theme", " rag ", memory_file=memory_file)
        assert first["saved"] and not duplicate["saved"] and "- RAG" in memory_file.read_text(encoding="utf-8")
        try:
            memory_service.append_memory_entry("unknown", "value", memory_file=memory_file)
        except ValueError:
            pass
        else:
            raise AssertionError("Expected invalid memory category rejection")
        profile = memory_service.save_user_profile(
            university="南京理工大学",
            education="硕士研究生",
            research_interest=["Agent", "LLM", "RAG"],
            user_file=user_file,
        )
        corrected = memory_service.save_user_profile(university="南京理工大学", user_file=user_file)
        assert profile["saved"] and not corrected["saved"]
        user_content = user_file.read_text(encoding="utf-8")
        assert "university: 南京理工大学" in user_content and "  - RAG" in user_content
        replacement = memory_service.save_user_profile(research_interest=["LLM"], user_file=user_file)
        assert replacement["saved"]
        user_content = user_file.read_text(encoding="utf-8")
        assert "  - LLM" in user_content and "  - RAG" not in user_content
        try:
            memory_service.save_user_profile(research_interest=[""], user_file=user_file)
        except ValueError:
            pass
        else:
            raise AssertionError("Expected invalid user profile entry rejection")
        assert soul_file.read_text(encoding="utf-8") == "immutable soul"

        original_memory_file = memory_service.MEMORY_FILE
        original_user_file = memory_service.USER_PROFILE_FILE
        memory_service.MEMORY_FILE = memory_file
        memory_service.USER_PROFILE_FILE = user_file
        try:
            registry = ToolRegistry()
            register_memory_tools(registry)
            tool_result = execute_tool_call(
                AgentToolCall("memory", "save_research_memory", '{"category":"finding","entry":"检索增强在方法分析中有用"}'),
                registry,
            )
            assert tool_result.ok and json.loads(tool_result.content)["saved"]

            profile_result = execute_tool_call(
                AgentToolCall("profile", "save_user_profile", '{"technical_background":["Python","FastAPI"]}'),
                registry,
            )
            assert profile_result.ok and json.loads(profile_result.content)["saved"]

            untouched = memory_file.read_text(encoding="utf-8")
            direct = run_paper_agent(
                "这篇论文的创新是什么？",
                llm_call=lambda *_: AgentLLMResponse("普通问答，不保存记忆。", []),
            )
            assert direct.final_answer and memory_file.read_text(encoding="utf-8") == untouched

            responses = [
                AgentLLMResponse("", [AgentToolCall("save", "save_research_memory", '{"category":"paper","entry":"Paper p1: retrieval study"}')]),
                AgentLLMResponse("已记录。", []),
            ]
            calls = []

            def fake_llm(messages, tools):
                calls.append((list(messages), list(tools)))
                return responses[len(calls) - 1]

            saved = run_paper_agent("请记住这篇论文", llm_call=fake_llm)
            assert saved.final_answer == "已记录。" and "Paper p1" in memory_file.read_text(encoding="utf-8")
            assert "save_research_memory" in [tool["function"]["name"] for tool in calls[0][1]]

            profile_responses = [
                AgentLLMResponse("", [AgentToolCall("profile", "save_user_profile", '{"university":"南京理工大学"}')]),
                AgentLLMResponse("已更新个人档案。", []),
            ]
            profile_calls = []

            def profile_llm(messages, tools):
                profile_calls.append((list(messages), list(tools)))
                return profile_responses[len(profile_calls) - 1]

            saved_profile = run_paper_agent("我不是江南大学的，我是南京理工大学的", llm_call=profile_llm)
            assert saved_profile.final_answer == "已更新个人档案。"
            assert "save_user_profile" in [tool["function"]["name"] for tool in profile_calls[0][1]]
            assert "university: 南京理工大学" in user_file.read_text(encoding="utf-8")
        finally:
            memory_service.MEMORY_FILE = original_memory_file
            memory_service.USER_PROFILE_FILE = original_user_file

    print("ALL MEMORY SERVICE TESTS PASSED")


if __name__ == "__main__":
    main()
