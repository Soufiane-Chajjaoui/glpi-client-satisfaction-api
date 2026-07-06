"""
Initialize PostgreSQL medallion schemas (bronze, silver, gold, gold_alert)
and create watermarks table.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from sqlalchemy import text
from lib.db import pg_engine
from lib.watermark import ensure_control_table


def create_medallion_schemas():
    with pg_engine().connect() as conn:
        for schema in ["bronze", "silver", "gold", "gold_alert"]:
            conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {schema}"))
        conn.commit()
    print("Medallion schemas created: bronze, silver, gold, gold_alert")


def create_ingestion_watermarks():
    ensure_control_table()
    print("Ingestion watermarks table ready.")


if __name__ == "__main__":
    create_medallion_schemas()
    create_ingestion_watermarks()
    print("Database initialization complete.")
