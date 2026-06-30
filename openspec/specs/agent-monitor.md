# Capability: Agent Monitor

> **Status**: planned | **Milestone**: M3 | **Owner**: frontend + backend | **Depends on**: `console-shell`, `audit-trace`

## Summary

A real-time monitoring mode that shows live agent activity: what queries agents
are executing, their performance (latency, row count), execution traces with
timeline breakdown, conversation context (what the user asked → what SQL was
generated → what result was returned), and automatic error diagnosis.

## Motivation

- Agent developers currently have zero visibility into what queries their
  agents are generating. Debugging requires manually checking trace logs.
- This is deepferry's unique differentiator — no other SQL client or BI tool
  shows "what AI agents are doing to data" in real time.
- The trace infrastructure already exists (`audit-trace.md`, M2.5) — this
  spec adds real-time push and a human-facing UI on top of it.
- Error diagnosis is reactive today (developer checks logs after failure).
  With real-time monitoring, errors are surfaced instantly with root cause
  analysis.

## Specification

### Real-time Activity Feed

WebSocket endpoint `/ws/agents` pushes events:

```json
{
  "type": "query_executed",
  "agent": "Claude-3.5",
  "session_id": "a3f2...",
  "source_ids": ["mysql-main", "finance-api"],
  "statement": "SELECT c.name, SUM(i.total_amount)...",
  "status": "success",       // "success" | "error" | "running"
  "row_count": 5,
  "execution_time_ms": 312,
  "timestamp": "2026-06-30T15:34:21Z"
}
```

Feed display:
- Newest-first, scrollable with virtual list
- Each item shows: agent icon + name, SQL preview (collapsed), status badge,
  latency, source list
- Color-coded: green (success), red (error), blue (running)
- Click to expand → execution detail slide-out panel

### Statistics Cards

Four summary cards at the top of the feed:

| Card | Value | Source |
|---|---|---|
| Active Agents | Count + online indicator | WebSocket connections |
| Today's Queries | Count + % change vs yesterday | Trace DB aggregation |
| Avg Latency | ms + trend arrow | Rolling 1-hour window |
| Error Rate | % + status indicator | Rolling 1-hour window |

Cards update every 10 seconds or on each new event.

### Execution Detail Panel

Slide-out panel (or inline expand) showing for a specific execution:

1. **Agent conversation context**: the user message that triggered this query,
   the agent's intermediate response, and the final agent output
2. **Full SQL**: syntax-highlighted, copyable, with "Open in Query mode" button
3. **Execution timeline**: waterfall bar chart of each phase
   (parse → safety check → pool acquire → execute → serialize)
4. **Result preview**: first 5 rows of the returned data
5. **Cross-source breakdown** (when applicable): per-source timing with
   orchestration flow diagram (→ `cross-source-ui` spec)

### Conversation Context Extraction

Agent conversation context is extracted from the MCP protocol metadata:

```python
# In MCP call_tool handler, before executing query:
trace_context = {
    "agent_user_message": extract_from_mcp_metadata(request),  # if available
    "agent_conversation_id": request.params.get("_conversation_id"),
}

# Stored in trace DB, retrieved by monitor API
```

When the MCP client includes conversation metadata headers, they are captured.
When not available, context is shown as "未提供对话上下文".

### Error Diagnosis

When `status == "error"`, the feed item includes automatic diagnosis:

| Error Pattern | Diagnosis | Suggestion |
|---|---|---|
| `Table 'x' doesn't exist` | Agent referenced non-existent table | "表不存在，检查 schema 提示是否准确" |
| `Timeout` (>5000ms) | Query too slow | "查询超时，建议添加索引或缩小范围" |
| `Connection refused` | Source unreachable | "数据源连接失败，检查服务状态" |
| `MISSING_LIMIT` | No LIMIT on cross-source | "跨源查询缺少 LIMIT 子句" |

Diagnosis is rule-based (regex match on error message), not LLM-based, for
predictable latency.

### Feed Filters

Filter bar above the feed:

| Filter | Options |
|---|---|
| Agent | "All" / per-agent dropdown |
| Status | "All" / "Success" / "Error" / "Running" |
| Source | "All" / per-source multi-select |
| Time range | "Last 5 min" / "Last hour" / "Today" / Custom |

### Empty State (No Agents Connected)

When zero agents are online:
- Show empty state with setup instructions
- CLId command to start the MCP server
- JSON config snippet for Claude Desktop / Cursor
- Link to documentation

### API Endpoints

| Endpoint | Method | Description |
|---|---|---|
| `/ws/agents` | WS | Real-time agent activity stream |
| `/api/agents/sessions` | GET | Historical session list with pagination |
| `/api/agents/sessions/:id` | GET | Session detail with trace + context |
| `/api/agents/stats` | GET | Aggregate statistics for cards |

## Acceptance Criteria

- [ ] Agent executes query → appears in feed within 500ms
- [ ] Multiple agents querying simultaneously → all appear in feed, ordered by time
- [ ] Expand feed item → full execution timeline with per-phase latency
- [ ] Error query (table not found) → red badge + diagnosis message
- [ ] Cross-source query → orchestration flow shown in detail panel
- [ ] Filter to "Error only" → only failed queries shown
- [ ] Kill WebSocket connection → feed shows "连接断开" and auto-reconnects
- [ ] 100+ queries in feed → scrollable without performance degradation (virtual list)

## Out of Scope

- Agent-to-agent comparison dashboard
- Historical trend charts beyond statistics cards
- Alerting (Slack/email/pager) on error spikes
- Modifying agent behavior from the monitor (read-only)
