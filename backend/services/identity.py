"""Resolve whatever a source calls a vehicle to one canonical asset.

The OEM platform knows the VIN. The tracker knows the registration. The depot
camera knows the plate. None of them agree on an identifier, so every feed has to
be resolved against the fleet record before its readings can be compared.

This is also where the swapped-tracker problem lives. A device identifier points
at whichever vehicle it was fitted to at the time, so the mapping is time-bounded
rather than permanent. The current implementation resolves against the fleet
record as it stands today, and the bounds are the natural next step once device
history is available.
"""

from __future__ import annotations

import csv
import logging
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# Registry columns that can identify a vehicle, and the kind each one is.
IDENTIFIER_COLUMNS = {
    "placa": "plate",
    "vin": "vin",
    "id_equipamento": "device",
}


@dataclass
class IdentityIndex:
    """Maps every known identifier onto the canonical asset id."""

    canonical: dict[tuple[str, str], str] = field(default_factory=dict)
    unresolved: dict[str, int] = field(default_factory=dict)

    def add(self, kind: str, value: str, asset_id: str) -> None:
        if value:
            self.canonical[(kind, value.strip().upper())] = asset_id

    def resolve(self, kind: str, value: str) -> str | None:
        if not value:
            return None
        key = (kind, value.strip().upper())
        found = self.canonical.get(key)
        if found is None:
            # Try every kind before giving up: a feed occasionally labels a
            # column as one identifier and sends another.
            for other in ("vin", "plate", "device"):
                found = self.canonical.get((other, value.strip().upper()))
                if found:
                    return found
            self.unresolved[value] = self.unresolved.get(value, 0) + 1
        return found

    def __len__(self) -> int:
        return len(self.canonical)


def build_index(registry_path: Path) -> IdentityIndex:
    """The fleet record is the authority on which identifiers belong together."""
    index = IdentityIndex()
    with registry_path.open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            asset_id = (row.get("placa") or "").strip().upper()
            if not asset_id:
                continue
            for column, kind in IDENTIFIER_COLUMNS.items():
                index.add(kind, row.get(column, ""), asset_id)
    logger.info("Identity index: %d identifiers across the fleet", len(index))
    return index
