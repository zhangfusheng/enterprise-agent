# 验证记录

本次实现使用既有 FastAPI / SQLAlchemy Trace 存储；前端位于 `console/trace-ui`。没有修改数据库结构或模型供应商配置。

## 已通过

- 后端 Trace 相关测试：15 通过（新增 Explorer 接口用例 10 个、既有 tracing 用例 5 个）。使用独立临时 SQLite 数据库。
- `uv run mypy src apps`：165 个源文件通过。
- 本次新增及修改的 Python 文件通过 Ruff 检查。
- 前端 `npm run typecheck`、`npm run lint`、`npm test`：通过；3 个逻辑测试覆盖筛选保留祖先、时间轴边界和轮询状态。
- 5 个 Playwright 场景均通过：列表到详情/折叠/错误路径/复制/导出/深链接、租户隔离、390px 窄屏、空 Trace 自动轮询到完成、分页历史跨详情及刷新保留。修复后分次验证：后 4 项通过后，单独重跑第 1 项通过。
- `npm run build`：通过，生成 `dist/`。JS 约 288 KB（gzip 90 KB），CSS 约 23 KB（gzip 6 KB）。
- 新增 npm 依赖安装后的审计：0 个已知漏洞。
- 本机现有 API 冒烟验证：`GET /api/v1/traces` 返回 200，读取到 9 条历史 Trace；现有 Run 详情返回 200，节点含新增状态、耗时和计时来源字段。只读检查现有 SQLite：38 个 Span、9 个根 Trace。

## 全量检查中的已有范围问题

修改前运行 `ruff check .` 已有 5 项告警；本次结束时仍为相同 5 项，未顺带修改对应文件：

- `src/enterprise_agent/orchestration/routing/validation.py`：导入排序。
- `tests/integration/test_worker_serve.py`：导入排序。
- `tests/real/test_llm_planner.py`：导入排序。
- `tests/unit/test_graph.py`：导入排序、无占位符 f-string。

默认 `uv run pytest` 在修改前存在 real/unit 目录同名测试模块的收集冲突。使用以下命令完成全量回归：

```bash
EA_ENVIRONMENT=development EA_OFFLINE_MODE=true EA_REAL_TESTS=false \
  uv run python -m pytest --import-mode=importlib -q --tb=short
```

结果为 **187 通过、15 跳过、7 失败**。这些失败属于原有 Agent 测试，未为使其变绿而改动现有模型配置或凭证：

| 失败用例 | 实际原因 |
| --- | --- |
| `test_api_submit_query_events_and_artifacts` | 当前角色绑定 DeepSeek，测试时缺少 `DEEPSEEK_API_KEY` |
| `test_single_research_returns_fixture_source` | 同上 |
| `test_multi_research_analysis_creates_report` | 同上 |
| `test_worker_delegates_child_and_records_result` | 同上 |
| `test_missing_input_is_checkpointed_and_resumed` | 同上 |
| `test_serve_executes_multi_agent` | 实际返回 PARTIAL，断言要求 SUCCEEDED；该既有测试自行加载 `.env` |
| `test_container_rejects_unknown_model_and_invalid_parameters` | 既有测试构造的离线配置缺少 analysis 角色绑定 |

PostgreSQL 的查询方言已处理，但本次实际数据库验证使用 SQLite，未运行 PostgreSQL 集成测试。

## 复验

```bash
# 项目根目录
EA_ENVIRONMENT=development EA_OFFLINE_MODE=true EA_REAL_TESTS=false \
  uv run python -m pytest --import-mode=importlib -q \
  tests/integration/test_trace_explorer.py tests/unit/test_tracing.py

# console/trace-ui
npm run typecheck
npm run lint
npm test
npm run test:e2e
npm run build
```
