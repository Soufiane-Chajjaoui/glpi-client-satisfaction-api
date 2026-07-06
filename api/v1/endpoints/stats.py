from fastapi import APIRouter, Query
from api.db import query

router = APIRouter(prefix="/api/v1/stats", tags=["stats"])


@router.get("/summary", summary="Résumé global", description="KPI généraux : total tickets, ouverts/fermés, satisfaction, NPS, conformité SLA.")
def get_summary():
    return query("""
        SELECT
            COUNT(*)                                                           AS total_tickets,
            COUNT(*) FILTER (WHERE status_id = 5 OR status_id = 6)              AS closed_tickets,
            COUNT(*) FILTER (WHERE status_id = 2)                               AS open_tickets,
            ROUND(AVG(satisfaction_score)::numeric, 2)                          AS avg_satisfaction,
            ROUND(AVG(nps_score)::numeric, 2)                                   AS avg_nps,
            COUNT(*) FILTER (WHERE sentiment_moyen IS NOT NULL)                 AS scored_by_nlp,
            COUNT(*) FILTER (WHERE satisfaction_score IS NOT NULL)              AS scored_by_survey,
            COUNT(*) FILTER (WHERE nps_category = 'promoter')                   AS promoters,
            COUNT(*) FILTER (WHERE nps_category = 'detractor')                  AS detractors,
            COUNT(*) FILTER (WHERE tto_ok = TRUE)                               AS tto_compliant,
            COUNT(*) FILTER (WHERE ttr_ok = TRUE)                               AS ttr_compliant
        FROM gold.fact_ticket_satisfaction
    """)


@router.get("/by-entity", summary="Stats par entité", description="Nombre de tickets et score moyen par entité.")
def get_stats_by_entity():
    return query("""
        SELECT e.entity_name, e.entity_id,
               COUNT(*) AS ticket_count,
               ROUND(AVG(f.score_composite)::numeric, 2) AS avg_score
        FROM gold.fact_ticket_satisfaction f
        JOIN gold.dim_entity e ON f.entity_id = e.entity_id
        GROUP BY e.entity_id, e.entity_name
        ORDER BY ticket_count DESC
    """)


@router.get("/by-entity-satisfaction", summary="Satisfaction par entité", description="Score sentiment, conformité TTR, répartition positif/neutre/négatif par entité. Paginée et filtrable.")
def get_stats_by_entity_satisfaction(
    name: str = Query("", description="Filtrer par nom client"),
    tickets_min: int = Query(0, description="Nombre de tickets minimum"),
    tickets_max: int = Query(999999, description="Nombre de tickets maximum"),
    sat_min: float = Query(0, description="Score satisfaction minimum"),
    sat_max: float = Query(5, description="Score satisfaction maximum"),
    sla_min: int = Query(0, description="Conformité SLA minimum (%)"),
    sla_max: int = Query(100, description="Conformité SLA maximum (%)"),
    limit: int = Query(10, le=100, description="Nombre de lignes par page"),
    offset: int = Query(0, description="Décalage pour pagination"),
):
    cond = ["1=1"]
    params = {}
    if name:
        cond.append("(e.entity_comment ILIKE :name OR e.entity_name ILIKE :name)")
        params["name"] = f"%{name}%"

    where = "WHERE " + " AND ".join(cond)
    having = []
    if tickets_min > 0:
        having.append("COUNT(*) >= :tmin"); params["tmin"] = tickets_min
    if tickets_max < 999999:
        having.append("COUNT(*) <= :tmax"); params["tmax"] = tickets_max
    if sat_min > 0:
        having.append("AVG(f.score_composite) >= :smin"); params["smin"] = sat_min
    if sat_max < 5:
        having.append("AVG(f.score_composite) <= :smax"); params["smax"] = sat_max
    if sla_min > 0:
        having.append("(100.0 * COUNT(*) FILTER (WHERE f.ttr_ok = TRUE) / NULLIF(COUNT(*), 0)) >= :slamin")
        params["slamin"] = sla_min
    if sla_max < 100:
        having.append("(100.0 * COUNT(*) FILTER (WHERE f.ttr_ok = TRUE) / NULLIF(COUNT(*), 0)) <= :slamax")
        params["slamax"] = sla_max
    having_clause = "HAVING " + " AND ".join(having) if having else ""

    base = f"""
        SELECT
            e.entity_id, e.entity_name, e.entity_completename, e.entity_comment,
            COUNT(*)                                                               AS ticket_count,
            ROUND(AVG(f.score_composite)::numeric, 2)                               AS avg_note_sentiment,
            ROUND(AVG(f.score_sentiment)::numeric, 2)                              AS avg_sentiment,
            COUNT(*) FILTER (WHERE f.ttr_ok = TRUE)                                AS ttr_ok_count,
            ROUND(100.0 * COUNT(*) FILTER (WHERE f.ttr_ok = TRUE)
                / NULLIF(COUNT(*), 0), 1)                                          AS ttr_compliance_pct,
            COUNT(*) FILTER (WHERE f.score_sentiment >= 4.0)                       AS positive_count,
            COUNT(*) FILTER (WHERE f.score_sentiment >= 3.0 AND f.score_sentiment < 4.0) AS neutral_count,
            COUNT(*) FILTER (WHERE f.score_sentiment < 3.0)                        AS negative_count
        FROM gold.fact_ticket_satisfaction f
        JOIN gold.dim_entity e ON f.entity_id = e.entity_id
        {where}
        GROUP BY e.entity_id, e.entity_name, e.entity_completename, e.entity_comment
        {having_clause}
    """
    total = query(f"SELECT COUNT(*) AS cnt FROM ({base}) sub", params)[0]["cnt"]

    rows = query(f"""
        {base}
        ORDER BY ticket_count DESC
        LIMIT {limit} OFFSET {offset}
    """, params)
    return {"total": total, "data": rows, "page": offset // limit + 1, "per_page": limit}


@router.get("/entity-detail/{entity_id}", summary="Détail d'une entité", description="Stats complètes pour une entité : tickets, NPS, sentiments, enquêtes, conformité TTO/TTR.")
def get_entity_detail(entity_id: int):
    rows = query("""
        SELECT
            e.entity_name,
            e.entity_completename,
            e.entity_comment,
            COUNT(*)                                                               AS ticket_count,
            COUNT(*) FILTER (WHERE f.ttr_ok = TRUE)                                AS ttr_ok_count,
            COUNT(*) FILTER (WHERE f.tto_ok = TRUE)                                AS tto_ok_count,
            ROUND(AVG(f.score_composite)::numeric, 2)                               AS avg_note_sentiment,
            ROUND(AVG(f.score_sentiment)::numeric, 2)                              AS avg_sentiment,
            ROUND(AVG(f.satisfaction_score)::numeric, 2)                           AS avg_survey_score,
            COUNT(*) FILTER (WHERE f.satisfaction_score IS NOT NULL)                AS survey_count,
            COUNT(*) FILTER (WHERE f.score_sentiment >= 4.0)                       AS positive_count,
            COUNT(*) FILTER (WHERE f.score_sentiment >= 3.0 AND f.score_sentiment < 4.0) AS neutral_count,
            COUNT(*) FILTER (WHERE f.score_sentiment < 3.0)                        AS negative_count,
            ROUND(AVG(f.nps_score)::numeric, 2)                                    AS avg_nps,
            COUNT(*) FILTER (WHERE f.nps_category = 'promoter')                    AS promoters,
            COUNT(*) FILTER (WHERE f.nps_category = 'detractor')                   AS detractors
        FROM gold.fact_ticket_satisfaction f
        JOIN gold.dim_entity e ON f.entity_id = e.entity_id
        WHERE f.entity_id = :e
        GROUP BY e.entity_name, e.entity_completename, e.entity_comment
    """, {"e": entity_id})
    if not rows:
        return {"detail": "Entity not found"}
    return rows[0]


@router.get("/by-category", summary="Stats par catégorie", description="Nombre de tickets et score moyen par catégorie ITIL. Filtrable par entité et priorité.")
def get_stats_by_category(
    entity_id: int = Query(None, description="Filtrer par entité"),
    priority: int = Query(None, description="Filtrer par priorité"),
):
    cond = []
    params = {}
    if entity_id is not None:
        cond.append("f.entity_id = :e"); params["e"] = entity_id
    if priority is not None:
        cond.append("f.priority = :p"); params["p"] = priority
    where = "WHERE " + " AND ".join(cond) if cond else ""
    return query(f"""
        SELECT c.category_name, f.category_id,
               COUNT(*) AS ticket_count,
               ROUND(AVG(f.score_composite)::numeric, 2) AS avg_score,
               ROUND(AVG(f.score_sentiment)::numeric, 2) AS avg_sentiment
        FROM gold.fact_ticket_satisfaction f
        JOIN gold.dim_category c ON f.category_id = c.category_id
        {where}
        GROUP BY c.category_name, f.category_id
        ORDER BY ticket_count DESC
    """, params)


@router.get("/by-priority", summary="Stats par priorité", description="Nombre de tickets et score moyen par niveau de priorité.")
def get_stats_by_priority():
    return query("""
        SELECT priority,
               COUNT(*) AS ticket_count,
               ROUND(AVG(score_composite)::numeric, 2) AS avg_score
        FROM gold.fact_ticket_satisfaction
        GROUP BY priority
        ORDER BY priority
    """)


@router.get("/by-status", summary="Stats par statut", description="Nombre de tickets et score moyen par statut (nouveau, en cours, résolu, fermé…).")
def get_stats_by_status():
    return query("""
        SELECT s.status_label, f.status_id,
               COUNT(*) AS ticket_count,
               ROUND(AVG(f.score_composite)::numeric, 2) AS avg_score
        FROM gold.fact_ticket_satisfaction f
        JOIN gold.dim_status s ON f.status_id = s.status_id
        GROUP BY s.status_label, f.status_id
        ORDER BY f.status_id
    """)
