"""NL2SQL harness for the local MySQL sample database and the project Qwen profile."""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import httpx
import sqlglot
import yaml
from dotenv import load_dotenv
from sqlglot import exp
from sqlglot.errors import OptimizeError, ParseError, SchemaError, TokenError
from sqlglot.optimizer.qualify import qualify

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
DEFAULT_MODEL = "qwen3.8-max"
_STATEMENT = re.compile(r"^\s*(select|with)\b", re.IGNORECASE | re.DOTALL)
_FORBIDDEN = re.compile(
    r"\b("
    r"insert|update|delete|drop|alter|truncate|create|grant|revoke|replace|"
    r"call|do|handler|load|outfile|dumpfile|into|set|use"
    r")\b",
    re.IGNORECASE,
)
_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
_LINE_COMMENT = re.compile(r"(--|#).*?(?=\n|$)")


@dataclass(frozen=True)
class MysqlConfig:
    host: str = "127.0.0.1"
    port: int = 3306
    user: str = "root"
    password: str = ""
    database: str = "nl2sql"

    @classmethod
    def from_env(cls) -> MysqlConfig:
        database = os.environ.get("NL2SQL_DATABASE", "nl2sql")
        if not re.fullmatch(r"[A-Za-z0-9_]+", database):
            raise ValueError("NL2SQL_DATABASE must be a plain identifier")
        return cls(
            host=os.environ.get("NL2SQL_HOST", "127.0.0.1"),
            port=int(os.environ.get("NL2SQL_PORT", "3306")),
            user=os.environ.get("NL2SQL_USER", "root"),
            password=os.environ.get("NL2SQL_PASSWORD", ""),
            database=database,
        )


@dataclass(frozen=True)
class DatabaseSchema:
    """Prompt text plus the table and column names checked before execution."""

    text: str
    database: str
    columns: Mapping[str, frozenset[str]]


@dataclass(frozen=True)
class QwenConfig:
    model: str
    base_url: str
    api_key: str
    timeout_seconds: float = 120


def load_project_env() -> None:
    """Load the repository .env without overriding variables already set."""
    load_dotenv(PROJECT_ROOT / ".env")


def load_qwen_config() -> QwenConfig:
    """Use the qwen-default profile and the DashScope key named in .env."""
    load_project_env()
    document = yaml.safe_load((PROJECT_ROOT / "configs" / "models.yaml").read_text())
    profile = next(item for item in document["profiles"] if item["name"] == "qwen-default")
    provider = next(item for item in document["providers"] if item["name"] == profile["provider"])
    key_name = provider["credential_ref"]
    api_key = os.environ.get(key_name, "")
    if not api_key:
        raise RuntimeError(f"{key_name} is missing from the environment and .env")
    return QwenConfig(
        model=profile["model_name"],
        base_url=provider.get("endpoint") or DEFAULT_BASE_URL,
        api_key=api_key,
        timeout_seconds=float(profile.get("timeout_seconds") or 120),
    )


class MysqlClient:
    """Run read-only SQL through the local mysql client."""

    def __init__(self, config: MysqlConfig) -> None:
        self.config = config

    def query(self, sql: str) -> list[dict[str, str]]:
        guarded = assert_readonly(sql)
        output = self._run(guarded)
        return _parse_batch(output)

    def describe(self) -> DatabaseSchema:
        """Load the prompt schema and the table/column catalog used to check SQL."""
        tables = self._run(
            "SELECT TABLE_NAME, IFNULL(TABLE_COMMENT, '') "
            "FROM information_schema.TABLES "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_TYPE = 'BASE TABLE' "
            "ORDER BY TABLE_NAME"
        )
        columns = self._run(
            "SELECT TABLE_NAME, COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_KEY, "
            "IFNULL(COLUMN_COMMENT, '') "
            "FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() "
            "ORDER BY TABLE_NAME, ORDINAL_POSITION"
        )
        keys = self._run(
            "SELECT TABLE_NAME, COLUMN_NAME, REFERENCED_TABLE_NAME, REFERENCED_COLUMN_NAME "
            "FROM information_schema.KEY_COLUMN_USAGE "
            "WHERE TABLE_SCHEMA = DATABASE() AND REFERENCED_TABLE_NAME IS NOT NULL "
            "ORDER BY TABLE_NAME, COLUMN_NAME"
        )
        samples: list[str] = ["", "SAMPLE ROWS"]
        for row in _parse_batch(tables):
            name = row["TABLE_NAME"]
            if not re.fullmatch(r"[A-Za-z0-9_]+", name):
                continue
            samples.append(f"-- {name}")
            samples.append(self._run(f"SELECT * FROM `{name}` LIMIT 3").rstrip())
        text = "\n".join(
            [
                f"DATABASE {self.config.database}",
                "TABLES",
                tables.rstrip(),
                "",
                "COLUMNS",
                columns.rstrip(),
                "",
                "FOREIGN KEYS",
                keys.rstrip(),
                *samples,
            ]
        )
        grouped: dict[str, set[str]] = {}
        for row in _parse_batch(columns):
            grouped.setdefault(row["TABLE_NAME"], set()).add(row["COLUMN_NAME"])
        return DatabaseSchema(
            text=text,
            database=self.config.database,
            columns={name: frozenset(names) for name, names in grouped.items()},
        )

    def schema_text(self) -> str:
        return self.describe().text

    def _run(self, sql: str) -> str:
        path: str | None = None
        try:
            path = _defaults_file(self.config)
            completed = subprocess.run(
                [
                    "mysql",
                    f"--defaults-extra-file={path}",
                    "--batch",
                    "--raw",
                    "--default-character-set=utf8mb4",
                    "-e",
                    sql,
                ],
                check=False,
                capture_output=True,
                text=True,
            )
        finally:
            if path is not None:
                Path(path).unlink(missing_ok=True)
        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip()
            raise RuntimeError(detail)
        return completed.stdout


def assert_readonly(sql: str) -> str:
    """Accept one SELECT or WITH statement and reject anything that can write."""
    stripped = _BLOCK_COMMENT.sub(" ", sql)
    stripped = _LINE_COMMENT.sub(" ", stripped).strip()
    if stripped.endswith(";"):
        stripped = stripped[:-1].strip()
    if not stripped or ";" in stripped:
        raise ValueError("SQL must be a single statement")
    if not _STATEMENT.match(stripped):
        raise ValueError("SQL must be a SELECT query")
    if _FORBIDDEN.search(stripped):
        raise ValueError("SQL contains a statement that is not read-only")
    return stripped


def check_columns(sql: str, schema: DatabaseSchema) -> str:
    """Return one read-only statement whose tables and columns are in the schema.

    Syntax the parser does not understand is returned unchanged so MySQL can reject it.
    """
    guarded = assert_readonly(sql)
    try:
        expression = sqlglot.parse_one(guarded, read="mysql")
    except (ParseError, TokenError):
        return guarded
    _reject_unknown_tables(expression, schema)
    _fold_column_case(expression)
    mapping = {
        table: {column.casefold(): "TEXT" for column in names}
        for table, names in schema.columns.items()
    }
    try:
        qualify(
            expression,
            schema=mapping,
            dialect="mysql",
            validate_qualify_columns=True,
            quote_identifiers=False,
        )
    except (OptimizeError, SchemaError) as exc:
        raise ValueError(_column_error(str(exc))) from exc
    return guarded


def _reject_unknown_tables(expression: exp.Expression, schema: DatabaseSchema) -> None:
    cte_names = {cte.alias for cte in expression.find_all(exp.CTE)}
    for table in expression.find_all(exp.Table):
        if table.name in cte_names:
            continue
        qualified = f"{table.db}.{table.name}" if table.db else table.name
        if table.db and table.db != schema.database:
            raise ValueError(f"Unknown table '{qualified}'")
        if table.name not in schema.columns:
            raise ValueError(f"Unknown table '{qualified}'")


def _fold_column_case(expression: exp.Expression) -> None:
    """MySQL treats column names and column aliases as case-insensitive."""
    for column in expression.find_all(exp.Column):
        column.set("this", exp.to_identifier(column.name.casefold()))
    for alias in expression.find_all(exp.Alias):
        if alias.alias:
            alias.set("alias", exp.to_identifier(alias.alias.casefold()))


def _column_error(message: str) -> str:
    text = " ".join(message.split())
    unknown = re.fullmatch(r"Unknown column:\s*(.+)", text)
    if unknown:
        return f"Unknown column '{unknown.group(1)}'"
    return text


def generate_sql(
    config: QwenConfig,
    schema: str,
    question: str,
    *,
    error: str | None = None,
    failed_sql: str | None = None,
) -> str:
    """Ask Qwen for one MySQL SELECT and return the SQL string."""
    repair = ""
    if error:
        previous = f"\nSQL: {failed_sql}" if failed_sql else ""
        repair = f"\n上一次 SQL 未通过检查，请修正后重新给出 JSON。错误：{error}{previous}\n"
    payload = {
        "model": config.model,
        "temperature": 0,
        "enable_thinking": False,
        "messages": [
            {
                "role": "system",
                "content": (
                    "你是 MySQL 8.4 的 NL2SQL 程序。只根据给定 schema 生成一条只读查询。"
                    "只输出 JSON，对象里只有 sql 字段。不要 Markdown，不要解释。"
                    "只能使用 schema 中出现的表和列。金额用 quantity * unit_price。"
                    "中文问题里的部门、客户、产品名称对应各自表的 name 列。"
                ),
            },
            {
                "role": "user",
                "content": f"SCHEMA:\n{schema}\n\nQUESTION:\n{question}{repair}",
            },
        ],
    }
    response = httpx.post(
        config.base_url.rstrip("/") + "/chat/completions",
        headers={"Authorization": f"Bearer {config.api_key}"},
        json=payload,
        timeout=config.timeout_seconds,
    )
    response.raise_for_status()
    body = response.json()
    content = body["choices"][0]["message"].get("content") or ""
    return _sql_from_content(content)


def answer_question(
    client: MysqlClient,
    config: QwenConfig,
    schema: DatabaseSchema,
    question: str,
) -> tuple[str, list[dict[str, str]]]:
    """Generate SQL, reject unknown tables or columns, then execute.

    One repair follows a failed check. MySQL still reports syntax the checker cannot parse.
    """
    error: str | None = None
    failed_sql: str | None = None
    last_sql = ""
    for _ in range(2):
        last_sql = generate_sql(
            config,
            schema.text,
            question,
            error=error,
            failed_sql=failed_sql,
        )
        try:
            guarded = check_columns(last_sql, schema)
            return guarded, client.query(guarded)
        except (RuntimeError, ValueError) as exc:
            error = str(exc)
            failed_sql = last_sql
    raise RuntimeError(f"SQL still failed after retry: {error}\n{last_sql}")


def rows_match(rows: list[dict[str, str]], expected_rows: list[list[str]]) -> bool:
    """True when every expected value group appears together in some result row."""
    normalized = [{_normalize(value) for value in row.values()} for row in rows]
    for expected in expected_rows:
        needed = {_normalize(value) for value in expected}
        if not any(needed <= row for row in normalized):
            return False
    return True


def load_questions(path: Path | None = None) -> list[dict]:
    document = yaml.safe_load((path or Path(__file__).with_name("questions.yaml")).read_text())
    return list(document["questions"])


def _sql_from_content(content: str) -> str:
    text = content.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1).strip()
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match is None:
            raise ValueError(f"model did not return JSON: {content[:300]}") from None
        payload = json.loads(match.group(0))
    sql = str(payload["sql"]).strip()
    if not sql:
        raise ValueError("model returned an empty sql field")
    return sql


def _normalize(value: str) -> str:
    text = value.strip()
    if re.fullmatch(r"-?\d+\.\d+", text):
        text = text.rstrip("0").rstrip(".")
    return text


def _parse_batch(output: str) -> list[dict[str, str]]:
    lines = [line for line in output.splitlines() if line != ""]
    if not lines:
        return []
    headers = lines[0].split("\t")
    return [dict(zip(headers, line.split("\t"), strict=False)) for line in lines[1:]]


def _defaults_file(config: MysqlConfig) -> str:
    handle = tempfile.NamedTemporaryFile("w", delete=False, prefix="nl2sql-mysql-", suffix=".cnf")
    with handle:
        handle.write("[client]\n")
        handle.write(f"host={config.host}\n")
        handle.write(f"port={config.port}\n")
        handle.write(f"user={config.user}\n")
        handle.write(f"password={config.password}\n")
        handle.write(f"database={config.database}\n")
    os.chmod(handle.name, 0o600)
    return handle.name
