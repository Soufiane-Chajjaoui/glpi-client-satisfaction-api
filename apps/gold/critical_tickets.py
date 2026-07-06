"""
Critical tickets detection — Silver → gold_alert.critical_tickets
Identifies tickets that are open, untouched, and approaching/exceeding TTR.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

import polars as pl
from datetime import datetime
from lib.polars_helpers import read_pg, write_pg

ITIL_TTR_HOURS = {1: 4, 2: 24, 3: 72, 4: 120, 5: 360}


def detect_critical():
    tickets = read_pg("silver.glpi_tickets").filter(pl.col("is_deleted") == 0)
    entities = read_pg("silver.glpi_entities").select(
        pl.col("id").alias("entity_id"), pl.col("name").alias("client_nom")
    )
    slas = read_pg("silver.glpi_slas").filter(pl.col("type") == 0).select(
        pl.col("id").alias("sla_id"), "name", "number_time", "definition_time"
    )
    solutions = read_pg("silver.glpi_itilsolutions").filter(pl.col("itemtype") == "Ticket").select(
        pl.col("items_id").alias("ticket_id")
    )
    followups = read_pg("silver.glpi_itilfollowups").filter(pl.col("itemtype") == "Ticket").select(
        pl.col("items_id").alias("ticket_id")
    )
    tasks = read_pg("silver.glpi_tickettasks").select(
        pl.col("tickets_id").alias("ticket_id")
    )

    agg_sol = solutions.group_by("ticket_id").agg(pl.len().alias("nb_solutions"))
    agg_fup = followups.group_by("ticket_id").agg(pl.len().alias("nb_followups"))
    agg_tasks = tasks.group_by("ticket_id").agg(pl.len().alias("nb_tasks"))

    df = (
        tickets
        .join(entities, left_on="entities_id", right_on="entity_id", how="left")
        .join(slas, left_on="slas_id_ttr", right_on="sla_id", how="left")
        .join(agg_sol, left_on="id", right_on="ticket_id", how="left")
        .join(agg_fup, left_on="id", right_on="ticket_id", how="left")
        .join(agg_tasks, left_on="id", right_on="ticket_id", how="left")
        .with_columns(
            pl.col("nb_solutions").fill_null(0),
            pl.col("nb_followups").fill_null(0),
            pl.col("nb_tasks").fill_null(0),
        )
    )

    df = df.with_columns(
        pl.when(pl.col("definition_time") == "minute").then(pl.col("number_time") / 60)
        .when(pl.col("definition_time") == "hour").then(pl.col("number_time"))
        .when(pl.col("definition_time") == "day").then(pl.col("number_time") * 24)
        .otherwise(None).alias("ttr_heures_sla"),
    )

    df = df.with_columns(
        pl.col("ttr_heures_sla").fill_null(
            pl.col("priority").map_elements(lambda p: float(ITIL_TTR_HOURS.get(p, 360)), return_dtype=pl.Float64)
        ).alias("ttr_heures"),
    )

    now = datetime.now()
    df = df.with_columns(
        pl.col("date_creation").str.to_datetime().alias("date_creation_dt"),
    )
    df = df.with_columns(
        ((pl.lit(now) - pl.col("date_creation_dt")).dt.total_hours()).alias("age_heures"),
    )

    critical = df.filter(
        (pl.col("age_heures") >= pl.col("ttr_heures") - 24)
        & (pl.col("status") == 2)
        & (pl.col("nb_solutions") == 0)
        & (pl.col("nb_followups") == 0)
        & (pl.col("nb_tasks") == 0)
    ).select(
        pl.col("id").alias("ticket_id"),
        "entities_id", "client_nom",
        pl.col("name").alias("titre"),
        "date_creation", "status", "priority",
        pl.col("name_right").alias("sla_name"), "ttr_heures",
        "nb_followups", "nb_tasks", "nb_solutions",
        (pl.col("nb_followups") + pl.col("nb_tasks") + pl.col("nb_solutions")).alias("nb_actions_total"),
        pl.col("age_heures").round(1),
        (pl.col("ttr_heures") - pl.col("age_heures")).round(1).alias("heures_restantes_ttr"),
        pl.lit(1).alias("est_critique"),
        pl.lit(now).alias("date_alerte"),
    )

    print(f"Critical tickets found: {critical.height}")

    if critical.height > 0:
        write_pg(critical, "gold_alert.critical_tickets", if_exists="replace")
        print("gold_alert.critical_tickets updated.")
    else:
        print("No critical tickets detected.")


if __name__ == "__main__":
    detect_critical()
