from fastapi import APIRouter, Query
from api.db import query

router = APIRouter(prefix="/api/v1/facts", tags=["facts"])


@router.get("/ticket-satisfaction", summary="Lignes fact table", description="Données brutes de la table de faits satisfaction. Paginée, filtrable par ticket, entité, statut, priorité et score composite.")
def get_facts(
    ticket_id: int = Query(None, description="Filtrer par ID ticket"),
    entity_id: int = Query(None, description="Filtrer par entité"),
    status_id: int = Query(None, description="Filtrer par statut"),
    priority: int = Query(None, description="Filtrer par priorité"),
    min_score: float = Query(None, description="Score composite minimum"),
    max_score: float = Query(None, description="Score composite maximum"),
    limit: int = Query(100, le=5000, description="Nombre max de lignes"),
    offset: int = Query(0, description="Décalage pour pagination"),
):
    cond = []
    params = {}
    if ticket_id is not None:
        cond.append("ticket_id = :tid"); params["tid"] = ticket_id
    if entity_id is not None:
        cond.append("entity_id = :e"); params["e"] = entity_id
    if status_id is not None:
        cond.append("status_id = :s"); params["s"] = status_id
    if priority is not None:
        cond.append("priority = :p"); params["p"] = priority
    if min_score is not None:
        cond.append("score_composite >= :min"); params["min"] = min_score
    if max_score is not None:
        cond.append("score_composite <= :max"); params["max"] = max_score
    where = "WHERE " + " AND ".join(cond) if cond else ""
    return query(
        f"SELECT * FROM gold.fact_ticket_satisfaction {where} ORDER BY ticket_id DESC LIMIT {limit} OFFSET {offset}",
        params,
    )


agg_router = APIRouter(prefix="/api/v1/aggregations", tags=["aggregations"])


@agg_router.get("/satisfaction-daily", summary="Aggrégation quotidienne", description="Moyennes quotidiennes du score composite, satisfaction et NPS.")
def get_agg_daily(
    entity_id: int = Query(None, description="Filtrer par entité"),
    days: int = Query(90, description="Nombre de jours remontant"),
):
    cond = []
    params = {}
    if days > 0:
        cond.append("(ticket_date)::timestamp >= NOW() - INTERVAL '1 day' * :days")
        params["days"] = days
    if entity_id is not None:
        cond.append("entity_id = :e"); params["e"] = entity_id
    where = "WHERE " + " AND ".join(cond)
    return query(f"""
        SELECT (ticket_date)::date AS day,
               COUNT(*) AS ticket_count,
               ROUND(AVG(score_composite)::numeric, 2) AS avg_note_sentiment,
               ROUND(AVG(satisfaction_score)::numeric, 2) AS avg_satisfaction,
               ROUND(AVG(nps_score)::numeric, 2) AS avg_nps
        FROM gold.fact_ticket_satisfaction
        {where}
        GROUP BY DATE(ticket_date)
        ORDER BY day DESC
    """, params)
