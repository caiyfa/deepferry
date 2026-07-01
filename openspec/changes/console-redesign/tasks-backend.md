# Console Redesign — Backend Tasks

> **Change ID**: `console-redesign` | **Role**: Backend Agent
> **Specs**: `llm-integration`, `dataset-engine`, `explore-mode`, `agent-monitor`

---

## Tech Context (Read First)

### Stack

Python 3.12 + async (asyncmy, asyncpg, httpx2) / FastAPI + Pydantic v2 + uvicorn
DuckDB embedded / openai SDK >= 2.43 / Jinja2

### Code Patterns to Follow

| Pattern | Reference File |
|---|---|
| Route structure | `src/deepferry/web/routes/query.py` — APIRouter + Pydantic model + async handler |
| Error handling | `src/deepferry/core/errors.py` — `DataSourceError(code, message, suggestion)` |
| Config loading | `src/deepferry/config.py` — TOML + `${ENV_VAR}` injection |
| DuckDB usage | `src/deepferry/engine/duckdb.py` — `DuckDBEngine` |

### API Contract (Frontend expects these exact signatures)

| Endpoint | Response |
|---|---|
| `POST /api/explore` (SSE) | stream → `StructuredResult` |
| `GET /api/explore/suggestions` | `{suggestions: string[]}` |
| `GET/POST/DELETE /api/datasets[/:id]` | `Dataset[]` / `Dataset` |
| `POST /api/datasets/:id/snapshot` | `{version, row_counts}` |
| `GET /api/datasets/:id/diff?v1&v2` | `{additions, deletions, modifications}` |
| `WebSocket /ws/agents` | push events |
| `GET /api/agents/sessions[/:id]` | `Session[]` / `Session` |
| `GET /api/agents/stats` | `{active_agents, today_queries, ...}` |
| `GET/POST/DELETE /api/saved-queries[/:id]` | `SavedQuery[]` / `SavedQuery` |
| `POST /api/query/analyze` | `{performance, safety, readability}` |
| `GET /api/schema/relationships` | `[{left, right, confidence}]` |

---

## P2: LLM Integration + Explore API (3 周)

### 2.1 LLM Client

- [ ] 2.1.1 创建 `src/deepferry/core/llm.py`：`LLMConfig` dataclass + `LLMClient` ABC + `OpenAICompatibleClient`
  - `base_url` 支持 DeepSeek/OpenAI/Ollama
  - 验证: `mypy src/deepferry/core/llm.py` 无错误
- [ ] 2.1.2 单元测试 mock `AsyncOpenAI`
- [ ] 2.1.3 `config.py` 解析 `[llm]` section，支持 `${DEEPFERRY_LLM_API_KEY}`

### 2.2 Prompt Templates

- [ ] 2.2.1 创建 `src/deepferry/core/prompts/`：`system.txt` + `schema.j2` + `few_shot.json` + `explore.j2`
  - 验证: 渲染后含表名/列名，不超过 max_tokens
- [ ] 2.2.2 `tests/test_prompts.py`

### 2.3 Safety

- [ ] 2.3.1 LLM SQL 经 `_scan_sql()` 二次校验 → 拒绝即返回错误
  - 验证: `DROP TABLE` → 拦截

### 2.4 Explore API

- [ ] 2.4.1 `POST /api/explore` (SSE) — prompt → LLM → safety → execute → stream result
  - 验证: curl → SSE 流 → 最终 `StructuredResult`
- [ ] 2.4.2 `GET /api/explore/suggestions` — 基于 schema 生成推荐问题
- [ ] 2.4.3 注册 router 到 `web/app.py`
- [ ] 2.4.4 `tests/test_explore.py`（mock LLM）

### 2.5 Error Handling

- [ ] 2.5.1 LLM 不可用 → HTTP 503 + structured error（非 500）
- [ ] 2.5.2 LLM 返回无效 SQL → 错误 + 原始输出
- [ ] 2.5.3 LLM 超时 → TIMEOUT error

### 2.6 Config

- [ ] 2.6.1 `config.example.toml` + `config.docker.toml` 添加注释掉的 `[llm]` section

---

### 🛑 STOP GATE — P2 验收

- [ ] `POST /api/explore` SSE stream → `StructuredResult` 格式正确
- [ ] `GET /api/explore/suggestions` → 3+ 推荐问题
- [ ] LLM 不可用 → 503（非 crash）
- [ ] LLM 返回 `DROP TABLE` → 拦截
- [ ] `mypy src/` + `pytest tests/test_explore.py tests/test_prompts.py` 通过

> ⚠️ 可用 mock LLM 验收。必须模拟成功/失败/超时三种场景。

---

## P3: Dataset Engine (3.5 周)

### 3.1 Storage

- [x] 3.1.1 `src/deepferry/core/dataset_storage.py`：write_parquet / write_json / write_arrow
  - 验证: Parquet → `pd.read_parquet()` 可读
- [x] 3.1.2 目录结构：`{data_dir}/datasets/{id}/v{n}/` + metadata.yaml

### 3.2 Dataset Manager

- [x] 3.2.1 `src/deepferry/core/dataset.py` — `DatasetManager`：create / get / list / delete
  - 单元测试: create → list → get → delete

### 3.3 Snapshot

- [x] 3.3.1 `src/deepferry/core/snapshot.py`：create_snapshot + SHA256 fingerprint + version chain
- [x] 3.3.2 增量刷新：`WHERE updated_at > last_snapshot_ts`

### 3.4 Version + Diff

- [x] 3.4.1 `src/deepferry/core/versioning.py`：v1→v2→v3 自动递增
- [x] 3.5.1 `src/deepferry/core/diff.py`：DuckDB EXCEPT/INTERSECT → additions/deletions/modifications

### 3.6 DuckDB Upgrade

- [x] 3.6.1 `duckdb.py`：`read_json/read_parquet` 替代 `_build_insert_values`
  - 现有 `test_duckdb.py` 仍通过
- [x] 3.6.2 `StructuredResult.source_breakdown` 新字段

### 3.7 Dataset API

- [x] 3.7.1 `routes/datasets.py`：7 个端点完整实现
- [x] 3.7.2 导出：Parquet/CSV/JSON/Arrow 四种格式
- [x] 3.7.3 `tests/test_dataset.py`

---

### 🛑 STOP GATE — P3 验收

- [x] 创建 → v1/ 目录含 parquet + json
- [x] 刷新 → v2 创建 + 版本链更新
- [x] diff → 返回正确差异
- [x] 导出 → Parquet 可被外部工具读取
- [x] 删 `_cache/` → 查询仍正常
- [x] `mypy src/` + `pytest tests/test_dataset.py` 通过
- [x] 现有 `test_duckdb.py` 全部通过

---

## P4: Agent Monitor Backend (3 周)

### 4.1 WebSocket

- [x] 4.1.1 `src/deepferry/web/ws.py`：`/ws/agents` + heartbeat 30s + 断线处理
  - 验证: 两客户端同时连接 → 都收到事件

### 4.2 Agent API

- [x] 4.2.1 `routes/agents.py`：sessions 列表/详情 + stats 聚合
  - 从 trace DB 读取
  - 验证: curl → JSON 正确

### 4.3 Context + Diagnosis

- [x] 4.3.1 MCP handler 提取 `_conversation_id` → trace metadata
- [x] 4.4.1 `src/deepferry/core/diagnostics.py`：4 条规则引擎 → diagnosis + suggestion
  - 验证: 单元测试覆盖所有 error pattern

### 4.5 Trace Enhancement

- [x] 4.5.1 `Execution` 新增 `agent_name`, `conversation_id`, `source_breakdown_json`
  - 验证: 现有 `test_trace.py` 仍通过

---

### 🛑 STOP GATE — P4 验收

- [x] WS `/ws/agents` → 事件推送
- [x] sessions API → 返回历史
- [x] stats API → 返回聚合
- [x] diagnose() → 4 种错误全部覆盖
- [x] `mypy src/` + `pytest tests/test_trace.py` 通过

---

## P5: Cross-Source Enhancements (3 周)

### 5.1-5.4

- [x] 5.1.1 DuckDB 返回 `source_breakdown` 数据
- [x] 5.2.1 `GET /api/schema/relationships` → 跨源 JOIN 检测
- [x] 5.3.1 SQLite `saved_queries` 表 + CRUD API (`routes/saved.py`)
- [x] 5.3.3 参数化查询：`{{param}}` 模板解析
- [x] 5.4.1 `POST /api/query/analyze` → LLM 分析 SQL（复用 llm.py）

---

### 🛑 STOP GATE — P5 验收

- [x] 跨源查询返回 `source_breakdown`
- [x] schema relationships 返回可 JOIN 字段
- [x] saved query CRUD 完整
- [x] SQL analysis 返回结构化建议
- [x] `mypy src/` 通过

---

## Kickoff Prompt

```
You are working on deepferry console redesign backend.
Tech: Python 3.12 + FastAPI + Pydantic v2 + DuckDB + openai SDK.
Read openspec/changes/console-redesign/tasks-backend.md.
Start from 2.1.1 (LLM client).

Rules:
- Match existing patterns in src/deepferry/ (routes/query.py, core/errors.py, config.py)
- All errors: DataSourceError(code, message, suggestion), never raw tracebacks
- Secrets: ${ENV_VAR} in config.toml, never hardcoded
- STOP at every "🛑 STOP GATE" — do NOT proceed until checks pass
- Run `mypy src/ && pytest tests/ -v` at each gate
- DO NOT implement frontend — only API per contract

Current: P2 (LLM Integration + Explore API). Start task 2.1.1.
```
