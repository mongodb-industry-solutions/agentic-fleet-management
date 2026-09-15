"""Generate a fleet and its tracker extract.

    uv run python scripts/make_sample_data.py                    # 5,000 vehicles
    uv run python scripts/make_sample_data.py --vehicles 25000 --regions BR
    uv run python scripts/make_sample_data.py --hours 6 --interval 30
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sim.fleet_sim import (  # noqa: E402
    REGISTRY_COLUMNS,
    TELEMETRY_COLUMNS,
    generate,
    registry_rows,
)
from sim.oem_feed import OEM_COLUMNS  # noqa: E402


def write(path: Path, columns: list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="data/sample/tracker_br.csv")
    parser.add_argument("--registry", default="data/sample/vehicles.csv")
    parser.add_argument("--oem", default="data/sample/oem_connected.csv")
    parser.add_argument("--vehicles", type=int, default=5000)
    parser.add_argument("--hours", type=float, default=2.0)
    parser.add_argument("--interval", type=int, default=60,
                        help="Seconds between reports")
    parser.add_argument("--regions", default="BR,NA,EU")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    regions = [r.strip().upper() for r in args.regions.split(",") if r.strip()]

    started = time.perf_counter()
    rows, oem_rows, fleet = generate(
        size=args.vehicles,
        hours=args.hours,
        interval_seconds=args.interval,
        seed=args.seed,
        regions=regions,
    )
    write(Path(args.out), TELEMETRY_COLUMNS, rows)
    write(Path(args.oem), OEM_COLUMNS, oem_rows)
    write(Path(args.registry), REGISTRY_COLUMNS, registry_rows(fleet))

    elapsed = time.perf_counter() - started
    size_mb = Path(args.out).stat().st_size / (1024 ** 2)
    faulted = sum(1 for car in fleet if car.faults)

    print(f"  Vehicles    {len(fleet):,} across {', '.join(regions)}")
    oem_mb = Path(args.oem).stat().st_size / (1024 ** 2)
    oem_vehicles = len({r["vin"] for r in oem_rows})
    print(f"  Tracker     {len(rows):,} rows, {size_mb:,.1f} MB -> {args.out}")
    print(f"  OEM feed    {len(oem_rows):,} rows, {oem_mb:,.1f} MB -> {args.oem}")
    print(f"              covering {oem_vehicles:,} vehicles "
          f"({oem_vehicles / len(fleet):.0%} of the fleet)")
    print(f"  Registry    {len(fleet):,} rows -> {args.registry}")
    print(f"  With faults {faulted:,} vehicles ({faulted / len(fleet):.0%})")
    print(f"  Generated in {elapsed:,.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
