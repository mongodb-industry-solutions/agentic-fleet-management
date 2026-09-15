"""The canonical envelope every source lands in, and the shape of a rejection.

One row from a vendor file becomes a Reading (the row as a whole, carrying
identity, time and quality) plus one Observation per mapped column. Observations
are what get validated, stored and later fused.

Note the envelope carries sourceType rather than a trust number. How much to
believe a source is looked up per (sourceType, vssPath) at fusion time, so it can
be retuned without touching ingest.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class RejectReason(str, Enum):
    """Why an observation or reading was not accepted.

    The quality report groups by these, so the wording is what a fleet engineer
    reads when asking why a vendor's data is being dropped.
    """

    MISSING_IDENTITY = "MISSING_IDENTITY"
    MISSING_TIMESTAMP = "MISSING_TIMESTAMP"
    BAD_TIMESTAMP = "BAD_TIMESTAMP"
    TYPE_COERCION_FAILED = "TYPE_COERCION_FAILED"
    UNIT_CONVERSION_FAILED = "UNIT_CONVERSION_FAILED"
    OUT_OF_RANGE = "OUT_OF_RANGE"
    NOT_ALLOWED_VALUE = "NOT_ALLOWED_VALUE"
    PATTERN_MISMATCH = "PATTERN_MISMATCH"
    SENTINEL_VALUE = "SENTINEL_VALUE"
    NO_GPS_FIX = "NO_GPS_FIX"
    NULL_ISLAND = "NULL_ISLAND"
    COORD_OUT_OF_RANGE = "COORD_OUT_OF_RANGE"
    OUTSIDE_SERVICE_AREA = "OUTSIDE_SERVICE_AREA"
    IMPOSSIBLE_MOTION = "IMPOSSIBLE_MOTION"


REASON_TEXT = {
    RejectReason.MISSING_IDENTITY: "No vehicle identifier in the record",
    RejectReason.MISSING_TIMESTAMP: "No observation timestamp in the record",
    RejectReason.BAD_TIMESTAMP: "Timestamp could not be parsed",
    RejectReason.TYPE_COERCION_FAILED: "Value does not fit the VSS datatype",
    RejectReason.UNIT_CONVERSION_FAILED: "Unit could not be converted to the VSS unit",
    RejectReason.OUT_OF_RANGE: "Value outside the range the VSS spec allows",
    RejectReason.NOT_ALLOWED_VALUE: "Value outside the allowed set the VSS spec defines",
    RejectReason.PATTERN_MISMATCH: "Value does not match the format the VSS spec requires",
    RejectReason.SENTINEL_VALUE: "Vendor sentinel standing in for a missing reading",
    RejectReason.NO_GPS_FIX: "Position reported with no satellite fix",
    RejectReason.NULL_ISLAND: "Position at or beside 0, 0, which is an uninitialised GPS module",
    RejectReason.COORD_OUT_OF_RANGE: "Latitude or longitude outside valid bounds",
    RejectReason.OUTSIDE_SERVICE_AREA: "Position outside the operating territory",
    RejectReason.IMPOSSIBLE_MOTION: "Implied speed since the previous fix is not physically possible",
}


class Rejection(BaseModel):
    reason: RejectReason
    detail: str
    rule: str

    @property
    def text(self) -> str:
        return REASON_TEXT.get(self.reason, self.reason.value)


class Observation(BaseModel):
    """One signal reading from one source about one vehicle at one moment."""

    asset_ref: str = Field(alias="assetRef")
    asset_ref_kind: str = Field(alias="assetRefKind")
    source_id: str = Field(alias="sourceId")
    source_type: str = Field(alias="sourceType")
    vss_path: str = Field(alias="vssPath")
    value: Any
    unit: str | None = None
    observed_at: datetime = Field(alias="observedAt")
    received_at: datetime | None = Field(default=None, alias="receivedAt")
    quality: dict[str, Any] | None = None
    raw_value: Any = Field(default=None, alias="rawValue")
    raw_column: str | None = Field(default=None, alias="rawColumn")
    rejected: Rejection | None = None

    model_config = {"populate_by_name": True}

    @property
    def accepted(self) -> bool:
        return self.rejected is None

    def to_document(self) -> dict:
        doc = self.model_dump(by_alias=True, exclude_none=True)
        if self.rejected:
            doc["rejected"] = {
                "reason": self.rejected.reason.value,
                "detail": self.rejected.detail,
                "rule": self.rejected.rule,
            }
        return doc


class Position(BaseModel):
    """A latitude and longitude pulled out of a row so it can be checked together.

    VSS keeps CurrentLocation.Latitude and CurrentLocation.Longitude as separate
    leaves, but a position is only meaningful as a pair. Null Island passes a
    per-leaf range check and fails as a pair.
    """

    latitude: float
    longitude: float
    observed_at: datetime
    hdop: float | None = None
    satellites: int | None = None
    fix: str | None = None
    accuracy_m: float | None = None

    def as_geojson(self) -> dict:
        return {"type": "Point", "coordinates": [self.longitude, self.latitude]}


class ReadingResult(BaseModel):
    """What happened to one row of a vendor file."""

    row_number: int
    asset_ref: str | None = None
    # What kind of identifier the source used. The OEM sends a VIN, the tracker
    # sends a registration, so this is what identity resolution keys on.
    asset_ref_kind: str = "vin"
    observations: list[Observation] = Field(default_factory=list)
    position: Position | None = None
    position_rejection: Rejection | None = None
    row_rejection: Rejection | None = None

    @property
    def accepted_observations(self) -> list[Observation]:
        return [o for o in self.observations if o.accepted]

    @property
    def rejected_observations(self) -> list[Observation]:
        return [o for o in self.observations if not o.accepted]
