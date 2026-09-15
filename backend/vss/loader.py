"""Load COVESA VSS from vss.json into a flat signal registry.

vss.json is the single source of truth for signal metadata. Branches nest under
a "children" key and leaves are anything without one. Paths are Vehicle-relative,
so the root "Vehicle" is stripped: Powertrain.FuelSystem.RelativeLevel.

Every leaf carries at minimum a datatype, a description and a type, where type is
one of sensor, actuator or attribute. Most numeric leaves also carry a unit and
often min/max. Nothing about signals is hand-typed anywhere else in the codebase.
"""

import json
import logging
import os
import re
from dataclasses import dataclass, field, asdict
from typing import Any, Iterator

logger = logging.getLogger(__name__)

# vss.json lives at the repo root, two levels up from backend/vss/
DEFAULT_VSS_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "vss.json",
)

LEAF_KINDS = ("sensor", "actuator", "attribute")

# VSS datatype -> a coarse class the rest of the pipeline reasons about.
NUMERIC_TYPES = {
    "uint8", "uint16", "uint32", "uint64",
    "int8", "int16", "int32", "int64",
    "float", "double",
}

# Inclusive bounds implied by the VSS datatype itself. A leaf without an explicit
# min/max is still bounded by its storage width, which catches sentinel values
# such as 65535 arriving in a uint16 field.
DATATYPE_RANGE = {
    "uint8": (0, 255),
    "uint16": (0, 65535),
    "uint32": (0, 4294967295),
    "uint64": (0, 18446744073709551615),
    "int8": (-128, 127),
    "int16": (-32768, 32767),
    "int32": (-2147483648, 2147483647),
    "int64": (-9223372036854775808, 9223372036854775807),
}


@dataclass
class SignalSpec:
    """One VSS leaf, flattened."""

    path: str
    datatype: str
    kind: str
    description: str
    unit: str | None = None
    minimum: float | None = None
    maximum: float | None = None
    allowed: list[str] | None = None
    pattern: str | None = None
    comment: str | None = None
    default: Any = None
    deprecation: str | None = None
    branch: str = ""

    @property
    def is_numeric(self) -> bool:
        return self.datatype in NUMERIC_TYPES

    @property
    def is_array(self) -> bool:
        return self.datatype.endswith("[]")

    @property
    def is_deprecated(self) -> bool:
        return self.deprecation is not None

    def effective_range(self) -> tuple[float | None, float | None]:
        """Explicit min/max where the spec gives them, otherwise the datatype width."""
        lo, hi = self.minimum, self.maximum
        implied = DATATYPE_RANGE.get(self.datatype)
        if implied:
            lo = implied[0] if lo is None else lo
            hi = implied[1] if hi is None else hi
        return lo, hi

    def to_document(self) -> dict:
        doc = asdict(self)
        doc["_id"] = self.path
        lo, hi = self.effective_range()
        doc["effectiveMin"] = lo
        doc["effectiveMax"] = hi
        doc["isNumeric"] = self.is_numeric
        doc["isArray"] = self.is_array
        return doc


@dataclass
class VssRegistry:
    """The whole VSS tree, flattened and indexed by path."""

    version: str
    signals: dict[str, SignalSpec] = field(default_factory=dict)
    branches: list[str] = field(default_factory=list)

    def get(self, path: str) -> SignalSpec | None:
        return self.signals.get(path)

    def __contains__(self, path: str) -> bool:
        return path in self.signals

    def __len__(self) -> int:
        return len(self.signals)

    def by_kind(self, kind: str) -> list[SignalSpec]:
        return [s for s in self.signals.values() if s.kind == kind]

    def under(self, branch: str) -> list[SignalSpec]:
        """Every signal at or below a branch, e.g. under('Powertrain')."""
        prefix = branch + "."
        return [s for s in self.signals.values() if s.path.startswith(prefix)]

    def search(self, term: str, limit: int = 25) -> list[SignalSpec]:
        """Rank signals against a phrase, for the mapping helper.

        Someone writing a mapping types what a column means rather than a path,
        so "fuel level" has to reach Powertrain.FuelSystem.RelativeLevel. Tokens
        are matched independently and a hit in the path counts for more than a
        hit in the description.
        """
        phrase = term.strip().lower()
        if not phrase:
            return []
        tokens = [t for t in re.split(r"[^a-z0-9]+", phrase) if t]

        scored: list[tuple[float, int, SignalSpec]] = []
        for spec in self.signals.values():
            path = spec.path.lower()
            # Split CamelCase so "level" matches RelativeLevel.
            words = re.sub(r"(?<!^)(?=[A-Z])", " ", spec.path).lower()
            description = spec.description.lower()

            score = 0.0
            if phrase in path:
                score += 10.0
            for token in tokens:
                if token in words.split():
                    score += 4.0
                elif token in path:
                    score += 2.5
                if token in description:
                    score += 1.0
            if not score:
                continue
            if spec.is_deprecated:
                score -= 3.0
            scored.append((score, -len(spec.path), spec))

        scored.sort(key=lambda row: (-row[0], -row[1]))
        return [spec for _, _, spec in scored[:limit]]

    def summary(self) -> dict:
        units: dict[str, int] = {}
        kinds: dict[str, int] = {}
        for s in self.signals.values():
            kinds[s.kind] = kinds.get(s.kind, 0) + 1
            if s.unit:
                units[s.unit] = units.get(s.unit, 0) + 1
        return {
            "version": self.version,
            "branches": len(self.branches),
            "leaves": len(self.signals),
            "kinds": kinds,
            "distinctUnits": len(units),
            "leavesWithUnit": sum(units.values()),
            "deprecated": sum(1 for s in self.signals.values() if s.is_deprecated),
        }


def _walk(node: dict, prefix: str, branches: list[str]) -> Iterator[SignalSpec]:
    children = node.get("children")
    if children is None:
        return
    for name, child in children.items():
        path = f"{prefix}.{name}" if prefix else name
        if "children" in child:
            branches.append(path)
            yield from _walk(child, path, branches)
            continue
        kind = child.get("type", "sensor")
        if kind not in LEAF_KINDS:
            logger.warning("Leaf %s has unexpected type %r, treating as sensor", path, kind)
            kind = "sensor"
        yield SignalSpec(
            path=path,
            datatype=child.get("datatype", "string"),
            kind=kind,
            description=child.get("description", ""),
            unit=child.get("unit"),
            minimum=child.get("min"),
            maximum=child.get("max"),
            allowed=child.get("allowed"),
            pattern=child.get("pattern"),
            comment=child.get("comment"),
            default=child.get("default"),
            deprecation=child.get("deprecation"),
            branch=prefix,
        )


def _read_version(vehicle: dict) -> str:
    """VSS ships its own version as attribute defaults under Vehicle.VersionVSS."""
    node = vehicle.get("children", {}).get("VersionVSS", {}).get("children", {})
    parts = [node.get(k, {}).get("default") for k in ("Major", "Minor", "Patch")]
    if any(p is None for p in parts):
        return "unknown"
    version = ".".join(str(p) for p in parts)
    label = node.get("Label", {}).get("default")
    return f"{version}-{label}" if label else version


def load_vss(path: str | None = None) -> VssRegistry:
    """Parse vss.json into a flat, path-indexed registry."""
    path = path or os.getenv("VSS_JSON_PATH") or DEFAULT_VSS_PATH
    with open(path, "r", encoding="utf-8") as fh:
        raw = json.load(fh)

    vehicle = raw.get("Vehicle")
    if vehicle is None:
        raise ValueError(f"{path} has no Vehicle root; is this a VSS export?")

    branches: list[str] = []
    signals = {s.path: s for s in _walk(vehicle, "", branches)}
    registry = VssRegistry(
        version=_read_version(vehicle),
        signals=signals,
        branches=branches,
    )
    logger.info(
        "Loaded VSS %s: %d branches, %d leaves",
        registry.version, len(branches), len(signals),
    )
    return registry


_registry: VssRegistry | None = None


def get_registry() -> VssRegistry:
    """Process-wide singleton. The tree is read-only once parsed."""
    global _registry
    if _registry is None:
        _registry = load_vss()
    return _registry
