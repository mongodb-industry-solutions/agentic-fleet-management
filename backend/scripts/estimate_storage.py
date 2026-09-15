"""Estimate MongoDB storage from the documents this pipeline actually produces.

Rather than guessing, this runs a real extract through the real mapping and
validation, builds the time series measurement documents and the asset shadow
that would be written, and measures their BSON size. That gives a logical size
per observation grounded in the actual schema.

Physical size is smaller. A time series collection buckets measurements sharing a
metaField and compresses each column: delta encoding for continuous numerics like
speed and temperature, run length encoding for repetitive state like ignition and
door. How much smaller depends on the signal mix, so the projection is given as a
range and should be replaced with a measured figure as soon as a cluster exists.

    uv run python scripts/estimate_storage.py
    uv run python scripts/estimate_storage.py --fleet 600000 --reports-per-day 1152
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from bson import BSON

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ingest.pipeline import read_csv, run  # noqa: E402
from services.profile_service import ProfileService  # noqa: E402
from vss.loader import get_registry  # noqa: E402

GB = 1024 ** 3
TB = 1024 ** 4

# Observed compression on time series data regions for mixed automotive signals.
# The low end assumes noisy floats that resist delta encoding; the high end
# assumes a signal mix with plenty of repeated state. Measure and replace.
COMPRESSION_RANGE = (8.0, 20.0)


def branch_of(vss_path: str, depth: int | None = None) -> str:
    """The branch a leaf is grouped under.

    depth=None groups by the full parent path, which is the obvious choice and
    turns out to pack poorly. A shallower depth puts more leaves in the same
    measurement document, so the timestamp and metaField are amortised over more
    values.
    """
    parts = vss_path.split(".")
    if len(parts) == 1:
        return ""
    if depth is None:
        return ".".join(parts[:-1])
    return ".".join(parts[:depth])


def leaf_of(vss_path: str) -> str:
    return vss_path.rsplit(".", 1)[-1]


def build_measurements(results, source_type: str, depth: int | None = None) -> list[dict]:
    """Group accepted observations into time series measurement documents.

    One document per vehicle, per branch, per timestamp. The metaField holds only
    the stable identifier, never a changing observation, so buckets do not split
    every time a value changes.
    """
    grouped: dict[tuple, dict] = defaultdict(dict)
    for result in results:
        for obs in result.observations:
            if not obs.accepted:
                continue
            key = (obs.asset_ref, branch_of(obs.vss_path, depth), obs.observed_at)
            # Leaves from different sub-branches share a document, so the key has
            # to stay unique within it.
            label = obs.vss_path if depth is not None else leaf_of(obs.vss_path)
            grouped[key][label] = obs.value

    return [
        {
            "ts": ts,
            "src": {"assetId": asset, "branch": branch, "srcId": source_type},
            "v": values,
        }
        for (asset, branch, ts), values in grouped.items()
    ]


def build_shadow(results, source_type: str) -> list[dict]:
    """One current-state document per vehicle, with the VSS tree preserved."""
    state: dict[str, dict] = defaultdict(dict)
    for result in results:
        for obs in result.observations:
            if not obs.accepted:
                continue
            node = state[obs.asset_ref]
            parts = obs.vss_path.split(".")
            for part in parts[:-1]:
                node = node.setdefault(part, {})
            node[parts[-1]] = {
                "v": obs.value,
                "ts": obs.observed_at,
                "src": source_type,
                "trustScore": 0.87,
                "derived": False,
            }

    return [
        {
            "_id": asset,
            "assetClass": "vehicle",
            "ontology": f"vss@{get_registry().version}",
            "attributes": {"make": "", "model": "", "year": 2024, "powertrain": "ICE"},
            "state": tree,
            "health": {"openFaults": 0, "riskScore": 0.0},
            "geo": {"type": "Point", "coordinates": [-46.63, -23.55]},
            "updatedAt": datetime.now(timezone.utc),
        }
        for asset, tree in state.items()
    ]


def bson_bytes(documents: list[dict]) -> int:
    return sum(len(BSON.encode(doc)) for doc in documents)


def human(byte_count: float) -> str:
    if byte_count >= TB:
        return f"{byte_count / TB:,.1f} TB"
    if byte_count >= GB:
        return f"{byte_count / GB:,.1f} GB"
    return f"{byte_count / (1024 ** 2):,.1f} MB"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default=None)
    parser.add_argument("--source", default=None)
    parser.add_argument("--fleet", type=int, default=600_000)
    parser.add_argument(
        "--reports-per-day",
        type=int,
        default=1152,
        help="Position reports per vehicle per day. 1152 is 8h driving at 30s "
             "plus 16h parked at 5min. Always-on 30s would be 2880.",
    )
    parser.add_argument("--retention-days", type=int, default=365)
    parser.add_argument("--raw-retention-days", type=int, default=2)
    args = parser.parse_args()

    service = ProfileService(args.profile)
    name = args.source or next(iter(service.mappings))
    mapping = service.mappings[name]
    data_file = service.resolve_data_file(mapping)

    results = list(
        run(read_csv(data_file, mapping), mapping, service_area=service.service_area)
    )

    observations = sum(len(r.accepted_observations) for r in results)
    reports = sum(1 for r in results if r.row_rejection is None)
    vehicles = len({r.asset_ref for r in results if r.asset_ref})

    print("\n  Grouping strategy for the time series metaField")
    strategies = [
        ("full parent path", None),
        ("top-level branch", 1),
        ("one doc per report", 0),
    ]
    best = None
    for label, depth in strategies:
        docs = build_measurements(results, mapping.source.source_type, depth)
        size = bson_bytes(docs)
        per_obs = size / observations
        print(
            f"    {label:<22} {len(docs):>8,} docs   "
            f"{observations / len(docs):>4.1f} values/doc   {per_obs:>5.1f} B/observation"
        )
        if best is None or per_obs < best[1]:
            best = (depth, per_obs, docs)

    measurements = best[2]
    print(f"    Using the densest of these for the projection below.")
    shadow = build_shadow(results, mapping.source.source_type)

    ts_bytes = bson_bytes(measurements)
    shadow_bytes = bson_bytes(shadow)
    raw_bytes = bson_bytes([
        {
            "assetRef": r.asset_ref,
            "sourceType": mapping.source.source_type,
            "observedAt": o.observed_at,
            "receivedAt": o.received_at,
            "vssPath": o.vss_path,
            "value": o.value,
            "raw": {"col": o.raw_column, "val": str(o.raw_value)},
        }
        for r in results for o in r.observations
    ])

    per_obs_logical = ts_bytes / observations
    per_obs_raw = raw_bytes / max(1, sum(len(r.observations) for r in results))
    obs_per_report = observations / max(1, reports)
    per_vehicle_bytes = shadow_bytes / max(1, vehicles)

    print(f"\n  Measured from {data_file.name}, {mapping.source.source_type}")
    print(f"    Vehicles                     {vehicles:,}")
    print(f"    Reports                      {reports:,}")
    print(f"    Accepted observations        {observations:,}")
    print(f"    Observations per report      {obs_per_report:.1f}")
    print(f"    Time series documents        {len(measurements):,}")
    print(f"    Logical BSON per observation {per_obs_logical:.1f} B")
    print(f"    Landing zone per observation {per_obs_raw:.1f} B")
    print(f"    Asset shadow per vehicle     {per_vehicle_bytes:,.0f} B")

    daily_obs = args.fleet * args.reports_per_day * obs_per_report
    print(f"\n  Projection: {args.fleet:,} vehicles, {args.reports_per_day:,} reports/vehicle/day")
    print(f"    Observations per day         {daily_obs / 1e9:,.2f} billion")

    print(f"\n  Signal history, {args.retention_days} day retention")
    logical_year = daily_obs * per_obs_logical * args.retention_days
    print(f"    Logical (uncompressed)       {human(logical_year)}")
    lo, hi = COMPRESSION_RANGE
    print(f"    At {hi:.0f}x compression           {human(logical_year / hi)}")
    print(f"    At {lo:.0f}x compression            {human(logical_year / lo)}")

    print(f"\n  Other collections")
    raw_total = daily_obs * per_obs_raw * args.raw_retention_days
    print(f"    Landing zone ({args.raw_retention_days}d TTL)        {human(raw_total)}")
    print(f"    Asset shadow                 {human(args.fleet * per_vehicle_bytes)}")
    rollup = args.fleet * 24 * 4 * 220 * args.retention_days
    print(f"    Hourly rollups               {human(rollup)}")

    total_lo = logical_year / hi + raw_total + args.fleet * per_vehicle_bytes + rollup
    total_hi = logical_year / lo + raw_total + args.fleet * per_vehicle_bytes + rollup
    print(f"\n    Total, one year              {human(total_lo)} to {human(total_hi)}")
    print("\n  Compression is modelled, not measured. Replace with $collStats")
    print("  from a real cluster before anyone plans capacity on it.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
