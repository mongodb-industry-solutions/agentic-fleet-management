"""The declarative column-to-VSS mapping file.

This is the only thing a fleet operator writes to get their own data through the
pipeline. Everything else, including unit conversion, range checks and type
coercion, is driven off vss.json once a path is named. Getting a data quality
report on a historical extract should not require writing Python.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

IdentityKind = Literal["vin", "plate", "device", "agreement", "internal"]


class SourceInfo(BaseModel):
    """Who sent the data. sourceType is the key trust scoring is looked up by."""

    name: str
    source_type: str = Field(alias="sourceType")
    description: str | None = None

    model_config = {"populate_by_name": True}


class IdentityColumn(BaseModel):
    """Which column names the vehicle, and what kind of identifier it holds."""

    column: str
    kind: IdentityKind = "vin"
    strip: bool = True
    upper: bool = True


class TimestampColumn(BaseModel):
    column: str
    tz: str | None = None
    format: str | None = None
    unit: Literal["s", "ms", "us"] | None = None  # for epoch numeric columns


class QualityColumns(BaseModel):
    """Vendor-reported quality signals, used by the confidence factor in fusion."""

    hdop: str | None = None
    satellites: str | None = None
    fix: str | None = None
    accuracy_m: str | None = Field(default=None, alias="accuracyM")

    model_config = {"populate_by_name": True}


class SignalMapping(BaseModel):
    """One vendor column mapped onto one VSS leaf."""

    vss_path: str = Field(alias="vssPath")
    unit: str | None = None
    value_map: dict[str, Any] | None = Field(default=None, alias="map")
    scale: float | None = None
    offset: float | None = None
    sentinels: list[Any] | None = None

    model_config = {"populate_by_name": True}

    @field_validator("vss_path")
    @classmethod
    def strip_vehicle_root(cls, v: str) -> str:
        """Paths are Vehicle-relative. Accept the prefixed form and strip it."""
        return v[len("Vehicle."):] if v.startswith("Vehicle.") else v


class IngestOptions(BaseModel):
    null_values: list[str] = Field(
        default_factory=lambda: ["", "NULL", "null", "NaN", "-", "N/A"],
        alias="nullValues",
    )
    delimiter: str = ","
    encoding: str = "utf-8"
    skip_unmapped_columns: bool = Field(default=True, alias="skipUnmappedColumns")

    model_config = {"populate_by_name": True}


class SourceMapping(BaseModel):
    """A whole mapping file."""

    source: SourceInfo
    identity: IdentityColumn
    timestamp: TimestampColumn
    received_timestamp: TimestampColumn | None = Field(default=None, alias="receivedTimestamp")
    quality: QualityColumns | None = None
    signals: dict[str, SignalMapping]
    options: IngestOptions = Field(default_factory=IngestOptions)

    model_config = {"populate_by_name": True}

    @property
    def mapped_paths(self) -> list[str]:
        return [m.vss_path for m in self.signals.values()]


class MappingIssue(BaseModel):
    """One problem found while checking a mapping file against the VSS registry."""

    severity: Literal["error", "warning"]
    column: str | None = None
    vss_path: str | None = None
    message: str


class MappingValidation(BaseModel):
    """Result of checking a mapping file. Errors block ingest, warnings do not."""

    source_name: str
    ok: bool
    signal_count: int
    issues: list[MappingIssue] = Field(default_factory=list)

    @property
    def errors(self) -> list[MappingIssue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def warnings(self) -> list[MappingIssue]:
        return [i for i in self.issues if i.severity == "warning"]
