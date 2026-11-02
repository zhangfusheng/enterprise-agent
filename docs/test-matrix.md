# 测试矩阵

| 层级 | 覆盖 | 命令 | 离线/真实 |
| --- | --- | --- | --- |
| unit | 路由、计划 DAG、模型结构化协议、计算、网络/文件策略、预算/委派 | `uv run pytest tests/unit` | 离线确定性 |
| contract | Pydantic 契约、模型绑定、工具返回语义 | `uv run pytest tests/contract` | 离线 |
| integration | API/Worker 同一仓库、direct/single/multi/报告/子委派 | `uv run pytest tests/integration` | SQLite + fixture |
| recovery | 租约竞争、心跳续租、fencing token、原子提交、死信 | `uv run pytest tests/recovery` | SQLite |
| security | 租户隔离、路径穿越、SSRF 私网地址 | `uv run pytest tests/security` | 离线 |
| workflows | 四个 examples 的端到端入口 | `uv run pytest tests/workflows` | SQLite + fixture |
| evals | 路由数据集和 grader | `uv run python -m evals.run_offline` | 离线 |
| real | OpenAI-compatible/Anthropic 与 DuckDuckGo | 显式 `EA_REAL_TESTS=1` 后另行运行 | 不属于无凭证 CI |

提示注入用例只证明指定输入下的权限边界；不能据此声称彻底解决提示注入。

