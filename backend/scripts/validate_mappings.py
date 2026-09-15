"""Check every mapping file in a profile against the VSS registry.

Run this in CI. A mapping that names a signal which does not exist, declares a
unit that cannot be converted, or produces values the datatype cannot hold should
break the build rather than surface months later as a fleet of vehicles quietly
reporting nothing.

    uv run python scripts/validate_mappings.py
    uv run python scripts/validate_mappings.py --profile localiza --strict
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ingest.mapping_loader import load_profile_mappings, validate_mapping  # noqa: E402
from vss.loader import get_registry  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default="leafy")
    parser.add_argument(
        "--strict", action="store_true", help="Treat warnings as failures too"
    )
    args = parser.parse_args()

    registry = get_registry()
    profile_dir = REPO_ROOT / "profiles" / args.profile
    mappings = load_profile_mappings(profile_dir)

    print(f"COVESA VSS {registry.version}, {len(registry):,} signals")
    print(f"Profile {args.profile!r}: {len(mappings)} source(s)\n")

    failed = False
    for name, mapping in mappings.items():
        result = validate_mapping(mapping, registry)
        status = "OK" if result.ok else "FAILED"
        print(f"  {status:<7} {name}  ({result.signal_count} signals)")

        for issue in result.issues:
            marker = "error " if issue.severity == "error" else "warn  "
            print(f"    {marker} {issue.column}: {issue.message}")

        if not result.ok or (args.strict and result.warnings):
            failed = True

    print()
    if failed:
        print("Mapping validation failed.")
        return 1
    print("All mappings resolve against the specification.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
