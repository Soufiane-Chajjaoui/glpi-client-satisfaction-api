from fastapi import APIRouter, Query
from api.db import query

router = APIRouter(prefix="/api/v1/alerts", tags=["alerts"])


@router.get("/critical-tickets", summary="Tickets critiques", description="Liste des tickets critiques avec détails SLA, priorité, temps restant TTR. Filtrable par entité, catégorie et priorité.")
def get_critical_tickets(
    limit: int = Query(100, le=1000, description="Nombre max de lignes"),
    offset: int = Query(0, description="Décalage pour pagination"),
    entity_id: int = Query(0, description="Filtrer par entité"),
    category_id: int = Query(0, description="Filtrer par catégorie"),
    priority: int = Query(0, description="Filtrer par priorité"),
):
    try:
        sql = "SELECT c.* FROM gold_alert.critical_tickets c"
        joins = ""
        where = ["c.est_critique = 1"]
        if entity_id > 0:
            where.append(f"c.entities_id = {entity_id}")
        if priority > 0:
            where.append(f"c.priority = {priority}")
        if category_id > 0:
            joins = " JOIN gold.fact_ticket_satisfaction f ON c.ticket_id = f.ticket_id"
            where.append(f"f.category_id = {category_id}")
        sql += joins + " WHERE " + " AND ".join(where) + f" ORDER BY c.date_alerte DESC LIMIT {limit} OFFSET {offset}"
        return query(sql)
    except Exception:
        return []


@router.get("/critical-tickets/count", summary="Compteur tickets critiques", description="Nombre total de tickets critiques actifs.")
def count_critical_tickets():
    rows = query("SELECT COUNT(*) AS cnt FROM gold_alert.critical_tickets WHERE est_critique = 1")
    return {"count": rows[0]["cnt"]}
