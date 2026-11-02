# 租约丢失导致同一 Run 无限重跑

记录一次线上现象：`run_99de21ec24684c428155` 的图每次都执行成功，但 Run 一直停在 `RUNNING`，同一个工作项被反复认领。

## 问题

Worker 认领任务后拿到 30 秒租约，然后同步执行整张图。在线路由、计划和工具调用一轮就要 45–70 秒。租约在执行中途过期，结果却要等图返回后才写。

过期之后会出现五件事：

1. 没有心跳，租约不会延长。
2. 执行端发现自己不再持有租约，就跳过落库，不报错。
3. `finish_work()` 同样被跳过，工作项保持 `RUNNING`。
4. 过期的 `RUNNING` 工作项会被立刻再次认领。
5. 工具幂等只在进程内存里，重跑会再次产生搜索、报告和事件。

当时数据库里的状态是：`runs.status=RUNNING`，`updated_at` 停在首次开始执行的时间，`work_items.attempts` 已经到 27。分析任务的 `attempts` 随 checkpoint 里的旧计划一直增加。图节点日志里的 `SUCCEEDED` 不是 Run 的终态。

只把 `lease_seconds` 改成 120 不能解决这个问题。一次工具调用本身就可以超过任何固定租约，崩溃恢复也会被拖到整段租约那么久。`configs/runtime.yaml` 当时没有被进程加载，改文件不会影响正在跑的 worker。

## 问题分析

认领入口在 `apps/worker/main.py`。`renew_lease()` 已经写在仓库里，worker 和命令行都没有调用它。

`execute_run` 原先在图返回后做一次 `owns_lease()` 查询，查询通过才调用 `_persist()`。查询和写入不是同一个事务。租约可以在这两步之间过期或被别人拿走，旧 worker 仍可能写下终态。所有权只比较 `worker_id`，没有 fencing token，过期后恢复的旧进程无法和后认领的新进程区分开。

`_persist()`、状态迁移、事件、计划和 `finish_work()` 各开各的事务。可能出现结果写了一半、工作项仍是 `RUNNING` 的窗口。

`ToolGateway.idempotency_store` 和 `ReportStore._by_operation` 都在内存中。进程重启或换 worker 后，同一个操作会再执行一次。副作用超时虽然标成 `UNKNOWN`，但这个标记没有落库，下一次仍会重做。

工作项没有认领、执行、丢租的上限，也没有退避。过期项按 `available_at` 立刻回到队列，所以 `attempts=10` 仍会自动重跑。

## 执行方案

按下面的顺序改，不把加长租约当作修复：

1. 认领后启动独立心跳，间隔取 `min(配置间隔, lease_seconds / 3)`。数据库短暂失败只记录并在下一次间隔重试。确认不是当前 owner 时，立刻标记 `LeaseLost`。
2. 节点入口、任务循环前后和工具调用前检查 `LeaseLost`。失租抛出异常，不再静默跳过 `_persist()`。
3. `work_items` 增加 `lease_token`、`lease_generation`、`last_heartbeat_at`，以及 `claim_attempts`、`execution_attempts`、`lease_loss_count`。每次认领生成新 token。续租、开始执行和提交都要同时满足 worker、token、`RUNNING` 和未过期。
4. 新增 `commit_outcome()`。在一个事务里校验 token、写 Run 状态和结果、写事件、计划、产物和委派，然后把工作项改成 `DONE` 并清除租约。任何一步失败都整笔回滚。
5. 工具操作写入 `operations` 表，键为 `run_id + task_id + tool_name + arguments_hash`。有副作用的工具把 `operation_id` 传给实现。超时或中断后的副作用保持 `UNKNOWN`，后续调用直接返回，不再执行。
6. 认领、执行、丢租超过上限后，工作项改为 `DEAD`，Run 改为 `FAILED`，原因写入状态和 `work.dead` 事件。可重试的失败使用 5 秒、15 秒、45 秒、120 秒加抖动，写到 `available_at`。
7. 补心跳、失租、fencing、原子提交、崩溃后接管、操作重放和死信测试。

过期工作项用 `uv run enterprise-agent recover-expired` 重新排队。这个命令只处理租约已经过期的 `RUNNING` 行，并写 `work.requeued` 事件。调用前先停掉仍在执行该 Run 的 worker。

## 最终方案

租约默认仍是 30 秒。心跳默认每 10 秒续租一次，也就是每段租约至少续三次。进程读取 `Settings`，对应环境变量 `EA_LEASE_SECONDS`、`EA_LEASE_RENEW_INTERVAL_SECONDS`、`EA_MAX_CLAIM_ATTEMPTS`、`EA_MAX_EXECUTION_ATTEMPTS`、`EA_MAX_LEASE_LOSSES`。`configs/runtime.yaml` 只记录这组期望值，启动路径不加载它。

执行顺序是：

```text
claim（新 fencing token）
  -> 心跳开始
  -> begin_execution（执行次数 + 1，超限则 DEAD）
  -> 图执行；节点和工具边界检查 LeaseLost
  -> commit_outcome（校验 token，并在同一事务里完成 Run 和 DONE）
  -> 心跳停止
```

失租后的旧 worker 调用 `note_lease_loss()`。只有原 token 仍挂在该行上时才增加丢租计数并延迟重新排队；token 已经属于新 worker 时什么都不改。未捕获的执行错误由 `release_work()` 按认领次数退避后重新排队，并且只更新仍属于该 worker 的 `RUNNING` 行。

终态使用现有的 `FAILED`，没有新增 `NEEDS_ATTENTION`，这样已有客户端不用识别新的 Run 状态。死信工作项使用新的 `DEAD` 状态，认领语句不会再选中它。

崩溃恢复保持原来的语义：心跳随进程停止，最多一个租约周期后，新 worker 用新 token 接管。旧 token 不能覆盖新结果。

数据库变更在 `alembic/versions/0004_lease_fencing.py`。测试库用 `create_all` 建新表；已经存在的本地库需要 `uv run alembic upgrade head`。
