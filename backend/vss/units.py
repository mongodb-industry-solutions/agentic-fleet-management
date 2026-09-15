"""Unit normalisation into VSS canonical units.

An adapter names a VSS path and the unit its vendor reports in. The target unit
comes from vss.json, so a mapping file never states what a signal should be
converted to, only what it currently is. A Brazilian tracker reporting fuel in
litres and an OEM feed reporting Powertrain.FuelSystem.RelativeLevel as a
percentage both land on the same signal without anyone maintaining a lookup table
by hand.

Conversions are linear: value_in_target = value * factor + offset.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


class UnitError(ValueError):
    """Raised when a conversion is requested that we cannot perform."""


# Vendor spellings folded onto the VSS spelling. Case is normalised first.
ALIASES = {
    "kph": "km/h", "kmh": "km/h", "km/hr": "km/h", "kmph": "km/h",
    "kilometersperhour": "km/h",
    "mph": "mph", "milesperhour": "mph",
    "m/s": "m/s", "mps": "m/s", "meterspersecond": "m/s",
    "kn": "knots", "kt": "knots", "kts": "knots", "knot": "knots",
    "c": "Celsius", "degc": "Celsius", "celcius": "Celsius",
    "celsius": "Celsius", "°c": "Celsius", "graus": "Celsius",
    "f": "fahrenheit", "degf": "fahrenheit", "°f": "fahrenheit",
    "k": "kelvin", "kelvin": "kelvin",
    "%": "percent", "pct": "percent", "porcentagem": "percent",
    "km": "km", "mi": "miles", "mile": "miles", "miles": "miles",
    "m": "m", "meter": "m", "metre": "m", "meters": "m", "metres": "m",
    "ft": "feet", "feet": "feet", "foot": "feet",
    "cm": "cm", "mm": "mm", "in": "inch", "inch": "inch", "inches": "inch",
    "l": "l", "liter": "l", "litre": "l", "liters": "l", "litres": "l",
    "litros": "l",
    "gal": "gallon", "gallon": "gallon", "gallons": "gallon",
    "kpa": "kPa", "psi": "psi", "bar": "bar",
    "v": "V", "volt": "V", "volts": "V",
    "a": "A", "amp": "A", "amps": "A", "ampere": "A",
    "kw": "kW", "w": "W", "kwh": "kWh", "wh": "Wh",
    "rpm": "rpm",
    "s": "s", "sec": "s", "second": "s", "seconds": "s", "segundos": "s",
    "ms": "ms", "millisecond": "ms", "milliseconds": "ms",
    "min": "min", "minute": "min", "minutes": "min",
    "h": "h", "hr": "h", "hour": "h", "hours": "h", "horas": "h",
    "kg": "kg", "g": "g", "lb": "pound", "lbs": "pound", "pound": "pound",
    "deg": "degrees", "degree": "degrees", "degrees": "degrees", "°": "degrees",
    "rad": "rad", "radian": "rad", "radians": "rad",
    "rad/s": "rad/s", "deg/s": "degrees/s", "degrees/s": "degrees/s",
    "km/l": "km/l", "l/100km": "l/100km", "mpg": "mpg",
    "nm": "Nm", "n": "N",
    "iso8601": "iso8601",
}

# (from, to): (factor, offset)
CONVERSIONS: dict[tuple[str, str], tuple[float, float]] = {
    # speed
    ("mph", "km/h"): (1.609344, 0.0),
    ("m/s", "km/h"): (3.6, 0.0),
    ("knots", "km/h"): (1.852, 0.0),
    ("km/h", "m/s"): (1 / 3.6, 0.0),
    # temperature
    ("fahrenheit", "Celsius"): (5 / 9, -32 * 5 / 9),
    ("kelvin", "Celsius"): (1.0, -273.15),
    # distance
    ("miles", "km"): (1.609344, 0.0),
    ("m", "km"): (0.001, 0.0),
    ("feet", "m"): (0.3048, 0.0),
    ("km", "m"): (1000.0, 0.0),
    ("cm", "mm"): (10.0, 0.0),
    ("m", "mm"): (1000.0, 0.0),
    ("inch", "mm"): (25.4, 0.0),
    ("cm", "m"): (0.01, 0.0),
    ("mm", "m"): (0.001, 0.0),
    # volume
    ("gallon", "l"): (3.785411784, 0.0),
    # pressure
    ("psi", "kPa"): (6.894757, 0.0),
    ("bar", "kPa"): (100.0, 0.0),
    ("kPa", "bar"): (0.01, 0.0),
    # energy and power
    ("W", "kW"): (0.001, 0.0),
    ("kW", "W"): (1000.0, 0.0),
    ("Wh", "kWh"): (0.001, 0.0),
    ("kWh", "Wh"): (1000.0, 0.0),
    # time
    ("ms", "s"): (0.001, 0.0),
    ("s", "ms"): (1000.0, 0.0),
    ("min", "s"): (60.0, 0.0),
    ("h", "s"): (3600.0, 0.0),
    ("s", "h"): (1 / 3600, 0.0),
    ("min", "h"): (1 / 60, 0.0),
    # mass
    ("pound", "kg"): (0.45359237, 0.0),
    ("g", "kg"): (0.001, 0.0),
    # angle
    ("rad", "degrees"): (57.29577951308232, 0.0),
    ("degrees", "rad"): (0.017453292519943295, 0.0),
    ("rad/s", "degrees/s"): (57.29577951308232, 0.0),
    ("degrees/s", "rad/s"): (0.017453292519943295, 0.0),
    # ratios expressed as fractions rather than percentages
    ("fraction", "percent"): (100.0, 0.0),
    ("ratio", "percent"): (100.0, 0.0),
}


def canonical(unit: str | None) -> str | None:
    """Fold a vendor unit spelling onto the spelling used in conversions."""
    if unit is None:
        return None
    key = unit.strip().lower().replace(" ", "")
    return ALIASES.get(key, unit.strip())


@dataclass(frozen=True)
class Conversion:
    source_unit: str | None
    target_unit: str | None
    factor: float
    offset: float

    @property
    def is_identity(self) -> bool:
        return self.factor == 1.0 and self.offset == 0.0

    def apply(self, value: float) -> float:
        if self.is_identity:
            return value
        return value * self.factor + self.offset


IDENTITY = Conversion(None, None, 1.0, 0.0)


def resolve(source_unit: str | None, target_unit: str | None) -> Conversion:
    """Work out how to get from a vendor unit to the VSS unit.

    A missing unit on either side means we pass the value through untouched.
    Booleans, strings and enums have no unit in VSS and take this path.
    """
    src = canonical(source_unit)
    tgt = canonical(target_unit)

    if src is None or tgt is None or src == tgt:
        return Conversion(src, tgt, 1.0, 0.0)

    pair = CONVERSIONS.get((src, tgt))
    if pair is None:
        raise UnitError(
            f"No conversion from {source_unit!r} to {target_unit!r}. "
            f"Add it to CONVERSIONS in vss/units.py or fix the mapping file."
        )
    return Conversion(src, tgt, pair[0], pair[1])


def convert(value: float, source_unit: str | None, target_unit: str | None) -> float:
    return resolve(source_unit, target_unit).apply(value)
