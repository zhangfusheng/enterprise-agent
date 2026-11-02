# Trace Console

EnterpriseAgent 的独立观测控制台，目录为 `console/trace-ui`。`apps/` 继续承载 API、Worker 和未来的 Agent 交互入口。

## 启动

需要 Node.js 22.12+。在项目根目录启动现有 API（开发模式）：

```bash
EA_ENVIRONMENT=development uv run uvicorn apps.api.main:app --host 127.0.0.1 --port 8000
```

在另一个终端：

```bash
cd console/trace-ui
npm ci
npm run dev
```

浏览器打开 <http://127.0.0.1:5173>。读取历史 Trace 不需要启动 Worker；新任务仍由项目原有 Worker 执行。

默认通过 Vite 的 `/api` 同源代理访问 `http://127.0.0.1:8000`，无需开放后端 CORS。自定义地址：

```bash
TRACE_API_TARGET=http://127.0.0.1:9000 npm run dev
```

默认开发租户为 `local-tenant`，右上角可以切换。可通过 `VITE_TRACE_TENANT` 配置初始值；浏览器请求携带项目原有的 `X-Tenant-Id` 开发身份头。租户切换后使用独立缓存，后端列表、详情、摘要及导出均按租户过滤。此开发身份机制不是生产认证；项目现有 production 身份适配器未配置时继续返回 503。

`.env.local` 仅用于公开前端配置，参考 `.env.example`。不要填写模型供应商密钥。

## 功能和数据含义

- 历史列表：按根节点开始时间倒序，30 条/页的游标分页；支持 Run ID / Trace ID / 根节点名称搜索、根节点状态、根标签、时间范围、含错误筛选。
- 每条记录代表一个根 Trace；一个业务 Run 恢复执行后可能有多个根 Trace，可在详情页切换。
- 树和 Waterfall 使用同一组行和滚动区域。支持展开/折叠、名称/ID/类型搜索、保留祖先的错误路径筛选、缩放、横向滚动。
- 节点详情：输入、输出、Metadata、错误、JSON、Token 用量（数据存在时）、ID 和 JSON 复制。
- `run_id`、`trace_id`、`span_id`、租户及列表筛选均保存在 URL；可刷新、分享或浏览器回退。JSON 导出包含该 Run 的全部根 Trace。
- 列表自动刷新为 10 秒（第一页）；执行中的 Run 详情为 2 秒。支持暂停，后台标签页不轮询。终止或等待输入的 Run 停止自动刷新，手动刷新可重新检测恢复后的任务。
- 顶部列表指标仅统计当前页，非全量仪表盘。耗时按根节点的墙钟时间计算，不对子节点求和。
- `status` 是节点状态，业务 `run_status` 独立展示。成功父节点可能有失败子节点，`has_error` 和错误计数会提示。未写入结束时间的停止运行节点显示“未闭合”。
- 部分历史 LLM / Tool 节点由消息回放补录，没有真实调用计时。用 ◆ 展示记录时间，耗时显示“未采集”；不将补录耗时用于性能结论。新补录记录增加 `extra.metadata.timing_source` 标记，历史数据按已有采集特征识别。
- 输入、输出和 Metadata 来自已脱敏、可能截断的存库快照；导出同样保留这些限制。未知 Token、成本不填充为 0。
- 时间 API 统一为 UTC，UI 显示浏览器本地时区。SQLite 读出的无时区历史时间按 UTC 解释。
- Agent 操作工作台：左侧 Runs 页面支持创建任务、搜索筛选和实时同步；Run 详情通过 SSE 展示事件时间线，并支持取消、补充输入后恢复、查看 Artifacts、Raw JSON 和跳转 Trace Explorer。操作入口位于左侧 Runs / “新建任务”，也可访问 `/?view=runs` 或 `/?view=new`。

## 构建与验证

```bash
npm run lint
npm run typecheck
npm test
npm run build
npm run preview
```

`npm run preview` 在 <http://127.0.0.1:4173> 预览构建产物，并使用同样的 API 代理。生产环境将 `dist/` 交给静态服务器，并把 `/api/` 反向代理到 FastAPI；生产身份认证仍由既有 API 边界负责。

真实 API + 浏览器端到端验证：

```bash
npx playwright install chromium
npm run test:e2e
```

E2E 自动启动 `tests/support/trace_preview.py` 的临时 SQLite API（8765）和 Vite（5175），测试结束关闭。测试样例只在这个隔离进程中生成，不读取或修改现有数据库，不调用 LLM。截图在 `test-results/`。

后端验证（项目根目录）：

```bash
EA_ENVIRONMENT=development EA_OFFLINE_MODE=true EA_REAL_TESTS=false \
  uv run python -m pytest --import-mode=importlib tests/integration/test_trace_explorer.py tests/unit/test_tracing.py tests/integration/test_api.py
```

使用 `python -m pytest --import-mode=importlib` 兼容现有测试目录中的同名模块及仓库根模块导入。

## API

| 接口 | 返回 |
| --- | --- |
| `GET /api/v1/runs` | `items`；参数 `q/status/limit`，按更新时间倒序返回当前租户的 Run |
| `GET /api/v1/models` | 从 `configs/models.yaml` 返回模型 Profile、模型卡展示名、能力和当前可用状态 |
| `POST /api/v1/runs` | 创建 Run，返回 `run_id`、`session_id`、生效的 `model_profile` 和 `PENDING` 状态；带 `session_id` 时继续已有聊天 |
| `GET /api/v1/sessions` | 当前用户最近聊天会话，按更新时间倒序 |
| `GET /api/v1/sessions/{session_id}` | 当前用户指定会话的完整消息 |
| `GET /api/v1/runs/{run_id}` | Run 状态、摘要、路由、缺失信息和预算 |
| `GET /api/v1/runs/{run_id}/events` | SSE 执行事件流，支持 `after` 游标 |
| `POST /api/v1/runs/{run_id}/cancel` | 请求取消 Run |
| `POST /api/v1/runs/{run_id}/input`、`POST /api/v1/runs/{run_id}/resume` | 补充输入并恢复等待中的 Run |
| `GET /api/v1/runs/{run_id}/artifacts` | 当前 Run 的文件列表 |
| `GET /api/v1/traces` | `items`、`next_cursor`；参数 `q/status/name/tag/has_error/start_time/end_time/limit/cursor` |
| `GET /api/v1/traces/{trace_id}/summary` | 一个根 Trace 的摘要 |
| `GET /api/v1/runs/{run_id}/trace` | 兼容原有 `runs/roots`，新增业务状态、节点状态/耗时/深度/错误及计时来源 |
| `GET /api/v1/runs/{run_id}/trace/export` | 相同可见内容的 JSON 附件 |

分页固定按 `start_time DESC, id DESC`，不提供耗时排序。标签为根节点标签的精确匹配。未指定时区的 API 时间参数按 UTC 处理。新接口不需要数据库结构迁移。

实现参考：[Vite 代理配置](https://vite.dev/config/server-options#server-proxy)、[TanStack Query](https://tanstack.com/query/latest/docs/framework/react/reference/useQuery)。

本次通过项、全量回归结果和现有检查问题见 [验证记录](VALIDATION.md)。
