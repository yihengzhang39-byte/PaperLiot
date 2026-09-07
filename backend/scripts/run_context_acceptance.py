"""Reproducible offline acceptance: isolated subprocess/storage for each script."""

import argparse
import logging
import os
from pathlib import Path
import runpy
import socket
import subprocess
import sys
import tempfile
from types import SimpleNamespace


CASES = (
    "test_context_recovery", "test_context_ui", "test_context_compaction", "test_session_run",
    "test_tool_result_control", "test_context_budget", "test_agent_loop", "test_agent_streaming",
    "test_paper_agent", "test_chat_agent_integration", "test_debug_trace_foundation",
    "test_turn_trace_projection", "test_turn_trace_api", "test_session_event_persistence",
    "test_tool_concurrency", "test_tool_runtime", "test_tool_contract", "test_memory_service",
    "test_model_history_projection", "test_tool_history_projection", "test_multi_paper", "test_retrieval",
    "test_paper_tools", "test_paper_delete", "test_paper_hash_dedup", "test_session_delete",
    "test_session_history", "test_session_persistence", "test_session_restore", "test_sqlite_persistence",
    "test_agent_runtime_ui",
)


def run_case(name):
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    # Do not read real .env files, and override only this child process's settings.
    sys.modules["dotenv"] = SimpleNamespace(load_dotenv=lambda *_a, **_kw: None)
    os.environ.update(LLM_PROVIDER="mock", LLM_API_KEY="", LLM_BASE_URL="", LLM_MODEL="",
        AGENT_CONTEXT_WINDOW="1000000", AGENT_MAX_OUTPUT_TOKENS="4096", AGENT_CONTEXT_SAFETY_TOKENS="1024",
        AGENT_MODEL_INPUT_LIMIT="", AGENT_MODEL_OUTPUT_LIMIT="", AGENT_CONTEXT_TRIGGER_RATIO="0.8",
        AGENT_CONTEXT_TARGET_RATIO="0.6", AGENT_CONTEXT_TARGET_BUDGET_RATIO="0.8", AGENT_OUTPUT_TOKEN_PARAMETER="max_tokens",
        COMPACTION_RECENT_RATIO="0.16", COMPACTION_TARGET_TOKENS="1200", COMPACTION_MAX_OUTPUT_TOKENS="4096",
        TOOL_RESULT_MAX_CHARS="12000", TOOL_READ_MAX_CHARS="2000", HARNESS_DEBUG_TRACE="true")
    def no_network(*_args, **_kwargs):
        raise AssertionError("Offline acceptance attempted a network connection")
    socket.socket.connect = no_network
    socket.socket.connect_ex = no_network
    logging.disable(logging.CRITICAL)
    with tempfile.TemporaryDirectory(prefix="paperpilot-acceptance-") as directory:
        from app.core import config
        root, previous = Path(directory), config.STORAGE_DIR
        for key, value in list(vars(config).items()):
            if isinstance(value, Path) and value.is_relative_to(previous):
                setattr(config, key, root / value.relative_to(previous))
        config.STORAGE_DIR = root
        runpy.run_module("scripts." + name, run_name="__main__")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", choices=CASES, help="Run only this case with offline isolation")
    args = parser.parse_args()
    if args.case:
        run_case(args.case)
        return
    failed = []
    for name in CASES:
        result = subprocess.run([sys.executable, "-B", str(Path(__file__).resolve()), "--case", name], text=True, capture_output=True)
        print(f"{name}: {'PASS' if result.returncode == 0 else 'FAIL'}", flush=True)
        if result.returncode:
            failed.append(name)
            print(result.stdout + result.stderr)
    print(f"{len(CASES) - len(failed)}/{len(CASES)} passed")
    raise SystemExit(bool(failed))


if __name__ == "__main__":
    main()
