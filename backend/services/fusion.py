"""Deciding which source to believe.

Two feeds describe the same car and disagree. Fusion picks a value and records
why, so nothing downstream has to guess and a dispute can be settled by looking
at the evidence rather than by arguing.

Every candidate gets a score:

    trustScore = reliability(sourceType, vssPath)
               × freshnessDecay(observedAt)
               × confidenceFactor(quality)

Reliability is per source *and* per signal, which is the part that matters. The
OEM platform wins on fuel level because it reads the tank sender directly. The
tracker wins on position because it has a dedicated GNSS module while the OEM
feed arrives a minute and a half late. Same two sources, opposite answers, both
defensible.

The numbers live in a fusion_config collection rather than in this file, so a
vendor whose firmware release degraded their GPS can be marked down that
afternoon without a deploy.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime

logger = logging.getLogger(__name__)

FUSION_CONFIG = "fusion_config"

# Applied when a source and signal have no entry of their own. Deliberately
# middling, so an unconfigured source neither wins nor loses by default.
DEFAULT_RELIABILITY = 0.60
DEFAULT_HALF_LIFE_SECONDS = 300

# Beyond this a reading is too old to enter the comparison at all, whatever its
# other qualities. A five minute old speed is not a speed.
MAX_STALENESS_SECONDS = 1800

# Per-path arbitration. Anything not listed takes the highest score.
STRATEGIES = {
    "TraveledDistance": "monotonic_max",
}


@dataclass(frozen=True)
class SourceProfile:
    """How much to believe one source about one signal."""

    source_type: str
    vss_path: str
    base_reliability: float = DEFAULT_RELIABILITY
    freshness_half_life: int = DEFAULT_HALF_LIFE_SECONDS
    confidence_fields: tuple[str, ...] = ()
    note: str = ""

    @property
    def key(self) -> str:
        return f"{self.source_type}:{self.vss_path}"

    def to_document(self) -> dict:
        return {
            "_id": self.key,
            "sourceType": self.source_type,
            "vssPath": self.vss_path,
            "baseReliability": self.base_reliability,
            "freshnessHalfLifeSeconds": self.freshness_half_life,
            "confidenceFields": list(self.confidence_fields),
            "note": self.note,
            "lastReviewedBy": "data-eng@leafymobility.com",
            "lastReviewedAt": datetime(2026, 9, 1),
        }


def _p(source_type, vss_path, reliability, half_life, fields=(), note=""):
    return SourceProfile(source_type, vss_path, reliability, half_life, fields, note)


# Seed values for the sources this profile carries. In production these are
# fitted from resolved disputes rather than guessed.
SEED_PROFILES: list[SourceProfile] = [
    # The manufacturer reads the vehicle bus, so anything the car measures about
    # itself is better from here.
    _p("OEM_CONNECTED", "Powertrain.FuelSystem.RelativeLevel", 0.95, 900,
       note="Tank sender read directly off the bus rather than inferred"),
    _p("OEM_CONNECTED", "TraveledDistance", 0.96, 3600,
       note="The odometer the dashboard shows"),
    _p("OEM_CONNECTED", "Powertrain.CombustionEngine.IsRunning", 0.94, 600,
       note="Ignition state from the vehicle itself"),
    _p("OEM_CONNECTED", "Powertrain.CombustionEngine.EngineCoolant.Temperature", 0.93, 600),
    # Position and speed arrive late through the manufacturer's cloud, so the
    # half life is short and freshness usually decides against them.
    _p("OEM_CONNECTED", "CurrentLocation.Latitude", 0.88, 180, ("accuracyM",),
       note="Accurate but delivered 60 to 150 seconds late"),
    _p("OEM_CONNECTED", "CurrentLocation.Longitude", 0.88, 180, ("accuracyM",)),
    _p("OEM_CONNECTED", "Speed", 0.85, 120,
       note="Sampled on a two minute cadence, so often stale"),

    # The aftermarket unit reports often and has its own GNSS, which wins on
    # anything positional. What it infers rather than measures is weaker.
    _p("CALAMP_TRACKER", "CurrentLocation.Latitude", 0.90, 240, ("hdop", "satellites", "fix"),
       note="Dedicated GNSS module reporting every 60 seconds"),
    _p("CALAMP_TRACKER", "CurrentLocation.Longitude", 0.90, 240, ("hdop", "satellites", "fix")),
    _p("CALAMP_TRACKER", "Speed", 0.88, 120,
       note="Derived from GNSS, fresher than the OEM sample"),
    _p("CALAMP_TRACKER", "Powertrain.FuelSystem.RelativeLevel", 0.72, 900,
       note="Inferred from the OBD port, drifts and fails to a sentinel"),
    _p("CALAMP_TRACKER", "TraveledDistance", 0.78, 3600,
       note="Accumulated from GNSS rather than read from the odometer"),
    _p("CALAMP_TRACKER", "Powertrain.CombustionEngine.IsRunning", 0.86, 600),
    _p("CALAMP_TRACKER", "Powertrain.CombustionEngine.EngineCoolant.Temperature", 0.80, 600),
    _p("CALAMP_TRACKER", "CurrentLocation.Heading", 0.88, 240),
    _p("CALAMP_TRACKER", "CurrentLocation.Altitude", 0.70, 600),
    _p("CALAMP_TRACKER", "LowVoltageBattery.CurrentVoltage", 0.85, 900),
]


class FusionConfig:
    """Lookup of reliability and decay by (sourceType, vssPath)."""

    def __init__(self, profiles: list[SourceProfile] | None = None) -> None:
        self._by_key: dict[str, SourceProfile] = {}
        for profile in profiles or SEED_PROFILES:
            self._by_key[profile.key] = profile

    @classmethod
    def from_db(cls, db) -> "FusionConfig":
        documents = list(db[FUSION_CONFIG].find({}))
        if not documents:
            logger.info("fusion_config empty, using seed profiles")
            return cls()
        profiles = [
            SourceProfile(
                source_type=doc["sourceType"],
                vss_path=doc["vssPath"],
                base_reliability=doc.get("baseReliability", DEFAULT_RELIABILITY),
                freshness_half_life=doc.get("freshnessHalfLifeSeconds", DEFAULT_HALF_LIFE_SECONDS),
                confidence_fields=tuple(doc.get("confidenceFields", [])),
                note=doc.get("note", ""),
            )
            for doc in documents
        ]
        return cls(profiles)

    def get(self, source_type: str, vss_path: str) -> SourceProfile:
        found = self._by_key.get(f"{source_type}:{vss_path}")
        if found is not None:
            return found
        return SourceProfile(source_type, vss_path)

    def seed(self, db) -> int:
        from pymongo import UpdateOne

        operations = [
            UpdateOne({"_id": p.key}, {"$set": p.to_document()}, upsert=True)
            for p in SEED_PROFILES
        ]
        db[FUSION_CONFIG].bulk_write(operations, ordered=False)
        db[FUSION_CONFIG].create_index([("sourceType", 1), ("vssPath", 1)])
        return len(operations)


def freshness_decay(observed_at: datetime, now: datetime, half_life: int) -> float:
    """Halve the score every half_life seconds since the reading was taken.

    A sixty second old GPS fix is stale; a sixty second old odometer reading is
    fine. The half life is per signal, which is why it is configuration rather
    than a single global timeout.
    """
    age = (now - observed_at).total_seconds()
    if age <= 0:
        return 1.0
    if age > MAX_STALENESS_SECONDS:
        return 0.0
    return 0.5 ** (age / half_life)


def confidence_factor(quality: dict | None, fields: tuple[str, ...]) -> float:
    """Scale by whatever the source says about its own reading.

    A signal with no natural quality measure, such as a boolean ignition state,
    scores 1.0 rather than being penalised for the absence.
    """
    if not fields or not quality:
        return 1.0

    factor = 1.0

    if "fix" in fields:
        fix = str(quality.get("fix", "")).strip().lower()
        if fix in ("none", "no", "0", "invalid", "nofix"):
            return 0.0
        if fix in ("2d",):
            factor *= 0.7

    if "hdop" in fields and quality.get("hdop") is not None:
        try:
            hdop = float(quality["hdop"])
            # Below 2 is good, above 10 is close to useless.
            factor *= max(0.15, min(1.0, 2.0 / max(hdop, 0.5)))
        except (TypeError, ValueError):
            pass

    if "satellites" in fields and quality.get("satellites") is not None:
        try:
            satellites = int(float(quality["satellites"]))
            factor *= max(0.2, min(1.0, satellites / 8.0))
        except (TypeError, ValueError):
            pass

    if "accuracyM" in fields and quality.get("accuracyM") is not None:
        try:
            accuracy = float(quality["accuracyM"])
            # Five metres or better is full marks, fifty metres is poor.
            factor *= max(0.2, min(1.0, 5.0 / max(accuracy, 1.0)))
        except (TypeError, ValueError):
            pass

    return max(0.0, min(1.0, factor))


@dataclass
class Candidate:
    """One source's answer for one signal, with its score broken out."""

    source_type: str
    value: object
    observed_at: datetime
    unit: str | None = None
    quality: dict | None = None
    reliability: float = 0.0
    freshness: float = 0.0
    confidence: float = 0.0
    trust_score: float = 0.0
    used: bool = False
    rejected: str | None = None

    def to_document(self) -> dict:
        doc = {
            "src": self.source_type,
            "v": self.value,
            "ts": self.observed_at,
            "trustScore": round(self.trust_score, 4),
            "score": {
                "reliability": round(self.reliability, 3),
                "freshness": round(self.freshness, 3),
                "confidence": round(self.confidence, 3),
            },
            "used": self.used,
        }
        if self.rejected:
            doc["rejected"] = self.rejected
        return doc


@dataclass
class FusedLeaf:
    """The value written down, and every candidate that was considered."""

    vss_path: str
    value: object
    observed_at: datetime
    source_type: str
    unit: str | None
    trust_score: float
    strategy: str
    witnesses: list[Candidate] = field(default_factory=list)

    @property
    def contested(self) -> bool:
        """More than one source answered, and they did not agree."""
        answers = {repr(w.value) for w in self.witnesses if w.rejected is None}
        return len(answers) > 1

    def to_leaf(self) -> dict:
        return {
            "v": self.value,
            "ts": self.observed_at,
            "src": self.source_type,
            "unit": self.unit,
            "derived": False,
            "trustScore": round(self.trust_score, 4),
            "sources": len(self.witnesses),
            "contested": self.contested,
        }

    def to_provenance(self) -> dict:
        return {
            "vssPath": self.vss_path,
            "chosen": {
                "src": self.source_type,
                "v": self.value,
                "trustScore": round(self.trust_score, 4),
                "strategy": self.strategy,
            },
            "witnesses": [w.to_document() for w in self.witnesses],
        }


def score(candidate: Candidate, profile: SourceProfile, now: datetime) -> Candidate:
    candidate.reliability = profile.base_reliability
    candidate.freshness = freshness_decay(
        candidate.observed_at, now, profile.freshness_half_life
    )
    candidate.confidence = confidence_factor(candidate.quality, profile.confidence_fields)
    candidate.trust_score = (
        candidate.reliability * candidate.freshness * candidate.confidence
    )
    if candidate.freshness == 0.0:
        candidate.rejected = "too_stale"
    elif candidate.confidence == 0.0:
        candidate.rejected = "no_usable_quality"
    return candidate


def fuse(
    vss_path: str,
    candidates: list[Candidate],
    config: FusionConfig,
    now: datetime,
) -> FusedLeaf | None:
    """Score every answer and choose one."""
    if not candidates:
        return None

    for candidate in candidates:
        score(candidate, config.get(candidate.source_type, vss_path), now)

    live = [c for c in candidates if c.rejected is None]
    if not live:
        return None

    strategy = STRATEGIES.get(vss_path, "highest_trust")

    if strategy == "monotonic_max":
        # An odometer only goes up. A lower reading is a regression, not news,
        # whichever source it came from.
        numeric = [c for c in live if isinstance(c.value, (int, float))]
        winner = max(numeric, key=lambda c: (c.value, c.trust_score)) if numeric else live[0]
    else:
        winner = max(live, key=lambda c: c.trust_score)

    winner.used = True

    return FusedLeaf(
        vss_path=vss_path,
        value=winner.value,
        observed_at=winner.observed_at,
        source_type=winner.source_type,
        unit=winner.unit,
        trust_score=winner.trust_score,
        strategy=strategy,
        witnesses=sorted(candidates, key=lambda c: -c.trust_score),
    )
