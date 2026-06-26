"""
Silver Layer — Bronze → Silver (cleaning + feature engineering)
Uses Polars for transformations. Incremental via date_mod watermark.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

import html as html_mod
import polars as pl
import re
from datetime import datetime
from lib.watermark import get_watermark_with_lookback, save_watermark
from lib.polars_helpers import read_pg, write_pg, upsert_pg

TABLE_CONFIG = {
    "glpi_tickets":             {"pk": "id", "watermark": "date_mod"},
    "glpi_itilfollowups":       {"pk": "id", "watermark": "date_mod"},
    "glpi_tickettasks":         {"pk": "id", "watermark": "date_mod"},
    "glpi_itilsolutions":       {"pk": "id", "watermark": "date_mod"},
    "glpi_ticketsatisfactions": {"pk": "id", "watermark": "date_answered"},
    "glpi_entities":            {"pk": "id", "watermark": "date_mod"},
    "glpi_users":               {"pk": "id", "watermark": "date_mod"},
    "glpi_itilcategories":      {"pk": "id", "watermark": "id"},
    "glpi_groups_tickets":      {"pk": "id", "watermark": "id"},
    "glpi_locations":           {"pk": "id", "watermark": None},
    "glpi_profiles":            {"pk": "id", "watermark": None},
    "glpi_profiles_users":      {"pk": "id", "watermark": "id"},
    "glpi_slalevelactions":     {"pk": "id", "watermark": None},
    "glpi_slalevels":           {"pk": "id", "watermark": None},
    "glpi_slalevels_tickets":   {"pk": "id", "watermark": None},
    "glpi_slas":                {"pk": "id", "watermark": None},
    "glpi_tickets_users":       {"pk": "id", "watermark": "id"},
}


def read_bronze(table: str) -> pl.DataFrame:
    return read_pg(f"bronze.{table}")


def clean_text(text) -> str:
    if text is None:
        return ""
    text = str(text)
    text = html_mod.unescape(text)
    text = re.sub(r"\r\n", "\n", text)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</(?:p|div|blockquote|pre|li)>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"https?://\S+|www\.\S+", " ", text)
    text = re.sub(r"\n--[ \t]*\n.*", "", text, flags=re.DOTALL)
    text = re.sub(r"\nEnvoy[ée] de mon[^\n]*", "", text)
    text = re.sub(r"\n(?:De|From|Envoy[ée]|Sent|À|To|Cc|Objet|Subject|Date)[ \t]*:[^\n]*", "", text)
    text = re.sub(r"\n>[^\n]*", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def clean_df(df: pl.DataFrame) -> pl.DataFrame:
    for col in df.columns:
        if col.startswith("_"):
            continue
        if df[col].dtype == pl.Utf8:
            df = df.with_columns(
                pl.col(col).map_elements(clean_text, return_dtype=pl.Utf8).alias(col)
            )
    if "content" in df.columns:
        df = df.with_columns(
            pl.col("content").alias("clean_content")
        )
    if "comment" in df.columns:
        df = df.with_columns(
            pl.col("comment").alias("clean_comment")
        )
    return df


def add_ticket_features(df: pl.DataFrame) -> pl.DataFrame:
    dt_cols = ["date", "solvedate", "closedate", "takeintoaccountdate"]
    for c in dt_cols:
        if c in df.columns and df[c].dtype != pl.Datetime:
            df = df.with_columns(pl.col(c).str.to_datetime().alias(c))
    return df.with_columns(
        pl.when(pl.col("solvedate").is_not_null() & pl.col("date").is_not_null())
        .then((pl.col("solvedate") - pl.col("date")).dt.total_hours().round(2))
        .otherwise(None).alias("resolution_time_hours"),
        pl.when(pl.col("takeintoaccountdate").is_not_null() & pl.col("date").is_not_null())
        .then((pl.col("takeintoaccountdate") - pl.col("date")).dt.total_hours().round(2))
        .otherwise(None).alias("first_response_hours"),
        pl.when((pl.col("priority") >= 4) & pl.col("solvedate").is_null() & pl.col("closedate").is_null())
        .then(True).otherwise(False).alias("is_critical"),
        pl.when(pl.col("date").is_not_null())
        .then((pl.lit(datetime.now()) - pl.col("date")).dt.total_days().round(0))
        .otherwise(None).alias("ticket_age_days"),
        pl.col("status").map_elements(
            lambda s: {1: "new", 2: "processing", 3: "waiting", 4: "solved", 5: "closed", 6: "accepted"}.get(s, "unknown"),
            return_dtype=pl.Utf8,
        ).alias("status_label"),
    )


def add_satisfaction_features(df: pl.DataFrame) -> pl.DataFrame:
    return df.with_columns(
        pl.col("satisfaction").map_elements(
            lambda s: {1: 0, 2: 2, 3: 5, 4: 8, 5: 10}.get(s),
            return_dtype=pl.Int32,
        ).alias("nps_score"),
        pl.col("satisfaction").map_elements(
            lambda s: "promoter" if s and s >= 4 else ("passive" if s and s >= 3 else "detractor") if s else "unknown",
            return_dtype=pl.Utf8,
        ).alias("nps_category"),
    )


FEATURE_FUNCS = {
    "glpi_tickets": add_ticket_features,
    "glpi_ticketsatisfactions": add_satisfaction_features,
}


def transform_table(table: str):
    config = TABLE_CONFIG[table]
    wm_col = config["watermark"]
    pk = config["pk"]

    df = read_bronze(table)
    if df.height == 0:
        print(f"  [{table}] empty bronze")
        return

    df = clean_df(df)

    fn = FEATURE_FUNCS.get(table)
    if fn:
        df = fn(df)

    df = df.with_columns(
        pl.lit(datetime.now()).alias("_silver_ingested_at"),
        pl.lit("CLEANSED").alias("_silver_processing_status"),
        pl.lit(1).alias("_data_version"),
    )

    is_incremental = False
    if wm_col:
        wm_val = get_watermark_with_lookback(f"silver_{table}")
        if wm_val:
            df = df.filter(pl.col(wm_col) > pl.lit(wm_val))
            if df.height == 0:
                print(f"  [{table}] already up to date (0 delta)")
                return
            is_incremental = True

    if is_incremental and pk:
        upsert_pg(df, f"silver.{table}", pk_col=pk)
    else:
        write_pg(df, f"silver.{table}", if_exists="replace")

    if wm_col:
        max_wm = df.select(pl.max(wm_col)).item()
        if max_wm:
            save_watermark(f"silver_{table}", str(max_wm))

    print(f"  [{table}] -> silver.{table} ({df.height} rows)")


def transform_all():
    print("Silver layer transformation starting...")
    for table in TABLE_CONFIG:
        try:
            transform_table(table)
        except Exception as e:
            msg = str(e).encode("ascii", errors="replace").decode("ascii")
            print(f"  ERROR [{table}]: {msg}")
    print("Silver layer complete.")


if __name__ == "__main__":
    transform_all()
