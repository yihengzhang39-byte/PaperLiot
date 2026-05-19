# PaperPilot

PaperPilot 是一个科研论文阅读 Agent 后端框架。当前阶段聚焦“单篇论文精读”闭环：

PDF 输入 -> PDF 文本解析 -> 基本信息抽取 -> 章节内容抽取 -> 方法分析 -> 实验分析 -> 中文 Markdown 精读笔记。

## 目录结构

```text
backend/
  app/
    main.py
    api/routes/paper.py
    agents/
      paper_state.py
      paper_graph.py
      nodes/
    services/
      pdf_service.py
      llm_service.py
      file_service.py
    schemas/
    core/
      config.py
  storage/
    papers/
    notes/
  scripts/
    run_analyze_paper.py
  requirements.txt
  .env.example
  README.md
```

## 安装依赖

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

如果你使用项目根目录已有虚拟环境，也可以在根目录执行：

```bash
uv pip install -r backend\requirements.txt --python .venv\Scripts\python.exe
```

## 配置 .env

在 `backend/` 目录创建 `.env`：

```env
LLM_PROVIDER=mock
LLM_API_KEY=
LLM_BASE_URL=
LLM_MODEL=
LLM_TIMEOUT=60
LLM_TEMPERATURE=0.2
```

环境变量说明：

- `LLM_PROVIDER`：默认 `mock`，可选 `mock` / `deepseek` / `openai_compatible`
- `LLM_API_KEY`：真实 LLM API Key，mock 模式留空
- `LLM_BASE_URL`：OpenAI-compatible 接口地址，例如 `https://api.deepseek.com/v1`
- `LLM_MODEL`：模型名称，例如 `deepseek-chat`
- `LLM_TIMEOUT`：请求超时时间，默认 `60`
- `LLM_TEMPERATURE`：采样温度，默认 `0.2`

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

启动服务后打开：

```text
http://127.0.0.1:8000/docs
```

调用顺序：

1. `POST /api/papers/upload` 上传 PDF，得到 `paper_id`
2. `POST /api/papers/{paper_id}/analyze` 生成精读笔记
3. `GET /api/papers/{paper_id}/note` 读取 Markdown 笔记

健康检查：

```bash
curl http://127.0.0.1:8000/health
```

## 章节抽取策略

当前 `section_extract_node` 使用“规则优先 + LLM 兜底”：

- 先通过常见标题规则切分 `Abstract`、`Introduction`、`Related Work/Background`、`Method`、`Experiments`、`Conclusion`
- 支持大小写不敏感、阿拉伯数字编号和罗马数字编号，例如 `1 Introduction`、`2. Related Work`、`III. Methodology`
- 如果规则已经抽到 `method` 和 `experiments`，直接使用规则结果
- 如果 `method` 或 `experiments` 缺失，会调用当前配置的 LLM Provider 做章节归类兜底
- 兜底时最多传入论文前 40000 字，要求模型只复制原文章节内容，不总结、不改写
- `References` 及其之后的内容不会放入 `conclusion`

## 当前限制

- 只支持单篇论文精读
- 章节抽取已支持规则优先 + LLM 兜底，但复杂 PDF 版式下仍可能不稳定
- 长论文目前是简单截断，还没有 chunk / RAG
- 还没有 MySQL、任务状态、异步队列和前端

## 后续扩展计划

- 接入更完整的真实 LLM Provider 管理
- 加入 MySQL 保存论文、任务和笔记元数据
- 加入多论文对比 Agent
- 加入 Related Work 生成
- 加入 Vue 前端
