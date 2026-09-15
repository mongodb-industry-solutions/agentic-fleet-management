"""Turn rows of a vendor file into validated observations.

The pipeline is deliberately pure: it reads rows and produces results without
touching MongoDB. That lets a fleet operator point it at a historical extract and
get a data quality report before granting anyone write access to a database,
which is the difference between a two week pilot and a two month procurement.
"""

from __future__ import annotations

import csv
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator
from zoneinfo import ZoneInfo

from models.mapping import SourceMapping, TimestampColumn
from models.telemetry import (
    Observation,
    Position,
    ReadingResult,
    RejectReason,
    Rejection,
)
from ingest.validation import MotionCheck, ServiceArea, check_position, check_value, coerce
from vss.loader import VssRegistry, get_registry
from vss.units import UnitError, resolve

logger = logging.getLogger(__name__)

LATITUDE_PATH = "CurrentLocation.Latitude"
LONGITUDE_PATH = "CurrentLocation.Longitude"

TIMESTAMP_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M:%S.%f",
    "%Y-%m-%dT%H:%M:%S",
    "%d/%m/%Y %H:%M:%S",
    "%m/%d/%Y %H:%M:%S",
    "%Y/%m/%d %H:%M:%S",
)


def parse_timestamp(raw, spec: TimestampColumn) -> datetime:
    """Parse a vendor timestamp into an aware UTC datetime."""
    if isinstance(raw, datetime):
        parsed = raw
    elif spec.unit:
        divisor = {"s": 1, "ms": 1_000, "us": 1_000_000}[spec.unit]
        parsed = datetime.fromtimestamp(float(raw) / divisor, tz=timezone.utc)
    elif spec.format:
        parsed = datetime.strptime(str(raw).strip(), spec.format)
    else:
        text = str(raw).strip()
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            for fmt in TIMESTAMP_FORMATS:
                try:
                    parsed = datetime.strptime(text, fmt)
                    break
                except ValueError:
                    continue
            else:
                raise ValueError(f"{raw!r} does not look like a timestamp")

    if parsed.tzinfo is None:
        zone = ZoneInfo(spec.tz) if spec.tz else timezone.utc
        parsed = parsed.replace(tzinfo=zone)
    return parsed.astimezone(timezone.utc)


@dataclass
class RowMapper:
    """Maps one vendor row onto VSS observations and validates them."""

    mapping: SourceMapping
    registry: VssRegistry = field(default_factory=get_registry)
    service_area: ServiceArea | None = None
    motion: MotionCheck = field(default_factory=MotionCheck)

    def __post_init__(self) -> None:
        self._nulls = {v.strip().lower() for v in self.mapping.options.null_values}
        # Resolve unit conversions once rather than per row.
        self._conversions = {}
        for column, sig in self.mapping.signals.items():
            spec = self.registry.get(sig.vss_path)
            if spec is None:
                continue
            try:
                self._conversions[column] = resolve(sig.unit, spec.unit)
            except UnitError:
                self._conversions[column] = None

    def _is_null(self, raw) -> bool:
        return raw is None or str(raw).strip().lower() in self._nulls

    def _identity(self, row: dict) -> str | None:
        raw = row.get(self.mapping.identity.column)
        if self._is_null(raw):
            return None
        text = str(raw)
        if self.mapping.identity.strip:
            text = text.strip()
        if self.mapping.identity.upper:
            text = text.upper()
        return text or None

    def _quality(self, row: dict) -> dict:
        cols = self.mapping.quality
        if cols is None:
            return {}
        out = {}
        for attr, column in (
            ("hdop", cols.hdop),
            ("satellites", cols.satellites),
            ("fix", cols.fix),
            ("accuracyM", cols.accuracy_m),
        ):
            if column and not self._is_null(row.get(column)):
                out[attr] = row[column]
        return out

    def _observation(self, column, sig, raw, ident, observed_at, received_at, quality):
        """Map, convert and validate one cell. Returns None if the cell is empty."""
        if self._is_null(raw):
            return None

        spec = self.registry.get(sig.vss_path)
        if spec is None:
            return None

        base = dict(
            assetRef=ident,
            assetRefKind=self.mapping.identity.kind,
            sourceId=self.mapping.source.name,
            sourceType=self.mapping.source.source_type,
            vssPath=spec.path,
            unit=spec.unit,
            observedAt=observed_at,
            receivedAt=received_at,
            quality=quality or None,
            rawValue=raw,
            rawColumn=column,
        )

        value = raw
        if sig.value_map:
            key = str(raw).strip()
            if key not in sig.value_map:
                return Observation(**base, value=None, rejected=Rejection(
                    reason=RejectReason.NOT_ALLOWED_VALUE,
                    detail=f"{raw!r} has no entry in the value map for {column}",
                    rule="mapping.map",
                ))
            value = sig.value_map[key]

        conversion = self._conversions.get(column)
        if conversion is None:
            return Observation(**base, value=None, rejected=Rejection(
                reason=RejectReason.UNIT_CONVERSION_FAILED,
                detail=f"cannot convert {sig.unit!r} to {spec.unit!r}",
                rule="units.resolve",
            ))

        # Convert before coercing. A fuel level of 0.1774 expressed as a
        # fraction is 17.74 percent, but rounding it to the uint8 the spec
        # declares first would make it zero and the conversion would then have
        # nothing left to scale.
        try:
            if spec.is_numeric and not isinstance(value, bool):
                number = float(value)
                if not conversion.is_identity:
                    number = conversion.apply(number)
                if sig.scale is not None:
                    number = number * sig.scale
                if sig.offset is not None:
                    number = number + sig.offset
                value = coerce(number, spec)
            else:
                value = coerce(value, spec)
        except (ValueError, TypeError) as exc:
            return Observation(**base, value=None, rejected=Rejection(
                reason=RejectReason.TYPE_COERCION_FAILED,
                detail=f"{raw!r} is not a valid {spec.datatype}: {exc}",
                rule="vss.datatype",
            ))

        rejection = check_value(value, spec, sig.sentinels)
        return Observation(**base, value=value, rejected=rejection)

    def map_row(self, row: dict, row_number: int) -> ReadingResult:
        result = ReadingResult(
            row_number=row_number, asset_ref_kind=self.mapping.identity.kind
        )

        ident = self._identity(row)
        if ident is None:
            result.row_rejection = Rejection(
                reason=RejectReason.MISSING_IDENTITY,
                detail=f"column {self.mapping.identity.column!r} was empty",
                rule="mapping.identity",
            )
            return result
        result.asset_ref = ident

        raw_ts = row.get(self.mapping.timestamp.column)
        if self._is_null(raw_ts):
            result.row_rejection = Rejection(
                reason=RejectReason.MISSING_TIMESTAMP,
                detail=f"column {self.mapping.timestamp.column!r} was empty",
                rule="mapping.timestamp",
            )
            return result
        try:
            observed_at = parse_timestamp(raw_ts, self.mapping.timestamp)
        except (ValueError, KeyError, OverflowError) as exc:
            result.row_rejection = Rejection(
                reason=RejectReason.BAD_TIMESTAMP,
                detail=f"{raw_ts!r}: {exc}",
                rule="mapping.timestamp",
            )
            return result

        received_at = None
        if self.mapping.received_timestamp:
            raw_recv = row.get(self.mapping.received_timestamp.column)
            if not self._is_null(raw_recv):
                try:
                    received_at = parse_timestamp(raw_recv, self.mapping.received_timestamp)
                except ValueError:
                    received_at = None

        quality = self._quality(row)

        for column, sig in self.mapping.signals.items():
            obs = self._observation(
                column, sig, row.get(column), ident, observed_at, received_at, quality
            )
            if obs is not None:
                result.observations.append(obs)

        self._resolve_position(result, quality, observed_at)
        return result

    def _resolve_position(self, result, quality, observed_at) -> None:
        """Pull latitude and longitude back out and check them as a pair."""
        lat = lon = None
        for obs in result.observations:
            if obs.vss_path == LATITUDE_PATH and obs.accepted:
                lat = obs.value
            elif obs.vss_path == LONGITUDE_PATH and obs.accepted:
                lon = obs.value
        if lat is None or lon is None:
            return

        position = Position(
            latitude=float(lat),
            longitude=float(lon),
            observed_at=observed_at,
            hdop=_maybe_float(quality.get("hdop")),
            satellites=_maybe_int(quality.get("satellites")),
            fix=quality.get("fix"),
            accuracy_m=_maybe_float(quality.get("accuracyM")),
        )
        result.position = position

        rejection = check_position(position, self.service_area)
        if rejection is None:
            rejection = self.motion.check(result.asset_ref, position)

        if rejection is not None:
            result.position_rejection = rejection
            for obs in result.observations:
                if obs.vss_path in (LATITUDE_PATH, LONGITUDE_PATH):
                    obs.rejected = rejection


def _maybe_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _maybe_int(value):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def read_csv(path: str | Path, mapping: SourceMapping) -> Iterator[dict]:
    opts = mapping.options
    with open(path, "r", encoding=opts.encoding, newline="") as fh:
        for row in csv.DictReader(fh, delimiter=opts.delimiter):
            yield row


def run(
    rows: Iterable[dict],
    mapping: SourceMapping,
    registry: VssRegistry | None = None,
    service_area: ServiceArea | None = None,
    limit: int | None = None,
) -> Iterator[ReadingResult]:
    """Map and validate an iterable of rows.

    Rows should arrive sorted by vehicle and time for the mobility test to mean
    anything. An unsorted file still validates at levels 1, 2 and 4.
    """
    mapper = RowMapper(
        mapping=mapping,
        registry=registry or get_registry(),
        service_area=service_area,
    )
    for number, row in enumerate(rows, start=1):
        if limit is not None and number > limit:
            return
        yield mapper.map_row(row, number)
