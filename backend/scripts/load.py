"""Load a profile's extracts into MongoDB.

    uv run python scripts/load.py                 # prepare and load everything
    uv run python scripts/load.py --drop          # clear operational data first
    uv run python scripts/load.py --limit 50000   # partial load while iterating
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from db.mdb import get_mongo_client  # noqa: E402
from db.schema import collection_stats, drop_fleet_data  # noqa: E402
from services.loader import load_profile  # noqa: E402
from services.profile_service import ProfileService  # noqa: E402
from vss.loader import get_registry  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(message)s")
logging.getLogger("pymongo").setLevel(logging.WARNING)


def human(size: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:,.1f} {unit}"
        size /= 1024


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default=None)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--drop", action="store_true")
    parser.add_argument("--keep-accepted-raw", action="store_true",
                        help="Also land accepted readings in telemetry_raw")
    args = parser.parse_args()

    client = get_mongo_client()
    if client is None:
        print("No MONGODB_URI set. Add it to backend/.env first.")
        return 1

    db = client[os.getenv("DATABASE_NAME", "fleet")]
    registry = get_registry()
    profile = ProfileService(args.profile)

    print(f"\n  COVESA VSS {registry.version}, {len(registry):,} signals")
    print(f"  Profile {profile.name!r}, sources: {', '.join(profile.mappings)}")
    print(f"  Database {db.name}\n")

    if args.drop:
        dropped = drop_fleet_data(db)
        print(f"  Dropped {', '.join(dropped) if dropped else '(nothing)'}\n")

    result = load_profile(db, profile, limit=args.limit)

    for name, action in result["collections"].items():
        print(f"  {name:<18} {action}")
    print(f"\n  signal_registry    {result['signalsLoaded']:,} signals")
    print(f"  asset_shadow       {result['vehiclesSeeded']:,} vehicles seeded")

    print(f"  identity index     {result['identifiers']:,} identifiers")
    print(f"  fusion_config      {result['fusionProfiles']:,} source/signal profiles")

    for run in result["runs"]:
        print(f"\n  Source {run['source']} ({run['sourceType']})")
        print(f"    rows           {run['rows']:,} read, {run['rowsRejected']:,} unusable")
        print(f"    observations   {run['observations']:,} read, "
              f"{run['observationsRejected']:,} rejected")
        print(f"    measurements   {run['measurements']:,} written to signal_history")
        print(f"    landing zone   {run['rawWritten']:,} documents")
        print(f"    vehicles       {run['vehicles']:,}")
        if run["rowsUnresolved"]:
            print(f"    unresolved     {run['rowsUnresolved']:,} rows, identifier not in the fleet record")
        print(f"    took           {run['seconds']:,.1f}s")

    f = result["fusion"]
    print("\n  Fusion")
    print(f"    vehicles           {f['vehicles']:,}")
    print(f"    seen by 2+ sources {f['multiSource']:,}")
    print(f"    signals fused      {f['fusedSignals']:,}")
    print(f"    of those contested {f['contestedSignals']:,}")
    print(f"    derived            {f['trips']:,} trips, {f['findings']:,} findings")
    print(f"    took               {f['seconds']:,.1f}s")
    print("\n  Who won, where two sources answered")
    for key, count in list(f["winsBySource"].items())[:10]:
        source, path = key.split(":", 1)
        print(f"    {count:>7,}  {source:<16} {path}")

    print("\n  Collections in Atlas")
    for stats in collection_stats(db):
        label = f"{stats['collection']}{' (time series)' if stats['timeseries'] else ''}"
        print(f"    {label:<30} {stats['count']:>10,} docs  "
              f"{human(stats['storageSize']):>10} data  "
              f"{human(stats['totalIndexSize']):>10} indexes")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
