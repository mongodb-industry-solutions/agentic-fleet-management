"""The validation ladder.

Four levels, each catching what the one below cannot:

  1. Value checks, generated from vss.json. Range, allowed set, format pattern.
     A latitude of 91.0 fails against a bound nobody on this project typed.
     Where the specification is silent, and it is silent about a lot, a small
     table of physical limits and per-adapter sentinels covers the gap.
  2. Null Island. Latitude 0, longitude 0 passes every per-leaf range check and
     puts a car in the Gulf of Guinea, so a position is checked as a pair.
  3. The mobility test. Every coordinate can be individually valid while the
     path between them is impossible.
  4. Service area. A position far outside the operating territory is flagged.

Nothing is thrown away. Rejections are recorded with a reason so the quality
report can group by them, which is how a fleet operator finds out that a firmware
release broke a batch of trackers.
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass, field

from models.telemetry import Position, RejectReason, Rejection
from vss.loader import SignalSpec

logger = logging.getLogger(__name__)

# How close to 0,0 still counts as Null Island. Roughly 11 km at the equator,
# which is comfortably inside the Gulf of Guinea and nowhere near a road.
NULL_ISLAND_DEGREES = 0.1

# A hard ceiling on implied speed between two fixes. Set well above anything a
# road vehicle does so that only genuine impossibilities trip it: a car carried
# on a transporter or a ferry still lands under this.
DEFAULT_MAX_IMPLIED_KMH = 350.0

# Below this gap, GPS jitter alone can imply a silly speed, so the check is
# skipped. Two fixes one second apart 40 m apart is multipath, not motion.
MIN_MOTION_INTERVAL_S = 2.0

EARTH_RADIUS_KM = 6371.0088

# COVESA VSS bounds some signals and leaves many unbounded. A float leaf with no
# min or max accepts anything, so a coolant temperature of -999 C passes every
# check the specification can generate.
#
# These are physical limits rather than customer configuration: no road vehicle
# has a coolant temperature of -999 C whoever operates it. Deliberately generous,
# so they catch broken sensors rather than unusual conditions, and applied only
# where the specification says nothing.
PLAUSIBLE_RANGES = {
    "Powertrain.CombustionEngine.EngineCoolant.Temperature": (-60.0, 200.0),
    "Powertrain.CombustionEngine.ECT": (-60.0, 200.0),
    "Powertrain.CombustionEngine.EngineOil.Temperature": (-60.0, 250.0),
    "Powertrain.TractionBattery.Temperature.Average": (-60.0, 120.0),
    "Exterior.AirTemperature": (-90.0, 70.0),
    "Cabin.HVAC.AmbientAirTemperature": (-60.0, 90.0),
    "Speed": (-20.0, 500.0),
    "AverageSpeed": (-20.0, 500.0),
    "CurrentLocation.Altitude": (-500.0, 9000.0),
    "LowVoltageBattery.CurrentVoltage": (0.0, 60.0),
    "Powertrain.TractionBattery.CurrentVoltage": (0.0, 1000.0),
}


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def point_in_ring(lat: float, lon: float, ring: list[list[float]]) -> bool:
    """Ray casting against one GeoJSON linear ring of [lon, lat] pairs."""
    inside = False
    n = len(ring)
    for i in range(n):
        x1, y1 = ring[i][0], ring[i][1]
        x2, y2 = ring[(i + 1) % n][0], ring[(i + 1) % n][1]
        if (y1 > lat) != (y2 > lat):
            x_at = (x2 - x1) * (lat - y1) / (y2 - y1) + x1
            if lon < x_at:
                inside = not inside
    return inside


def point_in_polygon(lat: float, lon: float, geometry: dict) -> bool:
    """Supports GeoJSON Polygon and MultiPolygon, holes included."""
    kind = geometry.get("type")
    if kind == "Polygon":
        polys = [geometry["coordinates"]]
    elif kind == "MultiPolygon":
        polys = geometry["coordinates"]
    else:
        return True

    for poly in polys:
        if not poly:
            continue
        if point_in_ring(lat, lon, poly[0]) and not any(
            point_in_ring(lat, lon, hole) for hole in poly[1:]
        ):
            return True
    return False


def coerce(value, spec: SignalSpec):
    """Force a value into the VSS datatype, raising ValueError if it will not fit."""
    dt = spec.datatype

    if dt == "boolean":
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if text in ("true", "1", "yes", "y", "on", "t", "l", "ligado", "sim"):
            return True
        if text in ("false", "0", "no", "n", "off", "f", "d", "desligado", "nao", "não"):
            return False
        raise ValueError(f"{value!r} is not a boolean")

    if dt.startswith(("uint", "int")):
        number = float(value)
        if number != int(number):
            # A tracker sending 61.4 for a uint8 percentage is normal; round it.
            number = round(number)
        return int(number)

    if dt in ("float", "double"):
        number = float(value)
        if math.isnan(number) or math.isinf(number):
            raise ValueError(f"{value!r} is not a finite number")
        return number

    if dt.endswith("[]"):
        if isinstance(value, (list, tuple)):
            return list(value)
        return [p.strip() for p in str(value).split(",") if p.strip()]

    return str(value)


def check_value(value, spec: SignalSpec, sentinels=None) -> Rejection | None:
    """Level 1. Everything here is generated from vss.json."""
    if sentinels and value in sentinels:
        return Rejection(
            reason=RejectReason.SENTINEL_VALUE,
            detail=f"{value!r} is declared as a sentinel for {spec.path}",
            rule="mapping.sentinels",
        )

    if spec.allowed and value not in spec.allowed:
        return Rejection(
            reason=RejectReason.NOT_ALLOWED_VALUE,
            detail=f"{value!r} is not one of {spec.allowed}",
            rule="vss.allowed",
        )

    if spec.pattern and isinstance(value, str):
        if not re.match(spec.pattern, value):
            return Rejection(
                reason=RejectReason.PATTERN_MISMATCH,
                detail=f"{value!r} does not match {spec.pattern}",
                rule="vss.pattern",
            )

    if spec.is_numeric and isinstance(value, (int, float)):
        low, high = spec.effective_range()
        if low is None and high is None:
            # The specification is silent, so fall back to physical plausibility.
            low, high = PLAUSIBLE_RANGES.get(spec.path, (None, None))
        source = "vss" if spec.effective_range() != (None, None) else "plausible"
        if low is not None and value < low:
            return Rejection(
                reason=RejectReason.OUT_OF_RANGE,
                detail=f"{value} is below the minimum of {low} {spec.unit or ''}".strip(),
                rule=f"{source}.min",
            )
        if high is not None and value > high:
            return Rejection(
                reason=RejectReason.OUT_OF_RANGE,
                detail=f"{value} is above the maximum of {high} {spec.unit or ''}".strip(),
                rule=f"{source}.max",
            )

    return None


@dataclass
class ServiceArea:
    """The operating territory, as GeoJSON. Positions outside it are flagged."""

    name: str
    geometry: dict | None = None

    def contains(self, lat: float, lon: float) -> bool:
        if self.geometry is None:
            return True
        return point_in_polygon(lat, lon, self.geometry)


def check_position(position: Position, service_area: ServiceArea | None = None) -> Rejection | None:
    """Levels 2 and 4. A position is only meaningful as a pair."""
    lat, lon = position.latitude, position.longitude

    if position.fix is not None and str(position.fix).strip().lower() in (
        "none", "no", "0", "invalid", "nofix", "sem", "false"
    ):
        return Rejection(
            reason=RejectReason.NO_GPS_FIX,
            detail=f"fix reported as {position.fix!r}",
            rule="position.fix",
        )

    if not (-90.0 <= lat <= 90.0) or not (-180.0 <= lon <= 180.0):
        return Rejection(
            reason=RejectReason.COORD_OUT_OF_RANGE,
            detail=f"{lat}, {lon} is outside valid coordinate bounds",
            rule="position.range",
        )

    if abs(lat) < NULL_ISLAND_DEGREES and abs(lon) < NULL_ISLAND_DEGREES:
        return Rejection(
            reason=RejectReason.NULL_ISLAND,
            detail=(
                f"{lat}, {lon} is in the Gulf of Guinea, which is where an "
                f"uninitialised GPS module reports itself to be"
            ),
            rule="position.null_island",
        )

    if service_area and not service_area.contains(lat, lon):
        return Rejection(
            reason=RejectReason.OUTSIDE_SERVICE_AREA,
            detail=f"{lat}, {lon} is outside {service_area.name}",
            rule="position.service_area",
        )

    return None


@dataclass
class MotionCheck:
    """Level 3, the mobility test. Stateful, so it holds the last fix per vehicle.

    A car cannot travel from Kenner to Tokyo in one second. Both endpoints are
    real places and each passes a range check on its own, so the transition is
    what has to be validated. This doubles as the cheapest theft and tow detector
    available, because a genuine impossible jump usually means the tracker moved
    rather than the vehicle.
    """

    max_implied_kmh: float = DEFAULT_MAX_IMPLIED_KMH
    _last: dict[str, Position] = field(default_factory=dict)

    def check(self, asset_ref: str, position: Position) -> Rejection | None:
        previous = self._last.get(asset_ref)
        self._last[asset_ref] = position

        if previous is None:
            return None

        seconds = (position.observed_at - previous.observed_at).total_seconds()
        if seconds < MIN_MOTION_INTERVAL_S:
            # Out of order rows or a burst after a tunnel. Keep the newer fix and
            # say nothing, since jitter over a tiny interval implies nonsense.
            if seconds < 0:
                self._last[asset_ref] = previous
            return None

        distance = haversine_km(
            previous.latitude, previous.longitude,
            position.latitude, position.longitude,
        )
        implied = distance / (seconds / 3600.0)

        if implied > self.max_implied_kmh:
            self._last[asset_ref] = previous
            return Rejection(
                reason=RejectReason.IMPOSSIBLE_MOTION,
                detail=(
                    f"{distance:.1f} km in {seconds:.0f} s implies "
                    f"{implied:,.0f} km/h"
                ),
                rule="position.mobility",
            )

        return None

    def reset(self) -> None:
        self._last.clear()
