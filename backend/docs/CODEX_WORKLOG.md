# Codex Worklog

## Purpose

This file records important Codex work sessions, decisions, and handoff notes.

## Log Format

每次工作后按以下格式追加：

```md
### YYYY-MM-DD HH:mm - Short Title

**Goal:**

**Files inspected:**

**Files changed:**

**Commands run:**

**Decisions made:**

**Validation:**

**Open questions / next steps:**

---
```

## Existing Context

- 项目名：PaperPilot。
- 项目类型：科研论文阅读 Agent 后端，当前聚焦单篇 PDF 论文精读和浏览器聊天式交互。
- 技术栈：Python、FastAPI、LangGraph、Pydantic、PyMuPDF、python-dotenv、requests、langchain-core；前端为单文件 HTML + Tailwind CDN + marked.js + highlight.js。
- 主要入口：`app/main.py`、`app/api/routes/paper.py`、`app/api/routes/chat.py`、`app/agents/paper_graph.py`、`scripts/run_analyze_paper.py`、`static/index.html`。
- 主要运行数据目录：`storage/papers/`、`storage/notes/`、`storage/paper_metadata/`、`storage/paper_section_json/`。
- 聊天长期记忆目录：`memory/soul.md`、`memory/user.md`、`memory/memory.md`。
- README、`AGENTS.md` 和 `CODEX_STATE.md` 是后续 Codex 的主要上下文入口。
- 从当前仓库无法确认更早的 Codex 工作历史；不要编造历史记录。

## Recent Work

### 2026-09-03 22:32 - DSH 风格 Agent Runtime

**Goal:**

将聊天中的“思考过程”和无限 loading 改为由 SSE Agent Event 驱动的真实运行阶段展示。

**Files changed:**

- `static/index.html`：保留现有聊天布局和折叠交互，名称改为“运行过程”；映射 Agent、Step、LLM 请求、Tool Call/Start/Result、最终回答和错误状态。
- `scripts/test_agent_runtime_ui.py`：静态验证 Runtime 映射完整、无“正在思考”文案及无 COT 内容渲染。

**Decisions made:**

- 不改 Agent Loop、Tool Runtime、SSE route 或 LangGraph；直接复用既有事件。
- Tool 执行中的状态只绑定 `tool_start`；`tool_result` 将其更新为完成/失败，下一次 `llm_start` 在失败后显示恢复状态。
- Runtime Trace 不展示 `llm_message`、`llm_delta` 或 hidden reasoning；最终文本仅由 `final_delta` 进入回答区域。

**Validation:**

- `test_agent_streaming`、`test_agent_runtime_ui`、内联 JavaScript 语法和 `git diff --check` 通过；未启动服务或调用真实 LLM/外网。

---

### 2026-09-03 17:47 - User Profile Memory

**Goal:**

为长期用户身份、院校、学历、兴趣、技术背景和偏好新增独立的受控写回路径，不混入 research memory。

**Files changed:**

- `app/services/memory_service.py`、`app/tools/memory_tools.py`：新增固定字段的 `save_user_profile`，仅写入 `user.md`；保留 `save_research_memory` 原行为。
- `app/agents/paper_agent.py`：仅在用户明确陈述或纠正长期档案信息时允许调用新 Tool。
- `memory/user.md`：转为固定字段的 YAML 格式，保留原有档案值。
- 本地 memory/paper-agent 回归：覆盖院校纠正、列表替换、Tool 注册和 research memory 隔离。

**Decisions made:**

- 标量字段覆盖更新；兴趣、技术背景和偏好列表由 Tool 提供完整目标值并整体替换，以支持删除旧条目。
- 不引入 YAML 依赖或可写路径参数；仅接受白名单字段，空值和错误类型被拒绝。

**Validation:**

- `test_memory_service`、`test_paper_agent`、`test_agent_loop`、`test_agent_streaming` 通过；未调用真实 LLM、外网或启动服务。

---

### 2026-09-03 17:10 - SSE 显示真实 LLM 与 Tool 事件

**Goal:**

将聊天“思考过程”从通用运行日志改为模型可见回复、Tool 调用参数和 Tool 结果摘要的完整步骤。

**Files changed:**

- `app/agents/agent_loop.py`：发送当前 `user_message`，每轮都发送 assistant `content`（包括空字符串）的 `llm_message`；流式内容发送 `llm_delta`，最终回答仍通过 `final_delta` 输出。
- `app/runtime/tool_executor.py`：`tool_result` 增加明确 `success` 字段。
- `static/index.html`：按“User Message / LLM Input / 步骤 / LLM / Tool Call / Tool Result / 最终回答”结构化展示；空模型 content 如实显示为 `content = ""`，参数以 JSON 显示，移除“正在分析/正在执行”等泛化日志。
- `scripts/test_agent_streaming.py`：覆盖带内容的流式 Tool Call 与非流式 Tool Call。

**Decisions made:**

- 仅发送 API 正常 assistant content、结构化 Tool Call 与安全结果摘要；不发送 hidden reasoning 或完整 Tool 原始 payload。

**Validation:**

- 本地 Agent Loop、streaming、Tool Runtime、Chat integration 回归及内联 JavaScript 语法检查通过；未启动服务或调用真实 LLM/外网。

---

### 2026-09-03 16:45 - Agent 自主选择 PDF Parser Tool

**Goal:**

取消基于 `paper_language` 的 parser 强制路由和 GROBID→PyMuPDF 内部 fallback；普通聊天改由 LLM 在现有 Agent Loop 中自主调用 GROBID/PyMuPDF Tool，旧 LangGraph API 保留并仅使用 `PDF_PARSER`。

**Files inspected:**

- `backend/app/services/parser_service.py`、`app/services/parsers/`
- `backend/app/tools/paper_tools.py`、`app/runtime/tool_executor.py`、`app/agents/paper_agent.py`
- `backend/app/agents/nodes/pdf_parse_node.py`、`paper_graph.py`、`app/api/routes/paper.py`
- `backend/app/services/file_service.py`、`retrieval_service.py`
- 既有 Agent、Tool、retrieval、delete、chat integration 与 streaming 测试脚本

**Files changed:**

- `parser_service.py`、`pdf_parse_node.py`：删除 language router 和 parser 内部 fallback；旧 LangGraph 仅用 `PDF_PARSER`。
- `paper_tools.py`：新增 `parse_pdf_with_grobid`、`parse_pdf_with_pymupdf`；现有论文信息、章节和 retrieval Tool 仅消费 parser cache。
- `config.py`、`file_service.py`、`retrieval_service.py`：增加按 `paper_id + parser` 隔离的 parser cache/chunk index，并在删除 paper 时清理。
- `grobid_parser.py`：补充 TEI keywords 和 references 提取。
- `tool_executor.py`、`paper_agent.py`：增加 parser Tool 的安全 SSE 摘要与自主选择提示。
- README、CLI help、Codex 状态及相关本地测试：同步新契约。

**Commands run:**

- Python AST 全量检查
- `scripts.test_agent_loop`、`test_tool_runtime`、`test_paper_tools`、`test_paper_agent`
- `test_chat_agent_integration`、`test_retrieval`、`test_session_persistence`、`test_memory_service`
- `test_multi_paper`、`test_paper_delete`、`test_agent_streaming`、`test_parser_selection`

**Decisions made:**

- GROBID 缺少 title/authors 仍是成功 Tool Result；真正失败也返回结构化失败结果，绝不自动调用 PyMuPDF。
- `parse_pdf_with_pymupdf` 默认读取第一页，可显式传 `pages`；第一次解析结果会缓存，后续同 parser Tool 不重复解析。
- 未引入额外 `PaperDocument` 抽象；既有 `ParsedPaper` 加 parser-specific cache 足以保留来源和避免重复解析。

**Validation:**

- 本地 scripted LLM 验证了 GROBID title missing → 下一轮 LLM 显式调用 PyMuPDF → final answer，且 SSE event 中出现两个 Tool 名与安全摘要。
- 本地验证 GROBID 异常会原样停止在 GROBID，不发生 PyMuPDF fallback；旧 `pdf_parse_node` 不传 language 给 parser。
- 未启动服务、未调用真实 LLM、未启动/访问 GROBID、未访问外网、未修改 `.env`。

**Open questions / next steps:**

- 真实 GROBID 服务的端到端兼容性仍需在用户允许启动本地服务后验证。

---

### 2026-09-03 16:09 - 完整显示可折叠的 Agent 步骤

**Goal:**

保留流式 Agent 的完整执行步骤，并让用户能展开或收起每条 assistant 消息的思考过程。

**Files inspected:**

- `backend/static/index.html`
- `backend/app/agents/agent_loop.py`

**Files changed:**

- `backend/static/index.html`：移除 activity 仅展示最新 8 条的前端裁剪；增加每条 assistant 消息独立的“展开/收起思考过程”按钮。

**Commands run:**

- `node --check`（从 HTML 提取的内联脚本）
- Node 静态断言
- `git diff --check -- backend/static/index.html`

**Decisions made:**

- 默认展开完整步骤；收起只影响步骤区，不影响最终回答；不修改 SSE、Agent Loop 或 Tool Runtime。

**Validation:**

- JavaScript 语法、完整步骤/按钮静态断言和 diff whitespace 检查通过。
- 未启动服务、未调用真实 LLM 或外网。

**Open questions / next steps:**

- 无。

---

### 2026-09-03 15:55 - Phase 7 Agent Streaming Runtime

**Goal:**

在不复制 Agent Loop、不改旧 LangGraph 或非流式 Chat API 的前提下，实现 SSE 的 Agent lifecycle、Tool 和真实 provider final delta 流式体验。

**Files inspected:**

- `backend/app/api/routes/chat.py`
- `backend/app/agents/agent_context.py`、`agent_loop.py`、`paper_agent.py`
- `backend/app/runtime/tool_executor.py`、`tool_registry.py`
- `backend/app/services/llm_service.py`
- `backend/static/index.html`
- 既有 Agent、Tool、Chat、RAG、session、memory 测试脚本

**Files changed:**

- `backend/app/agents/agent_loop.py`：同一 `run_agent` 增加统一 event sink、event 收集与可选 LLM delta 输入；没有第二套 while loop。
- `backend/app/runtime/tool_executor.py`：通过 HTTP 无关 callback 上报 `tool_start` 和安全摘要 `tool_result`。
- `backend/app/services/llm_service.py`：增加 OpenAI-compatible `stream=true` SSE 解析，实时转发 content delta，聚合 tool-call ID/name/arguments fragment。
- `backend/app/agents/paper_agent.py`：只透传 stream callback/event sink，保持 domain 配置职责。
- `backend/app/api/routes/chat.py`：新增 `POST /api/chat/stream`，以 queue + daemon worker 把 Agent event sink 转为 SSE；final answer 后才写入 session。
- `backend/static/index.html`：发送改用 SSE，执行过程展示为 activity，`final_delta` 追加 Markdown 正文。
- `backend/scripts/test_agent_streaming.py`：新增本地 fake streaming 回归；`test_chat_agent_integration.py` 补充 StreamingResponse stub。
- `backend/docs/CODEX_STATE.md`、`backend/docs/CODEX_WORKLOG.md`：更新状态与记录。

**Commands run:**

- `backend/.venv/bin/python -B -m scripts.test_agent_loop`
- `backend/.venv/bin/python -B -m scripts.test_tool_runtime`
- `backend/.venv/bin/python -B -m scripts.test_paper_tools`
- `backend/.venv/bin/python -B -m scripts.test_paper_agent`
- `backend/.venv/bin/python -B -m scripts.test_chat_agent_integration`
- `backend/.venv/bin/python -B -m scripts.test_retrieval`
- `backend/.venv/bin/python -B -m scripts.test_session_persistence`
- `backend/.venv/bin/python -B -m scripts.test_memory_service`
- `backend/.venv/bin/python -B -m scripts.test_multi_paper`
- `backend/.venv/bin/python -B -m scripts.test_paper_delete`
- `backend/.venv/bin/python -B -m scripts.test_agent_streaming`
- `node --check -`、Python AST、SSE route 静态检查、`git diff --check`

**Decisions made:**

- runtime events 是有限的可观察状态：`agent_start`、`step_start`、`llm_start`、`tool_call`、`tool_start`、`tool_result`、`step_end`、`final_start`、`final_delta`、`final_end`、`agent_done`、`error`；不包含 hidden reasoning 或 Tool 原始 payload。
- Tool Runtime 只返回安全摘要到 event sink，完整序列化结果仍只供 Agent 的 tool message 使用。
- 流式 endpoint 使用原生 `StreamingResponse` SSE；不使用 setTimeout 或完整文本切片伪造流式。
- client disconnect 可安全关闭 generator，但当前不会终止一次已发出的 provider HTTP 调用；停止生成需要后续取消 token。

**Validation:**

- 新测试覆盖 direct final delta、Tool→final、多 Tool、Tool error、invalid args、unknown tool、max steps、tool arguments fragment、SSE session persistence 和 generator close。
- provider SSE parser 通过本地 fake `urlopen` 验证 content/tool-call delta；所有回归脚本、JavaScript 语法和 `git diff --check` 通过。
- 未启动服务、未调用真实 DeepSeek/LLM、未访问外网、未修改 `.env`。

**Open questions / next steps:**

- 如需前端“停止生成”，需基于 request cancellation token 让 Agent Step 边界停止，且单次同步 provider HTTP 无法被强制中断。

---

### 2026-09-03 15:30 - 发送时立即收起 PDF 附件

**Goal:**

修复 pending PDF 附件会持续显示至聊天响应完成的问题；停止按钮需求留待后续单独处理。

**Files inspected:**

- `backend/static/index.html`
- `backend/app/api/routes/chat.py`

**Files changed:**

- `backend/static/index.html`：点击发送后立即把 pending attachment 转为当前 `paperId` 并移除输入区 chip，不等待 `/api/chat` 返回。
- `backend/docs/CODEX_WORKLOG.md`：追加本条记录。

**Commands run:**

- `sed -n '370,435p' backend/static/index.html`

**Decisions made:**

- 若聊天请求失败，PDF 仍作为当前会话论文保留在服务端，用户可直接再次提问；不重新显示已发送附件。

**Validation:**

- JavaScript 语法与 `git diff --check` 通过；断言确认 `pendingAttachment` 在 `/api/chat` 请求前清除。

**Open questions / next steps:**

- 停止 Agent 的前端按钮与后端协作取消机制由后续任务处理。

---

### 2026-09-03 15:26 - PDF 改为待发送附件并支持取消清理

**Goal:**

让浏览器选择 PDF 后只显示输入区域的 pending attachment；用户发送文字时才把 `paper_id` 交给 Chat，并可在发送前取消附件及其服务器文件。

**Files inspected:**

- `backend/static/index.html`
- `backend/app/api/routes/paper.py`
- `backend/app/services/file_service.py`
- `backend/app/core/config.py`
- `backend/app/services/retrieval_service.py`
- `backend/app/services/session_service.py`

**Files changed:**

- `backend/static/index.html`：新增 PDF 附件 chip 和 ×；上传、上传失败和取消不再写入聊天消息；pending 附件只在成功发送后转为当前 `paperId`。
- `backend/app/api/routes/paper.py`、`backend/app/services/file_service.py`：新增 `DELETE /api/papers/{paper_id}`，精确清理该生成 ID 的 PDF、metadata、note、chunk index 及匹配的 section JSON。
- `backend/scripts/test_paper_delete.py`：新增纯本地删除隔离验证，优先使用已安装 FastAPI，仅在缺失时使用最小 stub。
- `backend/docs/CODEX_STATE.md`、`backend/docs/CODEX_WORKLOG.md`：更新项目状态和本条记录。

**Commands run:**

- `backend/.venv/bin/python -B -m scripts.test_paper_delete`
- `backend/.venv/bin/python -B -c "from app.api.routes.paper import ..."`
- `node --check -`、Python AST 解析、`rg` 静态调用链检查、`git diff --check`

**Decisions made:**

- `paperId` 保留为已发送后的当前论文；`pendingAttachment` 是独立前端状态。发送失败时附件继续保留，成功时才成为当前论文。
- section JSON 的文件名不含 `paper_id`，删除时读取 JSON 后仅删除 payload 内 `paper_id` 相同的文件，避免误删同名论文的其他数据。
- 新建对话前会尝试删除当前未发送附件；删除失败时不会把它带入新会话，旧会话仍保留该附件以便重试。

**Validation:**

- `test_paper_delete` 已通过，覆盖 PDF、metadata、note、chunk、section JSON 清理、其他 `paper_id` 隔离和非法 ID 拒绝。
- 已用实际虚拟环境确认 FastAPI 可导入且 DELETE 路由注册；Node JavaScript 语法检查、Python AST 和 `git diff --check` 通过。
- 未启动服务、未上传真实文件、未调用 `/api/chat`、LangGraph、真实 LLM 或外网。

**Open questions / next steps:**

- 当前前端会话仅在浏览器内存中；刷新页面前仍未发送的附件无法再由浏览器取消，后续如需可增加服务端 pending-attachment 生命周期机制。

---

### 2026-09-03 - 调整 PDF 上传为仅绑定论文上下文

**Goal:**

让浏览器普通上传流程仅上传并绑定当前 conversation 的 `paperId`，不再自动进入固定 LangGraph 分析或读取精读笔记。

**Files inspected:**

- `backend/AGENTS.md`
- `backend/static/index.html`
- `backend/docs/CODEX_WORKLOG.md`

**Files changed:**

- `backend/static/index.html`：移除上传成功后对 `/api/papers/{paper_id}/analyze` 和 `/api/papers/{paper_id}/note` 的自动请求；保留 `paperId` 绑定和 `/api/chat` 请求字段，并显示“论文上传成功，可以开始提问”。
- `backend/docs/CODEX_STATE.md`、`backend/docs/CODEX_WORKLOG.md`：更新实际前端上传行为和工作记录。

**Commands run:**

- `sed -n '340,510p' backend/static/index.html`
- `rg ... backend/static/index.html`
- `git diff --check`

**Decisions made:**

- 固定 `/api/papers/{paper_id}/analyze` 和 `/note` API 保留，但不由普通前端上传流程自动调用。
- 上传成功后聚焦现有输入框；同一 conversation 后续 `/api/chat` 继续自动携带绑定的 `paperId`。

**Validation:**

- 静态确认 `handlePdfFile` 只调用 `/api/papers/upload`，`sendChatMessage` 仍发送 `message/session_id/paper_id` 到 `/api/chat`。
- 未启动服务、未上传文件、未调用真实 LLM 或外网。

**Open questions / next steps:**

- 如需手动触发固定 LangGraph 精读，可后续单独设计显式操作入口；本轮未添加。

---

### 2026-09-03 14:38 - 完成 RAG、持久化、Memory 与多论文基础（第 6/6 步）

**Goal:**

在不改变 Agent Loop、Tool Runtime 或固定 LangGraph 路径的前提下，按 6A→6B→6C→6D 增加本地检索、会话持久化、受控长期记忆和多论文上下文。

**Files inspected:**

- `backend/AGENTS.md`、`backend/docs/CODEX_STATE.md`、`backend/docs/CODEX_WORKLOG.md`
- `backend/app/agents/agent_context.py`、`agent_loop.py`、`paper_agent.py`、`paper_graph.py`、`paper_state.py`
- `backend/app/runtime/tool_registry.py`、`tool_executor.py`
- `backend/app/tools/paper_tools.py`、`paper_lookup_tools.py`
- `backend/app/api/routes/chat.py`、`paper.py`
- `backend/app/services/file_service.py`、`parser_service.py`、`llm_service.py`
- `backend/app/agents/nodes/`、`backend/static/index.html`、`backend/memory/`、`backend/storage/`
- `backend/app/core/config.py`、`.env.example`、`README.md`、`requirements.txt` 与现有 scripts

**Files changed:**

- `backend/app/services/retrieval_service.py`、`backend/app/tools/paper_tools.py`：新增 section-aware chunk、JSON index、纯 Python lexical retrieval 和 `retrieve_paper_context`。
- `backend/app/services/session_service.py`、`backend/app/api/routes/chat.py`：新增哈希文件名的 JSON session 持久化、最近历史裁剪、current/active paper state 和每轮 memory reload。
- `backend/app/services/memory_service.py`、`backend/app/tools/memory_tools.py`：新增唯一固定到 `memory.md` 的分类、去重写回 Tool。
- `backend/app/tools/multi_paper_tools.py`、`backend/app/agents/paper_agent.py`：新增多论文检索 Tool、active-paper prompt 与三类 Phase 6 Tool 注册。
- `backend/app/core/config.py`、`.env.example`、`README.md`、`plan_agent_node.py`：新增 RAG/history 配置和准确的固定流程说明。
- `backend/scripts/test_retrieval.py`、`test_session_persistence.py`、`test_memory_service.py`、`test_multi_paper.py`：新增四段纯本地验证；同步更新既有 Tool schema 断言和 Chat 临时 session storage。
- `backend/docs/CODEX_STATE.md`、`backend/docs/CODEX_WORKLOG.md`：更新状态与本次记录。

**Commands run:**

- `python3 -B -m scripts.test_retrieval`
- `python3 -B -m scripts.test_session_persistence`
- `python3 -B -m scripts.test_memory_service`
- `python3 -B -m scripts.test_multi_paper`
- `python3 -B -m scripts.test_agent_loop`
- `python3 -B -m scripts.test_tool_runtime`
- `python3 -B -m scripts.test_paper_tools`
- `python3 -B -m scripts.test_paper_agent`
- `python3 -B -m scripts.test_chat_agent_integration`
- `python3 -B -c "...ast.parse(...)"`
- `git diff --check`、`git status --short`

**Decisions made:**

- RAG 使用可配置长度/overlap 的 section-aware chunk 和 token-overlap lexical scoring；不增加 embedding、vector DB 或依赖。每篇 index 为 `storage/paper_chunks/<paper_id>.json`。
- session 使用 SHA-256 文件名保存 JSON；仅保存/恢复 user 与 final assistant，默认保留最近 20 条，不生成 LLM summary，也不持久化 Tool trace。
- 仅 `memory.md` 可写；`save_research_memory` 仅允许 paper/theme/finding 分类、500 字内规范化条目和跨分类去重。普通聊天不会自动写入。
- session 的显式新 `paper_id` 会加入 `active_paper_ids`；多论文 Tool 接收显式 ID，逐篇返回 `paper_id/chunks/error`，不做 Related Work 生成。
- 固定 LangGraph 不改路由或节点行为；仅更新它的长文策略描述以区分交互式 retrieval。

**Validation:**

- 6A 覆盖 chunk、section 边界、overlap、top-k 命中、未知论文、空 query、Runtime dispatch 与 Agent retrieval；结果不含 raw_text 字段。
- 6B 覆盖 session save/reload/restart、paper/active paper 恢复、隔离、哈希文件名、history trim 与 Tool trace 过滤。
- 6C 覆盖 memory read/append/dedupe/invalid category、soul/user 不变、Runtime dispatch、Agent 不写与显式写入。
- 6D 覆盖 active paper 去重、两篇论文 retrieval 来源、未知论文、Runtime dispatch 与 Agent multi-paper Tool；Phase 1～5 回归均通过。
- 所有测试均使用 fake LLM、fake paper 或 tempfile；未访问真实 LLM/网络、未启动 Uvicorn/Docker、未修改 `.env` 或用户真实 storage/memory。

**Open questions / next steps:**

- 后续如需生产化，可评估 embedding retrieval、数据库/并发控制、history summary、memory 人工审核和完整多论文分析；本轮不实施。

---

### 2026-09-03 13:46 - 将 Chat 接入 Paper Agent（第 5/6 步）

**Goal:**

让真实 LLM provider 的 `POST /api/chat` 复用 Paper Agent，并保持 session、当前论文、语义历史和只读 memory 上下文连续。

**Files inspected:**

- `backend/AGENTS.md`
- `backend/docs/CODEX_STATE.md`
- `backend/docs/CODEX_WORKLOG.md`
- `backend/app/api/routes/chat.py`
- `backend/app/api/routes/paper.py`
- `backend/app/agents/agent_context.py`
- `backend/app/agents/agent_loop.py`
- `backend/app/agents/paper_agent.py`
- `backend/app/runtime/tool_registry.py`
- `backend/app/runtime/tool_executor.py`
- `backend/app/tools/paper_tools.py`
- `backend/app/services/llm_service.py`
- `backend/app/services/file_service.py`
- `backend/app/main.py`
- `backend/static/index.html`
- `backend/memory/soul.md`
- `backend/memory/user.md`
- `backend/memory/memory.md`
- `backend/scripts/test_agent_loop.py`
- `backend/scripts/test_tool_runtime.py`
- `backend/scripts/test_paper_tools.py`
- `backend/scripts/test_paper_agent.py`

**Files changed:**

- `backend/app/api/routes/chat.py`：扩展可选 `paper_id`、内存 session state、Paper Agent 调用、语义历史保存和安全 Agent 错误响应；mock 保持本地占位回复。
- `backend/app/agents/paper_agent.py`：支持受控的 history 与额外 system context，保持每次运行仅一个 system prompt 和一个当前 user message。
- `backend/static/index.html`：前端会话保存 `paperId`；PDF 上传成功后绑定，聊天请求附带该值。
- `backend/scripts/test_chat_agent_integration.py`：新增纯本地 route-function 集成检查。
- `backend/docs/CODEX_STATE.md`、`backend/docs/CODEX_WORKLOG.md`：更新架构与本次记录。

**Commands run:**

- `python3 -B -c "...ast.parse(...)"`
- `cd backend && python3 -B -m scripts.test_chat_agent_integration`
- `cd backend && python3 -B -m scripts.test_paper_agent`
- `cd backend && python3 -B -m scripts.test_paper_tools`
- `cd backend && python3 -B -m scripts.test_tool_runtime`
- `cd backend && python3 -B -m scripts.test_agent_loop`
- `git diff --check`
- `git status --short`

**Decisions made:**

- Session state 使用最小 `ChatSessionState(messages, paper_id)`；显式 request `paper_id` 覆盖并更新 session，缺省时继承 session，无两者时传给 Agent `None`。
- session 只保存 user/final assistant，不保存 Tool message、tool call 或 `agent_trace`；调用 Agent 前传入当前 user 之前的 history，避免当前 message 重复。
- 长期 memory 在 chat 模块启动时只读加载，作为一次 system context 交给 Paper Agent；Paper Agent 不反向依赖 Chat。
- 选择 mock 方案 A：`LLM_PROVIDER=mock` 返回既有本地占位回复，不进入 Paper Agent，确保不触达真实 LLM。
- 未新增 Paper Tool、未修改 Tool Runtime、Agent Loop、固定 LangGraph 或现有 upload/analyze/note API。

**Validation:**

- 新增测试通过普通无论文聊天、`get_paper_info -> final`、`extract_sections -> final`、论文继承、session 隔离、论文切换、历史连续、当前 user 去重、memory 注入/读取失败降级、Tool trace 不持久化和 max-step HTTP 错误。
- Phase 1～4 的四组本地测试及 `git diff --check` 全部通过；未访问真实 LLM/外部网络，也未修改 `.env` 或 `storage/`。
- 当前运行环境缺少 FastAPI/Pydantic，因此集成测试以最小 stub 直接调用 route function；未运行 TestClient 或 Uvicorn，也未安装依赖。

**Open questions / next steps:**

- 第 6 步再考虑 RAG、检索、持久化会话、history compression、memory 写回和多论文能力；本轮未实施。

---

### 2026-09-03 - 新增目标驱动 Paper Agent（第 4/6 步）

**Goal:**

在不接入 Chat/API 的前提下，让 LLM 通过现有 Agent Loop 自主选择已注册的本地 Paper Tools，并根据 Tool Result 再次推理。

**Files inspected:**

- `backend/AGENTS.md`
- `backend/docs/CODEX_STATE.md`
- `backend/docs/CODEX_WORKLOG.md`
- `backend/app/agents/agent_context.py`
- `backend/app/agents/agent_loop.py`
- `backend/app/runtime/tool_registry.py`
- `backend/app/runtime/tool_executor.py`
- `backend/app/tools/paper_tools.py`
- `backend/app/tools/paper_lookup_tools.py`
- `backend/app/services/llm_service.py`
- `backend/scripts/test_agent_loop.py`
- `backend/scripts/test_tool_runtime.py`
- `backend/scripts/test_paper_tools.py`
- `backend/app/api/routes/chat.py`
- `backend/app/agents/paper_graph.py`
- `backend/app/agents/paper_state.py`

**Files changed:**

- `backend/app/agents/paper_agent.py`：新增 Paper Agent prompt、显式 `paper_id` 上下文、Paper Tool registry/schema 组装和 `run_paper_agent` 入口。
- `backend/scripts/test_paper_agent.py`：新增 fake LLM 的多步骤、无 Tool、未知 Tool 恢复、无论文和 max-step 验证。
- `backend/docs/CODEX_STATE.md`、`backend/docs/CODEX_WORKLOG.md`：最小架构记录更新。

**Commands run:**

- `python3 -B -c "...ast.parse(...)"`
- `cd backend && python3 -B -m scripts.test_paper_agent`
- `cd backend && python3 -B -m scripts.test_paper_tools`
- `cd backend && python3 -B -m scripts.test_tool_runtime`
- `cd backend && python3 -B -m scripts.test_agent_loop`
- `git diff --check`

**Decisions made:**

- Paper Agent 只有领域配置职责，直接复用 `run_agent`；没有关键词路由、隐式 `paper_id` 注入或第二套 while loop。
- 默认只注册并向 LLM 暴露 `get_paper_info`、`extract_sections`；没有开放外部 lookup Tool。
- Paper Agent 结果直接复用 `AgentLoopResult`，通过 `context.metadata["paper_agent"]` 和 `agent_trace` 保留可观察信息。

**Validation:**

- fake LLM 已跑通 `get_paper_info -> extract_sections -> final`，共 3 次 LLM 调用、2 次 Tool 成功执行。
- Phase 1～3 测试继续通过；未启动服务、未调用真实 LLM/网络，也未修改 `.env` 或 `storage/`。

**Open questions / next steps:**

- 第 5 步再将 `/api/chat` 接入 Paper Agent，并处理 session、`paper_id` 与 memory；本轮未实施。

---

### 2026-09-03 - 注册首批 Paper Capability Tools（第 3/6 步）

**Goal:**

将无需真实 LLM 或外部网络的既有论文能力封装为正式 Runtime Tool，且保持 LangGraph 固定工作流不变。

**Files inspected:**

- `backend/AGENTS.md`
- `backend/docs/CODEX_STATE.md`
- `backend/docs/CODEX_WORKLOG.md`
- `backend/app/agents/agent_context.py`
- `backend/app/agents/agent_loop.py`
- `backend/app/runtime/tool_registry.py`
- `backend/app/runtime/tool_executor.py`
- `backend/app/agents/paper_graph.py`
- `backend/app/agents/paper_state.py`
- `backend/app/agents/nodes/`
- `backend/app/services/file_service.py`
- `backend/app/services/parser_service.py`
- `backend/app/services/llm_service.py`
- `backend/app/tools/paper_lookup_tools.py`
- `backend/scripts/test_agent_loop.py`
- `backend/scripts/test_tool_runtime.py`

**Files changed:**

- `backend/app/tools/paper_tools.py`：新增 `get_paper_info`、`extract_sections`、手写 LLM schema 和注册函数。
- `backend/app/services/parser_service.py`、`backend/app/agents/nodes/pdf_parse_node.py`：抽出并复用语言对应 parser 选择能力。
- `backend/app/agents/nodes/section_extract_node.py`：抽出 `extract_sections_from_text`，Node 改为纯 `PaperState` adapter。
- `backend/scripts/test_paper_tools.py`：通过 Runtime 验证正式 Paper Tool、注册、schema、异常和结果序列化。
- `backend/app/agents/agent_loop.py`：补回 Phase 2 重构时遗漏的 `json` 导入，保证 dict arguments 可写入 assistant tool-call message。
- `backend/docs/CODEX_STATE.md`、`backend/docs/CODEX_WORKLOG.md`：最小架构记录更新。

**Commands run:**

- `python3 -B -c "...ast.parse(...)"`
- `cd backend && python3 -B -m scripts.test_paper_tools`
- `cd backend && python3 -B -m scripts.test_tool_runtime`
- `cd backend && python3 -B -m scripts.test_agent_loop`
- `git diff --check`

**Decisions made:**

- 仅注册 `get_paper_info` 与 `extract_sections`：前者使用 parser 本地元数据，后者复用规则优先的共享章节能力；`analyze_method` 继续留在后续步骤，避免本轮调用真实 LLM。
- Paper Tool 只接收显式 `paper_id`，不接收 `PaperState`、PDF 全文或 HTTP 请求；章节结果限制为长度、metadata 和每节最多 800 字预览。
- 测试以 fake parsed paper 避免依赖用户 `storage/`；当前 Python 环境缺少 `pydantic`，因此未实际导入 parser adapter 或解析真实 PDF，未安装依赖。

**Validation:**

- Paper Tool、Tool Runtime、Agent Loop 的本地测试均通过，且 `git diff --check` 通过。
- 未启动服务，未调用真实 LLM、外部 lookup 或 parser 服务，未修改 `.env` 或 `storage/`。

**Open questions / next steps:**

- 第 4 步再由 Paper Agent 根据目标和上下文选择已注册 Tool；本轮未接入 Chat/API。

---

### 2026-09-03 - 拆分独立 Tool Runtime（第 2/6 步）

**Goal:**

将第 1 步中与 Agent Loop 耦合的工具执行逻辑迁移到最小、业务无关的 Tool Runtime，保留既有 Agent Loop 调用兼容性。

**Files inspected:**

- `backend/AGENTS.md`
- `backend/docs/CODEX_STATE.md`
- `backend/docs/CODEX_WORKLOG.md`
- `backend/app/agents/agent_context.py`
- `backend/app/agents/agent_loop.py`
- `backend/app/services/llm_service.py`
- `backend/scripts/test_agent_loop.py`
- `backend/app/tools/paper_lookup_tools.py`
- `backend/app/agents/paper_graph.py`
- `backend/app/agents/paper_state.py`

**Files changed:**

- `backend/app/runtime/__init__.py`、`tool_registry.py`、`tool_executor.py`：新增最小 registry、解析/dispatch/executor 和统一结果结构。
- `backend/app/agents/agent_loop.py`：移除参数解析、callable 查找、执行和序列化细节，改为调用 Runtime；`tools={...}` 调用方式仍可用。
- `backend/scripts/test_tool_runtime.py`：新增 Runtime 单独验证；保留并继续运行原 Loop 集成验证。
- `backend/docs/CODEX_STATE.md`、`backend/docs/CODEX_WORKLOG.md`：最小架构与工作记录更新。

**Commands run:**

- `python3 -B -c "...ast.parse(...)"`
- `cd backend && python3 -B -m scripts.test_tool_runtime`
- `cd backend && python3 -B -m scripts.test_agent_loop`
- `rg ... backend/app/agents/agent_loop.py`
- `git diff --check`

**Decisions made:**

- 未创建 `tool_context.py`：当前没有 Tool 需要运行时上下文，`AgentContext.metadata["agent_trace"]` 已足够承载轻量事件。
- Runtime 按 LLM 返回顺序同步执行；错误使用统一 `ToolExecutionResult`，Tool 异常只暴露异常类型，避免意外回传敏感内容。
- 未注册现有论文检索 Tool，避免本轮产生外部网络访问；仅使用本地 fake Tool 验证。

**Validation:**

- Runtime 单测覆盖 registry、dispatch、JSON 参数、非法参数、未知 Tool、Tool 异常、ID 保留和多 Tool 顺序。
- Agent Loop 集成测试继续通过，且静态确认 Loop 不包含参数解析或直接 callable 执行。

**Open questions / next steps:**

- 第 3 步再逐步封装 PaperPilot 论文业务能力为 Runtime 可注册 Tool；本轮未实施。

---

### 2026-09-03 - 新增独立 Agent Loop 基础设施（第 1/6 步）

**Goal:**

在不接管现有 LangGraph 或 Chat API 的前提下，建立可用 fake LLM 和本地工具验证的最小 `AgentContext + Agent Loop`。

**Files inspected:**

- `backend/AGENTS.md`
- `backend/docs/CODEX_STATE.md`
- `backend/docs/CODEX_WORKLOG.md`
- `backend/app/services/llm_service.py`
- `backend/app/api/routes/chat.py`
- `backend/app/agents/paper_graph.py`
- `backend/app/agents/paper_state.py`
- `backend/app/agents/nodes/paper_info_node.py`
- `backend/app/tools/paper_lookup_tools.py`
- `backend/app/core/config.py`

**Files changed:**

- `backend/app/agents/agent_context.py`：新增独立运行上下文，保存会话、消息、步数上限与轻量 metadata。
- `backend/app/agents/agent_loop.py`：新增同步通用循环、最小 callable 映射执行器、结构化 tool error、max-step guard 与 trace。
- `backend/app/services/llm_service.py`：在既有 urllib 请求路径上增加 OpenAI-compatible tool-call 标准化函数；未改变既有 text/json 调用接口。
- `backend/scripts/test_agent_loop.py`：新增不访问网络或真实 LLM 的五项本地断言。

**Commands run:**

- `python3 -B -c "...ast.parse(...)"`
- `cd backend && python3 -B -m scripts.test_agent_loop`
- `cd backend && python3 -B -c "...run_agent(...)"`
- `git diff --check`

**Decisions made:**

- 保持循环同步，以复用当前同步 `llm_service.py` 与 FastAPI/chat 调用风格；后续 Tool Runtime 可替换 `execute_tool_call`。
- fake LLM 通过 `llm_call` 注入，正式 provider 调用仍统一经 `llm_service.call_llm_with_tools`；mock provider 不访问外部服务。
- 未复用 `paper_info_node` 的并发 executor，因为它包含论文元数据业务和外部 lookup；本轮按要求保持通用、顺序执行。

**Validation:**

- AST 静态解析、五个 fake-LLM 场景和 `git diff --check` 均通过。
- 未启动服务、未调用真实 LLM、未访问论文检索服务，未修改 storage 或 `.env`。

**Open questions / next steps:**

- 第 2 步再将当前 callable 映射演进为 Tool Runtime、registry、executor 与 dispatch；本轮不实施。

---

### 2026-06-10 17:40 - 改造聊天界面并新增 Chat API

**Goal:**

把现有 `backend/static/index.html` 改造成 ChatGPT 风格聊天界面；新增 `POST /api/chat`；创建 `backend/memory/` 三个记忆文件；更新 Codex 工作记忆。

**Files inspected:**

- `backend/AGENTS.md`
- `backend/static/index.html`
- `backend/app/main.py`
- `backend/app/api/routes/paper.py`
- `backend/app/services/llm_service.py`
- `backend/app/api/routes/`
- `backend/docs/CODEX_STATE.md`
- `backend/docs/CODEX_WORKLOG.md`

**Files changed:**

- `backend/static/index.html`：改造成 ChatGPT 风格单页聊天界面，支持前端内存会话、新建对话、PDF 上传自动分析、Markdown assistant 消息和 `/api/chat` 文本聊天。
- `backend/app/api/routes/chat.py`：新增 chat router，接收 `message/session_id`，读取 memory 文件，按 session 维护内存历史，复用 `llm_service.call_llm_text`，mock 模式返回本地占位回复。
- `backend/app/main.py`：注册 `chat_router` 到 `/api/chat`。
- `backend/memory/soul.md`：新增 Agent 性格和论文分析偏好。
- `backend/memory/user.md`：新增用户画像、研究兴趣、技术背景和偏好。
- `backend/memory/memory.md`：新增已分析论文、研究主题和跨论文规律的格式占位。
- `backend/docs/CODEX_STATE.md`：更新 Chat 层、Memory 层、目录、关键文件、能力、限制和验证状态。
- `backend/docs/CODEX_WORKLOG.md`：整理日志结构并追加本条记录。

**Commands run:**

- `Get-Content -Path backend\AGENTS.md`
- `Get-Content -Path backend\static\index.html`
- `Get-Content -Path backend\app\main.py`
- `Get-Content -Path backend\app\api\routes\paper.py`
- `Get-Content -Path backend\app\services\llm_service.py`
- `Get-ChildItem -Path backend\app\api\routes -Force`
- `Get-Content -Path backend\docs\CODEX_STATE.md`
- `Get-Content -Path backend\docs\CODEX_WORKLOG.md`
- `C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe -NoProfile -Command "Get-Date -Format 'yyyy-MM-dd HH:mm zzz'"`
- `Get-ChildItem -Path backend\memory -Force`
- `rg "POST /api/chat|/api/chat|/api/papers|XMLHttpRequest|crypto.randomUUID|marked|highlight" backend\static\index.html backend\app\api\routes\chat.py backend\app\main.py`
- `rg "Chat 层|Memory 层|backend/memory|chat.py|17:40" backend\docs\CODEX_STATE.md backend\docs\CODEX_WORKLOG.md`
- `python -c "import ast, pathlib; ..."`：尝试解析 `app/main.py` 和 `app/api/routes/chat.py`，沙箱返回 `windows sandbox: spawn setup refresh`，未完成。
- `git diff -- backend\app\main.py backend\app\api\routes\chat.py backend\static\index.html backend\docs\CODEX_STATE.md backend\docs\CODEX_WORKLOG.md backend\memory\soul.md backend\memory\user.md backend\memory\memory.md`
- `git status --short`

**Decisions made:**

- 前端历史只存在浏览器内存，刷新后清空，符合当前任务要求。
- 每个新对话用浏览器 `crypto.randomUUID()` 生成独立 `session_id`，发送文本时请求 `/api/chat`。
- PDF 上传使用 `XMLHttpRequest` 显示上传进度，因为原生 `fetch` 不直接提供上传进度事件。
- PDF 流程自动串联 `/api/papers/upload -> /api/papers/{paper_id}/analyze -> /api/papers/{paper_id}/note`，分析期间用 loading assistant 消息占位。
- `chat.py` 在模块导入时读取 `backend/memory/` 并拼接 system prompt；读取失败静默降级。
- `LLM_PROVIDER=mock` 时 chat 接口返回本地占位回复，避免误触真实外部 LLM；真实 provider 时复用 `call_llm_text`。

**Validation:**

- 已静态检查现有接口签名和 `llm_service.py` 可复用函数。
- 已确认 `backend/memory/` 三个文件存在。
- 已用 `rg` 确认前端包含 `/api/chat`、`/api/papers/upload`、`/api/papers/{paper_id}/analyze`、`/api/papers/{paper_id}/note`、`XMLHttpRequest`、`crypto.randomUUID`、marked.js 和 highlight.js。
- 已用 `rg` 确认 `main.py` 注册 `/api/chat`，Codex 状态/日志包含 Chat 层、Memory 层和 17:40 工作记录。
- Python AST 语法检查被当前沙箱启动问题阻止，未完成运行时验证。
- 未启动服务、未调用上传/分析/真实 LLM，避免产生 storage 数据或外部调用。

**Open questions / next steps:**

- 后续可以把前端历史持久化到后端或浏览器 localStorage。
- 后续可以展示 LangGraph memory/state/debug 信息，例如 `analysis_plan`、`section_meta`、`paper_info_debug`。
- 后续可以增加 arXiv 拉取入口，支持通过 arXiv ID 或 URL 获取论文。

---

### 2026-06-10 17:10 - 添加浏览器前端和静态挂载

**Goal:**

添加 PaperPilot 浏览器前端界面和 FastAPI 静态文件挂载。前端放在 `backend/static/index.html`，支持 PDF 上传、语言选择、开始分析、loading 状态、Markdown 精读笔记渲染、代码高亮和错误提示；后端通过 `/static` 提供静态文件，并让 `GET /` 返回前端页面。

**Files inspected:**

- `backend/AGENTS.md`
- `backend/app/main.py`
- `backend/app/api/routes/paper.py`
- `backend/docs/CODEX_STATE.md`
- `backend/docs/CODEX_WORKLOG.md`
- `backend/`

**Files changed:**

- `backend/static/index.html`
- `backend/app/main.py`
- `backend/docs/CODEX_STATE.md`
- `backend/docs/CODEX_WORKLOG.md`

**Commands run:**

- `Get-Content -Path backend\AGENTS.md`
- `Get-Content -Path backend\app\main.py`
- `Get-Content -Path backend\app\api\routes\paper.py`
- `Get-Content -Path backend\docs\CODEX_STATE.md`
- `Get-Content -Path backend\docs\CODEX_WORKLOG.md`
- `Get-ChildItem -Path backend -Force`
- `Get-ChildItem -Path backend\static -Force`
- `rg "app.mount|read_index|StaticFiles|marked|highlight|/api/papers" backend\app\main.py backend\static\index.html`

**Decisions made:**

- 使用单文件 HTML，不引入 npm、构建工具或前端工程。
- 使用 Tailwind CSS CDN、marked.js CDN 和 highlight.js CDN。
- 前端同源调用 `/api/papers/upload`、`/api/papers/{paper_id}/analyze` 和 `/api/papers/{paper_id}/note`。

**Validation:**

- 静态确认 `backend/static/index.html` 已创建，`main.py` 包含 `StaticFiles`、`app.mount("/static", ...)` 和根路由。
- 未启动 FastAPI 服务，未调用上传/分析接口，避免产生 storage 运行数据。
- Python AST 语法检查、本地 health check 和后台启动 uvicorn 尝试未完成或被审批策略拒绝，运行时验证待确认。

**Open questions / next steps:**

- 后续可以增加对话界面，让用户围绕论文笔记追问。
- 后续可以展示 LangGraph memory/state/debug 信息。
- 后续可以增加 arXiv 拉取入口。

---

### 2026-06-10 16:53 - 初始化 Codex 工作记忆

**Goal:**

为当前项目初始化/补充 Codex 长期工作记忆文件：`AGENTS.md`、`docs/CODEX_STATE.md`、`docs/CODEX_WORKLOG.md`。

**Files inspected:**

- `backend/README.md`
- `backend/requirements.txt`
- `backend/AGENTS.md`
- `backend/docs/CODEX_STATE.md`
- `backend/docs/CODEX_WORKLOG.md`
- `backend/app/main.py`
- `backend/app/api/routes/paper.py`
- `backend/app/core/config.py`
- `backend/app/agents/paper_graph.py`
- `backend/app/agents/paper_state.py`
- `backend/app/schemas/paper.py`
- `backend/app/services/file_service.py`
- `backend/app/services/parser_service.py`
- `backend/app/services/llm_service.py`
- `backend/scripts/run_analyze_paper.py`

**Files changed:**

- `backend/AGENTS.md`
- `backend/docs/CODEX_STATE.md`
- `backend/docs/CODEX_WORKLOG.md`

**Commands run:**

- `Get-ChildItem -Force`
- `Get-ChildItem -Path backend -Force`
- `git status --short`
- `rg --files backend`
- `Get-Content -Path backend\README.md`
- `Get-Content -Path backend\requirements.txt`
- `Get-Content -Path backend\AGENTS.md`
- `Get-Content -Path backend\docs\CODEX_STATE.md`
- `Get-Content -Path backend\docs\CODEX_WORKLOG.md`
- `Get-Content -Path backend\app\main.py`
- `Get-Content -Path backend\app\api\routes\paper.py`
- `Get-Content -Path backend\app\core\config.py`
- `Get-Content -Path backend\app\agents\paper_graph.py`
- `Get-Content -Path backend\app\agents\paper_state.py`
- `Get-Content -Path backend\app\schemas\paper.py`
- `Get-Content -Path backend\app\services\file_service.py`
- `Get-Content -Path backend\app\services\parser_service.py`
- `Get-Content -Path backend\app\services\llm_service.py`
- `Get-Content -Path backend\scripts\run_analyze_paper.py`
- `git diff --stat`

**Decisions made:**

- 将用户提到的 `AGENTS.md`、`docs/CODEX_STATE.md`、`docs/CODEX_WORKLOG.md` 解析为 `backend/` 下的实际文件。
- 不运行安装依赖、启动服务、Docker、数据库迁移、测试、commit 或 push。
- 对无法确认的测试、lint、构建、历史工作和可选 parser 依赖标注“待确认”。

**Validation:**

- 通过只读命令确认项目结构、README、requirements、FastAPI 入口、API 路由、配置、LangGraph、state、服务层和 CLI 入口。
- `git diff --stat` 显示工作区已有大量非本次任务变更；未回滚或整理用户已有改动。

**Open questions / next steps:**

- 待确认测试命令、lint/format 命令和 CI 规范。
- 待确认当前业务代码和 storage 改动是否需要后续整理。
- 待确认可选 parser adapter 的环境依赖与实际可用性。

---
