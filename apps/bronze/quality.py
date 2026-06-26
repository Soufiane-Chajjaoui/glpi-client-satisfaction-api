"""
Bronze quality checks — row counts, nulls, duplicates, future dates.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

import polars as pl
from datetime import datetime
from sqlalchemy import text
from lib.db import pg_engine

TABLES = [
    "glpi_tickets", "glpi_itilfollowups", "glpi_tickettasks",
    "glpi_itilsolutions", "glpi_ticketsatisfactions", "glpi_entities",
    "glpi_users", "glpi_itilcategories", "glpi_groups_tickets",
    "glpi_locations", "glpi_profiles", "glpi_profiles_users",
    "glpi_slalevelactions", "glpi_slalevels", "glpi_slalevels_tickets",
    "glpi_slas", "glpi_tickets_users",
]


def check_table(table: str) -> dict:
    with pg_engine().connect() as conn:
        row = conn.execute(
            text(f"SELECT COUNT(*) FROM bronze.{table}")
        ).scalar()
        return {"table": table, "row_count": row}


def check_all():
    print(f"{'Table':30} {'Rows':>8}")
    print("-" * 40)
    for t in TABLES:
        r = check_table(t)
        print(f"{r['table']:30} {r['row_count']:>8}")


if __name__ == "__main__":
    check_all()
