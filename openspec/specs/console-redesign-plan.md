# deepferry Console — 分阶段改造计划

> **DEPRECATED** — 每个 Phase 的详细内容已拆分到各 capability spec。
> 此文件保留作为高层路线图参考。
> 基于: `console-shell.md`, `explore-mode.md`, `llm-integration.md`,
>   `agent-monitor.md`, `cross-source-ui.md`, `query-enhancement.md`,
>   `dataset-engine.md`, `dataset-ui.md`

---

## 总览

| Phase | 名称 | 工期 | 前后端并行 |
|---|---|---|---|
| **P1** | 基础设施：三模式框架 + 命令面板 | 2 周 | ✅ |
| **P2** | 探索模式：NL → SQL | 3 周 | ✅ |
| **P3** | 数据集引擎：格式无关存储 + 快照版本 | 3.5 周 | 后端先行 |
| **P4** | 监控模式：Agent 实时 Feed | 3 周 | ✅ |
| **P5** | 跨源编排 + 查询增强 | 3 周 | ✅ |
| **P6** | 打磨上线 | 1 周 | ✅ |

**总工期：15.5 周**（P3 可与 P1 并行启动）

---

## P1: 基础设施（2 周）

### 目标
三模式侧边栏导航、全局命令面板、统一状态栏。**无新 API**，纯前端重构。

### 前端任务

| # | 任务 | 文件 | 估时 |
|---|---|---|---|
| F1.1 | 重构 `App.tsx`：三模式路由（Explore / Monitor / Query） | `frontend/src/App.tsx` | 2h |
| F1.2 | 实现 `Sidebar.tsx`：模式切换 + 数据源列表 + 多选 toggle | `frontend/src/components/Sidebar.tsx` | 4h |
| F1.3 | 实现 `StatusBar.tsx`：连接状态 + Agent 在线数 + 版本号 | `frontend/src/components/StatusBar.tsx` | 2h |
| F1.4 | 实现 `CommandPalette.tsx`：`Ctrl+K` 全局搜索/跳转 | `frontend/src/components/CommandPalette.tsx` | 4h |
| F1.5 | 全局快捷键注册（`Ctrl+1/2/3` 切换模式） | `frontend/src/App.tsx` | 1h |
| F1.6 | 统一 CSS 变量 + 深色主题 | `frontend/src/index.css` | 3h |
| F1.7 | 数据源状态 Store（Zustand 或 Context） | `frontend/src/store/` | 2h |
| F1.8 | 响应式布局适配（侧边栏折叠） | CSS | 2h |

**交付物**：三模式可切换、数据源可多选、`Ctrl+K` 可用，旧的 4 个页面保留但不再主导航

### 后端任务

| # | 任务 | 文件 | 估时 |
|---|---|---|---|
| B1.1 | `/api/config/sources` 返回数据源类型标签（mysql/pg/http） | `src/deepferry/web/routes/config.py` | 1h |

**交付物**：API 返回数据源类型字段

### 验收标准
- [ ] 侧边栏点击切换模式，URL 不跳转（纯状态切换）
- [ ] `Ctrl+K` 弹出命令面板，ESC 关闭，搜索过滤
- [ ] `Ctrl+1/2/3` 切换模式
- [ ] 数据源多选 toggle，状态栏实时反映
- [ ] 浏览器窗口 < 1024px 时侧边栏自动折叠

---

## P2: 探索模式（3 周）

### 目标
用户输入自然语言 → LLM 生成 SQL → 执行 → 展示结果 + 图表。零 SQL 可操作。

### 前置
- P1 完成
- LLM 后端可访问（DeepSeek API 或兼容接口）

### 前端任务

| # | 任务 | 文件 | 估时 |
|---|---|---|---|
| F2.1 | 实现 `ExplorePage.tsx`：NL 输入框 + 推荐问题 + 流式反馈 | `frontend/src/pages/ExplorePage.tsx` | 6h |
| F2.2 | 实现 NL 输入组件：多行自适应、Enter 发送、Shift+Enter 换行 | `frontend/src/components/NLInput.tsx` | 2h |
| F2.3 | 实现推荐问题组件：基于所选数据源动态生成 | `frontend/src/components/Suggestions.tsx` | 2h |
| F2.4 | 实现流式进度指示器：识别源 → 匹配表 → 生成 SQL → 执行 | `frontend/src/components/ExploreProgress.tsx` | 3h |
| F2.5 | 实现结果展示组件：表格 + SQL 折叠面板 + 操作栏 | `frontend/src/components/ExploreResult.tsx` | 4h |
| F2.6 | 多轮对话 UI：追问输入框 + 对话历史 | `frontend/src/pages/ExplorePage.tsx` | 3h |
| F2.7 | 自动图表生成（数值列 → 趋势图，分类列 → 分布图） | `frontend/src/components/AutoChart.tsx` | 6h |
| F2.8 | 错误处理 UI：NL 无法理解 → 建议 + 降级入口 | `frontend/src/components/ExploreError.tsx` | 2h |
| F2.9 | API client 扩展：`/api/explore` + `/api/explore/suggestions` | `frontend/src/api/client.ts` | 1h |

**交付物**：完整的探索模式，输入 NL → 看结果 → 追问

### 后端任务

| # | 任务 | 文件 | 估时 |
|---|---|---|---|
| B2.1 | 实现 `POST /api/explore`：接收 NL 文本 → 构造 prompt → 调 LLM → 生成 SQL → 安全扫描 → 执行查询 | `src/deepferry/web/routes/explore.py` | 8h |
| B2.2 | 实现 `GET /api/explore/suggestions`：基于数据源 schema 生成推荐问题 | `src/deepferry/web/routes/explore.py` | 3h |
| B2.3 | LLM Client 封装：支持 OpenAI/DeepSeek 兼容 API，`config.toml` `[llm]` section | `src/deepferry/core/llm.py` | 4h |
| B2.4 | Prompt 模板：schema context 注入 + few-shot 示例 + 安全约束 | `src/deepferry/core/prompts/` | 3h |
| B2.5 | 流式响应支持（SSE）：探索进度逐步推送 | `src/deepferry/web/routes/explore.py` | 3h |
| B2.6 | 安全扫描增强：LLM 生成的 SQL 二次校验 | `src/deepferry/core/safeguards.py` | 2h |

**交付物**：`/api/explore` 可用，LLM 配置灵活

### 验收标准
- [ ] 输入 "上个季度销售额最高的三个产品" → 表格展示 3 行
- [ ] 输入 "查看不存在的表" → 友好错误 + 数据源浏览建议
- [ ] 追问 "这些产品的退货率" → 保持上下文
- [ ] `config.toml` 切换 LLM 后端（DeepSeek ↔ OpenAI）无需改代码
- [ ] LLM 不可用时，探索模式回退为 Schema 浏览

---

## P3: 数据集引擎（3.5 周）

### 目标
格式无关的多格式数据集存储，版本快照，diff 对比。后端先行。

### 前置
- P1 完成（前端仅需结果页入口）
- 可与 P1/P2 并行

### 后端任务（Week 1-2.5：引擎）

| # | 任务 | 文件 | 估时 |
|---|---|---|---|
| B3.1 | DuckDB 引擎升级：`_build_insert_values` 替换为 `read_json/read_parquet` | `src/deepferry/engine/duckdb.py` | 6h |
| B3.2 | 实现 `DatasetManager`：数据集 CRUD（创建、列表、详情、删除） | `src/deepferry/core/dataset.py` | 6h |
| B3.3 | 实现数据集存储层：多格式写入（JSON/Parquet/Arrow/CSV） | `src/deepferry/core/dataset_storage.py` | 8h |
| B3.4 | 实现 `SnapshotManager`：从查询结果创建快照 | `src/deepferry/core/snapshot.py` | 4h |
| B3.5 | 实现版本链：版本号自动递增 + `metadata.yaml` 管理 | `src/deepferry/core/versioning.py` | 3h |
| B3.6 | 实现 Diff 引擎：DuckDB `EXCEPT/INTERSECT` 计算行级差异 | `src/deepferry/core/diff.py` | 4h |
| B3.7 | 实现增量刷新：`WHERE updated_at > last_snapshot_ts` | `src/deepferry/core/refresh.py` | 4h |
| B3.8 | API 路由：`/api/datasets/*` (CRUD + snapshot + diff + refresh + export) | `src/deepferry/web/routes/datasets.py` | 6h |
| B3.9 | 多格式导出：Parquet / CSV / JSON / Arrow | `src/deepferry/core/export.py` | 3h |

**交付物**：数据集引擎完整可用，API 就绪

### 前端任务（Week 2.5-3.5：仅 UI 层）

| # | 任务 | 文件 | 估时 |
|---|---|---|---|
| F3.1 | 探索结果页新增 "💾 保存为数据集" 按钮 | `frontend/src/components/ExploreResult.tsx` | 1h |
| F3.2 | 创建数据集弹窗：名称、描述、刷新策略、格式选择 | `frontend/src/components/CreateDatasetModal.tsx` | 3h |
| F3.3 | 侧边栏新增 "数据集" 区域 | `frontend/src/components/Sidebar.tsx` | 2h |
| F3.4 | 实现 `DatasetDetailPage.tsx`：版本时间线 + diff 视图 | `frontend/src/pages/DatasetDetailPage.tsx` | 6h |
| F3.5 | 版本差异可视化：新增行(绿)、删除行(红)、修改行(黄) | `frontend/src/components/DiffView.tsx` | 4h |
| F3.6 | 导出按钮：多格式选择下拉 | `frontend/src/components/ExportMenu.tsx` | 2h |
| F3.7 | API client 扩展：`/api/datasets/*` | `frontend/src/api/client.ts` | 1h |

**交付物**：数据集创建、浏览、对比在 UI 中可用

### 验收标准
- [ ] `POST /api/explore` 查询 → `POST /api/datasets` 保存 → 磁盘出现 `v1/customers.parquet`
- [ ] 手动刷新 → 创建 v2 → `GET /api/datasets/:id/diff?v1&v2` 返回差异
- [ ] `GET /api/datasets/:id/export?format=parquet` 下载 Parquet 文件
- [ ] 删 `_duckdb_cache/` 后查询仍正常（缓存可重建）
- [ ] Pandas 直接读 `v1/customers.parquet` 无依赖 deepferry

---

## P4: 监控模式（3 周）

### 目标
实时 Agent Activity Feed，执行链路详情，对话上下文，错误诊断。

### 前置
- P1 完成
- MCP Server 的 trace sink 已启用

### 前端任务

| # | 任务 | 文件 | 估时 |
|---|---|---|---|
| F4.1 | 实现 `MonitorPage.tsx`：统计卡片 + Activity Feed | `frontend/src/pages/MonitorPage.tsx` | 6h |
| F4.2 | 实时 Feed 组件：WebSocket 连接 + 虚拟滚动 | `frontend/src/components/ActivityFeed.tsx` | 5h |
| F4.3 | 统计卡片组件：活跃 Agent 数、查询量、延迟、错误率 | `frontend/src/components/StatsCards.tsx` | 3h |
| F4.4 | 执行链路详情页：时间线瀑布图 + 跨源编排可视化 | `frontend/src/pages/ExecutionDetailPage.tsx` | 6h |
| F4.5 | Agent 对话上下文展示：User → Agent → SQL → Result → Agent 响应 | `frontend/src/components/AgentContext.tsx` | 4h |
| F4.6 | 错误诊断组件：失败查询自动分析原因 | `frontend/src/components/ErrorDiagnosis.tsx` | 3h |
| F4.7 | Feed 筛选器：按 Agent、状态、数据源、时间范围 | `frontend/src/components/FeedFilters.tsx` | 2h |
| F4.8 | WebSocket client 封装 | `frontend/src/api/ws.ts` | 2h |

**交付物**：实时 Agent 监控面板

### 后端任务

| # | 任务 | 文件 | 估时 |
|---|---|---|---|
| B4.1 | 实现 WebSocket `/ws/agents`：推送实时 Agent 查询事件 | `src/deepferry/web/ws.py` | 6h |
| B4.2 | 实现 `GET /api/agents/sessions`：历史 Session 列表 + 筛选 | `src/deepferry/web/routes/agents.py` | 3h |
| B4.3 | 实现 `GET /api/agents/sessions/:id`：Session 详情（链路 + 对话） | `src/deepferry/web/routes/agents.py` | 3h |
| B4.4 | Trace 数据增强：关联 Agent 对话上下文（从 MCP 请求中提取） | `src/deepferry/core/trace.py` | 4h |
| B4.5 | 错误自动诊断：失败查询 → 分析原因 → 建议 SQL | `src/deepferry/core/diagnostics.py` | 4h |
| B4.6 | 统计聚合 API：`/api/agents/stats` | `src/deepferry/web/routes/agents.py` | 2h |

**交付物**：WebSocket 实时推送 + 历史查询 API

### 验收标准
- [ ] Agent 执行查询 → 前端实时显示（< 500ms 延迟）
- [ ] 点击 Feed 条目 → 展开完整链路时间线
- [ ] 失败查询 → 自动显示错误原因和建议
- [ ] 筛选 "仅显示错误" → Feed 只显示失败条目
- [ ] 无 Agent 连接时 → 显示引导提示

---

## P5: 跨源编排 + 查询增强（3 周）

### 目标
多数据源并行查询可视化、DuckDB 编排流程图、Schema 面板重构、SQL 智能补全。

### 前置
- P1 完成
- P3（数据集引擎）完成

### 前端任务

| # | 任务 | 文件 | 估时 |
|---|---|---|---|
| F5.1 | 重构 `QueryPage.tsx`：SQL 编辑器 + Schema 面板分栏布局 | `frontend/src/pages/QueryPage.tsx` | 4h |
| F5.2 | 实现 `SchemaPanel.tsx`：多源树形展示 + 关联提示 + 点击插入 | `frontend/src/components/SchemaPanel.tsx` | 6h |
| F5.3 | SQL 编辑器增强：语法高亮 + 自动补全（表名、列名） | `frontend/src/components/SqlEditor.tsx` | 5h |
| F5.4 | 跨源模式编辑器：DuckDB ATTACH 语法高亮 + 模板 | `frontend/src/components/SqlEditor.tsx` | 3h |
| F5.5 | 编排流程图组件：MySQL → HTTP → DuckDB 节点 + 箭头 | `frontend/src/components/OrchestraFlow.tsx` | 4h |
| F5.6 | 结果固定 + 对比视图：并排表格 + 差异高亮 | `frontend/src/components/ResultCompare.tsx` | 5h |
| F5.7 | AI 优化面板：SQL 分析（性能/安全/可读性） | `frontend/src/components/AiOptimize.tsx` | 3h |
| F5.8 | 收藏 + 参数化查询 UI | `frontend/src/components/SavedQueries.tsx` | 3h |

**交付物**：增强查询编辑器 + 跨源可视化

### 后端任务

| # | 任务 | 文件 | 估时 |
|---|---|---|---|
| B5.1 | 跨源查询元数据 API：返回编排链路信息（每源耗时、行数） | `src/deepferry/web/routes/query.py` | 3h |
| B5.2 | SQL 分析 API：`POST /api/query/analyze` | `src/deepferry/web/routes/query.py` | 3h |
| B5.3 | Schema 增强 API：跨源关联检测（自动发现可 JOIN 字段） | `src/deepferry/web/routes/schema.py` | 4h |
| B5.4 | 收藏查询 API：`/api/saved-queries/*` | `src/deepferry/web/routes/saved.py` | 3h |
| B5.5 | 参数化查询 API：模板变量解析 + 执行 | `src/deepferry/web/routes/query.py` | 2h |

**交付物**：跨源查询链路可见、Schema 智能关联、收藏系统

### 验收标准
- [ ] 选中 mysql-main + finance-api → Schema 面板显示关联提示 `customers.tax_no ⇄ invoices.buyer_tax_no`
- [ ] 跨源查询执行 → 编排流程图为 MySQL(156ms) → HTTP(250ms) → DuckDB(62ms)
- [ ] `Ctrl+Space` 在 SQL 编辑器中弹出表名列名补全
- [ ] 收藏查询 → 侧边栏出现 → 点击恢复 SQL + 数据源
- [ ] `{{status}}` 参数化查询 → 执行时弹出参数输入框

---

## P6: 打磨上线（1 周）

### 目标
响应式适配、主题、性能优化、快捷键全覆盖。

### 前端任务

| # | 任务 | 估时 |
|---|---|---|
| F6.1 | 响应式适配（1024px 小屏侧边栏折叠） | 3h |
| F6.2 | 深色/浅色主题切换 | 2h |
| F6.3 | 键盘快捷键全覆盖（ESC 关闭面板、Ctrl+Enter 执行等） | 2h |
| F6.4 | 虚拟滚动优化长列表（AG Grid 或 react-window） | 3h |
| F6.5 | 懒加载 + Code Splitting（动态 import 模式页面） | 2h |
| F6.6 | Empty State 统一组件 + 引导文案 | 2h |
| F6.7 | Loading Skeleton 统一组件 | 1h |
| F6.8 | 构建配置优化（Tree shaking, gzip） | 1h |

### 验收标准
- [ ] Lighthouse Performance > 90
- [ ] 1024px 宽度可用（侧边栏折叠为图标）
- [ ] 所有弹窗支持 ESC 关闭
- [ ] 3 秒内完成首屏加载（production build）

---

## 依赖关系图

```
P1 (基础设施)
 ├──→ P2 (探索模式)
 ├──→ P4 (监控模式)
 └──→ P5 (跨源编排) ←── P3 (数据集引擎)
       └── 依赖 P3 的格式无关存储
P3 (数据集引擎) → 可与 P1 并行启动
P6 (打磨) → 依赖 P2-P5 全部完成
```

## 团队分工建议

| 角色 | Phase |
|---|---|
| **前端 A** | P1 → P2（探索模式 UI）→ P5（查询增强） |
| **前端 B** | P1 → P4（监控模式 UI）→ P3（数据集 UI） |
| **后端 A** | P2（LLM + explore API）→ P5（跨源编排 API） |
| **后端 B** | P3（数据集引擎）→ P4（WebSocket + Agent API） |
| **全栈/DevOps** | 环境、CI、性能 |

## 风险与缓解

| 风险 | 缓解 |
|---|---|
| LLM API 不稳定 | `/api/explore` 降级返回 "请使用查询模式手写 SQL" |
| DuckDB 大文件性能 | Parquet 列式分区 + 只读需要的列 |
| WebSocket 连接数 | 单连接复用 + 心跳保活 |
| 跨源 Schema 检测误报 | 人工确认 + 用户反馈机制 |
