"""Domain rules shared by the loader and the read path.

A rejection reason is a fact about data. A finding is a problem with a vehicle,
with a consequence and something to do about it. Turning the first into the
second is what makes the difference between a report nobody acts on and a work
queue, so the translation lives in one place and both sides use it.
"""

from __future__ import annotations

from datetime import datetime

IGNITION = "Powertrain.CombustionEngine.IsRunning"
FUEL = "Powertrain.FuelSystem.RelativeLevel"
SPEED = "Speed"
ODOMETER = "TraveledDistance"
COOLANT = "Powertrain.CombustionEngine.EngineCoolant.Temperature"
BATTERY = "LowVoltageBattery.CurrentVoltage"

LOW_FUEL_PERCENT = 15
LOW_BATTERY_VOLTS = 12.0
MOVING_ABOVE_KMH = 3

SEVERITY_ORDER = {"critical": 0, "warning": 1, "info": 2}

FINDING_RULES = {
    "IMPOSSIBLE_MOTION": {
        "title": "Tracker may be fitted to a different vehicle",
        "consequence": "Mileage billing and theft recovery are unreliable for this car",
        "severity": "critical",
        "action": "Verify the tracker serial against the vehicle record",
    },
    "NULL_ISLAND": {
        "title": "GPS module failing to acquire a fix",
        "consequence": "This car cannot be located when a customer needs roadside help",
        "severity": "critical",
        "action": "Schedule the tracker for replacement",
    },
    "NO_GPS_FIX": {
        "title": "Position reported without a satellite fix",
        "consequence": "Location shown may be stale or wrong",
        "severity": "warning",
        "action": "Check antenna placement at the next service",
    },
    "SENTINEL_VALUE": {
        "title": "Sensor disconnected, firmware sending a placeholder",
        "consequence": "Fuel theft detection is blind on this vehicle",
        "severity": "warning",
        "action": "Reconnect the tank sender",
    },
    "OUT_OF_RANGE": {
        "title": "Sensor reporting values outside its physical range",
        "consequence": "Readings from this sensor cannot be trusted",
        "severity": "warning",
        "action": "Inspect wiring at the next service",
    },
    "TYPE_COERCION_FAILED": {
        "title": "Firmware sending the wrong format for a signal",
        "consequence": "This field is discarded on every report",
        "severity": "info",
        "action": "Raise with the telematics vendor, it is an integration bug",
    },
    "OUTSIDE_SERVICE_AREA": {
        "title": "Vehicle reported outside the operating territory",
        "consequence": "Either a cross-border movement or a bad fix",
        "severity": "warning",
        "action": "Confirm against the rental agreement",
    },
    "PATTERN_MISMATCH": {
        "title": "Identifier does not match the format the standard requires",
        "consequence": "This vehicle may not reconcile against the fleet record",
        "severity": "warning",
        "action": "Correct the record in the fleet management system",
    },
}


def leaf_value(state: dict, path: str):
    leaf = state.get(path)
    return leaf["v"] if leaf else None


def activity_of(state: dict) -> str:
    ignition = leaf_value(state, IGNITION)
    if ignition is None:
        return "unknown"
    if ignition and (leaf_value(state, SPEED) or 0) > MOVING_ABOVE_KMH:
        return "moving"
    return "idling" if ignition else "parked"


def trust_from_findings(findings: list[dict]) -> str:
    if any(f["severity"] == "critical" for f in findings):
        return "unreliable"
    return "degraded" if findings else "good"


def build_findings(reasons: dict[str, int], state: dict) -> list[dict]:
    """Rejection counts and current state become problems worth acting on."""
    findings = []

    for reason, count in reasons.items():
        rule = FINDING_RULES.get(reason)
        if rule is None or count == 0:
            continue
        findings.append({
            "kind": reason,
            "title": rule["title"],
            "consequence": rule["consequence"],
            "severity": rule["severity"],
            "action": rule["action"],
            "occurrences": count,
            "source": "validation",
        })

    fuel = leaf_value(state, FUEL)
    if fuel is not None and fuel <= LOW_FUEL_PERCENT:
        findings.append({
            "kind": "LOW_FUEL",
            "title": f"Fuel at {fuel}%",
            "consequence": "Needs refuelling before the next rental",
            "severity": "warning",
            "action": "Schedule refuelling at the depot",
            "occurrences": 1,
            "source": "state",
        })

    battery = leaf_value(state, BATTERY)
    if battery is not None and battery < LOW_BATTERY_VOLTS:
        findings.append({
            "kind": "LOW_BATTERY",
            "title": f"Auxiliary battery at {battery} V",
            "consequence": "Risk of a no-start callout with a customer in the car",
            "severity": "warning",
            "action": "Test the battery at the next depot visit",
            "occurrences": 1,
            "source": "state",
        })

    findings.sort(key=lambda f: (SEVERITY_ORDER[f["severity"]], -f["occurrences"]))
    return findings


def build_trips(readings: list[tuple]) -> list[dict]:
    """Reconstruct trips from ignition transitions.

    Nothing stores a trip. The ignition series is walked for off to on and on to
    off transitions and paired, so a missed processing run costs nothing: the
    trips regenerate from the source series.
    """
    trips: list[dict] = []
    current: dict | None = None

    for ts, running, lat, lon, odo in readings:
        if running and current is None:
            current = {
                "startedAt": ts, "startLat": lat, "startLon": lon,
                "startOdo": odo, "points": 0,
            }
        elif not running and current is not None:
            current.update(endedAt=ts, endLat=lat, endLon=lon, endOdo=odo)
            trips.append(current)
            current = None
        if current is not None:
            current["points"] += 1

    if current is not None:
        current["ongoing"] = True
        trips.append(current)

    out = []
    for trip in trips:
        started: datetime = trip["startedAt"]
        ended: datetime | None = trip.get("endedAt")
        distance = None
        if trip.get("startOdo") is not None and trip.get("endOdo") is not None:
            distance = round((trip["endOdo"] - trip["startOdo"]) / 1000, 1)
        out.append({
            "startedAt": started,
            "endedAt": ended,
            "ongoing": trip.get("ongoing", False),
            "durationMin": round(((ended or started) - started).total_seconds() / 60, 1),
            "distanceKm": distance,
            "start": [trip["startLon"], trip["startLat"]] if trip.get("startLat") else None,
            "end": [trip["endLon"], trip["endLat"]] if trip.get("endLat") else None,
            "points": trip["points"],
        })
    return out
