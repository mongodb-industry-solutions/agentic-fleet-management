"""Load vendor extracts into MongoDB, fusing sources that overlap.

Each extract is streamed independently: rows are mapped onto COVESA VSS,
validated, written to the time series history, and rejects are landed with their
reason. What each source last said about each signal is held back.

Once every source has been read, the held-back answers are fused. Two feeds
describing the same car disagree, so a value is chosen by trust score and every
candidate is kept as provenance. That ordering matters: fusion cannot run per
extract, because it needs all the answers before it can compare them.
"""

from __future__ import annotations

import csv
import logging
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from pymongo import UpdateOne
from pymongo.database import Database

from db.schema import (
    ASSET_SHADOW,
    FINDINGS,
    INGEST_RUNS,
    PROVENANCE,
    QUALITY_REPORTS,
    QUALITY_SUMMARY_ID,
    SIGNAL_HISTORY,
    SIGNAL_REGISTRY,
    TELEMETRY_RAW,
    TRIPS,
    ensure_collections,
)
from ingest.pipeline import read_csv, run
from models.mapping import SourceMapping
from models.telemetry import ReadingResult
from services.fleet_rules import (
    IGNITION,
    build_findings,
    build_trips,
    trust_from_findings,
)
from services.fusion import Candidate, FusionConfig, fuse
from services.identity import IdentityIndex, build_index
from services.profile_service import ProfileService
from vss.loader import get_registry

logger = logging.getLogger(__name__)

BATCH = 20_000

LATITUDE = "CurrentLocation.Latitude"
LONGITUDE = "CurrentLocation.Longitude"


def _nest(flat: dict[str, dict]) -> dict:
    """Turn path-keyed leaves into the nested tree VSS describes.

    Storing "Powertrain.FuelSystem.RelativeLevel" as a single field name is the
    anti-pattern: MongoDB will accept it, but a dotted query path then reads as
    nested traversal and matches nothing.
    """
    tree: dict = {}
    for path, leaf in flat.items():
        node = tree
        parts = path.split(".")
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = leaf
    return tree


def registry_path(profile: ProfileService) -> Path | None:
    for candidate in (
        profile.directory / "data" / "vehicles.csv",
        Path(__file__).resolve().parents[1] / "data" / "sample" / "vehicles.csv",
    ):
        if candidate.exists():
            return candidate
    return None


def load_signal_registry(db: Database) -> int:
    """Write every VSS leaf as a document, so the spec is queryable from Atlas."""
    registry = get_registry()
    operations = [
        UpdateOne({"_id": spec.path}, {"$set": spec.to_document()}, upsert=True)
        for spec in registry.signals.values()
    ]
    for start in range(0, len(operations), BATCH):
        db[SIGNAL_REGISTRY].bulk_write(operations[start:start + BATCH], ordered=False)
    logger.info("signal_registry: %d signals from VSS %s", len(operations), registry.version)
    return len(operations)


def load_vehicle_registry(db: Database, profile: ProfileService) -> int:
    """Seed shadow documents from the fleet management system's own records."""
    path = registry_path(profile)
    if path is None:
        logger.warning("No vehicle registry found")
        return 0

    with path.open(encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))

    operations = [
        UpdateOne(
            {"_id": row["placa"].strip().upper()},
            {"$set": {
                "assetClass": "vehicle",
                "ontology": f"vss@{get_registry().version}",
                "registry": row,
            }},
            upsert=True,
        )
        for row in rows if row.get("placa")
    ]
    for start in range(0, len(operations), BATCH):
        db[ASSET_SHADOW].bulk_write(operations[start:start + BATCH], ordered=False)
    logger.info("asset_shadow: %d vehicles seeded from the registry", len(operations))
    return len(operations)


def _measurement(asset_id: str, result: ReadingResult, source_type: str) -> dict | None:
    """One measurement document per report.

    Every signal from a single vendor report shares one document, so the
    timestamp and the metaField are amortised across all of them rather than
    repeated per leaf.
    """
    accepted = result.accepted_observations
    if not accepted:
        return None
    return {
        "ts": accepted[0].observed_at,
        "src": {"assetId": asset_id, "srcId": source_type},
        "v": {obs.vss_path: obs.value for obs in accepted},
    }


def _raw_document(asset_id: str, obs, source_type: str, ingested_at: datetime) -> dict:
    doc = {
        "assetRef": asset_id,
        "sourceType": source_type,
        "vssPath": obs.vss_path,
        "value": obs.value,
        "unit": obs.unit,
        "observedAt": obs.observed_at,
        "receivedAt": obs.received_at,
        # The TTL runs from here rather than from observedAt, so loading a
        # historical extract does not expire it on arrival.
        "ingestedAt": ingested_at,
        "raw": {"column": obs.raw_column, "value": str(obs.raw_value)},
    }
    if obs.rejected:
        doc["rejected"] = {
            "reason": obs.rejected.reason.value,
            "detail": obs.rejected.detail,
            "rule": obs.rejected.rule,
        }
    return doc


class FleetAccumulator:
    """Holds what every source last said, so fusion can compare answers."""

    def __init__(self) -> None:
        # assetId -> vssPath -> sourceType -> Candidate
        self.candidates: dict[str, dict[str, dict[str, Candidate]]] = defaultdict(
            lambda: defaultdict(dict)
        )
        # assetId -> sourceType -> ignition readings, for trip reconstruction
        self.series: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))
        self.reasons: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        self.counts: dict[str, dict[str, int]] = defaultdict(
            lambda: {"reports": 0, "rejected": 0}
        )
        self.sources_seen: dict[str, set[str]] = defaultdict(set)

    def offer(self, asset_id: str, obs, quality: dict | None) -> None:
        """Keep the most recent answer from each source for each signal."""
        by_source = self.candidates[asset_id][obs.vss_path]
        current = by_source.get(obs.source_type)
        if current is None or obs.observed_at >= current.observed_at:
            by_source[obs.source_type] = Candidate(
                source_type=obs.source_type,
                value=obs.value,
                observed_at=obs.observed_at,
                unit=obs.unit,
                quality=quality,
            )


class ExtractLoader:
    """Streams one extract, writing history and feeding the accumulator."""

    def __init__(
        self,
        mapping: SourceMapping,
        accumulator: FleetAccumulator,
        identity: IdentityIndex,
        keep_accepted_raw: bool = False,
    ):
        self.mapping = mapping
        self.source_type = mapping.source.source_type
        self.accumulator = accumulator
        self.identity = identity
        self.keep_accepted_raw = keep_accepted_raw
        # One timestamp for the whole extract. Every raw document in this load
        # expires together, and it is read once rather than per observation.
        self.ingested_at = datetime.now(timezone.utc)

        self.rows = 0
        self.rows_rejected = 0
        self.rows_unresolved = 0
        self.observations = 0
        self.observations_rejected = 0
        self.assets: set[str] = set()

    def absorb(self, result: ReadingResult) -> tuple[dict | None, list[dict]]:
        self.rows += 1
        if result.row_rejection is not None or not result.asset_ref:
            self.rows_rejected += 1
            return None, []

        # The OEM knows the VIN and the tracker knows the registration, so every
        # feed is resolved against the fleet record before anything is compared.
        asset_id = self.identity.resolve(result.asset_ref_kind, result.asset_ref)
        if asset_id is None:
            self.rows_unresolved += 1
            return None, []

        self.assets.add(asset_id)
        self.accumulator.counts[asset_id]["reports"] += 1
        self.accumulator.sources_seen[asset_id].add(self.source_type)

        quality = None
        if result.position is not None:
            quality = {
                "hdop": result.position.hdop,
                "satellites": result.position.satellites,
                "fix": result.position.fix,
                "accuracyM": result.position.accuracy_m,
            }

        raw_docs = []
        for obs in result.observations:
            self.observations += 1
            if obs.rejected is not None:
                self.observations_rejected += 1
                self.accumulator.counts[asset_id]["rejected"] += 1
                self.accumulator.reasons[asset_id][obs.rejected.reason.value] += 1
                raw_docs.append(
                    _raw_document(asset_id, obs, self.source_type, self.ingested_at)
                )
                continue

            if self.keep_accepted_raw:
                raw_docs.append(
                    _raw_document(asset_id, obs, self.source_type, self.ingested_at)
                )

            self.accumulator.offer(asset_id, obs, quality)

            if obs.vss_path == IGNITION:
                self.accumulator.series[asset_id][self.source_type].append((
                    obs.observed_at,
                    bool(obs.value),
                    result.position.latitude if result.position else None,
                    result.position.longitude if result.position else None,
                    None,
                ))

        return _measurement(asset_id, result, self.source_type), raw_docs


def _chunks(items: Iterator, size: int):
    batch = []
    for item in items:
        batch.append(item)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


def load_extract(
    db: Database,
    profile: ProfileService,
    source_name: str,
    accumulator: FleetAccumulator,
    identity: IdentityIndex,
    path: str | None = None,
    limit: int | None = None,
    keep_accepted_raw: bool = False,
) -> dict:
    """Stream one extract into the time series and the landing zone."""
    mapping = profile.mappings[source_name]
    data_file = profile.resolve_data_file(mapping, path)
    started = time.perf_counter()
    started_at = datetime.now(timezone.utc)

    loader = ExtractLoader(mapping, accumulator, identity, keep_accepted_raw)
    results = run(
        read_csv(data_file, mapping),
        mapping,
        service_area=profile.service_area,
        limit=limit,
    )

    measurements: list[dict] = []
    raw: list[dict] = []
    written_measurements = 0
    written_raw = 0

    def flush() -> None:
        nonlocal written_measurements, written_raw
        if measurements:
            db[SIGNAL_HISTORY].insert_many(measurements, ordered=False)
            written_measurements += len(measurements)
            measurements.clear()
        if raw:
            db[TELEMETRY_RAW].insert_many(raw, ordered=False)
            written_raw += len(raw)
            raw.clear()

    for result in results:
        measurement, raw_docs = loader.absorb(result)
        if measurement:
            measurements.append(measurement)
        raw.extend(raw_docs)
        if len(measurements) >= BATCH or len(raw) >= BATCH:
            flush()
    flush()

    summary = {
        "source": source_name,
        "sourceType": loader.source_type,
        "file": str(data_file),
        "startedAt": started_at,
        "seconds": round(time.perf_counter() - started, 2),
        "rows": loader.rows,
        "rowsRejected": loader.rows_rejected,
        "rowsUnresolved": loader.rows_unresolved,
        "observations": loader.observations,
        "observationsRejected": loader.observations_rejected,
        "vehicles": len(loader.assets),
        "measurements": written_measurements,
        "rawWritten": written_raw,
    }
    db[INGEST_RUNS].insert_one(dict(summary))
    logger.info("Read %s in %.1fs", source_name, summary["seconds"])
    return summary


def fuse_and_write(db: Database, accumulator: FleetAccumulator) -> dict:
    """Choose a value per signal, then write shadows, trips and findings."""
    config = FusionConfig.from_db(db)

    # Freshness is measured against the newest reading in the load rather than
    # against wall-clock time. For a batch of historical data the question is
    # which source was fresher at the time, not how long ago the extract was
    # taken, and scoring an archive against today would decay every candidate to
    # nothing equally. A live stream would pass the current time instead.
    now = max(
        (
            candidate.observed_at
            for by_path in accumulator.candidates.values()
            for by_source in by_path.values()
            for candidate in by_source.values()
        ),
        default=datetime.now(timezone.utc),
    )
    logger.info("Fusing as of %s, the newest reading in this load", now)

    shadow_ops: list[UpdateOne] = []
    provenance_docs: list[dict] = []
    trip_docs: list[dict] = []
    finding_docs: list[dict] = []
    contested = 0
    multi_source = 0
    wins: dict[str, int] = defaultdict(int)

    for asset_id, by_path in accumulator.candidates.items():
        state: dict[str, dict] = {}
        contested_paths: list[str] = []

        for vss_path, by_source in by_path.items():
            fused = fuse(vss_path, list(by_source.values()), config, now)
            if fused is None:
                continue
            state[vss_path] = fused.to_leaf()

            if len(by_source) > 1:
                wins[f"{fused.source_type}:{vss_path}"] += 1
                provenance_docs.append({
                    "assetRef": asset_id,
                    "fusedAt": now,
                    **fused.to_provenance(),
                })
                if fused.contested:
                    contested_paths.append(vss_path)

        if len(accumulator.sources_seen[asset_id]) > 1:
            multi_source += 1
        contested += len(contested_paths)

        findings = build_findings(accumulator.reasons[asset_id], state)
        counts = accumulator.counts[asset_id]

        latitude, longitude = state.get(LATITUDE), state.get(LONGITUDE)
        geo = None
        if latitude and longitude:
            geo = {"type": "Point", "coordinates": [longitude["v"], latitude["v"]]}

        shadow_ops.append(UpdateOne(
            {"_id": asset_id},
            {"$set": {
                "assetClass": "vehicle",
                "state": _nest(state),
                "geo": geo,
                "sources": sorted(accumulator.sources_seen[asset_id]),
                "health": {
                    "trust": trust_from_findings(findings),
                    "openFindings": len(findings),
                    "topFinding": findings[0]["title"] if findings else None,
                    "topSeverity": findings[0]["severity"] if findings else None,
                    "reports": counts["reports"],
                    "rejected": counts["rejected"],
                    "contestedSignals": contested_paths,
                },
                "updatedAt": max((leaf["ts"] for leaf in state.values()), default=None),
            }},
            upsert=True,
        ))

        for finding in findings:
            finding_docs.append({**finding, "assetRef": asset_id, "detectedAt": now})

        # Trips come from whichever source sampled ignition most often, since
        # transitions are what matter and more samples resolve them better.
        by_source_series = accumulator.series.get(asset_id) or {}
        if by_source_series:
            best = max(by_source_series.values(), key=len)
            for trip in build_trips(sorted(best, key=lambda r: r[0])):
                trip_docs.append({**trip, "assetRef": asset_id})

    for batch in _chunks(iter(shadow_ops), BATCH):
        db[ASSET_SHADOW].bulk_write(batch, ordered=False)

    for collection, documents in (
        (TRIPS, trip_docs),
        (FINDINGS, finding_docs),
        (PROVENANCE, provenance_docs),
    ):
        db[collection].delete_many({})
        for batch in _chunks(iter(documents), BATCH):
            db[collection].insert_many(batch, ordered=False)

    return {
        "vehicles": len(shadow_ops),
        "multiSource": multi_source,
        "fusedSignals": len(provenance_docs),
        "contestedSignals": contested,
        "trips": len(trip_docs),
        "findings": len(finding_docs),
        "winsBySource": dict(sorted(wins.items(), key=lambda kv: -kv[1])),
    }


def write_quality_reports(db: Database, profile: ProfileService) -> list[str]:
    """Persist each source's quality report, and the headline across all of them.

    The report is a pure function of the extract, and a load is the one moment
    the extract is guaranteed to be in hand. Writing it here is what lets the API
    answer the Data quality tab with a findOne instead of re-reading 800,000
    rows, and it is cheap: ProfileService has usually just cached the report, so
    this is a lookup rather than a second pass.

    The combined asset count is computed here rather than in the route because
    the reports still hold the identifier sets, so the vehicles both feeds saw
    are counted once instead of twice.
    """
    reports = profile.run_all()
    if not reports:
        logger.warning("No extracts found, so no quality reports were written")
        return []

    generated_at = datetime.now(timezone.utc)
    written: list[str] = []

    for name, report in reports.items():
        document = report.to_dict()
        document["_id"] = name
        document["generatedAt"] = generated_at
        db[QUALITY_REPORTS].replace_one({"_id": name}, document, upsert=True)
        written.append(name)

    observations = sum(r.observations_read for r in reports.values())
    rejected = sum(r.observations_rejected for r in reports.values())
    assets: set[str] = set()
    for report in reports.values():
        assets |= report.assets_seen

    summary = {
        "_id": QUALITY_SUMMARY_ID,
        "profile": profile.name,
        "generatedAt": generated_at,
        "sources": [
            {
                "name": name,
                "sourceType": report.source_type,
                "rowsRead": report.rows_read,
                "observationsRead": report.observations_read,
                "observationsRejected": report.observations_rejected,
                "rejectRate": round(report.reject_rate, 5),
                "assetsSeen": len(report.assets_seen),
                "topReasons": [
                    {"reason": reason, "count": count}
                    for reason, count in report.observation_reasons.most_common(3)
                ],
            }
            for name, report in reports.items()
        ],
        "totals": {
            "rowsRead": sum(r.rows_read for r in reports.values()),
            "observationsRead": observations,
            "observationsRejected": rejected,
            "rejectRate": round(rejected / observations, 5) if observations else 0.0,
            "assetsSeen": len(assets),
        },
    }
    db[QUALITY_REPORTS].replace_one({"_id": QUALITY_SUMMARY_ID}, summary, upsert=True)

    logger.info("Quality reports written for %s", ", ".join(written))
    return written


def load_profile(
    db: Database, profile: ProfileService, limit: int | None = None
) -> dict:
    """Prepare the database, read every source, then fuse."""
    actions = ensure_collections(db)
    signals = load_signal_registry(db)
    vehicles = load_vehicle_registry(db, profile)

    path = registry_path(profile)
    identity = build_index(path) if path else IdentityIndex()
    seeded = FusionConfig().seed(db)

    accumulator = FleetAccumulator()
    runs = []
    for name in profile.mappings:
        try:
            runs.append(
                load_extract(db, profile, name, accumulator, identity, limit=limit)
            )
        except FileNotFoundError as exc:
            logger.warning("Skipping %s: %s", name, exc)

    started = time.perf_counter()
    fusion = fuse_and_write(db, accumulator)
    fusion["seconds"] = round(time.perf_counter() - started, 2)

    # Only for a full load. A limited run has read part of an extract, and a
    # partial report is worse than the one already stored.
    quality_reports = write_quality_reports(db, profile) if limit is None else []

    return {
        "collections": actions,
        "signalsLoaded": signals,
        "vehiclesSeeded": vehicles,
        "identifiers": len(identity),
        "fusionProfiles": seeded,
        "runs": runs,
        "fusion": fusion,
        "qualityReports": quality_reports,
    }
