# PaperPilot

PaperPilot 是一个本地优先的科研论文阅读 Agent，提供浏览器界面、FastAPI API 和 CLI。

当前支持：

- PDF 上传、去重、删除与本地 SQLite 持久化
- PyMuPDF / GROBID 解析，以及可选的 Docling / Marker / MinerU adapter
- 论文信息、章节、方法和实验分析，生成中文 Markdown 精读笔记
- 基于工具调用的单篇/多篇论文对话，支持 SSE 流式输出
- 本地检索、版本化证据回读、会话恢复、上下文压缩和 Harness 调试轨迹

默认 `mock` 模式不访问真实 LLM；设置 DeepSeek 或 OpenAI-compatible 配置后才会发起外部模型请求。

## 目录结构

```text
backend/
  app/
    main.py
    api/routes/
      paper.py
      chat.py
    agents/
      agent_loop.py
      paper_agent.py
      paper_state.py
      paper_graph.py
      nodes/
    repositories/
    runtime/
    services/
      parsers/
      pdf_service.py
      parser_service.py
      llm_service.py
      file_service.py
    tools/
      paper_lookup_tools.py
      paper_tools.py
      multi_paper_tools.py
      memory_tools.py
      session_history_tools.py
    schemas/
    core/
      config.py
  memory/
  static/
  storage/
    papers/
    notes/
    paper_parse_cache/
    paper_section_json/
    chat_sessions/
    paperpilot.db
  scripts/
    run_analyze_paper.py
    run_context_acceptance.py
  requirements.txt
  .env.example
  README.md
```

## 安装依赖

Python 要求：`>=3.10`。

```bash
cd backend
python -m venv .venv
source .venv/bin/activate          # Linux / macOS
# .venv\Scripts\Activate.ps1       # Windows PowerShell
python -m pip install -r requirements.txt
```

`requirements.txt` 锁定的是当前已验证的直接依赖版本。Docling、Marker、MinerU 和 `tiktoken` 均为可选能力，不在默认安装中。

## 配置 .env

在 `backend/` 目录创建 `.env`：

```env
LLM_PROVIDER=mock
LLM_API_KEY=
LLM_BASE_URL=
LLM_MODEL=
LLM_TIMEOUT=60
LLM_TEMPERATURE=0.2
PDF_PARSER=pymupdf
GROBID_BASE_URL=http://localhost:8070
PDF_PARSER_TIMEOUT=30
PAPER_INFO_TOOL_AGENT_ENABLED=true
PAPER_INFO_WEB_ENRICH_ENABLED=false
PAPER_LOOKUP_TIMEOUT=10
PAPER_LOOKUP_MAX_RESULTS=5
PAPER_LOOKUP_PROVIDERS=arxiv,crossref,openalex
SECTION_REPAIR_LLM_ENABLED=false
SECTION_REPAIR_MAX_ROUNDS=1
PLAN_AGENT_LLM_ENABLED=false
PLAN_AGENT_MAX_INPUT_CHARS=3000
PLAN_AGENT_CONFIDENCE_THRESHOLD=0.7
TOOL_MAX_PARALLEL_CALLS=10
HARNESS_DEBUG_TRACE=true
```

环境变量说明：

- `LLM_PROVIDER`：默认 `mock`，可选 `mock` / `deepseek` / `openai_compatible`
- `LLM_API_KEY`：真实 LLM API Key，mock 模式留空
- `LLM_BASE_URL`：OpenAI-compatible 接口地址，例如 `https://api.deepseek.com/v1`
- `LLM_MODEL`：模型名称，例如 `deepseek-chat`
- `LLM_TIMEOUT`：请求超时时间，默认 `60`
- `LLM_TEMPERATURE`：采样温度，默认 `0.2`
- `PDF_PARSER`：默认 `pymupdf`，可选 `pymupdf` / `grobid` / `docling` / `marker` / `mineru`
- `GROBID_BASE_URL`：GROBID 服务地址，默认 `http://localhost:8070`
- `PDF_PARSER_TIMEOUT`：parser 外部服务请求超时时间，默认 `30`
- `PAPER_INFO_TOOL_AGENT_ENABLED`：是否启用 `paper_info_node` 的工具调用 Agent，默认 `true`
- `PAPER_INFO_WEB_ENRICH_ENABLED`：旧配置兼容项，建议新项目使用 `PAPER_INFO_TOOL_AGENT_ENABLED`
- `PAPER_LOOKUP_TIMEOUT`：论文元数据工具请求超时时间，默认 `10`
- `PAPER_LOOKUP_MAX_RESULTS`：每个 provider 最大候选数量，默认 `5`
- `PAPER_LOOKUP_PROVIDERS`：启用的 provider，默认 `arxiv,crossref,openalex`
- `SECTION_REPAIR_LLM_ENABLED`：章节修复是否允许调用 LLM，默认 `false`
- `SECTION_REPAIR_MAX_ROUNDS`：章节修复最大轮次，当前第一版默认 `1`
- `PLAN_AGENT_LLM_ENABLED`：是否启用受控 LLM planner，默认 `false`
- `PLAN_AGENT_MAX_INPUT_CHARS`：`plan_agent_node` 发送给 LLM 的 `raw_text_preview` 最大长度，默认 `3000`
- `PLAN_AGENT_CONFIDENCE_THRESHOLD`：LLM plan 被采纳的最低置信度，默认 `0.7`
- `TOOL_MAX_PARALLEL_CALLS`：同一 Agent Step 内安全 Tool 调用的最大并发数，默认 `10`；设为 `1` 时全部串行
- `HARNESS_DEBUG_TRACE`：是否将本地 Harness 调试事实写入独立 SQLite trace 表，默认 `true`；生产环境可设为 `false`

## PDF Parser 架构

当前 PDF 解析层已经拆成 parser adapter：

```text
app/services/parser_service.py
app/services/parsers/
  schema.py
  base.py
  pymupdf_parser.py
  grobid_parser.py
  docling_parser.py
  marker_parser.py
  mineru_parser.py
```

默认 parser 是 `PyMuPDF`：

```env
PDF_PARSER=pymupdf
```

`PyMuPDFParser` 已真实实现，会返回统一的 `ParsedPaper`，其中 `raw_text` 与旧流程兼容。后续章节抽取、方法分析、实验分析仍然读取 `raw_text`，所以现有 LangGraph 主流程不会被破坏。

后端接口现在也支持 `paper_language`：

- `paper_language` 是上传 metadata 和后续分析策略信息，不决定 parser。
- 旧 `POST /api/papers/{paper_id}/analyze` LangGraph 路线使用 `PDF_PARSER` 配置指定一个 parser。
- 普通聊天路线把 `parse_pdf_with_grobid`、`parse_pdf_with_pymupdf` 提供给 LLM；模型可在同一任务中分别调用二者并根据 Tool Result 决定下一步。
- parser 不会自动 fallback：GROBID 失败或字段缺失会原样作为 Tool Result 返回，由 Agent 决定是否调用 PyMuPDF。

可以通过 `.env` 切换 parser：

```env
PDF_PARSER=grobid
GROBID_BASE_URL=http://localhost:8070
PDF_PARSER_TIMEOUT=30
```

当前状态：

- `pymupdf`：默认可用，真实抽取 PDF 文本
- `grobid`：调用本地 GROBID `/api/processFulltextDocument`，解析 TEI XML 并返回 `ParsedPaper`
- `docling` / `marker` / `mineru`：预留实验 adapter，依赖未安装或未接入时会安全失败

parser_service 只执行明确指定的 parser；失败会返回给调用方，不会在服务层自动切换到另一个 parser。

之所以仍保留 `raw_text`：当前章节抽取和 LLM 分析节点都依赖纯文本输入。新的 `ParsedPaper` 会同时写入 state 的 `parsed_paper`，方便后续比较 PyMuPDF / GROBID / Marker / MinerU / Docling 的结构化解析质量。

## 论文信息提取与外部补全

`paper_info_node` 现在同时负责 PDF 内部信息提取和工具调用补全。英文论文优先级是：

```text
parser / parser_meta -> tool-calling agent -> default
```

中文论文优先级是：

```text
parser -> PyMuPDF 首页规则候选(parser_meta) -> first_page_llm -> default
```

中文论文不再默认依赖 Crossref/OpenAlex/arXiv 补全基础元数据。PyMuPDF 会从第一页和摘要区域尽量抽取：

- `candidate_title`
- `candidate_title_confidence`
- `candidate_title_candidates`
- `candidate_authors`
- `candidate_abstract`
- `candidate_keywords`
- `first_page_text`，最多 3000 字

中文标题候选不再主要依赖 `raw_text` 的文本顺序，因为部分 PDF 的文本流开头可能就是摘要或页眉。`PyMuPDFParser` 会读取第一页 `page.get_text("dict")` 的视觉布局，基于 bbox、字号、页面上方位置、居中程度、中文比例、标题长度和多行合并生成最多 5 个候选，并写入 `candidate_title_candidates`。

`paper_info_node` 使用中文标题候选时采用保守策略：

- `candidate_title_confidence >= 0.75`：直接使用 layout 高置信候选，source 为 `parser_meta`
- `candidate_title_confidence < 0.75`：不直接使用；如果真实 LLM 可用，只允许 LLM 从候选列表中选择，不能自由编写标题
- LLM 认为候选不可靠或 mock 模式下无法确认时，title 保持 `未明确提及`

如果中文论文仍缺 `authors/abstract`，可以用轻量 LLM 只读取首页文本做校正；不会把完整 `raw_text` 交给 LLM，也不会让它抽取 `title/year/venue`。中文论文的 `year/venue` 允许暂时缺失。

LangGraph 中不再接入 `paper_info_enrich_node`，流程保持为：

```text
pdf_parse_node -> plan_agent_node -> paper_info_node -> section_extract_node -> section_verify_node -> section_repair_node -> method_analyze_node -> experiment_analyze_node -> summary_write_node
```

## Plan Agent

`plan_agent_node` 是策略规划 Agent。它位于 `pdf_parse_node` 之后、`paper_info_node` 之前，只生成 `analysis_plan`，不控制 graph 路由。

当前采用 “Rule as evidence, LLM as planner, Code as guardrail”：

- 规则先提取受控证据 `evidence`，并生成 `rule_plan`
- 默认 `PLAN_AGENT_LLM_ENABLED=false`，只使用规则计划，不增加耗时
- 开启 LLM planner 后，只把 `evidence` 和 `rule_plan` 发给 LLM
- 不会把完整 `raw_text` 发给 LLM，只会发送最多 `PLAN_AGENT_MAX_INPUT_CHARS` 字的 `raw_text_preview`
- LLM 不能调用工具，不能输出代码，不能决定或改变 LangGraph 路由
- LLM 输出的 `llm_plan` 会经过代码校验；低置信、非法字段、非法 JSON 或安全策略冲突都会 fallback 到 `rule_plan`

它会基于受控证据判断：

- `paper_type`：`algorithm_paper` / `experimental_research` / `engineering_system` / `review` / `thesis_or_report` / `generic_research`
- `structure_type`
- `required_sections`
- `optional_sections`
- `metadata_strategy`
- `section_strategy`
- `repair_strategy`
- `document_size_strategy`
- `analysis_focus`
- `risks`

当前 `analysis_plan` 只是策略建议，后续 `section_verify_node`、`section_repair_node`、RAG 或条件边可以逐步读取它。API 响应会返回 `analysis_plan`、`paper_type`、`structure_type`、`plan_debug` 和 `agent_decisions`，方便观察规划结果。

`plan_debug` 会记录：

- `planner_mode`：`rule_only` / `llm_accepted` / `llm_rejected` / `llm_failed`
- `rule_plan`
- `llm_plan`
- `validated_plan`
- `validation_warnings`
- `evidence_summary`

中文论文如果没有 DOI、arXiv ID 或明确英文标题，即使 LLM planner 建议 external lookup，代码校验也会强制 `metadata_strategy.use_external_lookup=false`。大 PDF 只会在计划里标记 `use_chunking/use_rag=true`，本轮不会真正执行 RAG。

工具函数仍用 `@tool` 封装，便于后续迁移到 LangGraph `ToolNode`。当前由 `paper_info_node` 内部执行 OpenAI-compatible tool calling，两阶段完成：第 1 次 LLM 决策工具，第 2 次 LLM 汇总 JSON，最多 2 次 LLM 调用。同一轮多个工具会并发执行。

`paper_info_debug` 用于查看 `title/authors/year/venue/abstract` 每个字段来自 parser、parser_meta、first_page_llm、tool 还是默认值。`paper_info_debug["_zh_metadata"]` 会记录中文元数据候选、首页 LLM 使用情况和外部检索跳过原因。`paper_info_debug["_tool_agent"]` 会记录：

- 是否启用工具 Agent
- 是否真的调用了工具
- 当前 LLM Provider 是否支持 tool calling
- 缺失字段列表
- 使用过的工具
- 工具返回结果
- 最终 Agent JSON
- 错误信息

可复用工具：

- `search_arxiv_paper`
- `search_crossref_paper`
- `search_openalex_paper`

工具 Agent 配置：

```env
PAPER_INFO_TOOL_AGENT_ENABLED=true
PAPER_INFO_WEB_ENRICH_ENABLED=false
PAPER_LOOKUP_PROVIDERS=arxiv,crossref,openalex
```

触发规则：

- 如果 `title/authors/year/venue/abstract` 都已由 parser/parser_meta 提供，`paper_info_node` 不调用 LLM 和工具
- 英文论文存在缺失字段且工具 Agent 开启时，LLM 可自主调用 arXiv、Crossref、OpenAlex 工具
- 中文论文无 DOI、arXiv ID、明确英文标题时，不调用 Crossref/OpenAlex/arXiv
- 中文论文有 DOI 时可调用 Crossref/OpenAlex；有 arXiv ID 时才允许调用 arXiv
- 工具只返回候选论文元数据，不直接修改 state
- `paper_info_node` 只补缺失字段，不覆盖 parser/parser_meta 中已有明确值
- 外部工具返回多个候选后，会先做本地 rerank，不完全依赖 Crossref/OpenAlex/arXiv 的原始排序
- 本地 rerank 会综合 `title_similarity`、`author_match_score`、`year_match`、`venue_match_score`、`doi_match`、`arxiv_match` 得到 `local_match_score`
- 如果 provider 第二个候选更像目标论文，它可以被提升为 `local_rank=1`
- 只有字段置信度 `confidence >= 0.75` 且基础校验通过，才允许使用工具结果
- `paper_info_debug["_tool_agent"]` 会记录 `candidate_rank_before`、`candidate_rank_after`、`selected_candidate`、`top_candidates_after_rerank` 和 `local_match_score`
- 如果当前 LLM Provider 是 `mock` 或不支持 tool calling，主流程不会崩溃，缺失字段会保持默认值，并在 debug 中记录 `tool_calling_supported=false`
- analyze 响应中会返回 `missing_info_fields`、`need_web_search`、`web_search_debug` 和 `web_search_results`

当前不使用 CNKI。如果后续需要中文外部元数据，建议接入机构或学校自己的合法元数据接口。

### GROBID 使用说明

启动 GROBID：

```bash
docker run -d --name grobid --init --ulimit core=0 -p 8070:8070 grobid/grobid:0.9.0-crf
```

检查服务：

```bash
curl.exe http://localhost:8070/api/isalive
```

`.env` 配置：

```env
PDF_PARSER=grobid
GROBID_BASE_URL=http://localhost:8070
PDF_PARSER_TIMEOUT=30
```

GROBID parser 会上传 PDF 到：

```text
POST {GROBID_BASE_URL}/api/processFulltextDocument
```

并从返回的 TEI XML 中尽量提取 `title`、`authors`、`abstract`、`keywords`、`sections`、`references` 和 `raw_text`。GROBID 服务不可用、超时、返回 204/400/500/503、XML 解析失败或抽取文本为空时，不会自动切换到 PyMuPDF；聊天 Agent 会收到失败 Tool Result，并自行决定是否调用 PyMuPDF Tool。

对中文论文，如果 GROBID 没有把“引言”“材料与方法”“结果与分析”“讨论”“结论”等识别成 TEI `head`，GROBID parser 会从 body 的 `head/p` block 中使用中文标题规则做 `zh_heading_fallback`，结果放入 `ParsedPaper.sections`，调试信息放入 `parser_meta.section_titles` 和 `parser_meta.section_extraction_method`。

## mock 模式运行

`.env` 保持：

```env
LLM_PROVIDER=mock
```

启动 FastAPI：

```bash
cd backend
uvicorn app.main:app --reload
```

命令行分析 PDF：

```bash
cd backend
python scripts/run_analyze_paper.py --pdf path/to/paper.pdf
```

指定论文语言：

```bash
python scripts/run_analyze_paper.py --pdf path/to/paper.pdf --paper-language zh
python scripts/run_analyze_paper.py --pdf path/to/paper.pdf --paper-language en
```

## 真实 LLM 模式运行

DeepSeek 示例：

```env
LLM_PROVIDER=deepseek
LLM_API_KEY=你的_API_Key
LLM_BASE_URL=https://api.deepseek.com/v1
LLM_MODEL=deepseek-chat
LLM_TIMEOUT=60
LLM_TEMPERATURE=0.2
```

OpenAI-compatible 示例：

```env
LLM_PROVIDER=openai_compatible
LLM_API_KEY=你的_API_Key
LLM_BASE_URL=https://your-provider.example.com/v1
LLM_MODEL=your-model-name
LLM_TIMEOUT=60
LLM_TEMPERATURE=0.2
```

真实模式会调用 `{LLM_BASE_URL}/chat/completions`，请求格式兼容 OpenAI Chat Completions。

## 用 Swagger 测试

启动服务后，浏览器首页为 `http://127.0.0.1:8000/`，Swagger 为：

```text
http://127.0.0.1:8000/docs
```

精读调用顺序：

1. `POST /api/papers/upload` 上传 PDF，得到 `paper_id`，可传 multipart 字段 `paper_language=zh|en`
2. `POST /api/papers/{paper_id}/analyze` 生成精读笔记，可传 query 参数 `paper_language=zh|en`
3. `GET /api/papers/{paper_id}/note` 读取 Markdown 笔记
4. `DELETE /api/papers/{paper_id}` 删除论文及其本地产物

对话与会话接口：

- `POST /api/chat`：同步对话
- `POST /api/chat/stream`：SSE 流式对话
- `POST /api/chat/sessions`：创建可恢复会话
- `GET /api/chat/sessions`：列出会话
- `GET /api/chat/sessions/{session_id}`：恢复会话
- `DELETE /api/chat/sessions/{session_id}`：删除会话
- `GET /api/chat/sessions/{session_id}/turns/{turn_id}/trace`：读取单轮调试轨迹

健康检查：

```bash
curl http://127.0.0.1:8000/health
```

## Agent 上下文预算（第一阶段）

普通聊天与 SSE 都经过 `chat -> run_paper_agent -> run_agent`。memory 注入 system prompt，完整模型历史与当前用户消息组成 messages；每次 LLM 调用前（包括工具执行后的下一步）由 `context_service.check_request(messages, tools, config, model=...)` 重新计量，再调用返回值的 `require_sendable()`。

真实 Provider 必须为当前 `LLM_MODEL` 明确设置 `AGENT_CONTEXT_WINDOW`（W）与 `AGENT_MAX_OUTPUT_TOKENS`（O）；换模型时应同步核对这些容量。缺失、非正整数、预算非正、非法比例或不支持的输出参数会报 `context_config_invalid`，不猜测 128k。mock 使用测试用窗口 65536、输出上限 4096；这不代表真实模型容量。

| 配置 | 含义 / 默认值 |
| --- | --- |
| `AGENT_CONTEXT_SAFETY_TOKENS` | 安全余量 S，默认 1024，必须为正 |
| `AGENT_MODEL_INPUT_LIMIT` / `AGENT_MODEL_OUTPUT_LIMIT` | 可选独立输入/输出上限；O 不得超过输出上限，I + S 不得超过输入上限 |
| `AGENT_CONTEXT_TRIGGER_RATIO` | 窗口触发比例，默认 0.8 |
| `AGENT_CONTEXT_TARGET_RATIO` | 窗口目标比例，默认 0.6，必须低于触发比例 |
| `AGENT_CONTEXT_TARGET_BUDGET_RATIO` | 输入预算目标比例，默认 0.8；所有比例必须在 (0, 1) |
| `AGENT_OUTPUT_TOKEN_PARAMETER` | 默认 `max_tokens`；兼容端点可明确选择 `max_completion_tokens`，DeepSeek 只允许前者 |

默认公式为 `B = W - O - S`、`T = min(0.8W, B)`、`G = min(0.6W, 0.8B)`。`I < T` 正常发送；第三阶段在 `I >= T` 时尝试受限的旧轮次摘要，再计量。使用最新已提交上下文时，`T <= I < B` 仍允许发送，`I >= B`（包括恰好等于 B）明确拒绝。输出参数实际发送的值与预算的 O 相同，并使用同一次运行读取的模型配置。固定 LangGraph 的 text/JSON 路径及其现有预算策略不受这些 Agent 配置影响；已有两参数 LLM/fake 调用接口保持兼容。

计量包含 system/memory、全部模型可见历史、当前用户消息、工具 schema、assistant 工具调用参数和当前工具结果的完整 JSON。若环境已安装且能匹配模型的 `tiktoken`，优先使用其 tokenizer；本项目未增加 tokenizer 依赖。否则按完整 JSON 的 UTF-8 字节数估算，再加每条消息/schema 的封装余量。该方法对中文和 JSON 比“字符数 / 4”保守，但可能提前拒绝请求。两条计量路径均标记 `measurement_kind=estimated`：Provider 内部聊天模板和工具封装未知，即便保留 S，也不能保证绝不发生 Provider 超限。未来可用实际 usage 校准或接入完整 Provider 计量。

`ContextBudget` 返回 W/I/O/S/B/T/G、计量方法、触发状态及共享/独立输入超限标志。`AgentContext.metadata.context_budgets` 保存逐步检查，`llm_outputs` 保存逐步 usage/finish_reason。开启既有 `HARNESS_DEBUG_TRACE` 后，SQLite debug trace 保存 `llm/context_budget`、`llm/output` 的 usage/finish_reason、`llm/error` 的安全错误元信息；数字 token usage 不会被密钥脱敏规则误删。预算事件本阶段不新增前端进度展示。

`AgentLLMResponse` 与 `AgentLLMDelta` 新增默认 `None` 的 `usage`、`finish_reason`；`AgentLoopResult.llm_response` 可读取最终 Provider 响应，原 `finish_reason="final_answer"` 语义保持兼容。流式解析保留 usage-only、空 choices、终止原因和 `[DONE]`。`stop` 是正常结束，`tool_calls` 进入工具步骤，`length` 抛出 `LLMOutputTruncatedError`，保留响应供检查，不执行被截断的工具调用、不保存为成功回答。

预算拒绝在普通 API 返回 HTTP 413 / `context_budget_exceeded`，SSE 发出同名 error 并结束。配置错误为 `context_config_invalid`（普通 API HTTP 500）。`LLMProviderError` 保留 Provider、HTTP 状态、原始响应体及 code/type/param/message，并通过明确的上下文错误码或容量错误消息识别 `provider_context_exceeded`；普通 400、鉴权、限流和网络故障不直接归类为上下文超限。原始 Provider 错误体不发送到前端或写入日志；Provider 错误及 `llm_output_truncated` 在普通 API 为 HTTP 502。

第二阶段新增工具结果控制，第三阶段新增旧轮次摘要及 checkpoint，见下文。**压缩后仍达硬预算会明确拒绝，不删除原始历史、不截断当前用户消息；第四阶段仅对明确 Provider 超限执行至多一次安全重试。**

纯本地专项检查（fake LLM/HTTP/SSE，临时数据库）：

```bash
cd backend
PYTHONDONTWRITEBYTECODE=1 LLM_PROVIDER=mock .venv/bin/python -m scripts.test_context_budget
```

专项覆盖计量字段、中文/JSON、输出预留、严格预算边界、逐步重检、普通/SSE 路由错误、实际输出参数、usage-only、截断、结构化错误分类、配置校验及固定 text/JSON 接口兼容性。运行包含大工具结果的旧回归时，显式设置测试容量 `AGENT_CONTEXT_WINDOW=1000000 AGENT_MAX_OUTPUT_TOKENS=4096 LLM_PROVIDER=mock`，并将默认 SQLite/session 存储隔离到临时目录。

## 工具结果控制与证据回读（第二阶段）

工具结果有三层：`ToolExecutionResult.content` 保留原始序列化结果，供状态判断、内部程序和历史投影使用；`model_content` 是实际追加到 Agent messages 的受限结果；`history_result` 是跨轮简化事实及可回读引用。工具执行完成后，`tool_result_service.project_tool_result` 按字段与片段生成模型投影，再由第一阶段 `check_request` 重新检查下一次完整输入。普通/SSE 使用同一路径，计量与发送读取相同的 messages。

| 配置 / 边界 | 规则 |
| --- | --- |
| `TOOL_RESULT_MAX_CHARS` | 默认 12000，范围 4096–64000；每个工具结果的**完整序列化模型内容**上限，包含元信息与转义字符；多论文共享同一个上限 |
| `TOOL_READ_MAX_CHARS` | 默认 2000；单次片段读取文本上限，必须在 1 到 `min(4000, TOOL_RESULT_MAX_CHARS / 2)` 之间 |
| `top_k` | 整数 1–8，每个 parser 最多返回这些候选；实际模型片段数还受总结果预算限制 |
| 多论文 / 页码 | 单次 `paper_ids`、`pages` 均最多 8 项；query 为 1–2000 字符；运行时再次校验，不依赖 schema |

投影保留状态、paper_id/parser、关键错误与警告、实际证据和来源。检索正文只保留在顶层 `chunks`，`sources` 不再重复正文；同一引用重复出现时去重。先缩短字段和片段，再减少片段数量，最后序列化并校验总长度，从不直接截断 JSON 字符串。多论文使用相同片段数量和文本额度，保留每篇论文的位置及状态；正文不足时通过 `coverage`、`omitted_chunks` 和 `omitted_chunk_range`（去重候选中的零基半开区间）说明未覆盖内容。极小预算或大量元信息可能使某篇 `coverage=none`，这不等于读取成功。字段裁减通过 `*_truncated` / `*_omitted_count` 和 `omitted` 标记；`complete_returned_chunks` 仅指此次返回的候选片段完整，不代表论文全文已经读取。

注册的论文工具：

| 工具 | 当前结果与回读方式 |
| --- | --- |
| `parse_pdf_with_grobid` / `parse_pdf_with_pymupdf` | 保留解析成功/失败、缺失字段、警告和有限章节/页面证据；未展示全文可通过 `retrieve_paper_context` 在已存在缓存中检索 |
| `get_paper_info` / `extract_sections` | 只读已有缓存，返回受限元信息或章节预览；省略正文可再次定向检索 |
| `retrieve_paper_context` | 当前模型获得实际片段、定位和版本引用；不会为了检索重新解析，也不再额外写全文 chunk 索引 |
| `get_multi_paper_context` | 各篇独立状态、证据与省略信息，总长度统一受控；部分论文缺失时 `status=partial`，全部缺失时为 error |
| `read_paper_chunk` | 根据完整引用读取同一缓存片段；支持 `offset`、`max_chars`，返回 `next_offset`，读取结果本身也受总预算限制 |
| memory 写入工具 | 保留 saved/更新事实并作有限字段投影；省略的返回文本不另存 spill 文件，`readback=null` |

来源引用格式如下，实际值应从返回的 chunk 或 `history_result.evidence_refs` 原样复制，不要自行构造：

```json
{
  "paper_id": "p1",
  "parser": "grobid",
  "cache_version": "sha256:<64位小写十六进制内容哈希>",
  "chunk_id": "c_<64位小写十六进制片段哈希>"
}
```

向 `read_paper_chunk` 传入以上四项，可再传 `offset=0, max_chars=500`。历史引用另存 `offset` 和 `shown_chars`，表示模型实际看过的范围；`shown_chars` 是记录字段，不是工具入参。模型中的 `readback` 说明了直接读取已知片段或重新定向检索省略片段的方式。

引用内容版本绑定 paper_id、parser、原文、章节内容/位置和分块配置；重复章节标题也保留独立位置。检索和指定回读都通过 `retrieval_service.cached_paper_chunks` 从同一 canonical parser cache 推导片段。不会复制全文到新持久化目录，不删除或重写旧索引、PDF、缓存或历史；分块暂时按请求在内存中重建。缓存内容或分块配置变化返回 `stale_evidence_reference`，缺失/无效缓存返回 `evidence_cache_missing`，错误 chunk_id 返回 `evidence_not_found`，不会把旧 ID 映射到新段落。

Paper Agent 的回读权限限于当前/active paper_ids；低层服务用于可信本地调用，仍只接受限定 paper_id/parser，不接受文件路径，并拒绝缓存 symlink 逃逸。引用超出 Agent 范围返回 `evidence_access_denied`。验证错误明确进入模型结果，内部工具异常继续隐藏细节。通用展示截断不截短引用标识；历史单独保存最多 64 个完整来源引用（当前 8 篇 × 每篇最多 8 个模型片段），额外或无效引用若被过滤会显式记录数量。

debug trace 的 `tool/result_debug` 同时记录 `raw_result` / `raw_result_size`、`current_run_result` / `model_result_size` 和 `history_result` / `history_result_size`；预览仍受原调试脱敏与长度限制。原始 `content` 和成功/失败事实不会被模型投影覆盖。投影异常单独报告 `projection_error`，不改变已执行工具的结果，也不发送无限制原文。

第三阶段可复用 `project_tool_result`、`model_evidence_refs`、版本化回读及 `check_request -> require_sendable`。旧历史如果只有“检索成功”或 chunk 数量、没有完整四元引用，无法精确恢复当时证据；可在仍存在的当前缓存中重新检索，但不能把新结果当成旧证据。已有无版本 chunk 索引不用于本阶段引用回读。旧版本缓存被外部替换后不保留历史副本，因此旧引用明确失效。metadata、memory 或 debug 预览中已被省略的旧文本也不保证恢复。

本地专项验证：

```bash
cd backend
PYTHONDONTWRITEBYTECODE=1 LLM_PROVIDER=mock .venv/bin/python -m scripts.test_tool_result_control
```

测试使用临时 parser cache、临时数据库及 fake LLM，覆盖总预算、去重、有效 JSON、警告/失败、多论文分配、历史引用回读、缓存变化、访问与参数边界、工具 ID 配对、原始缓存不变以及普通/SSE 下一步内容一致。**第三阶段先复用工具控制，再尝试旧历史摘要；最终完整输入仍达硬预算会明确拒绝；第四阶段恢复仍受同一硬预算和调用额度约束。**

## 旧轮次摘要、checkpoint 与恢复（第三阶段）

普通与 SSE 请求在修改论文绑定、创建 turn 之前共用会话占用检查。同一 session 的第二个活动请求返回 HTTP 409 / `session_busy`；不同 session 可以并行，单步独立工具仍可并行。占用由实际执行任务释放，SSE 断开时后台线程仍持有占用，直到成功或失败退出。**这是进程内保护，部署必须使用单 API worker，不支持多 worker 的互斥保证。**

`run_agent` 每步在第二阶段工具结果投影后调用 `context_compaction_service.compact_for_request`。仅摘要 SQLite 中已结束旧轮次；当前用户原文、当前 system/memory、当前论文与比较对象、当前轮次工具调用及受限结果保留。失败旧轮次保留目标、已确认且配对的工具结果及失败状态；无结果调用标为未确认，不注入孤立 tool 消息。仍在执行的旧轮次不作为已完成历史。

| 配置 | 默认 / 规则 |
| --- | --- |
| `COMPACTION_RECENT_RATIO` | 0.16，范围 `[0,1)`；首次优先保留约此比例 W 的近期完整轮次。按完整轮次调整；压力下至少尝试最早可覆盖旧轮次 |
| `COMPACTION_TARGET_TOKENS` | 1200；摘要正文的软目标，正整数，必须低于生成上限 |
| `COMPACTION_MAX_OUTPUT_TOKENS` | 4096；实际传给同一 Provider/model 的摘要输出上限，可能包含推理 token，独立检查 W/S/B 及模型输出限制 |

每个用户 turn 最多调用摘要 LLM **两次**，跨 step 共用，失败、超时、无效响应也计入。摘要不运行普通 Agent Loop、不提供工具、不发送回答 delta、不占普通推理步数。首次纳入较早完整旧轮次和已有摘要；第二次减半近期原文保留量和正文目标，扩大覆盖范围，不能重复同一边界。摘要请求也计量完整输入，超限时只选择预算内的完整旧历史前缀，未覆盖轮次留在历史中；分批也共用两次额度。

首次后 `I <= G` 或 `G < I < T` 均继续；仍 `I >= T` 且可扩大范围时尝试第二次。无完整可用前缀、不能扩大范围、摘要未缩短时提前停止。最终 `I < B` 可发送，即使仍达 T；否则抛出 `context_budget_exceeded` 并给出 system/memory、工具定义、历史、当前轮次分别估算的贡献。独立输入限制仍须满足。不会删除当前问题或历史来绕过预算。

摘要入口 `llm_service.call_llm_summary` 返回兼容的 `AgentLLMResponse`，保留 usage、finish_reason、elapsed_ms。输出要求七栏：用户目标与约束、当前论文及比较对象、已完成工作、关键结论及证据引用、用户纠正与已推翻判断、未解决问题、下一步。空栏为“无”。提示要求区分事实/推断/待确认，保留数字、单位、条件、纠正与来源，历史资料不作为指令，历史工具结果不证明当前状态。候选须非空、七栏完整、正常 `stop` 结束、引用来自程序提供的来源目录，且加包装后比被替换内容更短。长度截断、无效引用、不缩短等候选不启用。**这些检查不能证明语义无损，没有第二个 LLM 评审流程。**

每次有效压缩独立执行“生成 → 校验 → 数据库提交 → 启用”。`SessionEventRepository.append_checkpoint` 用短事务追加 `context/checkpoint`，保存 version、previous_checkpoint_seq、covered_through_seq、summary、source_refs、provider/model、前后输入估算、measurement_kind、usage/finish_reason/耗时；时间复用事件字段。事务检查会话存在、前一 checkpoint 未变化、覆盖范围不倒退且止于已结束旧轮次，不跨未结束轮次，不持有事务等待 LLM。保存错误明确反馈，不通过 debug 或展示截断路径保存；会话删除后不会为了保存 checkpoint 重建会话。第二次失败保留第一次已提交成果。

`session_model_history_service.load_model_history` 恢复最近有效 checkpoint 加覆盖边界后的历史；当前 turn 新建 checkpoint 不被 `before_turn_id` 排除。摘要只作为 assistant 历史背景，带来源说明，不成为 system 指令。消息与 origins 同步更新，当前用户只出现一次；旧 JSON 的限长历史不重新进入该 SQLite 模型历史路径。原始聊天事件不变，前端仍显示原对话；checkpoint 不是伪造用户消息。不向 `memory.md` 或 `user.md` 写摘要，恢复不依赖 `HARNESS_DEBUG_TRACE`。

摘要来源由程序建立稳定目录，正文使用 `[R1]`，例如：

```json
{"id":"R1","kind":"events","start_seq":1,"end_seq":12}
```

有论文证据时同时保留 `kind="paper"` 和第二阶段的 paper_id/parser/cache_version/chunk_id、已读范围。目录与覆盖边界由程序确定，LLM 只能选择引用。`read_paper_chunk` 仍用于版本化论文证据回读。

新增工具 `read_session_history(start_seq, end_seq, field_index=0, offset=0, max_chars=4000)` 只绑定当前会话，无 `session_id` 参数。单次最多 100 个事件、序列化结果最多 4000 字符（可设 512–4000），仅允许读取已结束旧轮次；大范围分段读取。返回 `fields` 中保存字段的 path/value、事件 seq、field_index 和文本 offset；通过 `omitted/next_seq/next_field/next_offset` 分页。该工具直接对原始文本字段分段后再序列化，不截断 JSON 或论文引用标识。缺失、越界、未完成轮次、非法参数明确报错。历史保存内容有限，不把未返回部分说成已读取。

第四阶段复用 `ContextBudget`、`compact_for_request` 和 `AgentContext.metadata.compaction`：`summary_calls` 是本 turn 已用额度，`revision/checkpoint_seqs` 表示已提交上下文变化，`attempts` 保存每次输入预算、结束原因、usage、耗时及拒绝原因。同一 turn 后续处理应继续使用该 context/state，不能重建或清零额度。既有 debug trace 增加 `context/compaction`，第四阶段在现有 Inspector 中展示预算、摘要和恢复记录。

本地验证（临时 SQLite、fake LLM，不需要 Provider 密钥）：

```bash
cd backend
PYTHONDONTWRITEBYTECODE=1 LLM_PROVIDER=mock .venv/bin/python -m scripts.test_context_compaction
PYTHONDONTWRITEBYTECODE=1 LLM_PROVIDER=mock .venv/bin/python -m scripts.test_session_run
```

mock 摘要入口不伪造可用摘要；上述脚本注入 fake 摘要。真实使用复用已配置 Provider/model，但本阶段未做真实调用验证。计量仍为保守 estimated，结构/引用检查不保证语义无损；单个完整旧轮次若连摘要请求也装不下，不会拆散工具组，明确停止压缩。旧历史未保存的工具正文、被裁去的内容，以及已被替换的旧缓存均不能凭空恢复。第四阶段增加有限 Provider 超限恢复与现有 Inspector 展示；多 worker 占用保护仍未实现。

## Provider 超限恢复与运行展示（第四阶段）

完整链路为：每步计量完整请求 → 已有工具结果去重/限额 → 必要时旧轮次摘要 → 校验并提交 checkpoint → 检查最新输入硬预算 → 主 LLM 请求 → 明确 Provider 超限时有限恢复。只有 `I < B` 且满足独立输入限制才能发送。

超限恢复只处理既有 `LLMProviderError.is_context_overflow` 明确分类，包括 HTTP/SSE 的结构化上下文错误；鉴权、限流、网络和通用 HTTP 400 不进入恢复。只有失败请求尚未收到正文或任何工具调用片段时，才检查本 turn 剩余额度并缩减失败请求：

- 对当前轮次可版本化回读的论文证据，把超过 256 字符的正文缩到 256 字符，保留所有论文/片段位置、完整引用、错误/警告和执行状态，更新 `omitted/coverage/next_offset`。只在序列化和完整输入估算确实减少时采用；原始工具结果、缓存及持久化事实不改写。无回读来源的记忆写入等结果不借此删除。
- 仍有摘要额度与完整旧轮次时，用 `compact_for_request(..., force=True)` 强化整理：取消近期原文保留偏好、进一步收紧摘要正文目标，复用原有自身预算、校验及 checkpoint 提交逻辑。不会清零已用次数、绕过无进展停止或开启递归摘要。
- 重计量后，只有实际发送的完整输入比失败请求更小、且满足硬预算，才允许重试一次。工具内容缩减不消耗摘要额度，即使两次摘要额度已经用完，仍可在确有工具缩减进展时使用唯一一次主请求重试。

**每 turn 最多两次摘要调用（失败也计数）和一次因超限触发的主 LLM 请求重试。** 所有 step 与恢复共用 `metadata.compaction.summary_calls` 和 `metadata.context_recovery.retries`。重试只发生在当前主 LLM 请求内，不增加 Agent step，不重新执行 PDF 解析、检索或记忆写入，不重新启动 Agent。输入没有缩减则停止；重试再次失败则明确结束。没有通用自动重试开关或无界重试配置。

收到部分正文或工具调用后发生任何请求错误，会返回 `llm_stream_interrupted`：“回答流中断，未自动重放”；残缺工具调用不执行、两次回答不拼接。真实 Provider SSE 若没有结束标记/finish_reason 就意外 EOF，也按中断处理。usage-only 不算正文，`[DONE]`、空 choices 和结束事件继续支持。摘要没有回答 delta。SSE 断开仍由实际后台任务最终释放会话占用。

失败反馈区分历史整理失败但预算允许继续、硬预算拒绝、无缩减进展/额度不足、重试再次失败及回答流中断。仅使用安全消息，不暴露 Provider 原始响应内容，不声称聊天被删除。普通 HTTP 继续使用结构化错误码；SSE error 及持久化运行状态在现有聊天运行过程显示。

运行区新增 `context_status`，显示“正在整理较早的对话”“整理完成”“整理失败”“正在缩减上下文后重试”。保存为 `context/status`，刷新后仍可显示；它与 `context/checkpoint` 都不会成为伪造用户消息或正式回答。

Inspector 复用现有 Drawer，每步增加上下文记录，显示 W/I/O/S/B/T/G、计量方式、工具原始/模型可见字符量、恢复时进一步缩减量、摘要次数/触发原因、checkpoint 覆盖边界、前后输入估算、摘要耗时/usage/finish_reason、是否重试及结果/停止原因。失败的主调用与成功重试分别配对，不把重试结果挂到失败调用上。三类计量分别标注：

| 数据 | 意义 |
| --- | --- |
| `llm/context_budget` | 本地 `estimated` 输入估算，包括完整 messages/tools；不代表 Provider 实际 usage |
| `llm/output` / `llm/error` usage | 正常 Agent 请求的 Provider usage，包括可取得的失败流 usage；缺失不代表零 |
| `context/compaction` usage | 独立摘要调用的 Provider usage，不计入普通工具步骤 |

`context/recovery` 记录恢复前后估算、工具缩减和唯一一次重试的结果；`context/compaction` 记录摘要及停止状态。Inspector 不展示隐藏推理，也不把摘要正文放入聊天回答。`HARNESS_DEBUG_TRACE=false` 仅关闭 Inspector 捕获/访问，预算、摘要、checkpoint 恢复、运行状态和并发保护仍工作。

配置与使用：沿用前述精确模型容量、正常输出上限、安全余量、工具限额和三项 COMPACTION 配置。真实模型必须显式填写 `AGENT_CONTEXT_WINDOW` / `AGENT_MAX_OUTPUT_TOKENS`，摘要生成上限也须满足同一模型窗口/独立输出上限；不同模型不要沿用未经核对的容量。没有新增重试次数配置，安全上限固定。部署仍限单 API worker；通过已有聊天/SSE 入口使用，已有用户消息的 Inspector 按钮可查看轨迹。

可重复的完整本地验收：

```bash
cd backend
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B scripts/run_context_acceptance.py
# 单独运行恢复验收，同样隔离配置、存储并禁止网络：
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B scripts/run_context_acceptance.py --case test_context_recovery
```

脚本在独立子进程运行 31 个既有/新增检查，不读取真实 `.env`，强制 mock/空密钥、禁止 socket 连接，把 storage 重定向临时目录，不改真实缓存、数据库或 memory。回归使用较大测试窗口避免改变无关用例；预算专项独立设置边界。`test_context_ui` 使用本地已安装 Node 做 JavaScript 语法及最小 DOM 函数验证，不加载 CDN、不启动浏览器或服务，不代表浏览器端到端视觉测试。

本次实际验收 31/31 通过。token 计量仍为估算，摘要有损，结构/来源验证不证明语义无损；只摘要完整已结束旧轮次，当前轮次或 system/tools 本身过大仍会失败。进程内互斥只保证单进程，历史未保存工具正文和被替换旧缓存不能凭空恢复。**未执行真实 LLM/外网验证，未验证真实模型摘要质量或特定 Provider 的生产兼容性。**

## Harness Turn Trace

当 `HARNESS_DEBUG_TRACE=true` 时，可只读查看某一聊天 Turn 的已持久化调试投影：

```text
GET /api/chat/sessions/{session_id}/turns/{turn_id}/trace
```

先通过 `GET /api/chat/sessions/{session_id}` 的 `events` 找到目标 user message 对应的 `turn_id`，再查询 Trace。该接口只读取 D1 已记录的 Provider-facing LLM 输入、公开输出和 Tool 结果预览；不会重跑 Agent、Tool 或 LLM。

聊天页面会在已持久化的每条用户消息旁显示“轨迹”入口；点击后在右侧 Harness Inspector 查看该 Turn。刷新后的历史消息同样可用；没有 D1 Trace 的旧 Turn 会显示友好提示。

## 章节抽取策略与调试

当前章节抽取链路已经升级为一个低风险的 `extract -> verify -> repair` 子系统：

```text
section_extract_node -> section_verify_node -> section_repair_node -> method_analyze_node
```

`section_extract_node` 负责初始规则切分和保留原有 LLM 兜底逻辑：

- 先通过中英文双语标题规则切分 `Abstract/摘要`、`Introduction/引言`、`Related Work/相关工作`、`Method/材料与方法`、`Experiments/实验结果与分析`、`Conclusion/结论`
- 支持英文大小写不敏感、阿拉伯数字编号、罗马数字编号、中文数字编号、章节编号和括号编号，例如 `1 Introduction`、`2. Related Work`、`III. Methodology`、`一、引言`、`第一章 绪论`、`（二）实验设计`
- 中文摘要支持 `摘要：正文` / `摘 要：正文` 这类同一行格式，并以 `关键词`、`Abstract`、`引言` 等作为结束边界，避免把关键词或引言混入摘要
- 规则抽取成功的章节优先保留
- 如果 `method` 或 `experiments` 缺失，会调用当前配置的 LLM Provider 做章节归类兜底
- 如果规则抽到的主要章节明显过短，会尝试用 LLM 做校正，但不会整体替换所有规则结果
- LLM 还会尝试识别 `Proposed Approach`、`Our Method`、`Results and Discussion` 等非标准标题
- 兜底时最多传入论文前 40000 字，要求模型只复制原文章节内容，不总结、不改写
- LLM 兜底失败时会安全降级，保留规则结果并在 `section_meta` 中记录 warning
- `References` 及其之后的内容不会放入 `conclusion`

`section_verify_node` 不调用 LLM，只做规则质量检查：

- 判断 `abstract/introduction/related_work/method/experiments/conclusion` 是否缺失或过短
- 检查 `abstract` 是否混入关键词或引言
- 检查 `method/experiments/conclusion` 是否被 `References/参考文献` 污染
- 检查 `method` 和 `experiments` 是否可能混淆或重复
- 输出 `section_quality`、`section_verify_debug` 和 `needs_section_repair`

`section_repair_node` 第一版固定接入，但内部可跳过：

- 如果 `needs_section_repair=false`，直接 passthrough，不改变章节内容
- 如果需要修复，默认只做一次规则修复
- 可截断参考文献污染，重新提取摘要边界，或从 `raw_text` 中复制规则命中的 `method/experiments/conclusion`
- 修复后会重新计算 `section_quality`
- LLM repair 只预留配置，默认关闭，不增加额外耗时

章节节点会在 state 中额外写入 `section_meta`，每个章节包含：

```json
{
  "source": "rule | llm | missing",
  "matched_title": "III. Methodology",
  "normalized_section": "method",
  "length": 1234,
  "start_char": 5678,
  "end_char": 6912,
  "confidence": 0.92,
  "warning": ""
}
```

命令行分析会直接打印章节调试信息：

```bash
cd backend
python scripts/run_analyze_paper.py --pdf path/to/paper.pdf
```

输出中会看到每个章节的 `source`、`matched_title`、`length` 和 `warning`。

API 调试方式：

1. 在 Swagger 中调用 `POST /api/papers/upload` 上传 PDF
2. 调用 `POST /api/papers/{paper_id}/analyze`
3. 响应中的 `section_meta` 字段会返回每个章节的 `source`、`matched_title`、`length`、`start_char`、`end_char` 和 `warning`
4. 响应中的 `section_quality`、`section_verify_debug`、`needs_section_repair`、`section_repair_debug` 可用于查看章节自检和修复过程

示例：

```json
{
  "success": true,
  "section_meta": {
    "method": {
      "source": "rule",
      "matched_title": "III. Our Method",
      "length": 2048,
      "start_char": 12000,
      "end_char": 14048,
      "warning": ""
    }
  }
}
```

章节解析 JSON 落盘：

- 每次 CLI 或 API 成功完成 analyze 后，都会尝试保存章节解析结果
- 保存目录：`backend/storage/paper_section_json/`
- 文件名：原 PDF 文件名加 `.json`，例如 `sam2.pdf` 会保存为 `sam2.json`
- 保存内容包含 `paper_id`、`filename`、六个章节正文和 `section_meta`
- JSON 使用 UTF-8 和 `ensure_ascii=False` 保存，方便直接查看中文
- 如果保存失败，只会打印 warning，不会影响 analyze 主流程

示例路径：

```text
D:\Agent-paper\backend\storage\paper_section_json\sam2.json
```

## 当前限制

- Docling / Marker / MinerU 只有 adapter，需要用户自行安装并接入对应运行时
- GROBID 需要独立服务；解析失败时不会在服务层自动 fallback
- 章节抽取对复杂排版、扫描件和异常文本流仍可能不稳定
- 会话占用锁是进程内实现，当前部署应使用单 API worker
- 本地检索是词法检索，暂无 embedding / 向量数据库

## 后续扩展计划

- 接入 OCR 与更完整的可选 parser 运行时
- 在实测词法检索不足时再引入 embedding 检索
- 需要多 worker 部署时，将会话占用锁迁移到共享存储
