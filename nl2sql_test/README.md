# NL2SQL 测试

用项目根目录 `.env` 里的 `DASHSCOPE_API_KEY`，以及 `configs/models.yaml` 的 `qwen-default`，把中文问题转成 MySQL 查询，并在本机 `nl2sql` 库上执行。

只允许单条 `SELECT` 或 `WITH ... SELECT`。

在项目根目录运行：

```bash
.venv/bin/python nl2sql_test/run.py --self-check
.venv/bin/python nl2sql_test/run.py
.venv/bin/python nl2sql_test/run.py "研发部有哪些员工？"
```

默认连接 `root@127.0.0.1:3306/nl2sql`，root 免密码。需要换连接时设置 `NL2SQL_HOST`、`NL2SQL_PORT`、`NL2SQL_USER`、`NL2SQL_PASSWORD`、`NL2SQL_DATABASE`。
