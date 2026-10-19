"""Create tables and apply the few additive column changes we have (no Alembic in the pilot)."""

from sqlalchemy import inspect, text

from sandbox_mcp.db.models import Base

# (table, column, DDL type with default) — only additive, idempotent changes.
ADDED_COLUMNS = [
    ("applications", "git_ref", "VARCHAR(200) NOT NULL DEFAULT 'main'"),
]


def ensure_schema(engine) -> None:
    Base.metadata.create_all(engine)
    insp = inspect(engine)
    with engine.begin() as conn:
        for table, column, ddl in ADDED_COLUMNS:
            existing = {c["name"] for c in insp.get_columns(table)}
            if column not in existing:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))
