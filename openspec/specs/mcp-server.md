# Capability: MCP Server

> **Status**: planned | **Milestone**: M1 | **Owner**: backend

## Summary

Implement an MCP-compliant server exposing data source query capabilities as `tools` to LLM agents (Claude Desktop, etc.). Supports both `stdio` and `Streamable HTTP` transport.

## Motivation

- LLM agents need a standardized protocol to discover and execute data queries
- MCP is the emerging standard for agent-tool communication
- Must support at least 3 tools: `list_tables`, `query`, `schema_info`

## Specification

### Transport Protocols

| Protocol | Use Case |
|----------|----------|
| `stdio` | Claude Desktop local integration |
| Streamable HTTP | Remote agent access, Tauri sidecar |

### Tools Exposed

| Tool | Parameters | Returns | Description |
|------|-----------|---------|-------------|
| `list_tables` | `source_id: str` | `[TableInfo]` | List all tables/views/resources in a data source |
| `query` | `source_id: str, sql: str, params?: dict` | `QueryResult` | Execute a query against a data source |
| `schema_info` | `source_id: str, table?: str` | `SchemaInfo` | Get schema metadata for tables |

### Error Handling

All errors returned as structured JSON, NOT Python tracebacks:

```json
{
  "error": {
    "code": "CONNECTION_FAILED",
    "message": "Cannot connect to MySQL at localhost:3306",
    "suggestion": "Check if MySQL is running and credentials are correct"
  }
}
```

Error codes: `CONNECTION_FAILED`, `AUTH_FAILED`, `QUERY_TIMEOUT`, `INVALID_SQL`, `SOURCE_NOT_FOUND`

### Configuration

```toml
[mcp_server]
transport = "stdio"          # or "http"
http_port = 8080             # only for Streamable HTTP
config_path = "config.toml"  # path to data source config file
```

## Acceptance Criteria (M1 DoD)

1. `pip install deepferry` installs successfully in fresh venv
2. `deepferry mcp-server --config config.toml` starts stdio MCP server
3. Claude Desktop connects and lists ≥3 tools
4. Agent executes `list_tables` → returns table list
5. Agent executes `query` with SQL → returns structured rows
6. Connection failure returns structured error (not traceback)
7. `ruff check .` passes, `mypy --strict` passes
8. Core logic test coverage >70%

## Interview Story

MCP three-primitive (tools/resources/prompts) trade-off: why `tools` are the right abstraction for data access — resources are for static content, prompts are for templating; tools map naturally to parameterized, state-changing queries.

## Dependencies

- [[datasource-abstraction]] — DataSource base class for query execution
- [[sql-datasource]] — MySQL and PostgreSQL implementations
