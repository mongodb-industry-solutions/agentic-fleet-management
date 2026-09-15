"""API routers."""

from routes.agent import router as agent_router
from routes.claims import router as claims_router
from routes.fleet import router as fleet_router
from routes.graph import router as graph_router
from routes.quality import router as quality_router
from routes.registry import router as registry_router
from routes.sources import router as sources_router

__all__ = [
    "agent_router",
    "claims_router",
    "fleet_router",
    "graph_router",
    "quality_router",
    "registry_router",
    "sources_router",
]
