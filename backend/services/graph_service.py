"""Build the ABox and traverse it.

Edges are one typed collection rather than a collection per relationship, which
keeps $graphLookup simple and lets a traversal follow several relationship types
in one pass. Every edge that can change over time carries validFrom and validTo,
because a part replacement or a tracker swap must not erase the fact that the old
one was once there.

A traversal is only permitted over edge types the TBox confirms. That is what
turns the ontology from documentation into a constraint.
"""

from __future__ import annotations

import logging
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone

from pymongo import ASCENDING, UpdateOne
from pymongo.database import Database

from db.schema import ASSET_SHADOW
from services.ontology import (
    BUSINESS_QUESTIONS,
    EDGES,
    TBOX,
    Tbox,
    node_id,
)

logger = logging.getLogger(__name__)

BATCH = 20_000

# Collections the ABox is generated into alongside the edges, so a traversal can
# return something readable rather than bare identifiers.
SUPPLIERS = "suppliers"
LOTS = "lots"
PARTS = "parts"


def ensure_graph_collections(db: Database) -> dict[str, str]:
    db[TBOX].create_index([("kind", ASCENDING)])
    db[TBOX].create_index([("edgeType", ASCENDING)])

    # $graphLookup connects on `from`, then restrictSearchWithMatch filters on
    # type and validTo. Covering all three in one index keeps every hop indexed
    # rather than rescanning.
    db[EDGES].create_index(
        [("from", ASCENDING), ("type", ASCENDING), ("validTo", ASCENDING)],
        name="traversal_forward",
    )
    db[EDGES].create_index(
        [("to", ASCENDING), ("type", ASCENDING), ("validTo", ASCENDING)],
        name="traversal_reverse",
    )
    db[EDGES].create_index([("type", ASCENDING)])

    db[LOTS].create_index([("supplier", ASCENDING)])
    db[LOTS].create_index([("partType", ASCENDING), ("criticality", ASCENDING)])
    db[PARTS].create_index([("lot", ASCENDING)])
    db[PARTS].create_index([("plate", ASCENDING)])

    return {
        TBOX: "indexed",
        EDGES: "indexed on from, to and type",
        SUPPLIERS: "indexed",
        LOTS: "indexed",
        PARTS: "indexed",
    }


def load_tbox(db: Database, tbox: Tbox | None = None) -> dict:
    """Write the blueprint. Small enough to read, which is the point."""
    tbox = tbox or Tbox()

    operations = [
        UpdateOne({"_id": doc["_id"]}, {"$set": doc}, upsert=True)
        for doc in tbox.documents()
    ]
    db[TBOX].bulk_write(operations, ordered=False)

    db[BUSINESS_QUESTIONS].bulk_write([
        UpdateOne({"_id": q["_id"]}, {"$set": q}, upsert=True)
        for q in tbox.questions
    ], ordered=False)

    logger.info(
        "tbox: %d classes, %d relationships, %d questions",
        len(tbox.classes), len(tbox.relationships), len(tbox.questions),
    )
    return tbox.summary()


def _edge(from_id, to_id, edge_type, valid_from=None, valid_to=None, **props):
    doc = {
        "_id": f"{from_id}|{edge_type}|{to_id}",
        "from": from_id,
        "to": to_id,
        "type": edge_type,
        "validFrom": valid_from,
        "validTo": valid_to,
    }
    if props:
        doc["props"] = props
    return doc


def build_abox(
    db: Database, suppliers, lots, fitments, recall_lot=None
) -> dict:
    """Turn the parts catalogue and the fleet record into nodes and edges."""
    started = time.perf_counter()
    now = datetime.now(timezone.utc)

    db[SUPPLIERS].delete_many({})
    db[SUPPLIERS].insert_many([
        {"_id": s.code, "name": s.name, "country": s.country} for s in suppliers
    ], ordered=False)

    db[LOTS].delete_many({})
    db[LOTS].insert_many([
        {
            "_id": lot.code,
            "supplier": lot.supplier,
            "partType": lot.part_type,
            "partName": lot.part_name,
            "criticality": lot.criticality,
            "manufacturedOn": lot.manufactured_on,
            "quantity": lot.quantity,
            "unitCost": lot.unit_cost,
            "recalled": bool(recall_lot and lot.code == recall_lot.code),
        }
        for lot in lots
    ], ordered=False)

    db[PARTS].delete_many({})
    for start in range(0, len(fitments), BATCH):
        db[PARTS].insert_many([
            {
                "_id": f.part_serial,
                "lot": f.lot,
                "partType": f.part_type,
                "plate": f.plate,
                "fittedOn": f.fitted_on,
                "position": f.position,
            }
            for f in fitments[start:start + BATCH]
        ], ordered=False)

    edges: list[dict] = []

    for lot in lots:
        edges.append(_edge(
            node_id("Supplier", lot.supplier), node_id("Lot", lot.code),
            "SUPPLIES", lot.manufactured_on,
            partType=lot.part_type, criticality=lot.criticality,
        ))

    for fitment in fitments:
        part = node_id("Part", fitment.part_serial)
        edges.append(_edge(
            node_id("Lot", fitment.lot), part, "CONTAINS", fitment.fitted_on,
        ))
        edges.append(_edge(
            part, node_id("Vehicle", fitment.plate), "FITTED_TO",
            fitment.fitted_on, None,
            partType=fitment.part_type, position=fitment.position,
        ))

    # The rest of the ABox comes from the fleet record that is already loaded.
    projection = {
        "registry.contrato": 1, "registry.cliente": 1, "registry.cliente_id": 1,
        "registry.deposito": 1,
        "registry.deposito_nome": 1, "registry.modelo": 1, "registry.marca": 1,
        "registry.id_equipamento": 1, "registry.status": 1, "registry.regiao": 1,
    }
    for doc in db[ASSET_SHADOW].find({}, projection).batch_size(5000):
        registry = doc.get("registry") or {}
        vehicle = node_id("Vehicle", doc["_id"])

        if registry.get("deposito"):
            edges.append(_edge(
                vehicle, node_id("Depot", registry["deposito"]), "HOMED_AT",
            ))
        if registry.get("modelo"):
            model = f"{registry.get('marca', '')} {registry['modelo']}".strip()
            edges.append(_edge(vehicle, node_id("Model", model), "OF_MODEL"))
        if registry.get("id_equipamento"):
            edges.append(_edge(
                vehicle, node_id("Device", registry["id_equipamento"]),
                "TRACKED_BY", None, None,
            ))
        if registry.get("contrato"):
            agreement = node_id("Agreement", registry["contrato"])
            edges.append(_edge(
                vehicle, agreement, "RENTED_UNDER", None, None,
                status=registry.get("status"),
            ))
            customer = registry.get("cliente_id") or registry.get("cliente")
            if customer:
                edges.append(_edge(
                    agreement, node_id("Customer", customer), "HELD_BY",
                    None, None, name=registry.get("cliente"),
                ))

    db[EDGES].delete_many({})
    for start in range(0, len(edges), BATCH):
        db[EDGES].insert_many(edges[start:start + BATCH], ordered=False)

    by_type = Counter(e["type"] for e in edges)
    return {
        "edges": len(edges),
        "byType": dict(by_type.most_common()),
        "suppliers": len(suppliers),
        "lots": len(lots),
        "parts": len(fitments),
        "seconds": round(time.perf_counter() - started, 2),
        "recallLot": recall_lot.code if recall_lot else None,
    }


class GraphService:
    """Traversals, constrained to what the TBox confirms."""

    def __init__(self, db: Database, tbox: Tbox | None = None) -> None:
        self.db = db
        self.tbox = tbox or Tbox()

    def blast_radius(self, lot_code: str, max_depth: int = 4) -> dict:
        """Supplier batch to the customers who need calling, in one traversal.

        $graphLookup walks from the batch outward, following only the edge types
        the ontology confirms and only edges still in force.
        """
        edge_types = ["CONTAINS", "FITTED_TO", "RENTED_UNDER", "HELD_BY"]
        allowed, unknown = self.tbox.confirm(edge_types)
        if unknown:
            raise ValueError(f"Not in the ontology: {unknown}")

        start = node_id("Lot", lot_code)
        pipeline = [
            {"$match": {"_id": lot_code}},
            {"$graphLookup": {
                "from": EDGES,
                # The batch document keys on the lot code, the graph keys on the
                # namespaced node id, so the traversal starts from a literal.
                "startWith": start,
                "connectFromField": "to",
                "connectToField": "from",
                "as": "reach",
                "maxDepth": max_depth,
                "depthField": "hop",
                "restrictSearchWithMatch": {
                    "type": {"$in": allowed},
                    # Only edges still in force. A part that has since been
                    # replaced is history, not exposure.
                    "validTo": None,
                },
            }},
            # The traversal only needs where each edge points, what kind it is
            # and how far out it was found. Returning whole edge documents for
            # every hop costs more in transfer than the traversal costs to run.
            {"$project": {
                "supplier": 1, "partName": 1, "partType": 1, "criticality": 1,
                "manufacturedOn": 1, "quantity": 1, "unitCost": 1,
                "reach.to": 1, "reach.type": 1, "reach.hop": 1,
            }},
        ]

        started = time.perf_counter()
        rows = list(self.db[LOTS].aggregate(pipeline))
        elapsed = round((time.perf_counter() - started) * 1000, 1)

        if not rows:
            return {"lot": lot_code, "found": False}

        lot = rows[0]
        reach = lot.pop("reach", [])

        by_hop: dict[int, list] = defaultdict(list)
        for edge in reach:
            by_hop[edge["hop"]].append(edge)

        vehicles = sorted({
            e["to"].split(":", 1)[1] for e in reach if e["type"] == "FITTED_TO"
        })
        agreements = sorted({
            e["to"].split(":", 1)[1] for e in reach if e["type"] == "RENTED_UNDER"
        })
        customers = sorted({
            e["to"].split(":", 1)[1] for e in reach if e["type"] == "HELD_BY"
        })

        return {
            "lot": {
                "code": lot["_id"],
                "supplier": lot.get("supplier"),
                "partName": lot.get("partName"),
                "partType": lot.get("partType"),
                "criticality": lot.get("criticality"),
                "manufacturedOn": lot.get("manufacturedOn"),
                "quantity": lot.get("quantity"),
                "unitCost": lot.get("unitCost"),
            },
            "found": True,
            "queryMs": elapsed,
            "edgesTraversed": len(reach),
            "hops": {
                str(hop): {
                    "count": len(edges),
                    "type": edges[0]["type"] if edges else None,
                }
                for hop, edges in sorted(by_hop.items())
            },
            "parts": sum(1 for e in reach if e["type"] == "CONTAINS"),
            "vehicles": vehicles,
            "agreements": agreements,
            "customers": customers,
            "allowedEdgeTypes": allowed,
        }

    def affected_vehicles(self, plates: list[str], limit: int = 500) -> list[dict]:
        """Enrich the traversal result with live fleet state.

        This is where the graph meets everything else: the blast radius is a set
        of registrations, and the shadow says where each one is and whether its
        data can be believed.
        """
        cursor = self.db[ASSET_SHADOW].find(
            {"_id": {"$in": plates[:limit]}},
            {
                "registry": 1, "geo": 1, "health": 1,
                "state.Powertrain.FuelSystem.RelativeLevel": 1,
            },
        ).batch_size(limit)

        out = []
        for doc in cursor:
            registry = doc.get("registry") or {}
            geo = doc.get("geo") or {}
            coordinates = geo.get("coordinates") if isinstance(geo, dict) else None
            health = doc.get("health") or {}
            out.append({
                "plate": doc["_id"],
                "make": registry.get("marca"),
                "model": registry.get("modelo"),
                "status": registry.get("status"),
                "region": registry.get("regiao"),
                "depot": registry.get("deposito_nome"),
                "customer": registry.get("cliente") or None,
                "contract": registry.get("contrato") or None,
                "lat": coordinates[1] if coordinates else None,
                "lon": coordinates[0] if coordinates else None,
                "trust": health.get("trust", "good"),
                "topFinding": health.get("topFinding"),
            })
        return out

    def recalled_lot(self) -> dict | None:
        return self.db[LOTS].find_one({"recalled": True})

    def lots(self, limit: int = 40) -> list[dict]:
        """Batches worth recalling, ranked by how widely they were fitted."""
        pipeline = [
            {"$lookup": {
                "from": PARTS, "localField": "_id",
                "foreignField": "lot", "as": "fitted",
            }},
            {"$project": {
                "supplier": 1, "partName": 1, "partType": 1, "criticality": 1,
                "manufacturedOn": 1, "unitCost": 1, "recalled": 1,
                "fittedCount": {"$size": "$fitted"},
            }},
            {"$match": {"fittedCount": {"$gt": 0}}},
            {"$sort": {"criticality": 1, "fittedCount": -1}},
            {"$limit": limit},
        ]
        return list(self.db[LOTS].aggregate(pipeline))

    def tbox_documents(self) -> list[dict]:
        return list(self.db[TBOX].find({}, {"embedding": 0}))

    def questions(self) -> list[dict]:
        return list(self.db[BUSINESS_QUESTIONS].find({}))

    def stats(self) -> dict:
        by_type = list(self.db[EDGES].aggregate([
            {"$group": {"_id": "$type", "count": {"$sum": 1}}},
            {"$sort": {"count": -1}},
        ]))
        return {
            "edges": self.db[EDGES].estimated_document_count(),
            "byType": {row["_id"]: row["count"] for row in by_type},
            "suppliers": self.db[SUPPLIERS].estimated_document_count(),
            "lots": self.db[LOTS].estimated_document_count(),
            "parts": self.db[PARTS].estimated_document_count(),
            "tbox": self.tbox.summary(),
        }
