from fastapi import APIRouter, Query
from api.db import query

satisfaction_router = APIRouter(prefix="/api/v1/satisfaction", tags=["satisfaction"])


@satisfaction_router.get("/summary", summary="Résumé satisfaction", description="KPIs satisfaction : taux de réponse, NPS, score NLP moyen.")
def get_satisfaction_summary():
    return query("""
        SELECT
            COUNT(*) FILTER (WHERE satisfaction_score IS NOT NULL)           AS nb_responded,
            ROUND(AVG(satisfaction_score)::numeric, 2)                       AS avg_satisfaction,
            ROUND(AVG(nps_score)::numeric, 2)                                AS avg_nps,
            COUNT(*) FILTER (WHERE nps_category = 'promoter')                AS promoters,
            COUNT(*) FILTER (WHERE nps_category = 'passive')                 AS passives,
            COUNT(*) FILTER (WHERE nps_category = 'detractor')               AS detractors,
            ROUND(
                100.0 * (COUNT(*) FILTER (WHERE nps_category = 'promoter')
                       - COUNT(*) FILTER (WHERE nps_category = 'detractor'))
                / NULLIF(COUNT(*) FILTER (WHERE nps_category IS NOT NULL), 0), 1
            )                                                                AS nps_score_final,
            ROUND(AVG(score_sentiment)::numeric, 2)                          AS avg_nlp_sentiment,
            COUNT(*) FILTER (WHERE score_sentiment IS NOT NULL)              AS nb_analyzed_nlp
        FROM gold.fact_ticket_satisfaction
    """)


@satisfaction_router.get("/distribution", summary="Distribution satisfaction", description="Répartition des notes de satisfaction (score 1-5).")
def get_satisfaction_distribution(
    entity_id: int = Query(None, description="Filtrer par entité"),
):
    cond = []
    params = {}
    if entity_id is not None:
        cond.append("entity_id = :e"); params["e"] = entity_id
    where = "WHERE " + " AND ".join(cond) if cond else ""
    return query(f"""
        SELECT satisfaction_score, COUNT(*) AS nb_tickets
        FROM gold.fact_ticket_satisfaction
        {where}
        GROUP BY satisfaction_score
        ORDER BY satisfaction_score
    """, params)


@satisfaction_router.get("/nps-distribution", summary="Distribution NPS", description="Répartition promoteurs/passifs/détracteurs.")
def get_nps_distribution(
    entity_id: int = Query(None, description="Filtrer par entité"),
):
    cond = []
    params = {}
    if entity_id is not None:
        cond.append("entity_id = :e"); params["e"] = entity_id
    where = "WHERE " + " AND ".join(cond) if cond else ""
    return query(f"""
        SELECT nps_category, COUNT(*) AS nb_tickets
        FROM gold.fact_ticket_satisfaction
        {where}
        GROUP BY nps_category
        ORDER BY nps_category
    """, params)


@satisfaction_router.get("/daily", summary="Évolution quotidienne", description="Moyenne satisfaction, NPS et score composite par jour. Filtrable par période et entité.")
def get_satisfaction_daily(
    entity_id: int = Query(None, description="Filtrer par entité"),
    days: int = Query(0, description="Nombre de jours remontant (0 = toutes les dates)"),
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


@satisfaction_router.get("/tickets", summary="Détail des tickets satisfaction", description="Liste paginée des tickets avec scores satisfaction, NPS, sentiment, score composite. Filtrable par entité, score et catégorie NPS.")
def get_satisfaction_tickets(
    entity_id: int = Query(None, description="Filtrer par entité"),
    min_score: int = Query(None, description="Score composite minimum"),
    max_score: int = Query(None, description="Score composite maximum"),
    nps_category: str = Query(None, description="Catégorie NPS (promoter/passive/detractor)"),
    limit: int = Query(50, le=1000, description="Nombre max de lignes"),
    offset: int = Query(0, description="Décalage pour pagination"),
):
    cond = []
    params = {}
    if entity_id is not None:
        cond.append("entity_id = :e"); params["e"] = entity_id
    if min_score is not None:
        cond.append("score_composite >= :min"); params["min"] = min_score
    if max_score is not None:
        cond.append("score_composite <= :max"); params["max"] = max_score
    if nps_category:
        cond.append("nps_category = :n"); params["n"] = nps_category
    where = "WHERE " + " AND ".join(cond) if cond else ""
    return query(f"""
        SELECT ticket_id, entity_id, status_id, priority,
               satisfaction_score, nps_score, nps_category,
               score_sentiment AS sentiment_moyen,
               score_composite AS note_sentiment,
               ticket_date
        FROM gold.fact_ticket_satisfaction
        {where}
        ORDER BY ticket_id DESC
        LIMIT {limit} OFFSET {offset}
    """, params)


dashboard_router = APIRouter(prefix="/api/v1/dashboard", tags=["dashboard"])


@dashboard_router.get("/overview", summary="Vue d'ensemble dashboard", description="Agrégation complète pour le dashboard : KPIs, niveaux de risque, distribution sentiment. Utilisé par le plugin Satisfaction AI.")
def get_dashboard_overview(
    entity_id: int = Query(None, description="Filtrer par entité"),
    category_id: int = Query(None, description="Filtrer par catégorie"),
    priority: int = Query(None, description="Filtrer par priorité"),
):
    fcond = []
    fparams = {}
    if entity_id is not None:
        fcond.append("entity_id = :e"); fparams["e"] = entity_id
    if category_id is not None:
        fcond.append("category_id = :c"); fparams["c"] = category_id
    if priority is not None:
        fcond.append("priority = :p"); fparams["p"] = priority
    fwhere = "WHERE " + " AND ".join(fcond) if fcond else ""

    row = query(f"""
        SELECT
            COUNT(*)                                                                  AS total_tickets,
            COUNT(*) FILTER (WHERE satisfaction_score IS NOT NULL)                    AS nb_responded,
            ROUND(AVG(satisfaction_score)::numeric, 2)                                AS avg_satisfaction,
            COUNT(*) FILTER (WHERE score_sentiment IS NOT NULL)                       AS nb_nlp,
            ROUND(AVG(score_sentiment)::numeric, 2)                                   AS avg_nlp,
            ROUND(AVG(score_composite)::numeric, 2)                                   AS avg_note_sentiment,
            COUNT(*) FILTER (WHERE nps_category = 'promoter')                         AS promoters,
            COUNT(*) FILTER (WHERE nps_category = 'passive')                          AS passives,
            COUNT(*) FILTER (WHERE nps_category = 'detractor')                        AS detractors,
            COUNT(*) FILTER (WHERE nps_category IS NOT NULL)                          AS nps_total
        FROM gold.fact_ticket_satisfaction
        {fwhere}
    """, fparams)[0]

    total        = row["total_tickets"] or 0
    nb_responded = row["nb_responded"] or 0
    avg_sat      = row["avg_note_sentiment"] or 0.0
    nb_nlp       = row["nb_nlp"] or 0
    promoters    = row["promoters"] or 0
    detractors   = row["detractors"] or 0
    nps_total    = row["nps_total"] or 0

    csat = round(float(avg_sat) * 20, 1) if avg_sat else 0.0
    nps = round(100.0 * (promoters - detractors) / nps_total, 1) if nps_total > 0 else 0
    response_rate = round(100.0 * nb_responded / total, 1) if total > 0 else 0

    risk_where = "WHERE sentiment_moyen IS NOT NULL" + (" AND " + " AND ".join(fcond) if fcond else "")
    risk_rows = query(f"""
        SELECT
            COUNT(*) FILTER (WHERE sentiment_moyen < 2.0)  AS high,
            COUNT(*) FILTER (WHERE sentiment_moyen >= 2.0 AND sentiment_moyen < 3.0) AS moderate,
            COUNT(*) FILTER (WHERE sentiment_moyen >= 3.0) AS low
        FROM gold.fact_ticket_satisfaction
        {risk_where}
    """, fparams)[0]

    try:
        q = "SELECT COUNT(*) AS cnt FROM gold_alert.critical_tickets WHERE est_critique = 1"
        if entity_id is not None:
            q += " AND ticket_id IN (SELECT ticket_id FROM gold.fact_ticket_satisfaction WHERE entity_id = :e)"
            critical = query(q, {"e": entity_id})[0]["cnt"] or 0
        else:
            critical = query(q)[0]["cnt"] or 0
    except Exception:
        critical = 0

    risk_levels = {
        "critical":  int(critical),
        "high":      int(risk_rows["high"] or 0),
        "moderate":  int(risk_rows["moderate"] or 0),
        "low":       int(risk_rows["low"] or 0),
    }

    sent_where = "WHERE sentiment_moyen IS NOT NULL" + (" AND " + " AND ".join(fcond) if fcond else "")
    sent_rows = query(f"""
        SELECT
            COUNT(*) FILTER (WHERE sentiment_moyen >= 4.0) AS positive,
            COUNT(*) FILTER (WHERE sentiment_moyen >= 3.0 AND sentiment_moyen < 4.0) AS neutral,
            COUNT(*) FILTER (WHERE sentiment_moyen < 3.0) AS negative
        FROM gold.fact_ticket_satisfaction
        {sent_where}
    """, fparams)[0]

    try:
        last_ingest = query("SELECT MAX(updated_at) AS dt FROM bronze.ingestion_watermarks")[0]["dt"]
        last_ingest_str = last_ingest.strftime("%d/%m/%Y %H:%M") if last_ingest else "N/A"
    except Exception:
        last_ingest_str = "N/A"

    return {
        "success": True,
        "data": {
            "kpis": {
                "csat":                   csat,
                "total_tickets_analyzed": nb_nlp,
                "nps":                    nps,
                "response_rate_percent":  response_rate,
            },
            "risk_levels": risk_levels,
            "sentiment_distribution": {
                "positive": int(sent_rows["positive"] or 0),
                "neutral":  int(sent_rows["neutral"] or 0),
                "negative": int(sent_rows["negative"] or 0),
            },
            "source": last_ingest_str,
        }
    }
