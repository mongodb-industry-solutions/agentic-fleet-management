"""Ontology and graph routes.

The recall traversal is the reason this module exists. A supplier flags a
production batch and the fleet needs the list of customers currently driving a
car that contains something from it. Five hops, five entity kinds, one query.
"""

from __future__ import annotations

import os

from fastapi import APIRouter, HTTPException, Query

from db.mdb import get_mongo_client
from services.graph_service import GraphService
from services.ontology import Tbox

router = APIRouter(prefix="/api/graph", tags=["graph"])


def _explain(title, capability, collection, code, note, **extra) -> dict:
    return {
        "title": title, "capability": capability, "collection": collection,
        "code": code, "note": note, **extra,
    }


def _service() -> GraphService:
    client = get_mongo_client()
    if client is None:
        raise HTTPException(
            status_code=503,
            detail="No database connection. Set MONGODB_URI in backend/.env.",
        )
    db = client[os.getenv("DATABASE_NAME", "fleet")]
    if db["edges"].estimated_document_count() == 0:
        raise HTTPException(
            status_code=503,
            detail="No graph loaded. Run: uv run python scripts/load_graph.py",
        )
    return GraphService(db)


@router.get("/ontology")
def ontology():
    """The TBox: what can be asked, and what an agent is allowed to traverse."""
    service = _service()
    documents = service.tbox_documents()
    return {
        "classes": [d for d in documents if d["kind"] == "class"],
        "relationships": [d for d in documents if d["kind"] == "relationship"],
        "questions": service.questions(),
        "stats": service.stats(),
        "explain": [
            _explain(
                "The blueprint and the facts, split apart",
                "Ontology as documents",
                "tbox",
                'db.tbox.find({ kind: "relationship" })\n\n'
                "// one relationship, as stored\n"
                "{\n"
                '  _id: "fleet:FITTED_TO",\n'
                '  kind: "relationship",\n'
                '  subject: "fleet:Part",\n'
                '  object: "fleet:Vehicle",\n'
                '  edgeType: "FITTED_TO",\n'
                "  timeBounded: true\n"
                "}",
                "The TBox is the blueprint and changes when the domain does. The "
                "ABox is the facts and changes constantly. Splitting them by rate "
                "of change keeps the schema small enough to reason about while "
                "the facts run to tens of thousands. Each class records the "
                "collection it is sourced from, so a relationship can always be "
                "traced back to the data behind it.",
            ),
            _explain(
                "Relationships, not tables",
                "Ontology-driven questions",
                "business_questions",
                "db.business_questions.find({})",
                "The ontology holds what the business actually asks rather than "
                "an exhaustive map of the schema. A relationship earns its place "
                "because a question needs it, which is what keeps the blueprint "
                "readable instead of becoming a second copy of the database.",
            ),
        ],
    }


@router.get("/lots")
def lots(limit: int = Query(default=40, le=200)):
    """Production batches, ranked by how widely they were fitted."""
    service = _service()
    return {"lots": service.lots(limit=limit), "recalled": service.recalled_lot()}


@router.get("/recall/{lot_code}")
def recall(
    lot_code: str,
    region: str | None = Query(default=None),
    on_rent_only: bool = Query(default=False),
):
    """Blast radius for one production batch, enriched with live fleet state."""
    service = _service()

    try:
        result = service.blast_radius(lot_code)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    if not result.get("found"):
        raise HTTPException(status_code=404, detail=f"No batch {lot_code!r}")

    vehicles = service.affected_vehicles(result["vehicles"])
    if region:
        vehicles = [v for v in vehicles if v["region"] == region]
    if on_rent_only:
        vehicles = [v for v in vehicles if v["status"] == "on_rent"]

    unreachable = [v for v in vehicles if v["trust"] == "unreliable"]
    by_region: dict[str, int] = {}
    for vehicle in vehicles:
        by_region[vehicle["region"]] = by_region.get(vehicle["region"], 0) + 1

    lot = result["lot"]
    exposure = round((lot.get("unitCost") or 0) * len(result["vehicles"]), 2)

    return {
        "lot": lot,
        "queryMs": result["queryMs"],
        "edgesTraversed": result["edgesTraversed"],
        "hops": result["hops"],
        "counts": {
            "parts": result["parts"],
            "vehicles": len(result["vehicles"]),
            "agreements": len(result["agreements"]),
            "customers": len(result["customers"]),
            "unreachable": len(unreachable),
        },
        "byRegion": by_region,
        "estimatedPartCost": exposure,
        "vehicles": vehicles,
        "unreachable": unreachable,
        "explain": [
            _explain(
                "Five hops in one query",
                "$graphLookup",
                "edges",
                "db.lots.aggregate([\n"
                f'  {{ $match: {{ _id: "{lot_code}" }} }},\n'
                "  { $graphLookup: {\n"
                '      from: "edges",\n'
                f'      startWith: "lot:{lot_code}",\n'
                '      connectFromField: "to",\n'
                '      connectToField: "from",\n'
                '      as: "reach",\n'
                "      maxDepth: 4,\n"
                '      depthField: "hop",\n'
                "      restrictSearchWithMatch: {\n"
                '        type: { $in: ["CONTAINS", "FITTED_TO",\n'
                '                      "RENTED_UNDER", "HELD_BY"] },\n'
                "        validTo: null\n"
                "      }\n"
                "  }}\n"
                "])",
                "Batch to parts to vehicles to open agreements to customers. "
                "Without the graph this is five collections and four joins, "
                "rewritten for every variation of the question. The edge types "
                "come from the ontology, so a traversal can only follow "
                "relationships the TBox confirms.",
                queryMs=result["queryMs"],
                edgesTraversed=result["edgesTraversed"],
                allowedEdgeTypes=result["allowedEdgeTypes"],
            ),
            _explain(
                "Edges that remember",
                "Time-bounded relationships",
                "edges",
                "{\n"
                '  from: "part:P00014823",\n'
                '  to: "vehicle:JV32 VRC",\n'
                '  type: "FITTED_TO",\n'
                "  validFrom: ISODate(...),\n"
                "  validTo: null\n"
                "}\n\n"
                "// the traversal filters on validTo: null",
                "A replaced part and a tracker moved into another car both have "
                "to stay answerable after the fact. Closing an edge rather than "
                "deleting it keeps history intact, and the traversal filters to "
                "what is currently in force.",
            ),
            _explain(
                "Where the graph meets the fleet",
                "Document model",
                "asset_shadow",
                "db.asset_shadow.find({\n"
                "  _id: { $in: <plates from the traversal> }\n"
                "})",
                "The traversal returns registrations. The shadow says where each "
                "one is, whether it is out on rent, and whether its position can "
                "be believed. That last column is the uncomfortable one: some of "
                "the affected cars have trackers we already know are lying, so "
                "the recall list includes vehicles nobody can currently locate.",
                unreachable=len(unreachable),
            ),
        ],
    }
