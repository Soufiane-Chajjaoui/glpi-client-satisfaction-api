"""Watermark management for incremental ingestion."""
from datetime import datetime, timedelta
from sqlalchemy import text
from lib.db import pg_engine

LOOKBACK_SECONDS = 3600


def ensure_control_table():
    with pg_engine().connect() as conn:
        conn.execute(text("""
            CREATE SCHEMA IF NOT EXISTS bronze;
            CREATE TABLE IF NOT EXISTS bronze.ingestion_watermarks (
                table_name   TEXT PRIMARY KEY,
                watermark    TEXT,
                updated_at   TIMESTAMP DEFAULT NOW()
            );
        """))
        conn.commit()


def get_watermark(table_name: str) -> str | None:
    with pg_engine().connect() as conn:
        row = conn.execute(
            text("SELECT watermark FROM bronze.ingestion_watermarks WHERE table_name = :t"),
            {"t": table_name}
        ).fetchone()
    return row[0] if row else None


def save_watermark(table_name: str, watermark: str):
    with pg_engine().connect() as conn:
        conn.execute(
            text("""
                INSERT INTO bronze.ingestion_watermarks (table_name, watermark, updated_at)
                VALUES (:t, :w, NOW())
                ON CONFLICT (table_name) DO UPDATE SET watermark = :w2, updated_at = NOW()
            """),
            {"t": table_name, "w": watermark, "w2": watermark}
        )
        conn.commit()


def get_watermark_with_lookback(table_name: str) -> str | None:
    raw = get_watermark(table_name)
    if raw is None:
        return None
    try:
        dt = datetime.fromisoformat(raw)
        effective = dt - timedelta(seconds=LOOKBACK_SECONDS)
        return effective.isoformat()
    except ValueError:
        return raw
