"""Suppliers, batches and the parts fitted to each vehicle.

A recall is the question a fleet operator cannot answer with a lookup. A supplier
flags a batch, and what they need back is the list of customers currently driving
a car containing something from it. That runs supplier to batch to part to
vehicle to rental agreement to customer, and every hop is a different entity.

None of this comes from telemetry. It comes from the parts system, the workshop
records and the rental system, which is the point: the graph is where data that
lives in different systems becomes one answerable question.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime, timedelta

SUPPLIERS = [
    ("SUP-BREMBO", "Brembo Componentes", "IT"),
    ("SUP-BOSCH", "Bosch Auto Parts", "DE"),
    ("SUP-DENSO", "Denso Manufacturing", "JP"),
    ("SUP-VALEO", "Valeo Systemes", "FR"),
    ("SUP-MAHLE", "Mahle Filtration", "DE"),
    ("SUP-AISIN", "Aisin Seiki", "JP"),
    ("SUP-MAGNA", "Magna Structures", "CA"),
    ("SUP-HELLA", "Hella Lighting", "DE"),
]

# Part types a rental fleet actually replaces, with how safety-critical they are.
# Criticality drives how loudly a recall on that part needs to be handled.
PART_TYPES = [
    ("brake-caliper", "Brake caliper", "critical", 340.0),
    ("brake-pad-set", "Brake pad set", "critical", 120.0),
    ("wheel-bearing", "Wheel bearing", "critical", 210.0),
    ("airbag-module", "Airbag control module", "critical", 890.0),
    ("steering-rack", "Steering rack", "critical", 1150.0),
    ("alternator", "Alternator", "major", 430.0),
    ("water-pump", "Water pump", "major", 280.0),
    ("fuel-pump", "Fuel pump", "major", 390.0),
    ("starter-motor", "Starter motor", "major", 310.0),
    ("radiator", "Radiator", "major", 360.0),
    ("headlamp-unit", "Headlamp unit", "minor", 520.0),
    ("wiper-motor", "Wiper motor", "minor", 140.0),
    ("cabin-filter", "Cabin air filter", "minor", 45.0),
    ("oxygen-sensor", "Oxygen sensor", "minor", 180.0),
]

# How many of a vehicle's parts we track. A real fleet system holds thousands per
# vehicle; a handful is enough to make the traversal real.
PARTS_PER_VEHICLE = (4, 8)

LOTS_PER_SUPPLIER_PART = 6


@dataclass(frozen=True)
class Supplier:
    code: str
    name: str
    country: str


@dataclass(frozen=True)
class Lot:
    code: str
    supplier: str
    part_type: str
    part_name: str
    criticality: str
    unit_cost: float
    manufactured_on: datetime
    quantity: int


@dataclass(frozen=True)
class Fitment:
    part_serial: str
    lot: str
    part_type: str
    plate: str
    fitted_on: datetime
    position: str | None


POSITIONS = {
    "brake-caliper": ["front-left", "front-right", "rear-left", "rear-right"],
    "brake-pad-set": ["front-axle", "rear-axle"],
    "wheel-bearing": ["front-left", "front-right", "rear-left", "rear-right"],
    "headlamp-unit": ["left", "right"],
}


def build_catalogue(rng: random.Random, now: datetime) -> tuple[list[Supplier], list[Lot]]:
    suppliers = [Supplier(*s) for s in SUPPLIERS]
    lots: list[Lot] = []

    for supplier in suppliers:
        # A supplier makes a subset of the catalogue rather than everything.
        makes = rng.sample(PART_TYPES, rng.randint(3, 6))
        for part_type, part_name, criticality, cost in makes:
            for index in range(LOTS_PER_SUPPLIER_PART):
                manufactured = now - timedelta(days=rng.randint(60, 900))
                lots.append(Lot(
                    code=f"{supplier.code.split('-')[1]}-{part_type.upper()[:6]}-{manufactured:%Y%m}-{index:02d}",
                    supplier=supplier.code,
                    part_type=part_type,
                    part_name=part_name,
                    criticality=criticality,
                    unit_cost=round(cost * rng.uniform(0.85, 1.15), 2),
                    manufactured_on=manufactured,
                    quantity=rng.randint(400, 4000),
                ))

    return suppliers, lots


def fit_parts(
    plates: list[str], lots: list[Lot], rng: random.Random, now: datetime
) -> list[Fitment]:
    """Fit parts to vehicles, biased so a lot lands across many vehicles.

    Batches are what make a recall expensive: one bad batch spreads across the
    fleet in a way nobody tracked at the time.
    """
    by_type: dict[str, list[Lot]] = {}
    for lot in lots:
        by_type.setdefault(lot.part_type, []).append(lot)

    types = list(by_type)
    fitments: list[Fitment] = []
    serial = 0

    for plate in plates:
        chosen = rng.sample(types, rng.randint(*PARTS_PER_VEHICLE))
        for part_type in chosen:
            lot = rng.choice(by_type[part_type])
            positions = POSITIONS.get(part_type)
            serial += 1
            fitments.append(Fitment(
                part_serial=f"P{serial:08d}",
                lot=lot.code,
                part_type=part_type,
                plate=plate,
                fitted_on=lot.manufactured_on + timedelta(days=rng.randint(10, 120)),
                position=rng.choice(positions) if positions else None,
            ))

    return fitments


def pick_recall_lot(
    fitments: list[Fitment], lots: list[Lot], rng: random.Random
) -> Lot:
    """Choose a batch worth recalling: safety-critical and widely fitted."""
    counts: dict[str, int] = {}
    for fitment in fitments:
        counts[fitment.lot] = counts.get(fitment.lot, 0) + 1

    by_code = {lot.code: lot for lot in lots}
    candidates = [
        (count, code) for code, count in counts.items()
        if by_code[code].criticality == "critical" and count >= 20
    ]
    if not candidates:
        candidates = [(count, code) for code, count in counts.items()]
    candidates.sort(reverse=True)

    # Not the very largest, which would be suspiciously convenient, but well
    # inside the top of the distribution.
    _, code = candidates[min(2, len(candidates) - 1)]
    return by_code[code]
