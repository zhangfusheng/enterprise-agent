# enterprise-agent

`enterprise-agent` 是一个可运行、可验证、可恢复、受控扩展的 Agent 应用底座第一版。它采用 Python + Pydantic + LangGraph/LangChain/Deep Agents + FastAPI，并把 API 与 Worker 分进程运行。离线模式不需要模型凭证或外部搜索服务，使用确定性假模型、假搜索和固定网页完成完整链路。

第一版是应用底座，不是“已经满足所有企业生产要求”的承诺：真实模型、真实搜索、生产认证、密钥管理、高可用数据库、审计归档和容量压测仍需结合部署环境补齐。

## 快速开始

```bash
uv sync --extra dev
cp .env.example .env
uv run alembic upgrade head
uv run uvicorn apps.api.main:app --reload
# 另一个终端
uv run python -m apps.worker.main
```

开发默认使用本地 SQLite；Docker Compose 使用 PostgreSQL。提交后 API 只写入运行记录和任务表，Worker 领取任务并执行 LangGraph 图。

```bash
curl -X POST http://127.0.0.1:8000/api/v1/runs \
  -H 'content-type: application/json' \
  -d '{"input":"请直接回答：什么是幂等性？"}'
curl http://127.0.0.1:8000/health
```

## 常用验证

```bash
uv run ruff check .
uv run mypy src apps
uv run pytest
uv run python -m evals.run_offline
docker compose -f deploy/compose.yaml up --build
```

四个可执行示例见 `examples/`；中文架构、状态所有权、恢复限制和 Deep Agents/LangGraph 版本能力见 `docs/`。真实模型和真实搜索测试需要显式设置 `EA_REAL_TESTS=1` 以及对应凭证，未设置时不会被 CI 假称通过。

## Trace 可视化控制台

独立前端位于 `console/trace-ui`，与 `apps/` 的 API、Worker、未来 Agent 交互入口分开。启动 API 后，在该目录运行 `npm ci && npm run dev`，访问 <http://127.0.0.1:5173>。

支持历史 Trace 搜索/分页、调用树和 Waterfall、节点输入/输出/错误、实时刷新及 JSON 导出。部分 LLM/Tool 节点为补录，界面会明确提示耗时未采集。启动、租户设置、构建和测试详见 [控制台 README](console/trace-ui/README.md)。
