# 恢复、取消和数据库升级

## 恢复语义

1. API 事务内创建 Run、WorkItem 和 `run.created` 事件；Worker 通过租约领取。
2. Worker 以 `run_id`/`thread_id` 调用 LangGraph，并在执行期间按租约的三分之一心跳续租。进程崩溃后心跳停止，过期租约可以被新 Worker 领取。
3. 每次认领生成新的 fencing token。续租、开始执行和最终提交都必须带上 `worker_id + lease_token`，并且租约尚未过期。旧 Worker 失去 token 后不能写终态，也不能把工作项标成完成。结果和 `work_items.status=DONE` 在同一个事务里提交。
4. checkpoint 只保证图状态恢复，不保证网络请求或报告写入 exactly-once。工具操作以 `run_id + task_id + tool_name + arguments_hash` 记在 `operations` 表。副作用超时或中断后为 UNKNOWN，后续调用直接返回该记录，不再执行。认领、执行和丢租都有上限，超过后工作项进入 `DEAD`，Run 进入 `FAILED`。

过期工作项不要手改数据库。先停掉仍在执行的 worker，再运行 `uv run enterprise-agent recover-expired`。细节见 [lease-fencing.md](lease-fencing.md)。

## 取消

`POST /api/v1/runs/{run_id}/cancel` 只持久化取消请求并发事件。Worker 在执行节点和工具 Gateway 边界检查，子 Agent 共享取消上下文。正在阻塞的第三方网络调用由其 timeout 收敛；第一版不强制杀死任意线程。

## 输入等待

解析发现必要资料缺失时，Run 进入 `WAITING_INPUT`，缺失项和补充输入写入 `state_json`，不执行 Worker。客户端使用 `/input` 追加补充信息，再调用 `/resume` 重新投递；Worker 始终使用原始请求和全部补充输入重新解析，避免把后续补充内容发送给已经结束的图状态。

## 升级

```bash
# 修改模型后先在开发库生成/审阅迁移
uv run alembic revision --autogenerate -m "describe change"
uv run alembic upgrade head

# Docker/生产环境
docker compose -f deploy/compose.yaml run --rm api alembic upgrade head
```

`create_all` 只服务测试和首次本地启动，不替代生产迁移。应用数据库迁移与 LangGraph checkpoint 表初始化是两个动作；`PostgresSaver.setup()` 初始化 checkpoint 表，需在数据库可连接后执行。

