# Capability: Dataset UI

> **Status**: planned | **Milestone**: M4 | **Owner**: frontend | **Depends on**: `console-shell`, `dataset-engine`

## Summary

Frontend UI for datasets: creation dialog, sidebar integration, dataset detail
page with version timeline, diff visualization, and multi-format export.

## Motivation

- `dataset-engine.md` provides the backend APIs for format-agnostic dataset
  storage. Without a UI, users must call REST APIs manually to create, browse,
  and diff datasets.
- The Explore mode result page is the natural entry point for dataset creation —
  after asking a question, users should be able to save the answer as a
  versioned dataset with one click.
- Version timelines and diff views make data evolution tangible — "how did our
  customer spend change from last month?" becomes a visual comparison, not a
  manual SQL task.

## Specification

### Entry Points

Two entry points for dataset creation:

1. **Explore mode**: "💾 保存为数据集" button in result action bar
2. **Query mode**: "💾 保存为数据集" button in result action bar

Both trigger the Create Dataset modal.

### Create Dataset Modal

```
┌─ 保存为数据集 ──────────────────────┐
│                                      │
│  名称                                │
│  ┌──────────────────────────────────┐│
│  │ 客户发票跨源分析                  ││
│  └──────────────────────────────────┘│
│                                      │
│  描述 (可选)                          │
│  ┌──────────────────────────────────┐│
│  │ 关联 MySQL 客户表与 HTTP API 发票 ││
│  └──────────────────────────────────┘│
│                                      │
│  刷新策略                             │
│  ○ 一次性快照                         │
│  ● 定期刷新  [每周 ▾]                 │
│                                      │
│  存储格式                             │
│  ☑ Parquet (表数据)                   │
│  ☑ JSON (API 原始响应)                │
│                                      │
│  数据源: mysql-main, finance-api      │
│  包含 5 rows · 已就绪 312ms           │
│                                      │
│          [取消]     [创建数据集]       │
└──────────────────────────────────────┘
```

- Name is required, description optional
- Refresh strategy: "one-time" or "scheduled" (hourly/daily/weekly/monthly)
  with a time picker for scheduled
- Format checkboxes pre-selected based on result source types (→ `dataset-engine`
  format strategy)
- Source list and row counts are informational (read-only)
- Creating the dataset calls `POST /api/datasets`

### Sidebar Integration

After the existing "Saved" section in the sidebar:

```
─── 数据集 ─────────────
📦 客户发票跨源分析
   v3 (today)
   [↻] [⟷]
📦 月度销售趋势
   v12 (2026-06-28)
   [↻] [⟷]
📦 客户活跃度分析
   v1 (2026-06-15)
```

- Each item shows: icon + name + latest version + date
- `[↻]` triggers manual refresh (creates new snapshot)
- `[⟷]` opens diff view comparing latest vs previous version
- Click dataset name → navigate to dataset detail page

### Dataset Detail Page

```
╔══════════════════════════════════════════════════════════════╗
║ ← 返回         📦 客户发票跨源分析               [⋮ 更多]   ║
║                                                              ║
║ 描述: 关联 MySQL 客户表与 HTTP API 发票 · 每周刷新             ║
║ 数据源: mysql-main, finance-api                               ║
║                                                              ║
║ ┌─ 版本历史 ────────────────────────────────────────────────┐ ║
║ │  v3  2026-06-30 14:00  ● 当前  · 8 rows  · 手动刷新       │ ║
║ │       [在当前版本探索] [导出 ▾]                            │ ║
║ │  v2  2026-06-30 10:00  ● · 9 rows  · 自动刷新              │ ║
║ │       对比 v3: ⊖1 修改 +0 新增 -1 删除                     │ ║
║ │  v1  2026-06-29 09:00  ● · 7 rows  · 首次创建              │ ║
║ └──────────────────────────────────────────────────────────┘ ║
║                                                              ║
║ [在当前版本上探索 ▸]  [手动刷新]  [删除数据集]                ║
╚══════════════════════════════════════════════════════════════╝
```

- Version timeline: newest first, current version highlighted
- Each version shows: version number, timestamp, row count, creation method
- "在当前版本探索" → opens Explore mode pre-loaded with this dataset version
- "导出 ▾" → dropdown: Parquet, CSV, JSON, Arrow (formats available for that version)
- "手动刷新" → creates new snapshot via `POST /api/datasets/:id/snapshot`

### Diff View

Triggered from sidebar `[⟷]` or version timeline. Compares two selected versions:

```
┌─ v2 → v3 差异 ────────────────────────────────────────────┐
│                                                            │
│  概要: +2 新增 · -0 删除 · ⊖1 修改                          │
│                                                            │
│  版本选择: [v2 ▾] vs [v3 ▾]                                 │
│                                                            │
│  ┌─ 新增行 ──────────────────────────────────────────┐    │
│  │ name             │ total_spent │ invoices │ status │    │
│  │──────────────────│─────────────│──────────│────────│    │
│  │ Grace Lee        │ ¥2,650      │ 1        │ 🟢 new │    │
│  │ Frank White      │ ¥5,300      │ 2        │ 🟢 new │    │
│  └───────────────────────────────────────────────────┘    │
│                                                            │
│  ┌─ 修改行 ──────────────────────────────────────────┐    │
│  │ name            │ total_spent (v2) │ total_spent (v3) │ │
│  │─────────────────│───────────────── │─────────────────│ │
│  │ Alice Johnson   │ ¥15,650          │ ¥18,200 (+16%)  │ │
│  └───────────────────────────────────────────────────┘    │
│                                                            │
│  [导出差异报告]                                              │
└────────────────────────────────────────────────────────────┘
```

- Green background for new rows
- Red background for deleted rows
- Yellow background for modified rows with old → new value
- Version selector dropdowns to choose which two versions to compare

### Export

Multi-format export dropdown available in:
- Dataset detail page (per version)
- Diff view (diff report)

Formats: Parquet (.parquet), CSV (.csv), JSON (.json), Arrow IPC (.arrow)

Export calls `GET /api/datasets/:id/export?format=X&version=v3`.

## Acceptance Criteria

- [ ] Explore mode result → "保存为数据集" → modal → create → sidebar shows new dataset
- [ ] Click dataset in sidebar → detail page with version timeline
- [ ] Click `[↻]` → new snapshot created → version list updates with vN+1
- [ ] Click `[⟷]` → diff view with additions, deletions, modifications color-coded
- [ ] Export as Parquet → downloaded file readable by `pd.read_parquet()`
- [ ] Delete dataset → removed from sidebar + disk
- [ ] Create dataset with 2 sources → sources listed on detail page
- [ ] Refresh a 10K-row dataset → snapshot completes without blocking UI

## Out of Scope

- Inline data editing (modifying rows in the dataset view)
- Sharing datasets via link/URL
- Dataset search across all datasets (sidebar scroll is sufficient for < 50 datasets)
- Automated anomaly detection between versions
