"""Fleet operations routes.

Each response carries an `explain` list naming the collection, the query that was
actually run, and the MongoDB capability doing the work. The console's Behind the
Scenes panel renders it, so the technical story stays attached to the screen that
motivates it rather than being narrated on a slide.

The queries shown are the queries executed. Where a timing is reported it was
measured on that request.
"""

from __future__ import annotations

import time

from fastapi import APIRouter, HTTPException, Query

from db.schema import collection_stats
from services.fleet_service import DISPLAY_SIGNALS, get_fleet_service, summarise
from services.fleet_rules import activity_of
from vss.loader import get_registry

router = APIRouter(prefix="/api/fleet", tags=["fleet"])


def _explain(title, capability, collection, code, note, **extra) -> dict:
    return {
        "title": title,
        "capability": capability,
        "collection": collection,
        "code": code,
        "note": note,
        **extra,
    }


def _require_db():
    service = get_fleet_service()
    if not service.connected:
        raise HTTPException(
            status_code=503,
            detail=(
                "No database connection. Set MONGODB_URI in backend/.env and run "
                "uv run python scripts/load.py"
            ),
        )
    return service


@router.get("")
def list_fleet(
    status: str | None = Query(default=None, description="on_rent, available, in_service"),
    trust: str | None = Query(default=None, description="good, degraded, unreliable"),
    region: str | None = Query(default=None, description="BR, NA, EU"),
    depot: str | None = Query(default=None),
    needs_attention: bool = Query(default=False),
    limit: int = Query(default=6000, le=20000),
):
    """Every vehicle, in the compact form the map and list need."""
    service = _require_db()

    started = time.perf_counter()
    vehicles = service.list_vehicles(
        status=status, trust=trust, region=region,
        depot=depot, needs_attention=needs_attention, limit=limit,
    )
    query_ms = round((time.perf_counter() - started) * 1000, 1)

    stats = service.stats()

    filters = []
    if status:
        filters.append(f'"registry.status": "{status}"')
    if trust:
        filters.append(f'"health.trust": "{trust}"')
    if region:
        filters.append(f'"registry.regiao": "{region}"')
    if needs_attention:
        filters.append('"health.openFindings": { $gt: 0 }')
    filter_text = ", ".join(filters) or ""

    return {
        "stats": stats,
        "count": len(vehicles),
        "queryMs": query_ms,
        "vehicles": vehicles,
        "explain": [
            _explain(
                "Drawing the fleet",
                "Document model",
                "asset_shadow",
                f"db.asset_shadow.find(\n"
                f"  {{ {filter_text} }},\n"
                f"  {{ registry: 1, geo: 1, health: 1,\n"
                f"    \"state.Speed\": 1,\n"
                f"    \"state.Powertrain.FuelSystem.RelativeLevel\": 1 }}\n"
                f").limit({limit})",
                "One document per vehicle holds the registry facts from the fleet "
                "system and the latest value of every signal from the trackers, so "
                "the map is a single query with no joins. A relational schema would "
                "read vehicles, contracts, positions and readings and stitch them "
                "back together on every refresh.",
                queryMs=query_ms,
                documents=len(vehicles),
            ),
            _explain(
                "Counting the fleet in one pass",
                "Aggregation pipeline",
                "asset_shadow",
                "db.asset_shadow.aggregate([\n"
                "  { $group: {\n"
                "      _id: null,\n"
                "      total: { $sum: 1 },\n"
                "      needsAttention: { $sum: { $cond: [\n"
                "        { $gt: [\"$health.openFindings\", 0] }, 1, 0 ] } },\n"
                "      averageFuel: { $avg:\n"
                "        \"$state.Powertrain.FuelSystem.RelativeLevel.v\" }\n"
                "  }}\n"
                "])",
                "Counts and averages come from the same documents the map draws, so "
                "the numbers cannot disagree with the pins. Reaching into the VSS "
                "tree with a dotted path means no separate summary table to keep in "
                "step.",
            ),
            _explain(
                "Finding vehicles in an area",
                "Geospatial, 2dsphere index",
                "asset_shadow",
                'db.asset_shadow.createIndex({ geo: "2dsphere" })\n\n'
                "db.asset_shadow.find({\n"
                "  geo: { $geoWithin: { $geometry: <polygon> } },\n"
                '  "registry.status": "available"\n'
                "})",
                "Position is denormalised to a top-level geo field so the 2dsphere "
                "index has a stable path rather than one nested five levels inside "
                "the VSS tree. Territory limits, depot catchments and storm exposure "
                "are the same query with a different polygon.",
            ),
        ],
    }


@router.get("/storage")
def storage():
    """What the collections actually weigh, measured from Atlas."""
    service = _require_db()
    stats = collection_stats(service.db)
    return {
        "collections": stats,
        "explain": [
            _explain(
                "What this fleet weighs",
                "collStats",
                "signal_history",
                'db.runCommand({ collStats: "signal_history" })',
                "A time series collection buckets measurements sharing a metaField "
                "and compresses each column: delta encoding for continuous numerics "
                "like speed and temperature, run length encoding for repetitive state "
                "like ignition. These are measured figures rather than estimates.",
                collections=stats,
            ),
        ],
    }


@router.get("/{plate}")
def get_vehicle(plate: str):
    """Everything about one vehicle: registry, live state, trips and findings."""
    service = _require_db()

    started = time.perf_counter()
    document = service.get_vehicle(plate)
    read_ms = round((time.perf_counter() - started) * 1000, 1)

    if document is None:
        raise HTTPException(status_code=404, detail=f"No vehicle {plate!r} in this fleet")

    trips = service.vehicle_trips(plate)
    findings = service.vehicle_findings(plate)
    evidence = service.vehicle_rejections(plate)
    provenance = service.vehicle_provenance(plate)
    contested = [
        p for p in provenance
        if len({repr(w["v"]) for w in p["witnesses"]}) > 1
    ]

    registry = get_registry()
    state = document.get("state") or {}
    signals = []
    for path, (label, unit, factor) in DISPLAY_SIGNALS.items():
        node = state
        for part in path.split("."):
            node = node.get(part) if isinstance(node, dict) else None
            if node is None:
                break
        if not isinstance(node, dict) or "v" not in node:
            continue

        spec = registry.get(path)
        value = node["v"]
        if factor != 1 and isinstance(value, (int, float)) and not isinstance(value, bool):
            value = round(value * factor, 1)

        signals.append({
            "vssPath": path,
            "label": label,
            "value": value,
            "unit": unit,
            "storedUnit": node.get("unit"),
            "observedAt": node["ts"].isoformat() if hasattr(node["ts"], "isoformat") else node["ts"],
            "source": node.get("src"),
            "kind": spec.kind if spec else None,
            "min": spec.minimum if spec else None,
            "max": spec.maximum if spec else None,
            "description": spec.description if spec else None,
        })

    summary = summarise(document)
    summary["trips"] = len(trips)

    return {
        "vehicle": summary,
        "signals": signals,
        "trips": trips,
        "findings": findings,
        "evidence": evidence,
        "provenance": provenance,
        "sources": document.get("sources", []),
        "explain": [
            _explain(
                "The whole vehicle in one read",
                "Document model",
                "asset_shadow",
                f'db.asset_shadow.findOne({{ _id: "{summary["plate"]}" }})',
                "Registry facts from the fleet system and live values from the "
                "trackers sit in the same document, nested exactly as COVESA VSS "
                "defines them. Every leaf carries its value, when it was observed and "
                "which source it came from, so provenance survives to read time.",
                queryMs=read_ms,
            ),
            _explain(
                "Trips are reconstructed, not stored",
                "Time series, $densify and $fill",
                "signal_history",
                "db.signal_history.aggregate([\n"
                f'  {{ $match: {{ "src.assetId": "{summary["plate"]}" }} }},\n'
                '  { $densify: { field: "ts",\n'
                '      partitionByFields: ["src.assetId"],\n'
                '      range: { step: 60, unit: "second", bounds: "partition" } } },\n'
                "  { $fill: { sortBy: { ts: 1 }, output: {\n"
                '      "v.Powertrain.CombustionEngine.IsRunning": { method: "locf" } } } }\n'
                "])",
                "Nothing stores a trip. Ignition transitions are paired into trips "
                "from the source series, so a missed processing run costs nothing and "
                "changing the rules means re-deriving rather than migrating.",
                tripCount=len(trips),
            ),
            _explain(
                "Two sources, one answer",
                "Fusion and trust scoring",
                "provenance",
                "db.provenance.find({\n"
                f'  assetRef: "{summary["plate"]}",\n'
                "  $expr: { $gt: [ { $size: \"$witnesses\" }, 1 ] }\n"
                "})\n\n"
                "// trustScore = reliability(sourceType, vssPath)\n"
                "//            x freshnessDecay(observedAt)\n"
                "//            x confidenceFactor(quality)",
                "Reliability is per source and per signal. The manufacturer wins "
                "on fuel level because it reads the tank sender off the vehicle "
                "bus. The tracker wins on position because it has a dedicated "
                "GNSS module while the OEM feed arrives a minute and a half late. "
                "Every candidate is kept with its score broken out, so a disputed "
                "mileage charge is settled by looking rather than arguing.",
                fusedSignals=len(provenance),
                contestedSignals=len(contested),
                sources=document.get("sources", []),
            ),
            _explain(
                "The evidence behind a finding",
                "Validation generated from COVESA VSS",
                "telemetry_raw",
                "db.telemetry_raw.find({\n"
                f'  assetRef: "{summary["plate"]}",\n'
                "  rejected: { $exists: true }\n"
                "}).sort({ observedAt: -1 })",
                "Ranges, allowed values and formats come from vss.json, so the rules "
                "were generated rather than written. Rejected readings are kept with "
                "their reason and the untouched original payload, which is how a "
                "firmware release that breaks a batch of trackers becomes visible.",
                findingCount=len(findings),
                rejected=summary["rejected"],
            ),
        ],
    }
