"""Agentic Fleet Management API.

Telemetry from several vendors, unified onto COVESA VSS, validated against the
spec, and reported on. The pipeline runs without a database or AI credentials so
that a fleet operator can point it at a historical extract on day one.
"""

import logging
import threading
import time
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from db.mdb import close_mongo_client, get_mongo_client
from routes import (
    agent_router,
    claims_router,
    fleet_router,
    graph_router,
    quality_router,
    registry_router,
    sources_router,
)
from services.profile_service import get_profile_service
from vss.loader import get_registry

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _warm_quality_reports(profile) -> None:
    began = time.perf_counter()
    try:
        profile.run_all()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Quality warm-up failed: %s", exc)
        return
    logger.info("Quality reports warm in %.1fs", time.perf_counter() - began)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Parse VSS and load the profile once, at startup.

    MongoDB is optional. Without a URI the quality report still runs, which keeps
    the first step of adoption free of any database access.
    """
    registry = get_registry()
    logger.info("VSS %s loaded: %d signals", registry.version, len(registry))

    try:
        profile = get_profile_service()
        logger.info(
            "Profile %r loaded with sources: %s",
            profile.name, sorted(profile.mappings),
        )
        # Reading 800,000 rows takes half a minute, and whoever opens the
        # overview first should not be the one paying for it. Warm it on a
        # thread so the API answers while the reports build.
        threading.Thread(
            target=_warm_quality_reports, args=(profile,), daemon=True
        ).start()
    except FileNotFoundError as exc:
        logger.warning("No profile loaded: %s", exc)

    if get_mongo_client():
        logger.info("MongoDB connection pool initialised")
    else:
        logger.info("No MONGODB_URI set, running without a database")

    yield

    close_mongo_client()


app = FastAPI(
    title="Agentic Fleet Management API",
    description=(
        "Fleet telemetry unification, validation and fusion on MongoDB Atlas, "
        "built on COVESA VSS"
    ),
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(fleet_router)
app.include_router(agent_router)
app.include_router(claims_router)
app.include_router(graph_router)
app.include_router(registry_router)
app.include_router(sources_router)
app.include_router(quality_router)


@app.get("/")
async def read_root(request: Request):
    return {
        "message": "Agentic Fleet Management API",
        "status": "running",
        "docs": "/docs",
        "endpoints": {
            "fleet": "/api/fleet",
            "graph": "/api/graph/ontology",
            "claims": "/api/claims/stats",
            "agent": "/api/agent/status",
            "registry": "/api/registry/summary",
            "sources": "/api/sources",
            "quality": "/api/quality",
        },
    }


def _health() -> dict:
    registry = get_registry()
    return {
        "status": "healthy",
        "vssVersion": registry.version,
        "signals": len(registry),
        "database": bool(get_mongo_client()),
    }


@app.get("/health")
async def health_check():
    """Container probe. Not proxied by the frontend rewrite."""
    return _health()


@app.get("/api/health")
async def api_health_check():
    """Same payload under /api so the browser can reach it through the proxy."""
    return _health()
