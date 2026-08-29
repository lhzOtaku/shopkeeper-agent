"""Initialize the meta_v2 schema from docker/mysql/meta_v2.sql."""

import asyncio
import os
import re
from pathlib import Path

import asyncmy
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = PROJECT_ROOT / "docker" / "mysql" / "meta_v2.sql"


async def main() -> None:
    load_dotenv(PROJECT_ROOT / ".env")
    root_password = os.environ["MYSQL_ROOT_PASSWORD"]
    app_user = os.environ["MYSQL_USER"]
    if re.fullmatch(r"[A-Za-z0-9_]+", app_user) is None:
        raise ValueError("MYSQL_USER may contain only letters, numbers, and underscores")

    sql = SCHEMA_PATH.read_text(encoding="utf-8")
    statements = [statement.strip() for statement in sql.split(";") if statement.strip()]

    conn = await asyncmy.connect(
        host="localhost",
        port=3307,
        user="root",
        password=root_password,
        autocommit=True,
    )
    try:
        async with conn.cursor() as cursor:
            for statement in statements:
                await cursor.execute(statement)
            await cursor.execute(
                f"GRANT ALL PRIVILEGES ON `meta_v2`.* TO '{app_user}'@'%'"
            )
        print(f"executed {len(statements)} statements from {SCHEMA_PATH}")
    finally:
        conn.close()


if __name__ == "__main__":
    asyncio.run(main())
