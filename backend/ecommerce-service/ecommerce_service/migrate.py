"""Additive v1 migration. Existing cash orders remain in the legacy project."""
from sqlalchemy import inspect, text
from ecommerce_service.database import engine


def migrate():
    additions = {
        "users": {"tenant_id": "VARCHAR(40) NOT NULL DEFAULT 'tenant_a'"},
        "orders": {
            "tenant_id": "VARCHAR(40) NOT NULL DEFAULT 'tenant_a'",
            "welfare_project_id": "VARCHAR(40) NOT NULL DEFAULT 'legacy'",
            "total_points": "INTEGER NOT NULL DEFAULT 0",
            "payment_type": "VARCHAR(30) NOT NULL DEFAULT 'legacy_cash'",
            "version": "INTEGER NOT NULL DEFAULT 1",
        },
    }
    with engine.begin() as connection:
        tables = set(inspect(connection).get_table_names())
        for table, columns in additions.items():
            if table in tables:
                for name, definition in columns.items():
                    connection.execute(text(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {name} {definition}"))
