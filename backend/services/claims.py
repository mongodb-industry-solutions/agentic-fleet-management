"""Damage photos as settled claims, and finding the ones like this one.

The valuable workflow is not "these two photos differ". It is "a car came back
damaged, find the closest matches among claims we have already settled, and price
this from what those actually cost". That turns a subjective assessment into an
evidenced one, which matters because damage charges get disputed.

Each claim holds the compressed photo, the labels a vision model read off it, and
a 1024 dimension embedding in a space shared with text. Repair cost is generated
here rather than assessed, since the source images carry no cost data, and the
UI says so rather than implying these are real settlements.
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

CLAIMS = "damage_claims"
VECTOR_INDEX = "damage_vector_index"

# What a repair costs, before labour and before severity. Panel work is dearer
# than trim, glass is dearer than paint.
BASE_COST = {
    "front-bumper": 620, "rear-bumper": 590, "bonnet": 740, "boot-lid": 680,
    "roof": 980, "front-door-left": 710, "front-door-right": 710,
    "rear-door-left": 690, "rear-door-right": 690,
    "front-wing-left": 540, "front-wing-right": 540,
    "rear-quarter-left": 860, "rear-quarter-right": 860,
    "headlamp": 520, "tail-light": 340, "windscreen": 480, "rear-screen": 390,
    "side-window": 260, "wing-mirror": 280, "wheel": 310, "grille": 300,
    "sill": 450, "unknown": 400,
}

SEVERITY_MULTIPLIER = {
    "cosmetic": 0.35, "minor": 0.7, "moderate": 1.25, "severe": 2.4,
}

TYPE_MULTIPLIER = {
    "scratch": 0.8, "scuff": 0.6, "dent": 1.1, "crack": 1.3, "shatter": 1.8,
    "tear": 1.2, "misalignment": 1.4, "paint-transfer": 0.5, "rust": 1.0,
    "missing-part": 1.6, "none": 0.0,
}

LABOUR_RATE = {"BR": 42.0, "NA": 95.0, "EU": 78.0}

OUTCOMES = ["charged-to-renter", "absorbed-by-fleet", "insurance-claim", "disputed"]


@dataclass
class ClaimCost:
    parts: float
    labour_hours: float
    labour: float
    total: float
    currency: str = "USD"

    def to_dict(self) -> dict:
        return {
            "parts": round(self.parts, 2),
            "labourHours": round(self.labour_hours, 1),
            "labour": round(self.labour, 2),
            "total": round(self.total, 2),
            "currency": self.currency,
        }


def estimate_cost(labels: dict, region: str, rng: random.Random) -> ClaimCost:
    """A settled figure, derived from the panel, the damage and the region.

    Deterministic given the same inputs and seed, with enough spread that
    retrieving three similar claims returns three different numbers rather than
    the same one three times.
    """
    base = BASE_COST.get(labels.get("panel", "unknown"), 400)
    severity = SEVERITY_MULTIPLIER.get(labels.get("severity", "minor"), 0.7)
    kind = TYPE_MULTIPLIER.get(labels.get("damageType", "scratch"), 0.8)

    parts = base * severity * kind * rng.uniform(0.82, 1.22)
    hours = max(0.5, severity * 3.2 * rng.uniform(0.8, 1.25))
    labour = hours * LABOUR_RATE.get(region, 78.0)

    return ClaimCost(parts=parts, labour_hours=hours, labour=labour, total=parts + labour)


def searchable_text(labels: dict, vehicle: dict | None = None) -> str:
    """What gets embedded and reranked alongside the image.

    Image similarity finds photos that look alike. This is what lets somebody
    search for what they mean rather than what they have a picture of.
    """
    parts = [
        labels.get("description", ""),
        f"{labels.get('severity', '')} {labels.get('damageType', '')}".strip(),
        f"on the {labels.get('panel', 'unknown').replace('-', ' ')}",
    ]
    if labels.get("bodyColour"):
        parts.append(f"{labels['bodyColour']} vehicle")
    if vehicle:
        make_model = f"{vehicle.get('make', '')} {vehicle.get('model', '')}".strip()
        if make_model:
            parts.append(make_model)
    return ". ".join(p for p in parts if p)


def build_claim(
    claim_id: str,
    image_bytes: bytes,
    labels: dict,
    vehicle: dict | None,
    rng: random.Random,
    source: str,
) -> dict:
    """One settled claim, ready for embedding."""
    region = (vehicle or {}).get("region") or rng.choice(["BR", "NA", "EU"])
    cost = estimate_cost(labels, region, rng)
    settled_on = datetime.now(timezone.utc) - timedelta(days=rng.randint(20, 900))

    return {
        "_id": claim_id,
        "source": source,
        "image": image_bytes,
        "imageFormat": "jpeg",
        "imageBytes": len(image_bytes),
        "labels": labels,
        "damageType": labels.get("damageType"),
        "panel": labels.get("panel"),
        "severity": labels.get("severity"),
        "angle": labels.get("angle"),
        "text": searchable_text(labels, vehicle),
        "vehicle": vehicle,
        "region": region,
        "cost": cost.to_dict(),
        "outcome": rng.choices(OUTCOMES, weights=[58, 22, 14, 6])[0],
        "settledOn": settled_on,
        "costIsSynthetic": True,
    }


def vector_index_definition() -> dict:
    """The index, with filters as well as the vector.

    Comparing a bumper photo against wheel photos is noise. Pre-filtering by
    panel, damage type and angle is a small detail with a large effect on what
    comes back, and it only works if the labels are a fixed vocabulary.
    """
    return {
        "name": VECTOR_INDEX,
        "type": "vectorSearch",
        "definition": {
            "fields": [
                {
                    "type": "vector",
                    "path": "embedding",
                    "numDimensions": 1024,
                    "similarity": "cosine",
                },
                {"type": "filter", "path": "panel"},
                {"type": "filter", "path": "damageType"},
                {"type": "filter", "path": "severity"},
                {"type": "filter", "path": "angle"},
                {"type": "filter", "path": "region"},
            ]
        },
    }
