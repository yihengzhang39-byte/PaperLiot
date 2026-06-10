# Codex Project State

## Project Summary

PaperPilot 是一个科研论文阅读 Agent 后端项目，当前目标是完成“单篇 PDF 论文 -> 文本解析 -> 元数据抽取 -> 章节抽取/验证/修复 -> 方法与实验分析 -> 中文 Markdown 精读笔记”的后端闭环。

项目目前位于 `backend/`，是 Python FastAPI 服务，没有发现独立前端构建配置。README 明确提到后续计划包括 MySQL、任务状态、异步队列、前端、多论文对比和 Related Work 生成，但当前实现仍以本地文件存储和单篇论文分析为主。

## Current Architecture

- Web 层：`app/main.py` 创建 FastAPI app，注册 CORS 和 `/api/papers` 路由，并提供 `/health`。
- Static 层：`backend/static/index.html` 是单文件 ChatGPT 风格浏览器前端；`app/main.py` 使用 FastAPI `StaticFiles` 将 `backend/static/` 挂载到 `/static`，并通过 `GET /` 返回 `index.html`。
- API 层：`app/api/routes/paper.py` 提供 PDF 上传、按 `paper_id` 分析、读取生成笔记三类接口。
- Chat 层：`app/api/routes/chat.py` 提供 `POST /api/chat`，按 `session_id` 在进程内维护对话历史，并复用 `llm_service.call_llm_text` 调用真实 LLM；mock 模式返回本地占位回复。
- Memory 层：`backend/memory/soul.md`、`backend/memory/user.md`、`backend/memory/memory.md` 在 chat 路由模块启动时读取并注入 system prompt；读取失败静默降级。
- Agent 层：`app/agents/paper_graph.py` 使用 LangGraph 串联节点，状态定义在 `app/agents/paper_state.py`。
- 节点层：`app/agents/nodes/` 包含 PDF 解析、策略规划、论文信息抽取、章节抽取、章节校验、章节修复、方法分析、实验分析、笔记写作等节点。
- 服务层：`app/services/` 负责本地文件存储、PDF parser 选择与 fallback、LLM 调用、论文外部元数据查询。
- Parser adapter：`app/services/parsers/` 已有 `pymupdf`、`grobid`、`docling`、`marker`、`mineru` 适配器；README 标注 PyMuPDF 默认可用，GROBID 依赖本地服务，其他 adapter 偏实验/预留。
- Tool 层：`app/tools/paper_lookup_tools.py` 暴露 arXiv、Crossref、OpenAlex 查询工具，供论文信息补全逻辑使用。
- 存储层：`storage/` 下保存上传 PDF、生成笔记、论文 metadata 和章节 JSON。

当前 LangGraph 流程：

```text
pdf_parse_node
-> plan_agent_node
-> paper_info_node
-> section_extract_node
-> section_verify_node
-> section_repair_node
-> method_analyze_node
-> experiment_analyze_node
-> summary_write_node
```

## Important Directories

- `backend/app/`：后端应用主体。
- `backend/app/api/routes/`：HTTP API 路由。
- `backend/app/agents/`：LangGraph workflow、state 和节点。
- `backend/app/services/`：文件、parser、LLM、lookup 等服务。
- `backend/app/services/parsers/`：PDF parser adapter。
- `backend/app/tools/`：可复用工具函数，当前主要是论文元数据查询。
- `backend/app/schemas/`：Pydantic API schema。
- `backend/app/core/`：配置、环境变量读取、本地存储目录创建。
- `backend/scripts/`：CLI 和 profiling 辅助脚本。
- `backend/static/`：单文件 HTML 前端静态资源目录，当前包含 ChatGPT 风格聊天界面 `index.html`。
- `backend/memory/`：聊天助手长期记忆目录，包含 agent 性格、用户画像和跨论文记忆占位。
- `backend/storage/`：运行时数据和样例/生成物；除非任务明确要求，不应随意改动或提交。
- `backend/docs/`：Codex 项目状态和工作日志。

## Key Files

- `backend/README.md`：当前最完整的项目说明，包含架构、配置、命令、限制和后续计划。
- `backend/requirements.txt`：依赖清单：FastAPI、uvicorn、python-multipart、pydantic、langgraph、PyMuPDF、python-dotenv、requests、langchain-core。
- `backend/app/main.py`：FastAPI 入口。
- `backend/app/core/config.py`：`.env` 读取、LLM/PDF parser/lookup/section repair/plan agent 配置、存储目录常量。
- `backend/app/api/routes/paper.py`：`POST /upload`、`POST /{paper_id}/analyze`、`GET /{paper_id}/note`。
- `backend/app/api/routes/chat.py`：`POST /api/chat`，接收 `message/session_id`，加载 memory 文件，维护内存会话历史并调用 LLM。
- `backend/app/agents/paper_graph.py`：LangGraph 编排和 `analyze_paper` 入口。
- `backend/app/agents/paper_state.py`：跨节点共享状态字段。
- `backend/app/services/file_service.py`：上传 PDF 保存、metadata/note/section JSON 读写。
- `backend/app/services/parser_service.py`：parser registry 与 fallback 到 PyMuPDF。
- `backend/app/services/llm_service.py`：mock provider、真实 OpenAI-compatible chat completions、JSON 清洗解析。
- `backend/scripts/run_analyze_paper.py`：CLI 分析单个 PDF。
- `backend/static/index.html`：浏览器前端入口，提供 ChatGPT 风格对话、内存会话历史、PDF 上传自动分析、Markdown 消息渲染、loading 和错误提示。
- `backend/memory/soul.md`：聊天助手性格和论文分析偏好。
- `backend/memory/user.md`：用户身份、研究兴趣、技术背景和摘要偏好。
- `backend/memory/memory.md`：已分析论文、关注主题和跨论文发现的长期记忆占位。
- `backend/AGENTS.md`：Codex 后续工作规则。
- `backend/docs/CODEX_WORKLOG.md`：Codex 工作日志和交接记录。

## Runtime / Environment

- 默认 LLM provider 是 `mock`，真实 provider 支持 `deepseek` 和 `openai_compatible`。
- `.env` 位于 `backend/.env`；README 中列出 `.env.example` 字段。真实密钥不应打印、提交或写入文档。
- 默认 PDF parser 是 `pymupdf`。
- `paper_language=zh` 默认走中文论文策略并强制/优先 PyMuPDF；`paper_language=en` 优先 GROBID，失败或低质量时 fallback 到 PyMuPDF。
- FastAPI 启动命令：`cd backend && uvicorn app.main:app --reload`。
- CLI 命令：`cd backend && python scripts/run_analyze_paper.py --pdf path/to/paper.pdf --paper-language zh|en`。
- API 分析和 CLI 分析都会产生本地文件，主要在 `storage/notes/`、`storage/paper_metadata/`、`storage/paper_section_json/`。
- GROBID 需要额外 Docker 服务，是否启动必须先向用户确认。
- 当前没有确认的测试命令、lint 命令或构建命令。

## Current Capabilities

- 上传 PDF 并生成 `paper_id`。
- 支持浏览器前端访问：`GET /` 返回单文件 HTML 界面，`/static` 提供静态文件。
- 单文件 HTML 前端支持 ChatGPT 风格对话、前端内存历史、新建对话、PDF 上传、自动分析、查看 Markdown 精读笔记、loading 状态和错误提示。
- 支持 `POST /api/chat` 对话接口，按 `session_id` 维护进程内对话历史。
- 支持在 chat system prompt 中注入 `backend/memory/` 下的长期记忆；读取失败不影响正常对话。
- 记录上传文件 metadata，包括 `paper_language`。
- 按 `paper_id` 找到本地 PDF 并运行 LangGraph 分析。
- 使用 PyMuPDF 解析 PDF 文本；GROBID/Docling/Marker/MinerU adapter 已在 registry 中，但可用性取决于环境和外部服务/依赖。
- 非默认 parser 失败、空文本或低质量时 fallback 到 PyMuPDF。
- 支持中文/英文论文语言参数对 parser 和元数据策略的影响。
- 抽取论文基础信息，支持 parser/parser_meta、first page LLM、tool-calling agent 等路径；mock provider 下可安全降级。
- 可查询 arXiv、Crossref、OpenAlex 作为英文论文或带 DOI/arXiv ID 论文的外部元数据候选；中文论文无 DOI/arXiv ID/明确英文标题时跳过外部检索。
- 通过 `plan_agent_node` 生成受控 `analysis_plan`，默认规则模式，LLM planner 默认关闭。
- 章节抽取支持中英文标题规则、LLM 兜底、章节质量检查和规则修复。
- 生成中文 Markdown 精读笔记，并支持 `GET /api/papers/{paper_id}/note` 读取。
- 分析响应暴露调试字段：`analysis_plan`、`plan_debug`、`agent_decisions`、`section_meta`、`section_quality`、`section_verify_debug`、`section_repair_debug`、`paper_info_debug`、`web_search_debug` 等。

## Known Constraints

- 当前只支持单篇论文精读。
- 长论文仍主要依赖截断和计划标记；README 标注 chunk/RAG 尚未真正执行。
- MySQL、任务状态、异步队列和完整前端工程尚未实现；当前只有无需构建的单文件 HTML 聊天前端。
- 对话历史只保存在后端进程内存和前端内存中，服务重启或页面刷新后不会持久化。
- 测试体系待确认，当前未发现专门 `tests/` 目录。
- 格式化/lint 配置待确认，当前未发现 `pyproject.toml` 或专用配置文件。
- Playwright/浏览器自动化未在后端文件清单中发现；如后续涉及浏览器自动化，先检查再安装。
- GROBID、真实 LLM、arXiv/Crossref/OpenAlex 调用都可能访问外部网络；执行前需要用户确认。
- `storage/` 包含大量运行数据和 PDF，容易产生大文件和隐私/版权风险，不应随意提交。
- 当前工作区 dirty，已有多处业务代码、`.env`、README、生成文件和 storage 文件改动；这些不是本次记忆文件任务产生的变更。

## Known Issues / TODO

- 待确认：当前业务代码改动是否全部来自用户预期修改，是否需要后续整理或提交。
- 待确认：测试命令、lint/format 命令和 CI 规范。
- 待确认：`docling`、`marker`、`mineru` adapter 的依赖安装方式和实际可用性。
- 待确认：真实 LLM provider 的推荐配置、模型名称和网络访问策略。
- 待确认：是否需要 `.gitignore` 忽略 `storage/`、`__pycache__/`、uvicorn 日志、`.env` 等生成/敏感文件；本次任务未修改配置文件。
- 待确认：是否需要补充正式测试覆盖，包括 parser fallback、章节抽取、API schema、file_service 存储行为。
- README 已标注后续计划：MySQL、任务状态、异步队列、多论文对比、Related Work 生成、Vue 前端。

## User Preferences

- 默认中文沟通。
- 修改前先分析，复杂任务先写计划。
- 不确定先查项目已有实现和文档，不要凭空编造。
- 不要重复安装已存在依赖；涉及 Playwright 时先检查是否已安装。
- 不要擅自执行依赖安装、启动长期服务、Docker、数据库迁移、commit、push。
- 不要擅自执行外部投递、发送、登录、删除数据或真实外部 API/LLM 调用。
- 只修改用户明确允许的文件；保护用户已有未提交改动。

## Last Verified State

- 验证日期：2026-06-10 17:40 +08:00。
- 当前检查范围：只读查看 `backend/` 文件清单、README、requirements、入口、路由、配置、LangGraph、state、file/parser/LLM service、schema、CLI 脚本、静态前端文件、chat 路由、memory 文件和 git diff/stat。
- 本次聊天前端改造后，已静态确认 `backend/static/index.html` 存在，并包含 Tailwind CDN、marked.js、highlight.js、`/api/chat`、`/api/papers/upload`、`/api/papers/{paper_id}/analyze`、`/api/papers/{paper_id}/note` 调用；`backend/app/main.py` 包含 `chat_router` 注册、`StaticFiles`、`app.mount("/static", ...)` 和 `GET /` 根路由。
- `backend/app/api/routes/chat.py` 已新增，包含 `ChatRequest`、`ChatResponse`、`SESSION_HISTORY`、memory 文件读取、mock 回复和真实 LLM 调用路径；`backend/memory/` 三个文件已创建。
- Python AST 语法检查尝试执行时，当前沙箱返回 `windows sandbox: spawn setup refresh`，未完成运行时语法验证；未启动服务、未调用上传/分析/chat/真实 LLM。
- Python AST 语法检查和本地 health check 尝试执行时，当前沙箱返回 `windows sandbox: spawn setup refresh`；后台启动 uvicorn 的尝试被审批策略拒绝，因为属于长期服务启动。未完成浏览器运行时验证，未调用上传/分析接口。
- `backend/AGENTS.md`、`backend/docs/CODEX_STATE.md`、`backend/docs/CODEX_WORKLOG.md` 在本次任务前为空文件或无可见内容。
- `git status --short` 显示工作区已有大量未提交变更，包括 `.env`、`.env.example`、README、业务代码、`__pycache__`、storage 数据和 uvicorn 日志删除；本次任务不应回滚或整理这些变更。
- `git log --oneline -n 20` 曾尝试执行但当前沙箱返回 `windows sandbox: spawn setup refresh`，未能读取历史提交；历史工作从当前仓库无法确认。
