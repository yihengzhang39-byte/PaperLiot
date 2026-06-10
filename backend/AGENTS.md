# AGENTS.md

本文件是 Codex 进入 `backend/` 项目时优先阅读的长期工作规则。后续修改前，先用只读命令确认当前仓库状态和相关实现，避免覆盖用户已有改动。

## 项目概览

- 项目名称：PaperPilot。
- 项目定位：科研论文阅读 Agent 后端框架，当前聚焦“单篇论文精读”闭环。
- 核心功能：PDF 上传、本地存储、PDF 文本解析、论文基础信息抽取、章节抽取与质量校验/修复、方法分析、实验分析、生成中文 Markdown 精读笔记。
- 主要技术栈：Python、FastAPI、LangGraph、Pydantic、PyMuPDF、python-dotenv、requests、langchain-core；可配置 OpenAI-compatible/DeepSeek 风格 LLM；可选 GROBID、Docling、Marker、MinerU parser adapter。
- 当前入口：`app/main.py` 创建 FastAPI app；`app/api/routes/paper.py` 提供 `/api/papers` 路由；`app/agents/paper_graph.py` 编排 LangGraph 单篇论文分析流程；`scripts/run_analyze_paper.py` 提供 CLI 分析入口。
- 当前主要流程：`pdf_parse_node -> plan_agent_node -> paper_info_node -> section_extract_node -> section_verify_node -> section_repair_node -> method_analyze_node -> experiment_analyze_node -> summary_write_node`。

## 主要目录结构

```text
backend/
  app/
    main.py                       # FastAPI 入口
    api/routes/paper.py            # PDF upload/analyze/note API
    agents/
      paper_graph.py               # LangGraph workflow
      paper_state.py               # shared state definition
      nodes/                       # graph nodes
    services/
      parsers/                     # PDF parser adapters
      file_service.py              # local PDF/note/metadata storage
      llm_service.py               # mock and OpenAI-compatible LLM calls
      parser_service.py            # parser selection and fallback
      paper_lookup_service.py      # paper metadata lookup service
    tools/
      paper_lookup_tools.py         # arXiv/Crossref/OpenAlex lookup tools
    schemas/
    core/config.py                  # environment config and storage dirs
  docs/
    CODEX_STATE.md                 # Codex long-term project state
    CODEX_WORKLOG.md               # Codex work log and handoff notes
  scripts/
    run_analyze_paper.py            # CLI single-PDF analysis
    profile_paper_info_node.py      # paper_info_node profiling helper
  storage/
    papers/                         # uploaded PDFs and generated sample PDFs
    notes/                          # generated Markdown notes
    paper_metadata/                 # local paper metadata JSON
    paper_section_json/             # extracted section JSON
  requirements.txt
  README.md
  .env.example
```

## 常用命令

在执行任何会改变环境或产生外部副作用的命令前，先向用户说明并确认。

安装依赖：

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

如果使用项目根目录已有虚拟环境：

```bash
uv pip install -r backend\requirements.txt --python .venv\Scripts\python.exe
```

运行 FastAPI 服务：

```bash
cd backend
uvicorn app.main:app --reload
```

健康检查：

```bash
curl http://127.0.0.1:8000/health
```

命令行分析 PDF：

```bash
cd backend
python scripts/run_analyze_paper.py --pdf path/to/paper.pdf
python scripts/run_analyze_paper.py --pdf path/to/paper.pdf --paper-language zh
python scripts/run_analyze_paper.py --pdf path/to/paper.pdf --paper-language en
```

GROBID 可选服务：

```bash
docker run -d --name grobid --init --ulimit core=0 -p 8070:8070 grobid/grobid:0.9.0-crf
curl.exe http://localhost:8070/api/isalive
```

测试：待确认。当前仓库未发现专门的 `tests/` 目录或 README 中的测试命令。

格式化/lint：待确认。当前仓库未发现 `pyproject.toml`、`setup.py` 或明确的 lint/format 配置。

构建：待确认。当前是 FastAPI 后端项目，未发现前端 `package.json` 或构建脚本。

## Codex 工作规则

- 修改代码或文档前，必须先阅读相关文件和相邻实现；复杂任务先列计划，简单任务也要先说明改动方向。
- 优先复用现有结构、命名、节点模式、服务层和配置读取方式，不要随意重构。
- 不要无脑新建大模块；确需新增文件时，先确认是否已有可扩展位置。
- 遇到不确定业务逻辑，先用 `rg` 搜索项目已有实现和 README/docs 说明，再决定方案。
- 不要提交 `.env`、token、API Key、密钥、缓存、日志、大文件、PDF、模型权重或生成数据。
- 不要随意执行 `git add -A`。如用户要求提交，必须先查看 `git status`，只暂存本次相关文件。
- 不要覆盖用户已有改动；工作区可能是 dirty 状态，修改前后都要核对 diff。
- 如果涉及浏览器自动化，先检查 Playwright 是否已安装或项目是否已有相关配置，不要重复安装。当前后端文件清单中未发现 Playwright 配置，是否需要浏览器自动化为待确认。
- 如果涉及依赖安装、外部登录、调用真实外部 API、发送消息、外部投递、删除数据、数据库迁移、启动长期后台服务、Docker 服务或真实 LLM 调用，必须先询问用户。
- 默认用中文和用户沟通；除非用户明确要求英文。
- 对不确定信息标注“待确认”，不要凭空补功能、命令或模块。

## 重要约束

- `storage/` 下多为运行产生的 PDF、Markdown、metadata、section JSON；除非任务明确要求，不要批量修改、删除或提交。
- `docs/CODEX_STATE.md` 和 `docs/CODEX_WORKLOG.md` 是 Codex 状态记录和交接记录；每次重要工作后应更新工作日志。
- `.env` 包含本地/私密运行配置；不要打印敏感值，不要提交真实密钥。
- `__pycache__/`、`*.pyc`、uvicorn 日志属于生成物；除非用户明确要求清理，不要主动改动。
- `paper_lookup_tools.py`、`paper_lookup_service.py`、GROBID、arXiv/Crossref/OpenAlex 查询、真实 LLM 调用都可能触达外部服务；执行前确认网络和副作用边界。
- API 上传与分析会写入 `storage/papers/`、`storage/notes/`、`storage/paper_metadata/`、`storage/paper_section_json/`；运行前应告知用户会产生文件。
- 当前仓库已有多处未提交业务文件和生成文件改动；后续 Codex 不应把这些改动当作自己产生的变更，也不应擅自回滚。
