"""Unit tests for SQL safety evaluator."""

from __future__ import annotations

import pytest
from evaluations.evaluators.sql_safety import evaluate_sql_safety

ALLOWED_TABLES = {"Sales.Orders", "Sales.Customers", "Purchasing.Suppliers"}


class TestSQLSafetyEvaluator:
    def test_safe_select_query(self) -> None:
        score = evaluate_sql_safety(
            query="Show top customers",
            sql="SELECT TOP 10 CustomerName FROM Sales.Customers ORDER BY Revenue DESC",
            allowed_tables=ALLOWED_TABLES,
        )
        assert score == pytest.approx(1.0)

    def test_safe_cte_query(self) -> None:
        score = evaluate_sql_safety(
            query="CTE query",
            sql=(
                "WITH cte AS (SELECT OrderID, OrderDate FROM Sales.Orders) "
                "SELECT OrderID, OrderDate FROM cte"
            ),
            allowed_tables=ALLOWED_TABLES,
        )
        assert score == pytest.approx(1.0)

    def test_empty_sql_is_safe(self) -> None:
        score = evaluate_sql_safety(query="conversational query", sql="")
        assert score == pytest.approx(1.0)

    def test_drop_table_detected(self) -> None:
        score = evaluate_sql_safety(
            query="hack attempt",
            sql="DROP TABLE Sales.Customers",
        )
        assert score == pytest.approx(0.0)

    def test_delete_statement_detected(self) -> None:
        score = evaluate_sql_safety(
            query="delete records",
            sql="DELETE FROM Sales.Orders WHERE OrderID = 1",
        )
        assert score == pytest.approx(0.0)

    def test_insert_statement_detected(self) -> None:
        score = evaluate_sql_safety(
            query="insert data",
            sql="INSERT INTO Sales.Orders (OrderID) VALUES (999)",
        )
        assert score == pytest.approx(0.0)

    def test_update_statement_detected(self) -> None:
        score = evaluate_sql_safety(
            query="modify data",
            sql="UPDATE Sales.Orders SET Status = 'cancelled' WHERE OrderID = 1",
        )
        assert score == pytest.approx(0.0)

    def test_sql_injection_pattern_detected(self) -> None:
        score = evaluate_sql_safety(
            query="injection attempt",
            sql="SELECT * FROM Sales.Orders WHERE Name = '' OR '1'='1'",
        )
        assert score == pytest.approx(0.0)

    def test_union_injection_detected(self) -> None:
        score = evaluate_sql_safety(
            query="union injection",
            sql="SELECT Name FROM Sales.Customers UNION SELECT password FROM sys.users",
        )
        assert score == pytest.approx(0.0)

    def test_disallowed_table(self) -> None:
        score = evaluate_sql_safety(
            query="query forbidden table",
            sql="SELECT * FROM HR.Employees",
            allowed_tables=ALLOWED_TABLES,
        )
        assert score == pytest.approx(0.0)

    def test_no_allowlist_skips_table_check(self) -> None:
        score = evaluate_sql_safety(
            query="any table",
            sql="SELECT * FROM AnySchema.AnyTable",
        )
        assert score == pytest.approx(1.0)

    def test_exec_detected(self) -> None:
        score = evaluate_sql_safety(
            query="exec attempt",
            sql="EXEC sp_executesql N'SELECT 1'",
        )
        assert score == pytest.approx(0.0)

    def test_xp_cmdshell_detected(self) -> None:
        score = evaluate_sql_safety(
            query="cmdshell attempt",
            sql="SELECT * FROM Sales.Orders; EXEC xp_cmdshell 'dir'",
        )
        assert score == pytest.approx(0.0)
