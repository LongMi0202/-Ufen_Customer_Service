import os

import psycopg
from psycopg import sql

DATABASES = (
    "ai_customer_service",
    "customer_service"
)


def clear_database(database_name: str) -> None:
    with psycopg.connect(
            host=os.environ["PGHOST"],
            port=os.environ["PGPORT"],
            user=os.environ["PGUSER"],
            password=os.environ["PGPASSWORD"],
            dbname=database_name
    ) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT tablename FROM pg_tables "
                "WHERE schemaname = 'public'"
            )
            table_names = [
                row[0]
                for row in cursor.fetchall()
            ]
            if table_names:
                cursor.execute(
                    sql.SQL(
                        "TRUNCATE TABLE {} RESTART IDENTITY CASCADE"
                    ).format(
                        sql.SQL(", ").join(
                            sql.Identifier("public", table_name)
                            for table_name in table_names
                        )
                    )
                )
    print(f"Cleared data from {database_name}.")


for database in DATABASES:
    clear_database(database)
