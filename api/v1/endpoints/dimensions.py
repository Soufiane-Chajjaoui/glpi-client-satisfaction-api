from fastapi import APIRouter, Query, HTTPException
from api.db import query

router = APIRouter(prefix="/api/v1/dimensions", tags=["dimensions"])


@router.get("/entities", summary="Liste des entités", description="Retourne toutes les entités GLPI triées par nom.")
def get_entities():
    return query("SELECT * FROM gold.dim_entity ORDER BY entity_name")


@router.get("/entities/{entity_id}", summary="Détail d'une entité", description="Retourne une entité par son ID.")
def get_entity(entity_id: int):
    rows = query("SELECT * FROM gold.dim_entity WHERE entity_id = :id", {"id": entity_id})
    if not rows:
        raise HTTPException(404, "Entity not found")
    return rows[0]


@router.get("/slas", summary="Liste des SLA", description="Retourne tous les SLA triés par nom.")
def get_slas():
    return query("SELECT * FROM gold.dim_sla ORDER BY sla_name")


@router.get("/users", summary="Liste des utilisateurs", description="Retourne les utilisateurs, optionnellement filtrés par is_active.")
def get_users(active: bool = Query(None, description="Filtrer par statut actif/inactif")):
    if active is not None:
        return query("SELECT * FROM gold.dim_user WHERE is_active = :a ORDER BY username", {"a": active})
    return query("SELECT * FROM gold.dim_user ORDER BY username")


@router.get("/categories", summary="Liste des catégories", description="Retourne toutes les catégories ITIL triées par nom.")
def get_categories():
    return query("SELECT * FROM gold.dim_category ORDER BY category_name")


@router.get("/status", summary="Liste des statuts", description="Retourne tous les statuts de ticket.")
def get_statuses():
    return query("SELECT * FROM gold.dim_status ORDER BY status_id")


@router.get("/date", summary="Calendrier", description="Retourne les dates du calendrier avec filtres année/mois et pagination.")
def get_dates(
    year: int = Query(None, description="Filtrer par année"),
    month: int = Query(None, description="Filtrer par mois"),
    limit: int = Query(100, le=1000, description="Nombre max de lignes"),
    offset: int = Query(0, description="Décalage pour pagination"),
):
    cond = []
    params = {}
    if year:
        cond.append("year = :y"); params["y"] = year
    if month:
        cond.append("month = :m"); params["m"] = month
    where = "WHERE " + " AND ".join(cond) if cond else ""
    return query(
        f"SELECT * FROM gold.dim_date {where} ORDER BY date_val LIMIT {limit} OFFSET {offset}",
        params,
    )
