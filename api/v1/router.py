"""API v1 router."""
from fastapi import APIRouter
from api.v1.endpoints import dimensions, facts, alerts, stats, analytics

router = APIRouter(prefix="/api/v1")
router.include_router(dimensions.router)
router.include_router(facts.router)
router.include_router(alerts.router)
router.include_router(stats.router)
router.include_router(analytics.satisfaction_router)
router.include_router(analytics.dashboard_router)
