"""Copy a current AgentFlow SQLite database into an empty PostgreSQL database.

Stop the API and worker before running this utility. It never changes the source,
requires the PostgreSQL schema to be at the current Alembic head, and refuses to
merge into a non-empty target.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sqlalchemy import MetaData, create_engine, func, inspect, select, text
from sqlalchemy.engine import URL

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agentflow.config import get_settings

EXPECTED_REVISION = "d142f6a8c903"
BATCH_SIZE = 500


def migrate(source_path: Path, target_url: str) -> dict[str, int]:
    source_path = source_path.expanduser().resolve()
    if not source_path.is_file():
        raise FileNotFoundError(f"SQLite source database does not exist: {source_path}")

    source_engine = create_engine(
        URL.create("sqlite", database=str(source_path)),
        connect_args={"check_same_thread": False},
    )
    target_engine = create_engine(target_url, pool_pre_ping=True)
    try:
        with source_engine.connect() as source, target_engine.connect() as target:
            if source.dialect.name != "sqlite" or target.dialect.name != "postgresql":
                raise ValueError("Source must be SQLite and DATABASE_URL must be PostgreSQL.")

            source_tables = set(inspect(source).get_table_names()) - {"sqlite_sequence"}
            if "alembic_version" not in source_tables:
                raise RuntimeError("SQLite source is missing alembic_version; migrate it first.")
            source_revision = source.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            if source_revision != EXPECTED_REVISION:
                raise RuntimeError(
                    f"SQLite source is at revision {source_revision}; expected {EXPECTED_REVISION}. "
                    "Back up and run `alembic upgrade head` against the SQLite source first."
                )

            target_tables = set(inspect(target).get_table_names())
            if "alembic_version" not in target_tables:
                raise RuntimeError("PostgreSQL target is missing Alembic migrations; run them first.")
            target_revision = target.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            if target_revision != EXPECTED_REVISION:
                raise RuntimeError(
                    f"PostgreSQL target is at revision {target_revision}; expected {EXPECTED_REVISION}."
                )

            source_data_tables = source_tables - {"alembic_version"}
            target_data_tables = target_tables - {"alembic_version"}
            if source_data_tables != target_data_tables:
                missing = sorted(source_data_tables - target_data_tables)
                unexpected = sorted(target_data_tables - source_data_tables)
                raise RuntimeError(
                    f"Database schemas differ; missing target tables={missing}, "
                    f"unexpected target tables={unexpected}."
                )
            target_empty_metadata = MetaData()
            target_empty_metadata.reflect(bind=target, only=target_data_tables)
            for name, target_table in target_empty_metadata.tables.items():
                if target.execute(select(target_table).limit(1)).first():
                    raise RuntimeError(f"PostgreSQL target table {name!r} is not empty; refusing merge.")

        source_metadata = MetaData()
        target_metadata = MetaData()
        source_metadata.reflect(bind=source_engine, only=source_data_tables)
        target_metadata.reflect(bind=target_engine, only=target_data_tables)
        counts: dict[str, int] = {}

        # Keep one consistent read snapshot and one atomic PostgreSQL transaction.
        with source_engine.begin() as source, target_engine.begin() as target:
            for target_table in target_metadata.sorted_tables:
                source_table = source_metadata.tables[target_table.name]
                count = 0
                rows = source.execute(select(source_table)).mappings()
                while batch := rows.fetchmany(BATCH_SIZE):
                    target.execute(target_table.insert(), [dict(row) for row in batch])
                    count += len(batch)
                counts[target_table.name] = count
                target_count = target.scalar(select(func.count()).select_from(target_table))
                if target_count != count:
                    raise RuntimeError(
                        f"Row count mismatch for {target_table.name!r}: copied {count}, "
                        f"found {target_count} in PostgreSQL."
                    )

            # Explicitly inserted primary keys must advance PostgreSQL sequences.
            for target_table in target_metadata.sorted_tables:
                for column in target_table.primary_key.columns:
                    if column.name != "id":
                        continue
                    maximum = func.max(target_table.c[column.name])
                    target.scalar(
                        select(
                            func.setval(
                                func.pg_get_serial_sequence(target_table.name, column.name),
                                func.coalesce(maximum, 1),
                                maximum.is_not(None),
                            )
                        ).select_from(target_table)
                    )

        return counts
    finally:
        source_engine.dispose()
        target_engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "source",
        nargs="?",
        type=Path,
        default=Path("agentflow.db"),
        help="SQLite database file (default: agentflow.db)",
    )
    args = parser.parse_args()
    counts = migrate(args.source, get_settings().database_url)
    print("Copied rows into PostgreSQL:")
    for table, count in sorted(counts.items()):
        print(f"  {table}: {count}")


if __name__ == "__main__":
    main()
