# Capability: LLM Integration

> **Status**: planned | **Milestone**: M3 | **Owner**: backend | **Depends on**: `mvp-init` M2

## Summary

A lightweight, pluggable LLM client that converts natural-language questions into
SQL queries. Supports OpenAI-compatible APIs (DeepSeek, local models). Used by
the Explore mode and AI SQL optimization features. The client is a backend-only
concern — the frontend never touches LLM API keys directly.

## Motivation

- `pyproject.toml` already lists `openai>=2.43,<3` with the note
  "DeepSeek via base_url". The dependency exists but has never been wired up.
- Explore mode (`explore-mode.md`) cannot function without NL→SQL capability.
  Delegating to the connected MCP agent creates a circular dependency and makes
  deepferry non-standalone.
- Multiple MCP servers already embed LLM clients (Vanna.ai, SQLChat) — this is
  a validated architectural pattern, not a violation of MCP purity.
- A clean LLM abstraction allows swapping backends without touching Explore
  or AI-optimization code.

## Specification

### Architecture

```
config.toml [llm] section
        │
        ▼
LLMClient (abstract)
   ├── OpenAICompatibleClient   ← DeepSeek, OpenAI, local models
   └── (future) AnthropicClient
        │
        ▼
Explore API ──→ LLMClient.generate_sql(question, schema_context)
AI Optimize  ──→ LLMClient.analyze_sql(statement)
```

The LLM client is a **backend-only** service. The frontend calls `/api/explore`;
the backend calls the LLM. API keys never reach the browser.

### Configuration

```toml
[llm]
provider = "deepseek"                          # "openai" | "deepseek" | "local"
api_key = "${DEEPFERRY_LLM_API_KEY}"           # env-var injection, never plaintext
model = "deepseek-chat"                        # model name
base_url = "https://api.deepseek.com/v1"       # compatible API endpoint
max_tokens = 2000
temperature = 0.1                              # low for deterministic SQL
timeout = 15                                   # seconds
```

`api_key` **must** use `${ENV_VAR}` syntax — `config.toml` is committed, secrets are not.

### LLMClient Interface

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass

@dataclass
class LLMConfig:
    provider: str
    api_key: str
    model: str
    base_url: str
    max_tokens: int = 2000
    temperature: float = 0.1
    timeout: int = 15

@dataclass
class GenerateSQLRequest:
    question: str
    schema_context: str    # table names, columns, types, row counts, samples
    source_ids: list[str]
    conversation_history: list[dict] | None = None  # for follow-up questions

@dataclass
class GenerateSQLResponse:
    sql: str
    explanation: str
    model: str
    tokens_used: int

class LLMClient(ABC):
    @abstractmethod
    async def generate_sql(self, request: GenerateSQLRequest) -> GenerateSQLResponse: ...
    @abstractmethod
    async def health_check(self) -> bool: ...
```

### Implementation: OpenAICompatibleClient

Uses the existing `openai` SDK with `AsyncOpenAI`:

```python
from openai import AsyncOpenAI

class OpenAICompatibleClient(LLMClient):
    def __init__(self, config: LLMConfig):
        self._client = AsyncOpenAI(
            api_key=config.api_key,
            base_url=config.base_url,
            timeout=config.timeout,
        )
```

The `base_url` parameter is what makes this compatible with DeepSeek
(`api.deepseek.com/v1`), local models (Ollama `localhost:11434/v1`), and
any OpenAI-compatible proxy.

### Prompt Construction

The `generate_sql()` method constructs a prompt with these sections:

1. **System message**: SQL expert role, safety rules (SELECT only, LIMIT required)
2. **Schema context**: For each selected source, list tables with columns, types,
   row counts, and 3 sample values per column (for LLM to understand value formats)
3. **Cross-source hints**: When 2+ sources selected, note potential JOIN columns
   (e.g., "customers.tax_no can be joined with invoices.buyer_tax_no")
4. **Few-shot examples**: 3 static examples showing question → SQL pairs
5. **User question**: The actual NL input

### Prompt Templates

Templates live in `src/deepferry/core/prompts/`:

```
src/deepferry/core/prompts/
├── __init__.py
├── system.txt           ← system message
├── schema.j2            ← Jinja2 template for schema context
├── cross_source.j2       ← cross-source hints
├── few_shot.json         ← question→SQL example pairs
└── explore.j2            ← final prompt combining all sections
```

### Safety

- LLM output is treated as **untrusted**. The generated SQL is re-scanned by
  `safeguards._scan_sql()` before execution.
- If the LLM returns non-SQL content (hallucination), the guard rejects it.
- `max_tokens` limits prevent runaway token consumption.
- Timeout prevents hanging on slow LLM responses.

### Error Handling

| Condition | Behavior |
|---|---|
| LLM unreachable | Return error with "AI 服务暂不可用" message; explore mode falls back to schema browser |
| LLM returns invalid SQL | Return error with the attempted SQL for user inspection |
| LLM exceeds timeout | Return partial result if any SQL was generated; otherwise timeout error |
| API key not configured | Skip LLM init; all LLM-dependent features return graceful fallback |

### Observability

- Each LLM call is logged with: question, model, tokens_used, latency_ms
- Tokens and latency are exposed via existing trace infrastructure (→ `audit-trace.md`)
- Configurable log level for prompt debugging (don't log API keys)

## Acceptance Criteria

- [ ] `config.toml` with `[llm]` section + valid API key → `POST /api/explore` returns SQL
- [ ] `config.toml` without `[llm]` section → explore mode returns graceful fallback, no crash
- [ ] Set `base_url` to `http://localhost:11434/v1` (Ollama) → works with local model
- [ ] LLM returns `DROP TABLE customers` → safeguard rejects, error returned
- [ ] LLM call > 15s → timeout triggered, partial result returned if available
- [ ] 10 concurrent `/api/explore` requests → no API key leak, no token exhaustion
- [ ] Prompt templates are editable without code change (Jinja2 files in `prompts/`)

## Out of Scope

- Streaming token-by-token (SSE for progress steps is in `explore-mode.md`)
- Fine-tuning / model training
- Multi-provider failover (primary DeepSeek, fallback OpenAI)
- Cost tracking / budget enforcement (P6 polish)
