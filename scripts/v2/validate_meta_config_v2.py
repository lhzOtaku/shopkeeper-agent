"""Validate v2 meta config against the v2 schema SQL."""

import re
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = PROJECT_ROOT / "scripts" / "v2" / "init_dw_v2_schema.sql"
META_CONFIG_PATH = PROJECT_ROOT / "conf" / "meta_config_v2.yaml"


def parse_schema_columns(sql_text: str) -> dict[str, set[str]]:
    """Parse CREATE TABLE column names from the v2 schema SQL."""

    tables: dict[str, set[str]] = {}
    table_pattern = re.compile(r"CREATE TABLE (\w+)\s*\((.*?)\) ENGINE", re.S)
    for table_name, table_body in table_pattern.findall(sql_text):
        columns: set[str] = set()
        for line in table_body.splitlines():
            line = line.strip()
            if (
                not line
                or line.startswith("CONSTRAINT")
                or line.startswith("FOREIGN")
            ):
                continue
            match = re.match(r"(\w+)\s+", line)
            if match:
                columns.add(match.group(1))
        tables[table_name] = columns
    return tables


def column_exists(column_id: str, schema_columns: dict[str, set[str]]) -> bool:
    """Return whether a table.column id exists in the v2 DW schema."""

    if "." not in column_id:
        return False
    table_name, column_name = column_id.split(".", 1)
    return table_name in schema_columns and column_name in schema_columns[table_name]


def main() -> None:
    schema_columns = parse_schema_columns(SCHEMA_PATH.read_text(encoding="utf-8"))
    meta_config = yaml.safe_load(META_CONFIG_PATH.read_text(encoding="utf-8"))

    missing_tables: list[str] = []
    missing_columns: list[str] = []
    missing_grains: list[str] = []
    missing_relations: list[str] = []
    missing_metric_columns: list[str] = []
    missing_metric_variants: list[str] = []

    for table in meta_config["tables"]:
        table_name = table["name"]
        if not table.get("grain"):
            missing_grains.append(table_name)
        if table_name not in schema_columns:
            missing_tables.append(table_name)
            continue
        for column in table["columns"]:
            column_name = column["name"]
            if column_name not in schema_columns[table_name]:
                missing_columns.append(f"{table_name}.{column_name}")

    for relation in meta_config.get("relations", []):
        left = f"{relation['left_table']}.{relation['left_column']}"
        right = f"{relation['right_table']}.{relation['right_column']}"
        if relation["left_table"] not in schema_columns:
            missing_relations.append(left)
        elif relation["left_column"] not in schema_columns[relation["left_table"]]:
            missing_relations.append(left)
        if relation["right_table"] not in schema_columns:
            missing_relations.append(right)
        elif relation["right_column"] not in schema_columns[relation["right_table"]]:
            missing_relations.append(right)

    for metric in meta_config["metrics"]:
        variants = metric.get("variants") or []
        default_variant = metric.get("default_variant")
        variant_names = {variant.get("name") for variant in variants}

        if not variants:
            missing_metric_variants.append(f"{metric['name']}: no variants configured")
            continue
        if not default_variant:
            missing_metric_variants.append(
                f"{metric['name']}: default_variant is required"
            )
        elif default_variant not in variant_names:
            missing_metric_variants.append(f"{metric['name']}.{default_variant}")

        for variant in variants:
            if not variant.get("name"):
                missing_metric_variants.append(
                    f"{metric['name']}: variant name is required"
                )
                continue
            if not variant.get("formula"):
                missing_metric_variants.append(
                    f"{metric['name']}.{variant['name']}: formula is required"
                )
            base_table = variant.get("base_table")
            if base_table and base_table not in schema_columns:
                missing_metric_variants.append(
                    f"{metric['name']}.{variant['name']}.base_table={base_table}"
                )
            for column_id in variant.get("relevant_columns") or []:
                if not column_exists(column_id, schema_columns):
                    missing_metric_columns.append(
                        f"{metric['name']}.{variant['name']}:{column_id}"
                    )

    print(f"表数量：{len(meta_config['tables'])}")
    print(f"关系数量：{len(meta_config.get('relations', []))}")
    print(f"指标数量：{len(meta_config['metrics'])}")
    print(f"缺失表：{missing_tables}")
    print(f"缺失字段：{missing_columns}")
    print(f"缺失粒度：{missing_grains}")
    print(f"缺失关系字段：{missing_relations}")
    print(f"缺失指标字段：{missing_metric_columns}")
    print(f"缺失指标 variant：{missing_metric_variants}")

    if (
        missing_tables
        or missing_columns
        or missing_grains
        or missing_relations
        or missing_metric_columns
        or missing_metric_variants
    ):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
