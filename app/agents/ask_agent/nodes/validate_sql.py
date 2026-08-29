"""SQL validation node.

The node validates generated SQL in two stages:
1. Programmatic safety checks with an AST parser.
2. MySQL EXPLAIN through the DW repository.

Safety blocks stop the graph immediately. Correctable SQL issues, such as
unknown columns or EXPLAIN errors, are written to state["error"] so the graph can
route into the SQL correction node.
"""

from langgraph.runtime import Runtime

from app.agents.ask_agent.context import DataAgentContext
from app.agents.ask_agent.sql_correction_test import (
    inject_sql_for_validation,
    is_sql_correction_test_mode,
    log_sql_correction_summary,
    log_sql_test_block,
)
from app.agents.ask_agent.sql_guard import validate_sql_safety
from app.agents.ask_agent.state import DataAgentState
from app.core.log import logger
from app.repositories.mysql.dw.dw_mysql_repository import DWMySQLRepository


async def validate_sql(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    """Validate SQL and update state fields used by graph routing."""

    writer = runtime.stream_writer
    step = "validate_sql"
    writer({"type": "progress", "step": step, "status": "running"})

    try:
        candidate_sql = state["sql"]
        validation_records = list(state.get("sql_validation_records") or [])
        safety_records = list(state.get("sql_safety_records") or [])
        validation_round = len(validation_records) + 1
        correction_count = state.get("correction_count", 0)
        sql_to_validate, injection_type = inject_sql_for_validation(
            candidate_sql,
            validation_round,
        )

        if injection_type:
            log_sql_test_block(
                (
                    "SQL validation injection test: "
                    f"round={validation_round}, type={injection_type}"
                ),
                [
                    f"original_sql={state.get('original_sql') or ''}",
                    f"correction_count={correction_count}",
                    f"candidate_sql={candidate_sql}",
                    f"validated_sql={sql_to_validate}",
                ],
            )

        safety_result = validate_sql_safety(
            sql_to_validate,
            state.get("generation_context", {}),
        )
        safety_records.append(
            {
                "validation_round": validation_round,
                "correction_count": correction_count,
                "candidate_sql": candidate_sql,
                "validated_sql": sql_to_validate,
                "result": safety_result.to_dict(),
            }
        )

        if not safety_result.ok:
            error_message = f"{safety_result.error_type}: {safety_result.error}"
            validation_records.append(
                {
                    "validation_round": validation_round,
                    "correction_count": correction_count,
                    "candidate_sql": candidate_sql,
                    "validated_sql": sql_to_validate,
                    "injection_type": injection_type,
                    "status": (
                        "blocked" if safety_result.blocked else "safety_error"
                    ),
                    "error": error_message,
                    "safety": safety_result.to_dict(),
                }
            )
            logger.info(f"SQL safety validation failed: {error_message}")
            if safety_result.blocked:
                writer(
                    {
                        "type": "error",
                        "code": "SQL_SAFETY_BLOCKED",
                        "message": error_message,
                    }
                )
            writer({"type": "progress", "step": step, "status": "success"})
            return {
                "error": error_message,
                "sql": sql_to_validate,
                "sql_validation_records": validation_records,
                "sql_safety_records": safety_records,
                "sql_safety_blocked": safety_result.blocked,
            }

        dw_mysql_repository: DWMySQLRepository = runtime.context["dw_mysql_repository"]

        try:
            await dw_mysql_repository.validate(sql_to_validate)
            validation_records.append(
                {
                    "validation_round": validation_round,
                    "correction_count": correction_count,
                    "candidate_sql": candidate_sql,
                    "validated_sql": sql_to_validate,
                    "injection_type": injection_type,
                    "status": "success",
                    "error": None,
                    "safety": safety_result.to_dict(),
                }
            )
            writer({"type": "progress", "step": step, "status": "success"})
            logger.info("SQL validation passed")
            log_sql_correction_summary(
                {
                    **state,
                    "sql": sql_to_validate,
                    "sql_validation_records": validation_records,
                    "sql_safety_records": safety_records,
                    "sql_safety_blocked": False,
                    "error": None,
                },
                "SQL correction loop test: final validation passed",
            )
            return {
                "error": None,
                "sql": sql_to_validate,
                "sql_validation_records": validation_records,
                "sql_safety_records": safety_records,
                "sql_safety_blocked": False,
            }
        except Exception as exc:
            error_message = str(exc)
            validation_records.append(
                {
                    "validation_round": validation_round,
                    "correction_count": correction_count,
                    "candidate_sql": candidate_sql,
                    "validated_sql": sql_to_validate,
                    "injection_type": injection_type,
                    "status": "error",
                    "error": error_message,
                    "safety": safety_result.to_dict(),
                }
            )
            logger.info(f"SQL EXPLAIN validation failed: {error_message}")
            if injection_type and is_sql_correction_test_mode():
                log_sql_test_block(
                    (
                        "SQL validation injection test triggered expected error: "
                        f"round={validation_round}"
                    ),
                    [
                        f"validated_sql={sql_to_validate}",
                        f"error={error_message}",
                        "next_step=correct_sql",
                    ],
                )
            writer({"type": "progress", "step": step, "status": "success"})
            return {
                "error": error_message,
                "sql": sql_to_validate,
                "sql_validation_records": validation_records,
                "sql_safety_records": safety_records,
                "sql_safety_blocked": False,
            }

    except Exception as exc:
        logger.error(f"{step} failed: {exc}")
        writer({"type": "progress", "step": step, "status": "error"})
        raise
