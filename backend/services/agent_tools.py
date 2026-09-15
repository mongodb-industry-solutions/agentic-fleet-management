"""What the recall agent is allowed to do.

Every tool is thin over something already built and every one is read-only,
except the last which records a proposal for a human to approve. The agent never
writes to the fleet.

Tool results carry a compact summary for the model and a fuller payload for the
console, because the model does not need five hundred registration numbers to
decide what to do with them and the operator does.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from pymongo.database import Database

from db.schema import ASSET_SHADOW, FINDINGS, PROVENANCE, TRIPS
from services.claims import CLAIMS
from services.claims_service import ClaimsService
from services.graph_service import GraphService, LOTS, PARTS
from services.ontology import EDGES, node_id

logger = logging.getLogger(__name__)

# Damage that could follow a brake failure rather than a car park scrape.
COLLISION_TYPES = ["dent", "crack", "shatter", "misalignment", "tear"]
COLLISION_PANELS = [
    "front-bumper", "bonnet", "grille", "headlamp",
    "front-wing-left", "front-wing-right", "windscreen",
]


@dataclass
class ToolResult:
    """What came back, in two sizes."""

    name: str
    summary: str
    data: dict = field(default_factory=dict)
    ms: float = 0.0

    def for_model(self) -> str:
        return self.summary

    def to_event(self) -> dict:
        return {
            "type": "tool_result",
            "name": self.name,
            "summary": self.summary,
            "ms": round(self.ms, 1),
            "data": self.data,
        }


class RecallTools:
    """The toolset, bound to one database."""

    def __init__(self, db: Database) -> None:
        self.db = db
        self.graph = GraphService(db)
        self.claims = ClaimsService(db)
        # The working set for this run. Scoping fills it, and later tools read
        # it. Making the model carry 391 registrations between calls wastes
        # context and it cannot do it reliably: it will invent placeholders.
        self.scoped_plates: list[str] = []
        self.scoped_lot: str | None = None

    # -- scoping ---------------------------------------------------------

    def scope_recall(self, lot_code: str) -> ToolResult:
        """Who is affected. A graph traversal, not a judgement."""
        began = time.perf_counter()
        result = self.graph.blast_radius(lot_code)
        if not result.get("found"):
            return ToolResult("scope_recall", f"No batch {lot_code!r} exists.")

        vehicles = self.graph.affected_vehicles(result["vehicles"])
        lot = result["lot"]
        self.scoped_plates = result["vehicles"]
        self.scoped_lot = lot_code

        on_rent = [v for v in vehicles if v["status"] == "on_rent"]
        unlocatable = [v for v in vehicles if v["trust"] == "unreliable"]
        by_region: dict[str, int] = {}
        for vehicle in vehicles:
            by_region[vehicle["region"]] = by_region.get(vehicle["region"], 0) + 1

        summary = (
            f"Batch {lot_code}: {lot['partName']} from {lot['supplier']}, "
            f"criticality {lot['criticality']}. "
            f"{len(vehicles)} vehicles affected, {len(on_rent)} currently out with a "
            f"customer, {len(result['customers'])} customers to contact. "
            f"By region: {by_region}. "
            f"{len(unlocatable)} vehicles have telemetry flagged unreliable and "
            f"cannot be located. "
            f"These vehicles are now the working set: the other tools operate on "
            f"them and take no vehicle list."
        )

        return ToolResult(
            "scope_recall", summary,
            {
                "lot": lot,
                "counts": {
                    "vehicles": len(vehicles),
                    "onRent": len(on_rent),
                    "customers": len(result["customers"]),
                    "unlocatable": len(unlocatable),
                },
                "byRegion": by_region,
                "vehicles": vehicles,
                "unlocatable": [v["plate"] for v in unlocatable],
                "edgesTraversed": result["edgesTraversed"],
            },
            (time.perf_counter() - began) * 1000,
        )

    # -- corroboration ---------------------------------------------------

    def find_collision_claims(self, days: int = 180) -> ToolResult:
        """Have any affected vehicles already been in a collision?

        A precautionary recall and one where the failure has started happening
        are different problems. Nobody joins brake batches to damage photographs,
        which is exactly why it is worth asking.
        """
        began = time.perf_counter()
        if not self.scoped_plates:
            return ToolResult(
                "find_collision_claims",
                "Nothing is scoped yet. Call scope_recall first.",
            )
        plates = self.scoped_plates
        # MongoDB hands back naive datetimes in UTC, so the comparison has to be
        # naive too or it raises rather than returning nothing.
        since = datetime.utcnow() - timedelta(days=days)

        rows = list(self.db[CLAIMS].find(
            {
                "vehicle.plate": {"$in": plates},
                "damageType": {"$in": COLLISION_TYPES},
                "panel": {"$in": COLLISION_PANELS},
                "severity": {"$in": ["moderate", "severe"]},
            },
            {"embedding": 0, "image": 0},
        ).sort("settledOn", -1).limit(25))

        def settled(row):
            value = row.get("settledOn")
            if value is None:
                return None
            return value.replace(tzinfo=None) if value.tzinfo else value

        recent = [r for r in rows if (s := settled(r)) and s >= since]

        if not rows:
            summary = (
                f"No collision-consistent damage claims among the {len(plates)} "
                f"affected vehicles. Treat this as precautionary."
            )
        else:
            summary = (
                f"{len(rows)} affected vehicles have collision-consistent damage "
                f"claims (front-end or glass, moderate or severe). "
                f"{len(recent)} of those settled within the last {days} days. "
                + "; ".join(
                    f"{r['vehicle']['plate']}: {r['labels'].get('description', '')[:70]}"
                    for r in rows[:4]
                )
            )

        return ToolResult(
            "find_collision_claims", summary,
            {
                "total": len(rows),
                "recent": len(recent),
                "claims": [
                    {
                        "_id": str(r["_id"]),
                        "plate": r["vehicle"]["plate"],
                        "damageType": r["damageType"],
                        "panel": r["panel"],
                        "severity": r["severity"],
                        "description": r["labels"].get("description"),
                        "settledOn": r.get("settledOn"),
                        "cost": r.get("cost", {}).get("total"),
                    }
                    for r in rows[:12]
                ],
            },
            (time.perf_counter() - began) * 1000,
        )

    # -- cohorting -------------------------------------------------------

    def cohort_vehicles(self) -> ToolResult:
        """A rule-based first cut, plus the facts needed to judge the edges.

        Status, distance from home depot and data trust are facts. What to do
        about a critical part on a car two hundred kilometres from a depot is a
        trade-off between risk and disruption, which is the agent's job.
        """
        began = time.perf_counter()
        if not self.scoped_plates:
            return ToolResult(
                "cohort_vehicles", "Nothing is scoped yet. Call scope_recall first."
            )
        plates = self.scoped_plates
        vehicles = self.graph.affected_vehicles(plates, limit=len(plates))

        cohorts: dict[str, list[dict]] = {
            "on_rent_locatable": [],
            "on_rent_unlocatable": [],
            "on_lot": [],
            "in_service": [],
            "unlocatable": [],
        }

        for vehicle in vehicles:
            trust = vehicle.get("trust")
            status = vehicle.get("status")
            if trust == "unreliable":
                key = "on_rent_unlocatable" if status == "on_rent" else "unlocatable"
            elif status == "on_rent":
                key = "on_rent_locatable"
            elif status == "in_service":
                key = "in_service"
            else:
                key = "on_lot"
            cohorts[key].append(vehicle)

        counts = {k: len(v) for k, v in cohorts.items()}
        summary = (
            f"Rule-based split of {len(vehicles)} vehicles: "
            f"{counts['on_rent_locatable']} out with a customer and locatable, "
            f"{counts['on_rent_unlocatable']} out with a customer but not locatable, "
            f"{counts['on_lot']} sitting on a lot, "
            f"{counts['in_service']} already in service, "
            f"{counts['unlocatable']} unlocatable and not on rent."
        )

        return ToolResult(
            "cohort_vehicles", summary,
            {"counts": counts, "cohorts": {k: v[:60] for k, v in cohorts.items()}},
            (time.perf_counter() - began) * 1000,
        )

    # -- investigation ---------------------------------------------------

    def vehicle_evidence(self, plate: str) -> ToolResult:
        """Everything needed to decide why a vehicle cannot be located."""
        began = time.perf_counter()
        plate = plate.strip().upper()

        shadow = self.db[ASSET_SHADOW].find_one(
            {"_id": plate},
            {"registry": 1, "health": 1, "geo": 1, "sources": 1, "updatedAt": 1},
        )
        if shadow is None:
            return ToolResult("vehicle_evidence", f"No vehicle {plate!r}.")

        findings = list(self.db[FINDINGS].find({"assetRef": plate}, {"_id": 0}))
        provenance = list(self.db[PROVENANCE].find(
            {"assetRef": plate}, {"_id": 0, "assetRef": 0}
        ))
        trips = list(self.db[TRIPS].find(
            {"assetRef": plate}, {"_id": 0}
        ).sort("startedAt", -1).limit(6))

        # What each source last said about position, and how much it was believed.
        position_witnesses = []
        for row in provenance:
            if row["vssPath"] not in ("CurrentLocation.Latitude", "CurrentLocation.Longitude"):
                continue
            for witness in row["witnesses"]:
                position_witnesses.append({
                    "vssPath": row["vssPath"],
                    "src": witness["src"],
                    "value": witness["v"],
                    "trustScore": witness["trustScore"],
                    "score": witness.get("score"),
                    "used": witness.get("used"),
                })

        sources = shadow.get("sources", [])
        health = shadow.get("health", {})

        summary_parts = [
            f"{plate} ({shadow.get('registry', {}).get('marca')} "
            f"{shadow.get('registry', {}).get('modelo')}), "
            f"status {shadow.get('registry', {}).get('status')}, "
            f"home depot {shadow.get('registry', {}).get('deposito_nome')}.",
            f"Reporting sources: {sources or 'none'}.",
            f"Trust {health.get('trust')}, {health.get('rejected', 0)} readings "
            f"rejected out of {health.get('reports', 0)}.",
        ]
        if findings:
            summary_parts.append(
                "Findings: " + "; ".join(
                    f"{f['title']} ({f['occurrences']}x)" for f in findings
                )
            )
        if position_witnesses:
            summary_parts.append(
                "Position witnesses: " + "; ".join(
                    f"{w['src']} trust {w['trustScore']}"
                    for w in position_witnesses[:4]
                )
            )
        summary_parts.append(
            f"{len(trips)} recent trips, last activity {shadow.get('updatedAt')}."
        )

        return ToolResult(
            "vehicle_evidence", " ".join(summary_parts),
            {
                "plate": plate,
                "registry": shadow.get("registry"),
                # Episodic memory keys on this: the conclusion stays valid for
                # exactly as long as the fused state it was drawn from.
                "lastReadingAt": shadow.get("updatedAt"),
                "health": health,
                "sources": sources,
                "findings": findings,
                "positionWitnesses": position_witnesses,
                "trips": trips,
                "geo": shadow.get("geo"),
            },
            (time.perf_counter() - began) * 1000,
        )

    def tracker_conflict(self, plate: str, radius_km: float = 2.0) -> ToolResult:
        """Is another vehicle sitting where this one claims to be?

        A tracker refitted into a different car reports that car's position. If
        the reported point has other vehicles on top of it and this one has a
        history of impossible jumps, the unit has moved rather than the vehicle.
        """
        began = time.perf_counter()
        plate = plate.strip().upper()
        shadow = self.db[ASSET_SHADOW].find_one({"_id": plate}, {"geo": 1})
        geo = (shadow or {}).get("geo")
        if not geo:
            return ToolResult(
                "tracker_conflict", f"{plate} has no usable position to compare."
            )

        nearby = list(self.db[ASSET_SHADOW].find(
            {
                "_id": {"$ne": plate},
                "geo": {
                    "$nearSphere": {
                        "$geometry": geo,
                        "$maxDistance": radius_km * 1000,
                    }
                },
            },
            {"registry.deposito_nome": 1, "registry.status": 1},
        ).limit(10))

        summary = (
            f"{len(nearby)} other vehicles within {radius_km} km of where {plate} "
            f"last reported."
            + (
                f" Nearest: {', '.join(n['_id'] for n in nearby[:5])}."
                if nearby else
                " Nothing else is there, so the position is not obviously another"
                " vehicle's."
            )
        )

        return ToolResult(
            "tracker_conflict", summary,
            {"plate": plate, "nearby": [n["_id"] for n in nearby], "geo": geo},
            (time.perf_counter() - began) * 1000,
        )

    # -- output ----------------------------------------------------------

    def propose_actions(self, actions: list[dict]) -> ToolResult:
        """Record what the agent wants done. Approval is a human's job."""
        return ToolResult(
            "propose_actions",
            f"{len(actions)} actions proposed, pending approval.",
            {"actions": actions},
        )


def tool_specs() -> list[dict]:
    """Bedrock toolConfig. Descriptions are what the model plans against."""
    return [
        {
            "toolSpec": {
                "name": "scope_recall",
                "description": (
                    "Find every vehicle carrying a part from a production batch, "
                    "which of them are out with a customer, and which cannot be "
                    "located. Run this first."
                ),
                "inputSchema": {"json": {
                    "type": "object",
                    "properties": {"lot_code": {"type": "string"}},
                    "required": ["lot_code"],
                }},
            }
        },
        {
            "toolSpec": {
                "name": "find_collision_claims",
                "description": (
                    "Search settled damage claims for collision-consistent damage "
                    "among the vehicles scoped by scope_recall. Use this to decide "
                    "whether the recall is precautionary or whether failures have "
                    "already started happening. Takes no vehicle list: it operates "
                    "on whatever scope_recall found."
                ),
                "inputSchema": {"json": {
                    "type": "object",
                    "properties": {
                        "days": {
                            "type": "integer",
                            "description": "How far back to look. Defaults to 180.",
                        },
                    },
                }},
            }
        },
        {
            "toolSpec": {
                "name": "cohort_vehicles",
                "description": (
                    "Group the scoped vehicles by rental status and whether their "
                    "telemetry can be believed. Returns a rule-based first cut for "
                    "you to judge, not a final answer. Takes no vehicle list: it "
                    "operates on whatever scope_recall found."
                ),
                "inputSchema": {"json": {"type": "object", "properties": {}}},
            }
        },
        {
            "toolSpec": {
                "name": "propose_actions",
                "description": (
                    "Record the actions you want taken, one per cohort or vehicle. "
                    "Each needs a cohort, an action, a reason and an urgency. "
                    "Nothing is executed; a human approves."
                ),
                "inputSchema": {"json": {
                    "type": "object",
                    "properties": {
                        "actions": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "cohort": {"type": "string"},
                                    "action": {"type": "string"},
                                    "urgency": {
                                        "type": "string",
                                        "enum": ["immediate", "today", "this-week", "next-service"],
                                    },
                                    "vehicleCount": {"type": "integer"},
                                    "reason": {"type": "string"},
                                },
                                "required": ["cohort", "action", "urgency", "reason"],
                            },
                        }
                    },
                    "required": ["actions"],
                }},
            }
        },
    ]


def investigator_tool_specs() -> list[dict]:
    """The smaller toolset the per-vehicle investigator gets."""
    return [
        {
            "toolSpec": {
                "name": "vehicle_evidence",
                "description": (
                    "Everything known about one vehicle: which sources report on "
                    "it, what each said about its position and how much each was "
                    "believed, its findings, and its recent trips."
                ),
                "inputSchema": {"json": {
                    "type": "object",
                    "properties": {"plate": {"type": "string"}},
                    "required": ["plate"],
                }},
            }
        },
        {
            "toolSpec": {
                "name": "tracker_conflict",
                "description": (
                    "Check whether other vehicles are sitting where this one "
                    "claims to be. A tracker refitted into another car reports "
                    "that car's position."
                ),
                "inputSchema": {"json": {
                    "type": "object",
                    "properties": {
                        "plate": {"type": "string"},
                        "radius_km": {"type": "number"},
                    },
                    "required": ["plate"],
                }},
            }
        },
    ]


def dispatch(tools: RecallTools, name: str, arguments: dict) -> ToolResult:
    handlers: dict[str, Callable[..., ToolResult]] = {
        "scope_recall": tools.scope_recall,
        "find_collision_claims": tools.find_collision_claims,
        "cohort_vehicles": tools.cohort_vehicles,
        "vehicle_evidence": tools.vehicle_evidence,
        "tracker_conflict": tools.tracker_conflict,
        "propose_actions": tools.propose_actions,
    }
    handler = handlers.get(name)
    if handler is None:
        return ToolResult(name, f"No tool called {name!r}.")
    try:
        return handler(**arguments)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Tool %s failed: %s", name, exc)
        return ToolResult(name, f"{name} failed: {exc}")
