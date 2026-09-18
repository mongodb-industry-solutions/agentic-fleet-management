"""Collections and indexes.

Two modelling decisions are worth reading before changing anything here.

signal_history is a time series collection whose metaField holds only the stable
identifier. Put a changing observation in there and the bucket splits every time
the value changes. Measured on this data, grouping every signal from one vendor
report into a single measurement document is close to twice as dense as grouping
by VSS branch, because the timestamp and the metaField are amortised over ten
values instead of two.

asset_shadow carries a top-level geo field that duplicates the location leaf.
2dsphere indexes want a stable path, and the alternative is indexing a value
nested five levels down inside the VSS tree. Deliberate denormalisation.
"""

from __future__ import annotations

import logging

from pymongo import ASCENDING, DESCENDING, GEOSPHERE
from pymongo.database import Database
from pymongo.errors import CollectionInvalid, OperationFailure

logger = logging.getLogger(__name__)

SIGNAL_REGISTRY = "signal_registry"
ASSET_SHADOW = "asset_shadow"
SIGNAL_HISTORY = "signal_history"
TELEMETRY_RAW = "telemetry_raw"
TRIPS = "trips"
PROVENANCE = "provenance"
FINDINGS = "findings"
INGEST_RUNS = "ingest_runs"

# One quality report per vendor source, written at load time so the API never
# has to read an extract to answer for the Data quality tab. The headline across
# every source is one more document in the same collection, under a reserved id.
QUALITY_REPORTS = "quality_reports"
QUALITY_SUMMARY_ID = "__summary__"

# Raw readings are a landing zone, not a system of record. They are kept long
# enough to investigate a bad vendor feed and no longer, because at 199 bytes per
# observation against 45 for the compressed series this is the most expensive
# collection per day of retention.
#
# The clock runs from ingestedAt, not observedAt. Hanging it off the observation
# time expires a historical extract the moment it lands, which is exactly the
# extract an operator is most likely to load first.
TELEMETRY_RAW_TTL_DAYS = 7


def ensure_collections(db: Database) -> dict[str, str]:
    """Create collections and indexes. Safe to run repeatedly."""
    actions: dict[str, str] = {}

    existing = set(db.list_collection_names())

    if SIGNAL_HISTORY not in existing:
        try:
            db.create_collection(
                SIGNAL_HISTORY,
                timeseries={
                    "timeField": "ts",
                    "metaField": "src",
                    "granularity": "seconds",
                },
            )
            actions[SIGNAL_HISTORY] = "created as a time series collection"
        except CollectionInvalid:
            actions[SIGNAL_HISTORY] = "already present"
    else:
        actions[SIGNAL_HISTORY] = "already present"

    db[SIGNAL_REGISTRY].create_index([("kind", ASCENDING)])
    db[SIGNAL_REGISTRY].create_index([("unit", ASCENDING)])
    actions[SIGNAL_REGISTRY] = "indexed"

    db[ASSET_SHADOW].create_index([("geo", GEOSPHERE)])
    db[ASSET_SHADOW].create_index([("registry.status", ASCENDING)])
    db[ASSET_SHADOW].create_index([("registry.regiao", ASCENDING), ("registry.deposito", ASCENDING)])
    db[ASSET_SHADOW].create_index([("health.trust", ASCENDING)])
    db[ASSET_SHADOW].create_index([("health.openFindings", DESCENDING)])
    actions[ASSET_SHADOW] = "indexed, including 2dsphere on geo"

    db[TELEMETRY_RAW].create_index([("assetRef", ASCENDING), ("observedAt", DESCENDING)])
    db[TELEMETRY_RAW].create_index([("rejected.reason", ASCENDING)])
    db[TELEMETRY_RAW].create_index([("sourceType", ASCENDING)])

    # A database written before the TTL moved still carries the old index, and
    # leaving it in place would expire the landing zone by observation time
    # whatever the new one says.
    try:
        if "observedAt_ttl" in db[TELEMETRY_RAW].index_information():
            db[TELEMETRY_RAW].drop_index("observedAt_ttl")
            logger.info("Dropped the legacy observedAt TTL index on %s", TELEMETRY_RAW)
    except OperationFailure as exc:
        logger.warning("Could not drop the legacy TTL index: %s", exc)

    try:
        db[TELEMETRY_RAW].create_index(
            [("ingestedAt", ASCENDING)],
            expireAfterSeconds=TELEMETRY_RAW_TTL_DAYS * 24 * 3600,
            name="ingestedAt_ttl",
        )
        actions[TELEMETRY_RAW] = f"indexed, TTL {TELEMETRY_RAW_TTL_DAYS}d from ingest"
    except OperationFailure as exc:
        logger.warning("Could not create TTL index on %s: %s", TELEMETRY_RAW, exc)
        actions[TELEMETRY_RAW] = "indexed, TTL unavailable"

    db[PROVENANCE].create_index([("assetRef", ASCENDING), ("vssPath", ASCENDING)])
    db[PROVENANCE].create_index([("vssPath", ASCENDING), ("chosen.src", ASCENDING)])
    actions[PROVENANCE] = "indexed"

    db[TRIPS].create_index([("assetRef", ASCENDING), ("startedAt", DESCENDING)])
    db[TRIPS].create_index([("startedAt", DESCENDING)])
    actions[TRIPS] = "indexed"

    db[FINDINGS].create_index([("assetRef", ASCENDING)])
    db[FINDINGS].create_index([("severity", ASCENDING), ("occurrences", DESCENDING)])
    db[FINDINGS].create_index([("kind", ASCENDING)])
    actions[FINDINGS] = "indexed"

    db[INGEST_RUNS].create_index([("startedAt", DESCENDING)])
    actions[INGEST_RUNS] = "indexed"

    return actions


def drop_fleet_data(db: Database) -> list[str]:
    """Clear operational data, leaving the signal registry in place."""
    dropped = []
    for name in (ASSET_SHADOW, SIGNAL_HISTORY, TELEMETRY_RAW, TRIPS, FINDINGS, PROVENANCE):
        if name in db.list_collection_names():
            db[name].drop()
            dropped.append(name)
    return dropped


def collection_stats(db: Database) -> list[dict]:
    """Size and count per collection, for the Behind the Scenes panel."""
    out = []
    for name in (
        SIGNAL_REGISTRY, ASSET_SHADOW, SIGNAL_HISTORY,
        TELEMETRY_RAW, TRIPS, FINDINGS, PROVENANCE,
    ):
        if name not in db.list_collection_names():
            continue
        try:
            stats = db.command("collStats", name)
        except OperationFailure:
            continue
        count = stats.get("count", 0)
        buckets = None
        if stats.get("timeseries"):
            # collStats reports bucket-level figures for a time series
            # collection, so the measurement count comes from the metadata.
            buckets = stats["timeseries"].get("bucketCount")
            count = stats["timeseries"].get("numBucketsClosedDueToCount", 0) or count
            count = db[name].estimated_document_count() or count
        out.append({
            "collection": name,
            "count": count,
            "buckets": buckets,
            "storageSize": stats.get("storageSize", 0),
            "totalIndexSize": stats.get("totalIndexSize", 0),
            "avgObjSize": stats.get("avgObjSize"),
            "timeseries": bool(stats.get("timeseries")),
        })
    return out
