"""Polars DB helpers — read/write PostgreSQL and MySQL without pandas."""
import polars as pl
from datetime import datetime, date
from sqlalchemy import text
from lib.db import pg_engine, mysql_engine

TYPE_MAP = {
    pl.Int64: "BIGINT", pl.Int32: "INTEGER", pl.Int16: "SMALLINT",
    pl.Float64: "DOUBLE PRECISION", pl.Float32: "REAL",
    pl.String: "TEXT", pl.Utf8: "TEXT",
    pl.Boolean: "BOOLEAN",
    pl.Datetime: "TIMESTAMP", pl.Date: "DATE",
}


def read_pg(query_or_table: str) -> pl.DataFrame:
    if query_or_table.startswith("SELECT") or query_or_table.startswith("WITH"):
        sql = query_or_table
    else:
        sql = f"SELECT * FROM {query_or_table}"
    with pg_engine().connect() as conn:
        result = conn.execute(text(sql))
        rows = result.fetchall()
        cols = list(result.keys())
    col_values = {c: [] for c in cols}
    for r in rows:
        d = dict(r._mapping)
        for c in cols:
            v = d.get(c)
            if isinstance(v, (datetime, date)):
                col_values[c].append(str(v))
            else:
                col_values[c].append(v)
    return pl.DataFrame(col_values, schema=cols)


def _ensure_table(df: pl.DataFrame, table: str):
    schema, tbl = table.split(".", 1)
    with pg_engine().connect() as conn:
        exists = conn.execute(
            text("SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_schema=:s AND table_name=:t)"),
            {"s": schema, "t": tbl}
        ).scalar()
        if not exists:
            conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {schema}"))
            cols_def = ", ".join(f'"{c}" {TYPE_MAP.get(d, "TEXT")}' for c, d in zip(df.columns, df.dtypes))
            conn.execute(text(f"CREATE TABLE {table} ({cols_def})"))
            conn.commit()


def write_pg(df: pl.DataFrame, table: str, if_exists: str = "append"):
    if df.height == 0:
        return
    if if_exists == "replace":
        with pg_engine().connect() as conn:
            conn.execute(text(f"DROP TABLE IF EXISTS {table} CASCADE"))
            conn.commit()
    _ensure_table(df, table)
    _insert_pg(df, table)


def upsert_pg(df: pl.DataFrame, table: str, pk_col: str = "id"):
    if df.height == 0:
        return
    pks = df.select(pl.col(pk_col)).to_series().to_list()
    pks = [p for p in pks if p is not None]
    if not pks:
        return
    _ensure_table(df, table)
    with pg_engine().connect() as conn:
        conn.execute(text(f"DELETE FROM {table} WHERE {pk_col} = ANY(:pks)"), {"pks": pks})
        conn.commit()
    _insert_pg(df, table)


def _insert_pg(df: pl.DataFrame, table: str):
    cols = list(df.columns)
    placeholders = ", ".join(f":{c}" for c in cols)
    col_names = ", ".join(f'"{c}"' for c in cols)
    sql = f"INSERT INTO {table} ({col_names}) VALUES ({placeholders})"
    batch = df.to_dicts()
    with pg_engine().connect() as conn:
        conn.execute(text(sql), batch)
        conn.commit()


def read_mysql(table: str, where: str | None = None) -> pl.DataFrame:
    sql = f"SELECT * FROM {table}"
    if where:
        sql += f" WHERE {where}"
    try:
        with mysql_engine().connect() as conn:
            result = conn.execute(text(sql))
            cols = list(result.keys())
            rows = result.fetchall()
        col_values = {c: [] for c in cols}
        for r in rows:
            d = dict(r._mapping)
            for c in cols:
                v = d.get(c)
                if isinstance(v, (datetime, date)):
                    col_values[c].append(str(v))
                else:
                    col_values[c].append(v)
        return pl.DataFrame(col_values, schema=cols)
    except Exception as e:
        print(f"  MySQL connection failed: {e}")
        return pl.DataFrame()
