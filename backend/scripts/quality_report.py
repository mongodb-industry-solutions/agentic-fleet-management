"""Print a data quality report for a profile, with no database and no UI.

This is the deliverable of step one: point it at a historical extract and it
answers questions most fleet operators have never had measured.

    uv run python scripts/quality_report.py
    uv run python scripts/quality_report.py --source tracker_br --path /data/august.csv
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.profile_service import ProfileService  # noqa: E402
from vss.loader import get_registry  # noqa: E402


def bar(share: float, width: int = 24) -> str:
    filled = min(width, round(share * width))
    return "#" * filled + "." * (width - filled)


def render(report) -> None:
    data = report.to_dict()
    totals, positions = data["totals"], data["positions"]

    print(f"\n  Source        {data['source']['name']}  ({data['source']['sourceType']})")
    print(f"  Window        {data['window']['from']} to {data['window']['to']}")
    print(f"  Vehicles      {totals['assetsSeen']:,}")
    print(f"  Rows          {totals['rowsRead']:,} read, {totals['rowsRejected']:,} unusable")
    print(
        f"  Readings      {totals['observationsRead']:,} read, "
        f"{totals['observationsRejected']:,} rejected "
        f"({totals['rejectRate'] * 100:.2f}%)"
    )
    print(
        f"  Positions     {positions['checked']:,} checked, "
        f"{positions['rejected']:,} flagged "
        f"({positions['rejectRate'] * 100:.2f}%)"
    )

    for title, rows in (
        ("Whole rows discarded", data["rowReasons"]),
        ("Individual readings rejected", data["observationReasons"]),
    ):
        if not rows:
            continue
        print(f"\n  {title}")
        for row in rows:
            print(
                f"    {row['count']:>7,}  {bar(row['share'] * 20)}  "
                f"{row['label']}"
            )

    if data["worstSignals"]:
        print("\n  Signals with the most unusable data")
        for row in data["worstSignals"][:6]:
            reasons = ", ".join(f"{k} {v}" for k, v in row["reasons"].items())
            print(
                f"    {row['rejectRate'] * 100:5.2f}%  {row['vssPath']:<52} {reasons}"
            )

    print("\n  Worked examples")
    for reason, samples in list(data["samples"].items())[:4]:
        if not samples:
            continue
        example = samples[0]
        print(f"    {reason}")
        print(
            f"      row {example['row']}, vehicle {example.get('assetRef')}, "
            f"raw value {example.get('rawValue')!r}"
        )
        print(f"      {example['detail']}")
    print()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default=None)
    parser.add_argument("--source", default=None, help="Default: every source")
    parser.add_argument("--path", default=None, help="Override the extract path")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--json", action="store_true", help="Emit the raw report")
    args = parser.parse_args()

    registry = get_registry()
    service = ProfileService(args.profile)

    if not args.json:
        print(f"\nCOVESA VSS {registry.version}, {len(registry):,} signals")
        print(f"Profile {service.name!r}")

    names = [args.source] if args.source else list(service.mappings)
    reports = []
    for name in names:
        try:
            reports.append(service.run_quality(name, path=args.path, limit=args.limit))
        except (KeyError, FileNotFoundError, ValueError) as exc:
            print(f"  Skipping {name}: {exc}")

    if not reports:
        return 1

    if args.json:
        print(json.dumps([r.to_dict() for r in reports], indent=2))
    else:
        for report in reports:
            render(report)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
