"""
GLPI Light REST API — serves Gold layer analytical data to GLPI.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from contextlib import asynccontextmanager
from dotenv import load_dotenv

from api.v1.endpoints import dimensions, facts, alerts, stats, analytics
from api.v1.endpoints.facts import agg_router
from api.db import get_engine, query

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

API_CLIENT_ID = os.getenv("API_CLIENT_ID", "satisfactionai")
API_CLIENT_SECRET = os.getenv("API_CLIENT_SECRET", "")


@asynccontextmanager
async def lifespan(app: FastAPI):
    get_engine()
    yield


app = FastAPI(
    title="GLPI Light Analytics API",
    description="REST API for GLPI analytical data (medallion architecture: bronze \u2192 silver \u2192 gold)",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    path = request.url.path
    if path in ("/health", "/openapi.json") or path.startswith("/docs") or path.startswith("/redoc") or request.method == "OPTIONS":
        return await call_next(request)
    cid = request.headers.get("x-client-id")
    csec = request.headers.get("x-client-secret")
    if cid != API_CLIENT_ID or csec != API_CLIENT_SECRET:
        return JSONResponse(status_code=401, content={"detail": "Invalid credentials"})
    return await call_next(request)


@app.get("/health")
def health():
    try:
        query("SELECT 1")
        return {"status": "ok", "database": "connected"}
    except Exception as e:
        raise HTTPException(500, f"Database error: {e}")


app.include_router(dimensions.router)
app.include_router(facts.router)
app.include_router(agg_router)
app.include_router(alerts.router)
app.include_router(stats.router)
app.include_router(analytics.satisfaction_router)
app.include_router(analytics.dashboard_router)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api.main:app", host="0.0.0.0", port=8000, reload=True)
