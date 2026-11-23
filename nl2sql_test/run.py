"""Run the NL2SQL questions against local MySQL with the project Qwen model."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from nl2sql import (  # noqa: E402
    DatabaseSchema,
    MysqlClient,
    MysqlConfig,
    answer_question,
    assert_readonly,
    check_columns,
    load_project_env,
    load_questions,
    load_qwen_config,
    rows_match,
)


def self_check() -> None:
    assert_readonly("SELECT name FROM departments")
    assert_readonly("WITH dept AS (SELECT id FROM departments) SELECT * FROM dept")
    for statement in (
        "DELETE FROM employees",
        "SELECT * FROM employees; DROP TABLE employees",
        "UPDATE employees SET salary = 1",
        "SELECT * INTO OUTFILE '/tmp/x' FROM employees",
    ):
        try:
            assert_readonly(statement)
        except ValueError:
            continue
        raise AssertionError(f"readonly guard accepted {statement}")
    print("readonly guard ok")
    schema = DatabaseSchema(
        text="",
        database="nl2sql",
        columns={
            "customers": frozenset({"id", "name", "city"}),
            "departments": frozenset({"id", "name", "location"}),
            "employees": frozenset({"id", "name", "department_id", "title", "salary", "hire_date"}),
            "order_items": frozenset({"id", "order_id", "product_id", "quantity", "unit_price"}),
            "orders": frozenset({"id", "customer_id", "order_date", "status"}),
            "products": frozenset({"id", "name", "category", "unit_price"}),
        },
    )
    accepted = (
        "SELECT e.name FROM employees e JOIN departments d ON e.department_id = d.id WHERE d.name = '研发'",
        "SELECT Name FROM employees",
        "SELECT name AS Employee_Name FROM employees ORDER BY employee_name",
        "SELECT d.name, COUNT(*) AS n FROM departments d LEFT JOIN employees e ON e.department_id = d.id GROUP BY d.name HAVING n > 0",
        "SELECT c.name, SUM(oi.quantity * oi.unit_price) AS amount FROM customers c JOIN orders o ON o.customer_id = c.id JOIN order_items oi ON oi.order_id = o.id GROUP BY c.name ORDER BY amount DESC",
        "SELECT p.name, SUM(oi.quantity * oi.unit_price) AS sales FROM products p JOIN order_items oi ON p.id = oi.product_id GROUP BY p.name ORDER BY sales DESC LIMIT 1",
        "SELECT * FROM employees",
        "WITH dept AS (SELECT id, name FROM departments) SELECT e.name FROM employees e JOIN dept d ON e.department_id = d.id",
        "SELECT t.amount FROM (SELECT quantity * unit_price AS Amount FROM order_items) t ORDER BY t.AMOUNT",
        "SELECT e.name FROM employees e WHERE EXISTS (SELECT 1 FROM departments d WHERE d.id = e.department_id)",
        "SELECT CAST(salary AS CHAR) FROM employees",
        "SELECT name FROM employees UNION SELECT name FROM departments",
    )
    for statement in accepted:
        check_columns(statement, schema)
    rejected = (
        "SELECT tools FROM employees",
        "SELECT e.tools FROM employees e",
        "SELECT name FROM tools",
        "SELECT Name FROM Employees",
        "SELECT d.salary FROM employees e JOIN departments d ON e.department_id = d.id",
        "SELECT name FROM employees e JOIN departments d ON e.department_id = d.id",
        "SELECT e.name FROM employees e WHERE EXISTS (SELECT 1 FROM departments d WHERE d.id = e.tools)",
        "SELECT E.name FROM employees e",
    )
    for statement in rejected:
        try:
            check_columns(statement, schema)
        except ValueError:
            continue
        raise AssertionError(f"schema check accepted {statement}")
    if "where" not in check_columns("SELECT name FROM employees WHERE", schema).lower():
        raise AssertionError("unparsed syntax should still reach MySQL")
    print("schema check ok")


def main() -> int:
    parser = argparse.ArgumentParser(description="Ask Qwen to answer a question with MySQL.")
    parser.add_argument("question", nargs="?", help="Ask one question instead of the fixture set.")
    parser.add_argument("--self-check", action="store_true", help="Check the SQL guard and exit.")
    parser.add_argument("--show-schema", action="store_true", help="Print the schema prompt and exit.")
    args = parser.parse_args()
    if args.self_check:
        self_check()
        return 0

    load_project_env()
    client = MysqlClient(MysqlConfig.from_env())
    schema = client.describe()
    if args.show_schema:
        print(schema.text)
        return 0

    qwen = load_qwen_config()
    print(f"model: {qwen.model}")
    print(f"database: {client.config.database}@{client.config.host}:{client.config.port}")
    if args.question:
        sql, rows = answer_question(client, qwen, schema, args.question)
        print(sql)
        _print_rows(rows)
        return 0

    failed = 0
    for item in load_questions():
        print(f"\n[{item['id']}] {item['question']}")
        try:
            sql, rows = answer_question(client, qwen, schema, item["question"])
        except Exception as exc:
            failed += 1
            print(f"FAIL {exc}")
            continue
        print(sql)
        _print_rows(rows)
        if rows_match(rows, item["expect_rows"]):
            print("PASS")
        else:
            failed += 1
            print(f"FAIL expected {item['expect_rows']}")
    print(f"\n{len(load_questions()) - failed} passed, {failed} failed")
    return 1 if failed else 0


def _print_rows(rows: list[dict[str, str]]) -> None:
    if not rows:
        print("(no rows)")
        return
    headers = list(rows[0])
    print("\t".join(headers))
    for row in rows:
        print("\t".join(row.get(header, "") for header in headers))


if __name__ == "__main__":
    raise SystemExit(main())
