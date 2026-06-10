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
