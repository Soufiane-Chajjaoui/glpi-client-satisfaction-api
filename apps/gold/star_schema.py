"""
Gold Layer — Couche finale du pipeline (Silver → Gold)
=======================================================
Construit un star schema (fait + dimensions) à partir des données Silver.
- Moteur : Polars (rapide, sans Spark)
- Chargement : incrémental via `date_mod` sur les tickets
- NLP : analyse de sentiment distilcamembert sur tickets + solutions + followups

Score de satisfaction composite (stocké dans `score_composite`) :
  Si enquête client présente (etoile_client non NULL) :
      score = moyenne(etoile_client, sentiment_comment)  si commentaire NLP dispo
             sinon etoile_client uniquement
      ─── score comportemental complètement ignoré

  Sinon (pas d'enquête) :
      score = 50% score comportemental (tto_ok, ttr_ok, priority, nb_solutions, nb_followups)
            + 50% score sentiment NLP   (distilcamembert, échelle 1–5)

  Résultat final sur échelle 1–5

Poids du score comportemental (somme = 100%) :
    tto_ok          15%   → 5 pts si respecté, 1 sinon
    ttr_ok          25%   → 5 pts si respecté, 1 sinon
    priority_weight 30%   → normalisé sur 1–5 depuis PRIORITY_WEIGHT (1–10)
    nb_solutions    10%   → pénalité (cap 5, inversé : 0 sol = 5 pts)
    nb_followups    20%   → pénalité (cap 10, inversé)
"""

import gc
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from pathlib import Path
import polars as pl
from datetime import datetime
from lib.polars_helpers import read_pg, write_pg, upsert_pg
from lib.watermark import get_watermark, save_watermark


# ============================================================
# CONSTANTES MÉTIER
# ============================================================

# Seuils ITIL par priorité (en secondes) — fallback si aucun SLA GLPI configuré
ITIL_TTR_SEC = {1: 14400, 2: 86400, 3: 259200, 4: 432000, 5: 1296000}
ITIL_TTO_SEC = {1: 1800,  2: 14400, 3: 86400,  4: 259200, 5: 432000}

# Poids de priorité bruts (1=critique → 10, 5=très bas → 1)
PRIORITY_WEIGHT = {1: 10, 2: 6, 3: 3, 4: 1, 5: 1}
PRIORITY_WEIGHT_MIN = 1
PRIORITY_WEIGHT_MAX = 10

STATUS_LABELS = {
    1: "Nouveau", 2: "En cours", 3: "Planifie",
    4: "En attente", 5: "Resolu", 6: "Clos",
}

STAR_MAPPING = {
    "1 star": 1, "2 stars": 2, "3 stars": 3,
    "4 stars": 4, "5 stars": 5,
}

# Poids du score comportemental
W_TTO          = 0.15
W_TTR          = 0.25
W_PRIORITY     = 0.30
W_SOLUTIONS    = 0.10
W_FOLLOWUPS    = 0.20

CAP_SOLUTIONS  = 5
CAP_FOLLOWUPS  = 10

SENTIMENT_FALLBACK = 3.0
TEST_MODE = False


# ============================================================
# PIPELINE NLP — SINGLETON (ONNX Runtime, FP16)
# ============================================================

_SENTIMENT_PIPELINE = None

_ONNX_DIR = Path(__file__).parent.parent.parent / "models" / "onnx"


def load_sentiment_pipeline():
    global _SENTIMENT_PIPELINE
    if _SENTIMENT_PIPELINE is None:
        from lib.onnx_sentiment import load_onnx_pipeline
        _SENTIMENT_PIPELINE = load_onnx_pipeline(_ONNX_DIR)
    return _SENTIMENT_PIPELINE


# ============================================================
# HELPERS — I/O PostgreSQL
# ============================================================

def write_gold(df: pl.DataFrame, table: str):
    if df.height == 0:
        print(f"  gold.{table} — vide, ignoré")
        return
    write_pg(df, f"gold.{table}", if_exists="replace")


# ============================================================
# HELPERS — CALCULS MÉTIER
# ============================================================

def to_seconds(num, unit: str) -> float | None:
    conversions = {"minute": 60, "hour": 3600, "day": 86400}
    if unit in conversions:
        return float(num * conversions[unit])
    return None


def label_to_star(label: str) -> int:
    return STAR_MAPPING.get(label, 0)


# ============================================================
# NLP — ANALYSE DE SENTIMENT
# ============================================================

def _collect_texts(tickets, solutions, followups) -> pl.DataFrame:
    parts = []
    if tickets.height > 0 and "clean_content" in tickets.columns:
        parts.append(tickets.select(pl.col("id").alias("items_id"), "clean_content"))
    for source_df in (solutions, followups):
        if source_df.height > 0 and "clean_content" in source_df.columns:
            parts.append(source_df.select("items_id", "clean_content"))
    if not parts:
        return pl.DataFrame()
    return (
        pl.concat(parts)
        .filter(pl.col("clean_content") != "")
        .with_columns(pl.col("clean_content").str.slice(0, 2000))
        .select("items_id", "clean_content")
    )


def compute_all_sentiment(tickets, solutions, followups) -> pl.DataFrame:
    texts = _collect_texts(tickets, solutions, followups)
    if texts.height == 0:
        return pl.DataFrame()

    if TEST_MODE:
        texts = texts.with_columns(pl.lit("3 stars").alias("sentiment_label"), pl.lit(3).alias("sentiment_score"))
        texts = texts.with_columns(
            pl.col("sentiment_label").map_elements(label_to_star, return_dtype=pl.Int64).alias("sentiment_score")
        )
    else:
        pipe = load_sentiment_pipeline()
        items_ids = texts["items_id"].to_list()
        all_texts_list = texts["clean_content"].to_list()
        del texts
        gc.collect()
        batch_size = 4
        n_total = len(all_texts_list)
        all_labels = []
        for i in range(0, n_total, batch_size):
            batch = all_texts_list[i:i + batch_size]
            results = pipe(batch)
            all_labels.extend(r[0]["label"] for r in results)
            done = min(i + batch_size, n_total)
            if (i // batch_size) % 10 == 0:
                print(f"    sentiment NLP : {done}/{n_total}")
            del batch, results
            gc.collect()
        del all_texts_list
        gc.collect()
        texts = pl.DataFrame({"items_id": items_ids, "sentiment_label": all_labels})
        texts = texts.with_columns(
            pl.col("sentiment_label").map_elements(label_to_star, return_dtype=pl.Int64).alias("sentiment_score")
        )
        del items_ids, all_labels
        gc.collect()

    result = (
        texts.group_by("items_id")
        .agg([
            pl.col("sentiment_score").mean().alias("sentiment_moyen"),
            pl.col("sentiment_score").median().alias("sentiment_median"),
            pl.col("sentiment_score").count().alias("nb_messages"),
        ])
        .rename({"items_id": "ticket_id"})
    )
    del texts
    gc.collect()
    return result


# ============================================================
# DIMENSIONS
# ============================================================

def build_dim_entity(entities: pl.DataFrame) -> pl.DataFrame:
    return (
        entities.select(
            pl.col("id").alias("entity_id"), pl.col("name").alias("entity_name"),
            pl.col("completename").alias("entity_completename"),
            pl.col("level").alias("entity_level"),
            pl.col("entities_id").alias("parent_entity_id"),
            "town", "country", pl.col("comment").alias("entity_comment"),
        )
        .with_columns(pl.col("entity_comment").fill_null(pl.col("entity_name")))
        .unique()
    )


def build_dim_sla(slas: pl.DataFrame) -> pl.DataFrame:
    return slas.with_columns(
        pl.when(pl.col("type") == 0).then(pl.lit("TTR"))
        .when(pl.col("type") == 1).then(pl.lit("TTO"))
        .otherwise(pl.lit("Inconnu")).alias("sla_type_label"),
        pl.struct(["number_time", "definition_time"])
        .map_elements(lambda r: to_seconds(r["number_time"], r["definition_time"]), return_dtype=pl.Float64)
        .alias("sla_seconds"),
    ).select(
        pl.col("id").alias("sla_id"), pl.col("name").alias("sla_name"),
        pl.col("type").alias("sla_type"), "sla_type_label",
        "number_time", pl.col("definition_time").alias("time_unit"),
        "sla_seconds", "end_of_working_day", "use_ticket_calendar",
        pl.col("entities_id").alias("entity_id"),
    )


def build_dim_user(users: pl.DataFrame) -> pl.DataFrame:
    if users.height == 0:
        return pl.DataFrame()
    return users.select(
        pl.col("id").alias("user_id"), pl.col("name").alias("username"),
        "firstname", "realname",
        pl.concat_str(["firstname", "realname"], separator=" ").alias("full_name"),
        pl.col("entities_id").alias("entity_id"),
        "locations_id", "is_active", "language",
    ).unique()


def build_dim_status() -> pl.DataFrame:
    return pl.DataFrame([{"status_id": k, "status_label": v} for k, v in STATUS_LABELS.items()])


def build_dim_category(categories: pl.DataFrame) -> pl.DataFrame:
    return categories.select(
        pl.col("id").alias("category_id"), pl.col("name").alias("category_name"),
        pl.col("completename").alias("category_completename"),
        pl.col("level").alias("category_level"),
        pl.col("itilcategories_id").alias("parent_category_id"),
        pl.col("entities_id").alias("entity_id"),
        "is_incident", "is_request",
    ).unique()


def build_dim_date(tickets: pl.DataFrame) -> pl.DataFrame:
    tickets_dt = tickets.with_columns(
        pl.col("date_creation").str.to_datetime().alias("date_creation_dt"),
        pl.col("closedate").str.to_datetime().alias("closedate_dt"),
    )
    all_dates = pl.concat([
        tickets_dt.select(pl.col("date_creation_dt").dt.date().alias("date_val")),
        tickets_dt.filter(pl.col("closedate_dt").is_not_null())
        .select(pl.col("closedate_dt").dt.date().alias("date_val")),
    ]).unique().filter(pl.col("date_val").is_not_null())
    return all_dates.with_columns(
        pl.col("date_val").dt.strftime("%Y%m%d").cast(pl.Int32).alias("date_id"),
        pl.col("date_val").dt.year().alias("year"),
        pl.col("date_val").dt.month().alias("month"),
        pl.col("date_val").dt.quarter().alias("quarter"),
        pl.col("date_val").dt.week().alias("week_of_year"),
        pl.col("date_val").dt.day().alias("day_of_month"),
    )


# ============================================================
# TABLE DE FAITS
# ============================================================

def compute_survey_comment_sentiment(satisfacts) -> pl.DataFrame:
    """NLP uniquement sur le commentaire d'enquête — séparé des autres textes."""
    if "clean_comment" not in satisfacts.columns:
        return pl.DataFrame()
    comments = satisfacts.filter(pl.col("clean_comment").is_not_null() & (pl.col("clean_comment") != "")).select(
        pl.col("tickets_id").alias("items_id"), pl.col("clean_comment").alias("clean_content")
    )

    if comments.height == 0:
        return pl.DataFrame()

    if TEST_MODE:
        comments = comments.with_columns(pl.lit("3 stars").alias("sentiment_label"), pl.lit(3).alias("sentiment_score"))
    else:
        pipe = load_sentiment_pipeline()
        items_ids = comments["items_id"].to_list()
        all_texts = comments["clean_content"].to_list()
        batch_size = 16
        all_labels = []
        for i in range(0, len(all_texts), batch_size):
            batch = all_texts[i:i + batch_size]
            results = pipe(batch, batch_size=len(batch))
            all_labels.extend(r[0]["label"] for r in results)
            gc.collect()
        del comments, all_texts
        gc.collect()
        comments = pl.DataFrame({"items_id": items_ids, "sentiment_label": all_labels})
        comments = comments.with_columns(
            pl.col("sentiment_label").map_elements(label_to_star, return_dtype=pl.Int64).alias("sentiment_comment")
        )

    return comments.group_by("items_id").agg(
        pl.col("sentiment_comment").mean().alias("sentiment_comment"),
        pl.col("sentiment_comment").count().alias("nb_comment_messages"),
    ).rename({"items_id": "ticket_id"})


def build_fact_ticket_satisfaction(
    tickets, satisfacts, slas, solutions, followups, tickets_users,
    sentiment_stats, sentiment_comment_stats,
) -> pl.DataFrame:
    sla_tto = slas.filter(pl.col("type") == 1).select(
        pl.col("id").alias("sla_tto_id"), pl.col("number_time").alias("sla_tto_num"),
        pl.col("definition_time").alias("sla_tto_unit"),
    )
    sla_ttr = slas.filter(pl.col("type") == 0).select(
        pl.col("id").alias("sla_ttr_id"), pl.col("number_time").alias("sla_ttr_num"),
        pl.col("definition_time").alias("sla_ttr_unit"),
    )
    agg_solutions = solutions.group_by("items_id").agg(pl.len().alias("nb_solutions")).rename({"items_id": "ticket_id"})
    agg_followups = followups.group_by("items_id").agg(pl.len().alias("nb_followups")).rename({"items_id": "ticket_id"})
    tech_assigned = tickets_users.filter(pl.col("type") == 2).group_by("tickets_id").agg(
        pl.first("users_id").alias("assigned_tech_id")
    ).rename({"tickets_id": "ticket_id"})

    fact = tickets.join(satisfacts.rename({"id": "sat_id"}), left_on="id", right_on="tickets_id", how="left")
    fact = fact.join(sla_tto, left_on="slas_id_tto", right_on="sla_tto_id", how="left")
    fact = fact.join(sla_ttr, left_on="slas_id_ttr", right_on="sla_ttr_id", how="left")
    fact = fact.join(agg_solutions, left_on="id", right_on="ticket_id", how="left")
    fact = fact.join(agg_followups, left_on="id", right_on="ticket_id", how="left")
    fact = fact.join(tech_assigned, left_on="id", right_on="ticket_id", how="left")

    if sentiment_stats.height > 0:
        fact = fact.join(sentiment_stats, left_on="id", right_on="ticket_id", how="left")
    else:
        fact = fact.with_columns(
            pl.lit(None).cast(pl.Float64).alias("sentiment_moyen"),
            pl.lit(None).cast(pl.Float64).alias("sentiment_median"),
            pl.lit(None).cast(pl.Int64).alias("nb_messages"),
        )

    if sentiment_comment_stats.height > 0:
        fact = fact.join(sentiment_comment_stats, left_on="id", right_on="ticket_id", how="left")
    else:
        fact = fact.with_columns(
            pl.lit(None).cast(pl.Float64).alias("sentiment_comment"),
            pl.lit(None).cast(pl.Int64).alias("nb_comment_messages"),
        )

    fact = fact.with_columns(
        pl.col("nb_solutions").fill_null(0),
        pl.col("nb_followups").fill_null(0),
        pl.struct(["sla_tto_num", "sla_tto_unit"]).map_elements(
            lambda r: to_seconds(r["sla_tto_num"], r["sla_tto_unit"]) if r["sla_tto_num"] else None,
            return_dtype=pl.Float64,
        ).alias("sla_tto_sec"),
        pl.struct(["sla_ttr_num", "sla_ttr_unit"]).map_elements(
            lambda r: to_seconds(r["sla_ttr_num"], r["sla_ttr_unit"]) if r["sla_ttr_num"] else None,
            return_dtype=pl.Float64,
        ).alias("sla_ttr_sec"),
    )

    fact = fact.with_columns(
        pl.col("priority").map_elements(lambda p: float(ITIL_TTO_SEC.get(p)) if p else None, return_dtype=pl.Float64).alias("tto_itil"),
        pl.col("priority").map_elements(lambda p: float(ITIL_TTR_SEC.get(p)) if p else None, return_dtype=pl.Float64).alias("ttr_itil"),
    )

    fact = fact.with_columns(
        pl.col("sla_tto_sec").fill_null(pl.col("tto_itil")).alias("tto_seuil_sec"),
        pl.col("sla_ttr_sec").fill_null(pl.col("ttr_itil")).alias("ttr_seuil_sec"),
    )

    fact = fact.with_columns(
        pl.when(pl.col("takeintoaccount_delay_stat").is_not_null() & (pl.col("takeintoaccount_delay_stat") <= pl.col("tto_seuil_sec")))
        .then(True).otherwise(False).alias("tto_ok"),
        pl.when(pl.col("solve_delay_stat").is_not_null() & (pl.col("solve_delay_stat") <= pl.col("ttr_seuil_sec")))
        .then(True).otherwise(False).alias("ttr_ok"),
    )

    fact = fact.with_columns(
        pl.col("priority").map_elements(lambda p: int(PRIORITY_WEIGHT.get(p, 1)) if p else 1, return_dtype=pl.Int32).alias("priority_weight"),
    )

    # ── Score comportemental ─────────────────────────────────────────────────
    pw_range = float(PRIORITY_WEIGHT_MAX - PRIORITY_WEIGHT_MIN)
    fact = fact.with_columns(
        pl.when(pl.col("tto_ok")).then(5.0).otherwise(1.0).alias("pts_tto"),
        pl.when(pl.col("ttr_ok")).then(5.0).otherwise(1.0).alias("pts_ttr"),
        (1.0 + (pl.col("priority_weight").cast(pl.Float64) - PRIORITY_WEIGHT_MIN) / pw_range * 4.0).alias("pts_priority"),
        (5.0 - (pl.col("nb_solutions").cast(pl.Float64).clip(0, CAP_SOLUTIONS)) / CAP_SOLUTIONS * 4.0).alias("pts_solutions"),
        (5.0 - (pl.col("nb_followups").cast(pl.Float64).clip(0, CAP_FOLLOWUPS)) / CAP_FOLLOWUPS * 4.0).alias("pts_followups"),
    )
    fact = fact.with_columns(
        (
            pl.col("pts_tto") * W_TTO
            + pl.col("pts_ttr") * W_TTR
            + pl.col("pts_priority") * W_PRIORITY
            + pl.col("pts_solutions") * W_SOLUTIONS
            + pl.col("pts_followups") * W_FOLLOWUPS
        ).alias("score_comportemental"),
    )

    # ── Score sentiment ──────────────────────────────────────────────────────
    fact = fact.with_columns(
        pl.col("sentiment_moyen").fill_null(SENTIMENT_FALLBACK).alias("score_sentiment"),
    )

    # ── Score composite : si enquête → étoiles client + NLP commentaire uniquement, sinon 50/50 ──
    fact = fact.with_columns(
        pl.col("satisfaction_scaled_to_5").fill_null(pl.col("satisfaction")).alias("etoile_client"),
    )
    fact = fact.with_columns(
        pl.when(pl.col("etoile_client").is_not_null() & pl.col("sentiment_comment").is_not_null())
        .then(
            (pl.col("etoile_client").cast(pl.Float64) + pl.col("sentiment_comment")) / 2.0
        )
        .when(pl.col("etoile_client").is_not_null())
        .then(pl.col("etoile_client").cast(pl.Float64))
        .otherwise(
            (pl.col("score_comportemental") * 0.5) + (pl.col("score_sentiment") * 0.5)
        )
        .round(2).alias("score_composite"),
    )

    fact = fact.with_columns(
        pl.lit("distilcamembert").alias("modele_nlp"),
        pl.lit(datetime.now()).alias("gold_ingestion_date"),
        pl.lit("BUSINESS_READY").alias("gold_processing_status"),
        pl.lit(1).alias("data_version"),
    )

    return fact.select(
        pl.col("id").alias("ticket_id"),
        pl.col("entities_id").alias("entity_id"),
        pl.col("itilcategories_id").alias("category_id"),
        pl.col("status").alias("status_id"),
        pl.col("date_creation").alias("ticket_date"),
        "priority", "priority_weight",
        "tto_ok", "ttr_ok",
        pl.col("nb_solutions").cast(pl.Int64),
        pl.col("nb_followups").cast(pl.Int64),
        pl.col("pts_tto").cast(pl.Float64),
        pl.col("pts_ttr").cast(pl.Float64),
        pl.col("pts_priority").cast(pl.Float64),
        pl.col("pts_solutions").cast(pl.Float64),
        pl.col("pts_followups").cast(pl.Float64),
        pl.col("score_comportemental").cast(pl.Float64),
        pl.col("score_sentiment").cast(pl.Float64),
        pl.col("score_composite").cast(pl.Float64),
        pl.col("etoile_client").cast(pl.Float64),
        pl.col("satisfaction").cast(pl.Float64).alias("satisfaction_score"),
        pl.col("nps_score").cast(pl.Float64),
        "nps_category",
        pl.col("sentiment_moyen").cast(pl.Float64),
        pl.col("sentiment_median").cast(pl.Float64),
        pl.col("nb_messages").cast(pl.Int64),
        pl.col("sentiment_comment").cast(pl.Float64),
        pl.col("nb_comment_messages").cast(pl.Int64),
        "modele_nlp", "gold_ingestion_date", "gold_processing_status", "data_version",
    )


# ============================================================
# POINT D'ENTRÉE PRINCIPAL
# ============================================================

def _read_silver_light(table: str, columns: list[str] | None = None) -> pl.DataFrame:
    try:
        return read_pg(f"silver.{table}").select(columns) if columns else read_pg(f"silver.{table}")
    except Exception:
        return pl.DataFrame()


def build_star_schema(limit: int | None = None):
    print("=== Gold Star Schema — démarrage ===\n")
    if limit:
        print(f"  Mode: TEST — limité à {limit} tickets")

    # 0. Watermark incremental
    wm = get_watermark("gold_tickets")
    is_full = wm is None
    if not limit:
        print(f"  Mode: {'FULL' if is_full else 'INCREMENTAL (depuis ' + wm + ')'}")

    # 1. Déterminer les nouveaux/modifiés via date_mod
    all_tickets = _read_silver_light("glpi_tickets")
    if all_tickets.height == 0:
        print("  Aucun ticket — abandon")
        return
    tickets_full = all_tickets.filter(pl.col("is_deleted") == 0)
    tickets = tickets_full if is_full else tickets_full.filter(pl.col("date_mod") > wm)
    if limit:
        tickets = tickets.head(limit)
    n_tickets = tickets.height
    print(f"  Tickets à traiter: {n_tickets}")
    if n_tickets == 0:
        print("  Aucun nouveau ticket — écriture dimensions uniquement")
    ticket_ids = tickets["id"].to_list() if n_tickets > 0 else []

    # 2. Dimensions (écriture immédiate + libération mémoire) — toujours full
    entities = _read_silver_light("glpi_entities", ["id", "name", "completename", "level", "entities_id", "town", "country", "comment"])
    write_gold(build_dim_entity(entities), "dim_entity")
    print(f"  dim_entity: {entities.height} lignes")
    del entities; gc.collect()

    slas = _read_silver_light("glpi_slas", ["id", "name", "type", "number_time", "definition_time", "end_of_working_day", "use_ticket_calendar", "entities_id"])
    write_gold(build_dim_sla(slas), "dim_sla")
    print(f"  dim_sla: {slas.height} lignes")
    del slas; gc.collect()

    users = _read_silver_light("glpi_users", ["id", "name", "firstname", "realname", "entities_id", "locations_id", "is_active", "language"])
    write_gold(build_dim_user(users), "dim_user")
    print(f"  dim_user: {users.height} lignes")
    del users; gc.collect()

    write_gold(build_dim_status(), "dim_status")
    print("  dim_status: 6 lignes")

    categories = _read_silver_light("glpi_itilcategories", ["id", "name", "completename", "level", "itilcategories_id", "entities_id", "is_incident", "is_request"])
    write_gold(build_dim_category(categories), "dim_category")
    print(f"  dim_category: {categories.height} lignes")
    del categories; gc.collect()

    if n_tickets == 0:
        print("=== Gold Star Schema — terminé (aucun nouveau ticket) ===")
        return

    # 3. NLP — uniquement sur les tickets modifiés
    print("\n  Analyse NLP sur tickets + solutions + followups...")
    tickets_light = tickets.select("id", "clean_content").filter(pl.col("clean_content").is_not_null() & (pl.col("clean_content") != ""))
    solutions_light = _read_silver_light("glpi_itilsolutions", ["items_id", "clean_content", "itemtype"]).filter(
        pl.col("itemtype") == "Ticket", pl.col("items_id").is_in(ticket_ids)
    )
    followups_light = _read_silver_light("glpi_itilfollowups", ["items_id", "clean_content", "itemtype"]).filter(
        pl.col("itemtype") == "Ticket", pl.col("items_id").is_in(ticket_ids)
    )
    sentiment_stats = compute_all_sentiment(tickets_light, solutions_light, followups_light)
    del tickets_light, solutions_light, followups_light; gc.collect()

    print("  Analyse NLP sur commentaires enquete...")
    satisfacts_comment = _read_silver_light("glpi_ticketsatisfactions", ["tickets_id", "clean_comment"]).filter(
        pl.col("tickets_id").is_in(ticket_ids)
    )
    sentiment_comment_stats = compute_survey_comment_sentiment(satisfacts_comment)
    del satisfacts_comment; gc.collect()

    # 4. Fait — charger uniquement les colonnes nécessaires pour les tickets modifiés
    slas = _read_silver_light("glpi_slas")
    satisfacts = _read_silver_light("glpi_ticketsatisfactions").filter(pl.col("tickets_id").is_in(ticket_ids))
    solutions = _read_silver_light("glpi_itilsolutions").filter(pl.col("itemtype") == "Ticket", pl.col("items_id").is_in(ticket_ids))
    followups = _read_silver_light("glpi_itilfollowups").filter(pl.col("itemtype") == "Ticket", pl.col("items_id").is_in(ticket_ids))
    tickets_users = _read_silver_light("glpi_tickets_users").filter(pl.col("tickets_id").is_in(ticket_ids))

    date_dim = build_dim_date(tickets)
    write_gold(date_dim, "dim_date")
    print(f"  dim_date: {date_dim.height} lignes")
    del date_dim; gc.collect()

    # 5. Watermark AVANT de supprimer tickets
    max_dm = tickets.select(pl.max("date_mod")).item()

    fact = build_fact_ticket_satisfaction(
        tickets, satisfacts, slas,
        solutions, followups, tickets_users,
        sentiment_stats, sentiment_comment_stats,
    )
    del tickets, satisfacts, slas, solutions, followups, tickets_users
    del sentiment_stats, sentiment_comment_stats
    gc.collect()

    # 6. Upsert (remplace les lignes existantes de ces tickets)
    if fact.height > 0:
        upsert_pg(fact, "gold.fact_ticket_satisfaction", pk_col="ticket_id")
        print(f"\n  fact_ticket_satisfaction: {fact.height} lignes upserted")
    del fact; gc.collect()

    # 7. Sauvegarder le watermark
    if max_dm:
        save_watermark("gold_tickets", str(max_dm))
        print(f"  Watermark gold_tickets: {max_dm}")

    print("\n=== Gold Star Schema — terminé ===")


if __name__ == "__main__":
    build_star_schema()
