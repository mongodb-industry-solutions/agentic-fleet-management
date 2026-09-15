"""The OEM connected-car feed: a second observer of the same fleet.

Manufacturers run their own telematics platforms that read values off the
vehicle bus and deliver them through an API. That feed and an aftermarket
tracker describe the same car and disagree, which is the problem fusion exists
to solve.

The two differ in ways that matter:

  cadence      the OEM reports every two minutes, the tracker every minute
  lag          the OEM goes through the manufacturer's cloud, so it arrives
               60 to 150 seconds late
  identity     the OEM knows the VIN, the tracker knows the registration
  units        fuel as a fraction, distance in kilometres, speed in m/s
  coverage     only vehicles new enough to have a connected platform
  accuracy     the tank sender is read directly rather than inferred, and the
               position comes from the vehicle's own antenna

Crucially the OEM does not carry the tracker's faults. When a tracker is refitted
into another car, the OEM keeps reporting the truth, which is what makes the
swapped unit provable rather than merely suspected.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta

OEM_COLUMNS = [
    "vin", "event_time_utc", "ingested_utc", "gnss_lat", "gnss_lon",
    "gnss_accuracy_m", "speed_mps", "odometer_km", "fuel_level_ratio",
    "engine_on", "coolant_c", "hv_battery_pct", "oem_brand",
]

# Manufacturers with a connected-car platform in this fleet. A vehicle only
# appears in the feed if its make is here and it is new enough.
CONNECTED_BRANDS = {
    "Volkswagen", "Toyota", "Ford", "Chevrolet", "BMW", "Mercedes-Benz",
    "Tesla", "Fiat", "Jeep", "Hyundai", "Kia", "Nissan", "Renault", "Peugeot",
}
CONNECTED_FROM_YEAR = 2023

REPORT_INTERVAL_SECONDS = 120
LAG_RANGE_SECONDS = (60, 150)

# The OEM position comes off the vehicle's own antenna and is generally better
# than an aftermarket unit's, but it is not perfect.
POSITION_JITTER_DEGREES = 0.00012
ACCURACY_RANGE_M = (3.0, 12.0)


def is_connected(car) -> bool:
    return car.make in CONNECTED_BRANDS and car.year >= CONNECTED_FROM_YEAR


def row(car, when: datetime, speed_kmh: float, rng: random.Random) -> dict:
    """One OEM report for one vehicle, describing the same instant differently."""
    lag = rng.randint(*LAG_RANGE_SECONDS)

    return {
        "vin": car.vin,
        "event_time_utc": when.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "ingested_utc": (when + timedelta(seconds=lag)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "gnss_lat": f"{car.lat + rng.gauss(0, POSITION_JITTER_DEGREES):.6f}",
        "gnss_lon": f"{car.lon + rng.gauss(0, POSITION_JITTER_DEGREES):.6f}",
        "gnss_accuracy_m": f"{rng.uniform(*ACCURACY_RANGE_M):.1f}",
        "speed_mps": f"{speed_kmh / 3.6:.2f}",
        "odometer_km": f"{car.odo:.2f}",
        # The tank sender read directly, as a fraction rather than a percentage.
        "fuel_level_ratio": f"{car.fuel / 100:.4f}",
        "engine_on": "true" if car.ignition else "false",
        "coolant_c": f"{rng.uniform(84, 95):.1f}" if car.ignition
                     else f"{rng.uniform(12, 44):.1f}",
        "hv_battery_pct": "" if car.category != "suv" else "",
        "oem_brand": car.make,
    }


def inject(row: dict, car, rng: random.Random) -> bool:
    """OEM-side imperfections. Returns False when the report is dropped.

    Manufacturer platforms fail differently from aftermarket units: they lose
    connectivity for a while rather than reporting nonsense, and occasionally
    send an empty field where a sensor is not fitted.
    """
    if rng.random() < 0.02:
        return False  # gap in the manufacturer's feed

    if rng.random() < 0.004:
        row["fuel_level_ratio"] = ""  # sensor not reporting this cycle

    if rng.random() < 0.002:
        row["coolant_c"] = ""

    return True
