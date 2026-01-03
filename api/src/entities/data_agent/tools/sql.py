"""
SQL execution tool for Azure SQL Database.
"""

import logging
import os
import struct
from typing import Any

import aioodbc
from agent_framework import ai_function
from azure.identity import DefaultAzureCredential

logger = logging.getLogger(__name__)


def _get_azure_sql_token() -> bytes:
    """
    Get an Azure AD token for SQL Database authentication.

    Returns:
        Token bytes formatted for pyodbc
    """
    credential = DefaultAzureCredential()
    token = credential.get_token("https://database.windows.net/.default")

    # Format token for SQL Server ODBC driver
    token_bytes = token.token.encode("utf-16-le")
    token_struct = struct.pack(f"<I{len(token_bytes)}s", len(token_bytes), token_bytes)

    return token_struct


@ai_function
async def execute_sql(query: str) -> dict[str, Any]:
    """
    Execute a read-only SQL SELECT query against the Wide World Importers database.

    This function connects to Azure SQL Database using Azure AD authentication
    and executes the provided query. Only SELECT queries are allowed for safety.

    Args:
        query: A SQL SELECT query to execute. Must be read-only (SELECT only).

    Returns:
        A dictionary containing:
        - success: Whether the query executed successfully
        - columns: List of column names in the result
        - rows: List of dictionaries, one per row
        - row_count: Number of rows returned
        - error: Error message if the query failed
    """
    logger.info("Executing SQL query: %s", query[:200])

    # Validate query is read-only
    query_upper = query.strip().upper()
    if not query_upper.startswith("SELECT"):
        return {
            "success": False,
            "error": "Only SELECT queries are allowed. Query must start with SELECT.",
            "columns": [],
            "rows": [],
            "row_count": 0
        }

    # Check for dangerous keywords
    dangerous_keywords = ["INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE", "TRUNCATE", "EXEC", "EXECUTE"]
    for keyword in dangerous_keywords:
        if keyword in query_upper:
            return {
                "success": False,
                "error": f"Query contains forbidden keyword: {keyword}. Only read-only SELECT queries are allowed.",
                "columns": [],
                "rows": [],
                "row_count": 0
            }

    try:
        # Get connection parameters
        server = os.getenv("AZURE_SQL_SERVER", "")
        database = os.getenv("AZURE_SQL_DATABASE", "WideWorldImporters")

        if not server:
            return {
                "success": False,
                "error": "AZURE_SQL_SERVER environment variable is required",
                "columns": [],
                "rows": [],
                "row_count": 0
            }

        # Build connection string with Azure AD token auth
        connection_string = (
            f"DRIVER={{ODBC Driver 18 for SQL Server}};"
            f"SERVER={server};"
            f"DATABASE={database};"
        )

        # Get token for Azure AD auth
        token_struct = _get_azure_sql_token()

        # Connect and execute
        async with aioodbc.connect(
            dsn=connection_string,
            attrs_before={
                1256: token_struct  # SQL_COPT_SS_ACCESS_TOKEN
            }
        ) as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(query)

                # Get column names
                columns = [column[0] for column in cursor.description] if cursor.description else []

                # Fetch all rows
                raw_rows = await cursor.fetchall()

                # Convert to list of dicts with JSON-safe values
                rows = []
                for row in raw_rows:
                    row_dict = {}
                    for i, col in enumerate(columns):
                        value = row[i]
                        # Convert non-JSON-serializable types
                        if value is None:
                            row_dict[col] = None
                        elif isinstance(value, (int, float, str, bool)):
                            row_dict[col] = value
                        else:
                            row_dict[col] = str(value)
                    rows.append(row_dict)

                logger.info("Query executed successfully. Returned %d rows.", len(rows))

                return {
                    "success": True,
                    "columns": columns,
                    "rows": rows,
                    "row_count": len(rows),
                    "error": None
                }

    except Exception as e:
        logger.error("SQL execution error: %s", e)
        return {
            "success": False,
            "error": str(e),
            "columns": [],
            "rows": [],
            "row_count": 0
        }
