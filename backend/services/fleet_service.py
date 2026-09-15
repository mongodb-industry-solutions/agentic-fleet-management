"""Fleet state, read from MongoDB.

The vehicle registry comes from the fleet management system and holds model,
depot, rental status and contract. Live state comes from the trackers. Both live
in one asset_shadow document per vehicle, which is why every screen here is a
single query: a relational shape would spread the same answer across a vehicles
table, a contracts table, a positions table and a readings table, and each view
would join them back.

Falls back to reading the extract in memory when no database is configured, so
the first step of adoption still works before anyone has an Atlas cluster.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from db.mdb import get_mongo_client
from db.schema import (
    ASSET_SHADOW,
    FINDINGS,
    PROVENANCE,
    SIGNAL_HISTORY,
    TELEMETRY_RAW,
    TRIPS,
)
from services.fleet_rules import (
    FUEL,
    ODOMETER,
    SPEED,
    activity_of,
)

logger = logging.getLogger(__name__)

# Signals the operations console shows: the label, the unit to display it in, and
# a factor from the VSS unit into that unit. VSS keeps TraveledDistance in
# metres, which is right for storage and useless on a dashboard, so the
# conversion happens at the display edge rather than by changing what is stored.
DISPLAY_SIGNALS = {
    "Speed": ("Speed", "km/h", 1),
    "CurrentLocation.Heading": ("Heading", "degrees", 1),
    "TraveledDistance": ("Odometer", "km", 0.001),
    "Powertrain.FuelSystem.RelativeLevel": ("Fuel", "%", 1),
    "Powertrain.CombustionEngine.IsRunning": ("Ignition", "", 1),
    "Powertrain.CombustionEngine.EngineCoolant.Temperature": ("Coolant", "C", 1),
    "LowVoltageBattery.CurrentVoltage": ("Battery", "V", 1),
}

# Projection for the fleet list. Pulling six fields out of a 400 byte document
# rather than the whole thing is what keeps five thousand pins cheap to draw.
SUMMARY_PROJECTION = {
    "registry": 1,
    "geo": 1,
    "health": 1,
    "updatedAt": 1,
    f"state.{SPEED}": 1,
    f"state.{FUEL}": 1,
    f"state.{ODOMETER}": 1,
    "state.Powertrain.CombustionEngine.IsRunning": 1,
}


def _leaf(state: dict, path: str) -> dict | None:
    """Walk a dotted path through the nested VSS tree stored in a document."""
    node: Any = state
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node if isinstance(node, dict) and "v" in node else None


def _value(state: dict, path: str):
    leaf = _leaf(state, path)
    return leaf["v"] if leaf else None


def _flatten(state: dict, prefix: str = "") -> dict[str, dict]:
    """Turn the nested state tree back into path-keyed leaves."""
    out: dict[str, dict] = {}
    for key, node in (state or {}).items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(node, dict) and "v" in node:
            out[path] = node
        elif isinstance(node, dict):
            out.update(_flatten(node, path))
    return out


def summarise(doc: dict) -> dict:
    """The compact form the fleet list and map need."""
    registry = doc.get("registry") or {}
    state = doc.get("state") or {}
    health = doc.get("health") or {}
    geo = doc.get("geo") or {}
    coordinates = geo.get("coordinates") if isinstance(geo, dict) else None
    odometer = _value(state, ODOMETER)

    return {
        "plate": doc["_id"],
        "vin": registry.get("vin"),
        "make": registry.get("marca"),
        "model": registry.get("modelo"),
        "year": registry.get("ano"),
        "category": registry.get("categoria"),
        "region": registry.get("regiao"),
        "depot": registry.get("deposito_nome"),
        "depotCode": registry.get("deposito"),
        "status": registry.get("status", "unknown"),
        "contract": registry.get("contrato") or None,
        "customer": registry.get("cliente") or None,
        "device": registry.get("id_equipamento"),
        "activity": activity_of(_flatten(state)),
        "speed": _value(state, SPEED),
        "fuel": _value(state, FUEL),
        "odometerKm": round(odometer / 1000, 1) if odometer else None,
        "lat": coordinates[1] if coordinates else None,
        "lon": coordinates[0] if coordinates else None,
        "trust": health.get("trust", "good"),
        "findingCount": health.get("openFindings", 0),
        "topFinding": health.get("topFinding"),
        "topSeverity": health.get("topSeverity"),
        "reports": health.get("reports", 0),
        "rejected": health.get("rejected", 0),
        "lastSeen": doc.get("updatedAt").isoformat() if doc.get("updatedAt") else None,
    }


class FleetService:
    """Reads fleet state from MongoDB."""

    def __init__(self) -> None:
        client = get_mongo_client()
        self.db = client[os.getenv("DATABASE_NAME", "fleet")] if client else None

    @property
    def connected(self) -> bool:
        return self.db is not None

    def list_vehicles(
        self,
        status: str | None = None,
        trust: str | None = None,
        region: str | None = None,
        depot: str | None = None,
        needs_attention: bool = False,
        limit: int = 6000,
    ) -> list[dict]:
        query: dict = {}
        if status:
            query["registry.status"] = status
        if trust:
            query["health.trust"] = trust
        if region:
            query["registry.regiao"] = region
        if depot:
            query["registry.deposito"] = depot
        if needs_attention:
            query["health.openFindings"] = {"$gt": 0}

        cursor = (
            self.db[ASSET_SHADOW]
            .find(query, SUMMARY_PROJECTION)
            .sort("health.openFindings", -1)
            .limit(limit)
            # One round trip rather than forty. The default batch size turns a
            # five thousand document read into repeated getMore calls, which over
            # a WAN link to Atlas costs more than the query itself.
            .batch_size(limit)
        )
        return [summarise(doc) for doc in cursor]

    def get_vehicle(self, plate: str) -> dict | None:
        return self.db[ASSET_SHADOW].find_one({"_id": plate.strip().upper()})

    def vehicle_trips(self, plate: str, limit: int = 25) -> list[dict]:
        cursor = (
            self.db[TRIPS]
            .find({"assetRef": plate.strip().upper()}, {"_id": 0})
            .sort("startedAt", -1)
            .limit(limit)
        )
        return list(cursor)

    def vehicle_findings(self, plate: str) -> list[dict]:
        cursor = self.db[FINDINGS].find(
            {"assetRef": plate.strip().upper()}, {"_id": 0}
        )
        order = {"critical": 0, "warning": 1, "info": 2}
        return sorted(
            cursor,
            key=lambda f: (order.get(f.get("severity"), 3), -f.get("occurrences", 0)),
        )

    def vehicle_provenance(self, plate: str) -> list[dict]:
        """Every signal two or more sources answered, and how it was decided."""
        cursor = self.db[PROVENANCE].find(
            {"assetRef": plate.strip().upper()}, {"_id": 0, "assetRef": 0}
        )
        rows = list(cursor)
        # Contested first: a signal where the sources actually disagreed is more
        # interesting than one where they happened to match.
        rows.sort(key=lambda r: (
            len({repr(w["v"]) for w in r["witnesses"]}) < 2,
            r["vssPath"],
        ))
        return rows

    def fusion_summary(self) -> dict:
        """Who wins which signal across the whole fleet."""
        pipeline = [
            {"$group": {
                "_id": {"path": "$vssPath", "src": "$chosen.src"},
                "count": {"$sum": 1},
                "avgTrust": {"$avg": "$chosen.trustScore"},
            }},
            {"$sort": {"count": -1}},
        ]
        rows = list(self.db[PROVENANCE].aggregate(pipeline))
        by_path: dict[str, list] = {}
        for row in rows:
            by_path.setdefault(row["_id"]["path"], []).append({
                "source": row["_id"]["src"],
                "wins": row["count"],
                "avgTrust": round(row["avgTrust"], 3),
            })
        return {
            "signals": [
                {"vssPath": path, "winners": winners}
                for path, winners in sorted(by_path.items())
            ],
            "fusedSignals": self.db[PROVENANCE].estimated_document_count(),
        }

    def vehicle_rejections(self, plate: str, limit: int = 20) -> list[dict]:
        """Recent rejected readings, which is the evidence behind a finding."""
        cursor = (
            self.db[TELEMETRY_RAW]
            .find(
                {"assetRef": plate.strip().upper(), "rejected": {"$exists": True}},
                {"_id": 0, "vssPath": 1, "raw": 1, "observedAt": 1, "rejected": 1},
            )
            .sort("observedAt", -1)
            .limit(limit)
        )
        return list(cursor)

    def stats(self) -> dict:
        """Every counter the console shows, in one pass over asset_shadow.

        Pushing the status, trust and region of 5,000 vehicles back to be tallied
        in Python sent 15,000 strings over the wire to produce nine numbers. A
        facet groups each of them server side, so the response is a handful of
        rows however large the fleet gets.
        """
        def bucket(field: str) -> list[dict]:
            return [{"$group": {"_id": f"${field}", "count": {"$sum": 1}}}]

        pipeline = [
            {
                "$facet": {
                    "totals": [
                        {
                            "$group": {
                                "_id": None,
                                "total": {"$sum": 1},
                                "needsAttention": {
                                    "$sum": {
                                        "$cond": [
                                            {"$gt": ["$health.openFindings", 0]}, 1, 0
                                        ]
                                    }
                                },
                                "averageFuel": {
                                    "$avg": "$state.Powertrain.FuelSystem"
                                            ".RelativeLevel.v"
                                },
                            }
                        }
                    ],
                    "byStatus": bucket("registry.status"),
                    "byTrust": bucket("health.trust"),
                    "byRegion": bucket("registry.regiao"),
                    "byActivity": [
                        {
                            "$group": {
                                "_id": {
                                    "$switch": {
                                        "branches": [
                                            {
                                                "case": {
                                                    "$eq": [
                                                        "$state.Powertrain"
                                                        ".CombustionEngine"
                                                        ".IsRunning.v",
                                                        None,
                                                    ]
                                                },
                                                "then": "unknown",
                                            },
                                            {
                                                "case": {
                                                    "$and": [
                                                        "$state.Powertrain"
                                                        ".CombustionEngine"
                                                        ".IsRunning.v",
                                                        {"$gt": ["$state.Speed.v", 3]},
                                                    ]
                                                },
                                                "then": "moving",
                                            },
                                            {
                                                "case": "$state.Powertrain"
                                                        ".CombustionEngine.IsRunning.v",
                                                "then": "idling",
                                            },
                                        ],
                                        "default": "parked",
                                    }
                                },
                                "count": {"$sum": 1},
                            }
                        }
                    ],
                }
            }
        ]

        # The cluster is remote and a round trip costs about 150ms, so the two
        # collection counts ride along rather than paying that twice more.
        pipeline += [
            {"$unionWith": {"coll": TRIPS, "pipeline": [{"$count": "totalTrips"}]}},
            {"$unionWith": {"coll": FINDINGS, "pipeline": [{"$count": "openFindings"}]}},
        ]

        rows = list(self.db[ASSET_SHADOW].aggregate(pipeline))
        if not rows:
            return {"total": 0, "byStatus": {}, "byTrust": {}, "byRegion": {}}

        facets = rows[0]
        extras = {k: v for row in rows[1:] for k, v in row.items()}
        totals = (facets.get("totals") or [{}])[0]

        def tally(name: str) -> dict[str, int]:
            return {
                (row["_id"] or "unknown"): row["count"] for row in facets.get(name, [])
            }

        average_fuel = totals.get("averageFuel")
        return {
            "total": totals.get("total", 0),
            "needsAttention": totals.get("needsAttention", 0),
            "averageFuel": round(average_fuel, 1) if average_fuel else None,
            "byStatus": tally("byStatus"),
            "byTrust": tally("byTrust"),
            "byRegion": tally("byRegion"),
            "byActivity": tally("byActivity"),
            "totalTrips": extras.get("totalTrips", 0),
            "openFindings": extras.get("openFindings", 0),
        }


_service: FleetService | None = None


def get_fleet_service() -> FleetService:
    global _service
    if _service is None:
        _service = FleetService()
    return _service


def reset_fleet_service() -> None:
    global _service
    _service = None
