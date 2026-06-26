"""
Bronze Layer — MySQL -> PostgreSQL (bronze schema)
Uses Polars for data manipulation and SQLAlchemy for DB I/O.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

import polars as pl
from datetime import datetime
from lib.watermark import get_watermark_with_lookback, save_watermark, ensure_control_table
from lib.polars_helpers import read_mysql, write_pg, upsert_pg

WATERMARK_COLS = {
    "glpi_tickets":             "date_mod",
    "glpi_itilfollowups":       "date_mod",
    "glpi_tickettasks":         "date_mod",
    "glpi_itilsolutions":       "date_mod",
    "glpi_ticketsatisfactions": "date_answered",
    "glpi_entities":            "date_mod",
    "glpi_users":               "date_mod",
    "glpi_groups_tickets":      "id",
    "glpi_itilcategories":      "id",
    "glpi_profiles_users":      "id",
    "glpi_tickets_users":       "id",
}

# Tables whose watermark can change (date_mod): upsert by PK to avoid duplicates
# Tables with id watermark are append-only (no updates) — excluded from UPSERT_TABLES
UPSERT_TABLES = {
    "glpi_tickets": "id",
    "glpi_itilfollowups": "id",
    "glpi_tickettasks": "id",
    "glpi_itilsolutions": "id",
    "glpi_ticketsatisfactions": "id",
    "glpi_entities": "id",
    "glpi_users": "id",
}

TABLES_NO_WATERMARK = [
    "glpi_locations", "glpi_profiles", "glpi_slalevelactions",
    "glpi_slalevels", "glpi_slalevels_tickets", "glpi_slas",
]


def read_mysql_table(table: str, watermark_col: str | None = None, since: str | None = None) -> pl.DataFrame:
    where = None
    if watermark_col and since:
        where = f"{watermark_col} > '{since}'"
    return read_mysql(table, where)


def write_bronze(df: pl.DataFrame, table: str, mode: str = "append"):
    ensure_control_table()
    df = df.with_columns(
        pl.lit(datetime.now()).alias("_bronze_ingested_at"),
        pl.lit("mysql").alias("_source_system"),
    )
    write_pg(df, f"bronze.{table}", if_exists=mode)
    return df.height


def upsert_bronze(df: pl.DataFrame, table: str, pk_col: str):
    ensure_control_table()
    df = df.with_columns(
        pl.lit(datetime.now()).alias("_bronze_ingested_at"),
        pl.lit("mysql").alias("_source_system"),
    )
    upsert_pg(df, f"bronze.{table}", pk_col)
    return df.height


def ingest_table(table: str):
    wm_col = WATERMARK_COLS.get(table)
    wm_val = get_watermark_with_lookback(table) if wm_col else None

    df = read_mysql_table(table, wm_col, wm_val)
    if df.height == 0:
        print(f"  [{table}] up to date or MySQL unavailable - 0 rows")
        return

    print(f"  [{table}] {df.height} rows ingested", end="")
    is_incremental = bool(wm_col and wm_val)
    pk = UPSERT_TABLES.get(table)
    if is_incremental and pk:
        upsert_bronze(df, table, pk)
    else:
        mode = "append" if is_incremental else "replace"
        write_bronze(df, table, mode=mode)

    if wm_col:
        max_wm = df.select(pl.max(wm_col)).item()
        if max_wm:
            save_watermark(table, str(max_wm))
    print(f" -> bronze.{table}")


def ingest_all():
    print("Bronze layer ingestion starting...")
    ensure_control_table()
    all_tables = list(WATERMARK_COLS.keys()) + TABLES_NO_WATERMARK
    for table in all_tables:
        try:
            ingest_table(table)
        except Exception as e:
            print(f"  ERROR [{table}]: {e}")
    print("Bronze ingestion complete.")


if __name__ == "__main__":
    ingest_all()
