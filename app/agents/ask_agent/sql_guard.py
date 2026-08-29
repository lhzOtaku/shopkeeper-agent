"""Programmatic SQL safety checks for the data-query agent.

The guard is intentionally stricter than MySQL EXPLAIN:
- It rejects non-read-only statements before they reach the database.
- It checks generated table and column names against the recalled Meta context.
- It returns correctable metadata errors separately from hard safety blocks.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

import sqlglot
from sqlglot import exp


@dataclass(frozen=True)
class SQLGuardResult:
    ok: bool
    blocked: bool
    error_type: str | None = None
    error: str | None = None
    tables: list[str] = field(default_factory=list)
    columns: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


BLOCKED_EXPRESSIONS = (
    exp.Alter,
    exp.Command,
    exp.Create,
    exp.Delete,
    exp.Drop,
    exp.Insert,
    exp.Merge,
    exp.TruncateTable,
    exp.Update,
)

READ_ONLY_ROOTS = (
    exp.Select,
    exp.Union,
    exp.Intersect,
    exp.Except,
)


def _clean_sql(sql: str) -> str:
    cleaned = sql.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()
    return cleaned


def _allowed_schema(
    generation_context: dict[str, Any] | None,
) -> tuple[dict[str, set[str]], set[str]]:
    table_to_columns: dict[str, set[str]] = {}
    all_columns: set[str] = set()
    if not generation_context:
        return table_to_columns, all_columns

    for table in generation_context.get("tables") or []:
        table_name = table.get("name")
        if not table_name:
            continue
        columns = {
            column.get("name")
            for column in table.get("columns") or []
            if column.get("name")
        }
        table_to_columns[table_name] = columns
        all_columns.update(columns)

    return table_to_columns, all_columns


def _cte_names(expression: exp.Expression) -> set[str]:
    return {
        cte.alias_or_name
        for cte in expression.find_all(exp.CTE)
        if cte.alias_or_name
    }


def _projection_output_names(select: exp.Select) -> set[str]:
    names: set[str] = set()
    for projection in select.expressions:
        if projection.alias:
            names.add(projection.alias)
        elif isinstance(projection, exp.Column) and projection.name:
            names.add(projection.name)
    return names


def _cte_columns(expression: exp.Expression) -> dict[str, set[str]]:
    columns: dict[str, set[str]] = {}
    for cte in expression.find_all(exp.CTE):
        cte_name = cte.alias_or_name
        if not cte_name:
            continue
        query = cte.this
        if isinstance(query, exp.Subquery):
            query = query.this
        columns[cte_name] = (
            _projection_output_names(query) if isinstance(query, exp.Select) else set()
        )
    return columns


def _derived_table_columns(expression: exp.Expression) -> dict[str, set[str]]:
    columns: dict[str, set[str]] = {}
    for subquery in expression.find_all(exp.Subquery):
        alias = subquery.alias_or_name
        if not alias:
            continue
        query = subquery.this
        columns[alias] = (
            _projection_output_names(query) if isinstance(query, exp.Select) else set()
        )
    return columns


def _select_aliases(expression: exp.Expression) -> set[str]:
    aliases: set[str] = set()
    for select in expression.find_all(exp.Select):
        aliases.update(
            projection.alias for projection in select.expressions if projection.alias
        )
    return aliases


def _table_aliases(expression: exp.Expression) -> tuple[dict[str, str], list[str]]:
    ctes = _cte_names(expression)
    aliases: dict[str, str] = {}
    physical_tables: list[str] = []
    for table in expression.find_all(exp.Table):
        table_name = table.name
        if not table_name:
            continue
        if table_name in ctes:
            aliases[table.alias_or_name] = table_name
            aliases[table_name] = table_name
            continue
        aliases[table.alias_or_name] = table_name
        aliases[table_name] = table_name
        physical_tables.append(table_name)
    return aliases, sorted(set(physical_tables))


def _is_relation_column(
    column_name: str, relation_columns: dict[str, set[str]]
) -> bool:
    return any(column_name in columns for columns in relation_columns.values())


def _validate_relation_column(
    relation_name: str,
    column_name: str,
    relation_columns: dict[str, set[str]],
    used_tables: list[str],
    used_columns: list[str],
) -> SQLGuardResult | None:
    if relation_name not in relation_columns:
        return None
    if relation_columns[relation_name] and column_name not in relation_columns[relation_name]:
        return SQLGuardResult(
            ok=False,
            blocked=False,
            error_type="unknown_column",
            error=(
                "SQL references a column outside derived relation output: "
                f"{relation_name}.{column_name}"
            ),
            tables=used_tables,
            columns=sorted(set(used_columns)),
        )
    return SQLGuardResult(ok=True, blocked=False)


def _expression_is_read_only(expression: exp.Expression) -> bool:
    if not isinstance(expression, READ_ONLY_ROOTS):
        return False
    return not any(expression.find_all(*BLOCKED_EXPRESSIONS))


def validate_sql_safety(
    sql: str,
    generation_context: dict[str, Any] | None = None,
) -> SQLGuardResult:
    """Validate generated SQL before EXPLAIN or execution.

    Dangerous statement classes are hard blocked. Unknown tables/columns are
    reported as correctable errors so the SQL repair node can try again with the
    same recalled context.
    """

    cleaned_sql = _clean_sql(sql)
    if not cleaned_sql:
        return SQLGuardResult(
            ok=False,
            blocked=True,
            error_type="empty_sql",
            error="Generated SQL is empty.",
        )

    try:
        expressions = [expr for expr in sqlglot.parse(cleaned_sql, read="mysql") if expr]
    except Exception as exc:
        return SQLGuardResult(
            ok=False,
            blocked=False,
            error_type="parse_error",
            error=f"SQL parse failed: {exc}",
        )

    if len(expressions) != 1:
        return SQLGuardResult(
            ok=False,
            blocked=True,
            error_type="multiple_statements",
            error="Only one SELECT statement is allowed.",
        )

    expression = expressions[0]
    if not _expression_is_read_only(expression):
        return SQLGuardResult(
            ok=False,
            blocked=True,
            error_type="not_read_only",
            error="Only read-only SELECT SQL is allowed.",
        )

    table_to_columns, all_columns = _allowed_schema(generation_context)
    aliases, used_tables = _table_aliases(expression)
    cte_columns = _cte_columns(expression)
    relation_columns = cte_columns | _derived_table_columns(expression)
    select_aliases = _select_aliases(expression)

    if table_to_columns:
        unknown_tables = sorted(
            table for table in used_tables if table not in table_to_columns
        )
        if unknown_tables:
            return SQLGuardResult(
                ok=False,
                blocked=False,
                error_type="unknown_table",
                error=f"SQL uses tables outside recalled context: {unknown_tables}",
                tables=used_tables,
            )

    used_columns: list[str] = []
    if table_to_columns:
        for column in expression.find_all(exp.Column):
            column_name = column.name
            table_alias = column.table
            if not column_name:
                continue

            if table_alias:
                table_name = aliases.get(table_alias, table_alias)
                used_columns.append(f"{table_name}.{column_name}")
                relation_result = _validate_relation_column(
                    table_name,
                    column_name,
                    relation_columns,
                    used_tables,
                    used_columns,
                )
                if relation_result:
                    if not relation_result.ok:
                        return relation_result
                    continue
                if table_name not in table_to_columns:
                    return SQLGuardResult(
                        ok=False,
                        blocked=False,
                        error_type="unknown_table_alias",
                        error=(
                            "SQL references an unknown table alias or table: "
                            f"{table_alias}"
                        ),
                        tables=used_tables,
                        columns=sorted(set(used_columns)),
                    )
                if column_name not in table_to_columns[table_name]:
                    return SQLGuardResult(
                        ok=False,
                        blocked=False,
                        error_type="unknown_column",
                        error=(
                            "SQL references a column outside recalled context: "
                            f"{table_name}.{column_name}"
                        ),
                        tables=used_tables,
                        columns=sorted(set(used_columns)),
                    )
                continue

            used_columns.append(column_name)
            if (
                column_name not in all_columns
                and column_name not in select_aliases
                and not _is_relation_column(column_name, relation_columns)
            ):
                return SQLGuardResult(
                    ok=False,
                    blocked=False,
                    error_type="unknown_column",
                    error=(
                        "SQL references a column outside recalled context: "
                        f"{column_name}"
                    ),
                    tables=used_tables,
                    columns=sorted(set(used_columns)),
                )

    return SQLGuardResult(
        ok=True,
        blocked=False,
        tables=used_tables,
        columns=sorted(set(used_columns)),
    )
