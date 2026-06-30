# Console Redesign — Frontend Tasks

> **Change ID**: `console-redesign` | **Role**: Frontend Agent
> **Specs**: `console-shell`, `explore-mode`, `agent-monitor`, `cross-source-ui`, `query-enhancement`, `dataset-ui`

---

## Tech Context (Read First)

### Stack

```
React 18 + TypeScript 5.6 + Vite 5
AG Grid Community 36     ← 表格组件（已在项目中使用）
Zustand                 ← 状态管理（新增依赖，替代 prop drilling）
Recharts                ← 图表库（新增依赖，探索模式自动图表）
react-window            ← 虚拟滚动（新增依赖，长列表性能）
```

### Code Patterns to Follow

| Pattern | Reference File |
|---|---|
| Page layout + routing | `frontend/src/App.tsx` |
| API client pattern | `frontend/src/api/client.ts` — use `request<T>(path, init)` |
| Component style | `frontend/src/components/Sidebar.tsx` — CSS modules + dark theme CSS variables |
| Error display | `frontend/src/components/ErrorBanner.tsx` |
| Empty states | `frontend/src/components/EmptyState.tsx` |
| Loading skeletons | Use `df-skeleton` + `df-skeleton__line` CSS classes |

### Global CSS Variables (Already Defined)

```css
--bg, --bg2, --bg3       /* backgrounds */
--border                  /* borders */
--text, --text2           /* text colors */
--accent, --accent2       /* primary blue */
--green, --red, --yellow, --purple, --teal  /* status / type colors */
--radius: 6px
--font: system sans-serif
--mono: JetBrains Mono
```

### Backend API Contract

The backend implements these endpoints. Frontend MUST design against these contracts, not assume implementation details.

| Frontend Feature | Calls | Request Shape | Response Shape |
|---|---|---|---|
| Data source list | `GET /api/config/sources` | — | `SourceSummary[]` (含 `type: "mysql"\|"postgresql"\|"http"`) |
| NL explore | `POST /api/explore` (SSE) | `{question, source_ids, conversation_history?}` | SSE stream: progress events → final `StructuredResult` |
| Suggestions | `GET /api/explore/suggestions?source_ids=a,b` | — | `{suggestions: string[]}` |
| Datasets CRUD | `GET/POST/DELETE /api/datasets[/:id]` | — / create spec | `Dataset[]` / `Dataset` |
| Snapshot | `POST /api/datasets/:id/snapshot` | — | `{version, row_counts, ...}` |
| Diff | `GET /api/datasets/:id/diff?v1&v2` | — | `{additions, deletions, modifications}` |
| Export | `GET /api/datasets/:id/export?format=parquet&version=v3` | — | file download |
| Agent feed | `WebSocket /ws/agents` | — | push events: `{type, agent, statement, status, ...}` |
| Agent stats | `GET /api/agents/stats` | — | `{active_agents, today_queries, avg_latency, error_rate}` |
| Agent sessions | `GET /api/agents/sessions[/:id]` | ?status, ?source, ?limit | `Session[]` / `Session` |
| Saved queries | `GET/POST/PUT/DELETE /api/saved-queries[/:id]` | — / query spec | `SavedQuery[]` / `SavedQuery` |
| SQL analyze | `POST /api/query/analyze` | `{statement, source_ids}` | `{performance, safety, readability}` |

**If backend is not ready**: Mock the API responses. Use `frontend/src/api/` modules with a mock adapter. The contract is the spec — frontend does not wait for backend.

### State Management

Use **Zustand** (new dependency via `npm install zustand`):

```typescript
// frontend/src/store/shell.ts
interface ShellStore {
  activeMode: 'explore' | 'monitor' | 'query';
  selectedSources: string[];
  sidebarCollapsed: boolean;
  commandPaletteOpen: boolean;

  switchMode: (mode: string) => void;
  toggleSource: (id: string) => void;
  toggleSidebar: () => void;
  toggleCommandPalette: () => void;
}
```

---

## P1: Console Shell (M3, Week 1-2)

### 1.1 Sidebar

- [ ] 1.1.1 重构 `frontend/src/App.tsx`：删除旧 4-tab 路由，改为三模式状态切换
  - 模式切换不触发 URL 变化（纯 state）
  - 保留旧页面路由在 `<Route path="/legacy/*">` 下（过渡期兼容）
  - 验证: 点击侧边栏项切换时不触发 URL 变化
- [ ] 1.1.2 实现 `frontend/src/components/Sidebar.tsx`：
  - 三个模式项（Explore / Monitor / Query），Monitor 带 agent-count 徽标
  - 数据源列表，每个带类型色点（黄=MySQL, 蓝=PG, 青=HTTP）和 toggle 多选
  - 底部"选中多个数据源 → 跨源查询"提示
  - 折叠态（< 1024px）：仅图标
  - 参考: 现有 `Sidebar.tsx` 的 CSS 模式
  - 验证: 多选 mysql-main + finance-api → sidebar state 含两个 id
- [ ] 1.1.3 实现 `frontend/src/components/StatusBar.tsx`：
  - 左: 数据源连接点（绿/黄/红），从 `GET /api/config/sources` 获取状态
  - 右: Agent 在线数 + 最后活动时间 + 版本号
  - 固定 26px 底部
  - 验证: mysql-main 连接正常时显示绿点

### 1.2 Command Palette

- [ ] 1.2.1 实现 `frontend/src/components/CommandPalette.tsx`：
  - `Ctrl+K` 打开浮层，ESC 关闭，点击外部关闭
  - 搜索输入框 + 分组结果（模式/收藏/最近）
  - 每个结果显示快捷键
  - 回车或点击执行操作
  - 参考: 原型 `openspec/specs/console-prototype.html` 的命令面板行为
  - 验证: `Ctrl+K` → 输入 "monitor" → 回车 → 切换到监控模式
- [ ] 1.2.2 在 `App.tsx` 注册全局快捷键：`Ctrl+1/2/3` 切换模式，`Ctrl+K` 命令面板
  - 验证: 在所有模式下快捷键一致

### 1.3 Store & State

- [ ] 1.3.1 安装 Zustand：`npm install zustand`
- [ ] 1.3.2 创建 `frontend/src/store/shell.ts`：
  - 全局 store：`activeMode`, `selectedSources`, `sidebarCollapsed`, `commandPaletteOpen`
  - `toggleSource(id)` — 至少保留一个选中
  - `switchMode(mode)` — 懒加载目标页面
  - 验证: 所有组件通过 store 读写状态，无 prop drilling

### 1.4 Lazy Loading

- [ ] 1.4.1 用 `React.lazy()` + `Suspense` 懒加载三个模式页面
  - 验证: Network tab 显示按需加载 JS chunk

### 1.5 API Client

- [ ] 1.5.1 扩展 `frontend/src/api/client.ts`：
  - 添加 `api.listSources()` 返回类型含 `type` 字段
  - 验证: 调用返回正确类型

---

### 🛑 STOP GATE — P1 完成验收

**必须全部通过才能进入 P2：**

- [ ] 三模式切换不触发 URL 变化或页面刷新
- [ ] `Ctrl+K`  → 输入 "explore" → 回车 → 切换到探索页面
- [ ] `Ctrl+1/2/3` 在所有页面下正确切换模式
- [ ] 数据源多选 → `selectedSources` 正确更新 → 状态栏实时反映
- [ ] 侧边栏 ≤1024px 自动折叠
- [ ] `npm run typecheck` 通过
- [ ] `npm run build` 成功（生产构建）
- [ ] 旧 4 个页面通过 `/legacy/*` 路由仍可访问

**验收方法**: 在浏览器中手动操作以上所有场景。完成后才能开始 P2。

> ⚠️ 如果任一验收项失败，修复后重新验收。不要带 bug 进入下一阶段。

---

## P2: Explore Mode UI (M3, Week 3-5)

### 2.1 Page Shell

- [ ] 2.1.1 创建 `frontend/src/pages/ExplorePage.tsx`：
  - 空态：居中引导语 + "用自然语言问你的数据"
  - 推荐问题列表（从 `GET /api/explore/suggestions` 获取）
  - 如果 API 不可用，显示硬编码默认问题
  - 对话态：chat bubble 列表（用户问题 + AI 响应）
  - 验证: 页面首次加载显示空态引导

### 2.2 NL Input

- [ ] 2.2.1 实现 `frontend/src/components/NLInput.tsx`：
  - 多行自适应 textarea，Enter 发送，Shift+Enter 换行
  - placeholder 根据单源/多源模式动态变化
  - 发送时调用 `POST /api/explore`（SSE 流式）
  - 验证: 输入问题 → Enter → 输入框清空 → 问题出现在对话中

### 2.3 Progress Indicator

- [ ] 2.3.1 实现 `frontend/src/components/ExploreProgress.tsx`：
  - 读取 SSE 事件流，逐行展示进度步骤
  - 已完成步骤显示 ✓（绿色），进行中显示 spinner
  - 验证: 流式事件到达时 UI 逐行更新

### 2.4 Result Display

- [ ] 2.4.1 实现 `frontend/src/components/ExploreResult.tsx`：
  - NL 摘要行
  - AG Grid 表格展示结果（复用已有 ResultGrid 或直接使用 AG Grid）
  - 折叠的 SQL 面板（点击展开/收起）
  - 操作栏：📋 复制、📥 CSV、💾 保存为数据集（按钮先放，P3 实现逻辑）
  - 验证: 结果返回后表格正确渲染
- [ ] 2.4.2 安装 Recharts：`npm install recharts`
- [ ] 2.4.3 实现 `frontend/src/components/AutoChart.tsx`：
  - 数值列 → 折线图/柱状图
  - 日期列 → 时间序列
  - 纯文本结果跳过图表
  - 验证: 数字列结果自动出图，纯文本不崩溃

### 2.5 Follow-up Questions

- [ ] 2.5.1 结果下方添加追问输入框
  - 追问时附带 `conversation_history`（上一轮 question + result summary）
  - 验证: 追问 "这些产品的退货率" → API 请求包含 history

### 2.6 Error Handling

- [ ] 2.6.1 实现 `frontend/src/components/ExploreError.tsx`：
  - LLM 无法理解 → 显示友好错误 + "试试别的问法"建议
  - LLM 不可用 → 降级提示 + "去查询模式" 链接
  - 验证: API 返回 503 → 显示降级 UI，不白屏

### 2.7 API

- [ ] 2.7.1 创建 `frontend/src/api/explore.ts`：
  - `exploreApi.ask(question, sourceIds, history?): AsyncGenerator<ProgressEvent>`
  - `exploreApi.getSuggestions(sourceIds): Promise<string[]>`
  - 如果后端不可用，返回 mock 数据
  - 验证: 函数签名正确，TS 类型推导无误

---

### 🛑 STOP GATE — P2 完成验收

**必须全部通过才能进入 P4/P5：**

- [ ] 输入 "上个季度销售额最高的三个产品" → 表格展示 3 行数据 + 自动图表
- [ ] 输入 "查看不存在的表" → 友好错误提示（非白屏，非 traceback）
- [ ] 追问 "这些产品的退货率" → 上下文正确传递
- [ ] 选中两数据源 → 输入跨源问题 → 结果含多源标注
- [ ] 结果 SQL 面板可折叠，展开后可见完整 SQL
- [ ] `npm run typecheck` 通过
- [ ] `npm run build` 通过

> ⚠️ 验收时不依赖真实 LLM——使用 mock 返回固定 SQL。后端 API 可在 mock 模式下开发。

---

## P4: Agent Monitor UI (M3, Week 6-8)

### 4.1 Page Shell

- [ ] 4.1.1 创建 `frontend/src/pages/MonitorPage.tsx`：
  - 顶部统计卡片 + Activity Feed
  - Feed 用 react-window 虚拟滚动
  - 验证: 100+ 项无性能问题
- [ ] 4.1.2 安装 react-window：`npm install react-window @types/react-window`

### 4.2 Statistics Cards

- [ ] 4.2.1 实现 `frontend/src/components/StatsCards.tsx`：
  - 4 卡片：活跃 Agent、今日查询、平均延迟、错误率
  - `GET /api/agents/stats` → 每 10s 刷新
  - 如果 API 不可用，显示 "— —" 占位
  - 验证: 数字变化时卡片更新

### 4.3 Activity Feed

- [ ] 4.3.1 实现 `frontend/src/components/ActivityFeed.tsx`：
  - WebSocket 连接 `/ws/agents`
  - 断线自动重连（指数退避：1s, 2s, 4s, max 30s）
  - 每项：agent 名 + 状态色点 + SQL 预览 + 耗时 + 源列表 + 标签
  - 验证: 断网 → 显示 "连接断开" → 恢复后自动重连
- [ ] 4.3.2 实现 `frontend/src/components/FeedFilters.tsx`：
  - 按 Agent、状态、数据源筛选
  - 验证: 选 "仅错误" → Feed 只显示失败条目

### 4.4 Execution Detail

- [ ] 4.4.1 实现侧滑面板：
  - Agent 对话上下文展示
  - 执行时间线瀑布图
  - SQL 完整展示 + "在查询模式打开" 按钮
  - 验证: 点击 Feed 项 → 面板滑出 → 内容完整

### 4.5 Empty State

- [ ] 4.5.1 无 Agent 连接时显示引导界面，含 CLI 命令 + JSON 配置示例

### 4.6 API

- [ ] 4.6.1 创建 `frontend/src/api/ws.ts`：WebSocket 封装（connect/reconnect/heartbeat）
- [ ] 4.6.2 创建 `frontend/src/api/agents.ts`：REST 端点封装

---

### 🛑 STOP GATE — P4 完成验收

- [ ] Agent 执行查询 → Feed 实时显示（< 1s 延迟）
- [ ] 点击 "错误" 筛选 → 仅显示失败条目
- [ ] 展开执行详情 → 完整时间线可见
- [ ] 断开 WebSocket → 重连提示 → 自动恢复
- [ ] 无 Agent 连接 → 引导 UI 显示

---

## P5: Query Enhancement UI (M3, Week 8-10)

### 5.1 Layout

- [ ] 5.1.1 重构 `frontend/src/pages/QueryPage.tsx` 为分栏：左 SQL 编辑器 + 右 Schema 面板
  - 验证: 拖拽分割线可调整比例

### 5.2 Schema Panel

- [ ] 5.2.1 实现 `frontend/src/components/SchemaPanel.tsx`：
  - 数据源 → 表 → 列（类型）树形展示
  - 搜索栏过滤
  - 点击列名 → `table.column` 插入 SQL 光标位
  - 多源时显示 `GET /api/schema/relationships` 的跨源关联提示
  - 验证: 点击 `customers.email` → 插入 `customers.email`

### 5.3 SQL Editor

- [ ] 5.3.1 增强 `frontend/src/components/QueryEditor.tsx`：
  - 语法高亮
  - 行号
  - 自动补全：`FROM` → 表列表，`table.` → 列列表
  - 格式化按钮（调用简单 SQL formatter 或 LLM）
  - 验证: `Ctrl+Space` → 根据上下文弹出补全列表

### 5.4 Saved Queries

- [ ] 5.4.1 `Ctrl+S` → 保存弹窗 → `POST /api/saved-queries`
- [ ] 5.4.2 侧边栏收藏区 → 点击恢复 SQL + 数据源
- [ ] 5.4.3 参数化：`WHERE status = {{status}}` → 弹出参数输入框
  - 验证: 输入 `vip` → SQL 转为 `WHERE status = 'vip'`

### 5.5 Result Pinning

- [ ] 5.5.1 结果固定为 tab → 最多 5 个
- [ ] 5.5.2 选中 2 个 → "对比" → 并排差异视图

---

### 🛑 STOP GATE — P5 完成验收

- [ ] Schema 面板点击插入列名到编辑器
- [ ] SQL 自动补全在 `FROM` / `table.` 后触发
- [ ] 保存/加载查询 → SQL + 数据源完整恢复
- [ ] 参数化查询 `{{status}}` → 弹出输入框 → 正确替换
- [ ] 固定两个结果 → 对比视图显示差异

---

## P3: Dataset UI (M4, Week 9-10)

### 3.1 Create Dataset

- [ ] 3.1.1 实现 `frontend/src/components/CreateDatasetModal.tsx`：
  - 名称、描述、刷新策略、格式选择
  - 调用 `POST /api/datasets`
  - 验证: 创建后侧边栏出现新数据集

### 3.2 Sidebar

- [ ] 3.2.1 侧边栏 "数据集" 区域：名称 + 最新版本 + `[↻]` `[⟷]` 按钮

### 3.3 Dataset Detail Page

- [ ] 3.3.1 创建 `frontend/src/pages/DatasetDetailPage.tsx`：
  - 版本时间线 + 导出下拉
  - 验证: v1, v2, v3 完整展示

### 3.4 Diff View

- [ ] 3.4.1 实现 `frontend/src/components/DiffView.tsx`：
  - 新增/删除/修改行着色
  - 版本选择下拉
  - 验证: v2 vs v3 差异正确

---

### 🛑 STOP GATE — P3 完成验收

- [ ] 探索模式结果 → "保存为数据集" → 创建 → 侧边栏出现
- [ ] 刷新数据集 → v{n+1} 出现
- [ ] Diff 视图颜色正确
- [ ] 导出 Parquet → 文件下载

---

## P6: Polish (M3, Week 11)

- [ ] 6.1 响应式：≤1024px 侧边栏折叠为图标
- [ ] 6.2 深色/浅色主题（CSS 变量交换）
- [ ] 6.3 统一 Empty/Loading 组件
- [ ] 6.4 Lighthouse > 90

---

## Kickoff Prompt (Copy-paste to start a frontend agent)

```
You are working on the deepferry console redesign frontend.

Tech stack: React 18 + TypeScript 5.6 + Vite 5 + AG Grid 36 + Zustand + Recharts.
Read openspec/changes/console-redesign/tasks-frontend.md for the full task list.
Read the P1 tasks first. Start from 1.1.1.

Key rules:
- Match existing patterns in frontend/src/components/ (Sidebar, ErrorBanner, EmptyState).
- Use the CSS variables: --bg, --text, --accent, --green, --red, --yellow, --purple, --teal, --radius.
- API contracts are in the tasks-frontend.md header. If backend API is unavailable, mock responses in frontend/src/api/.
- STOP at every "🛑 STOP GATE". Do NOT proceed to the next P-phase until all gate checks pass.
- Run `npm run typecheck` after every file change. Run `npm run build` at each gate.
- Keep existing legacy routes under /legacy/* for backward compatibility.

Current milestone: P1 (Console Shell). Start with task 1.1.1.
```
