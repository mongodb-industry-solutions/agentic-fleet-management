"""Load mapping files and check them against the VSS registry.

The check is the thing that keeps a mapping honest: every path has to exist in
VSS 6.1.0, every unit has to be convertible into the unit the spec declares, and
every value map has to produce values the declared datatype can hold. A typo in a
mapping should break the build rather than surface months later as a fleet of
vehicles silently reporting nothing.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import yaml

from models.mapping import (
    MappingIssue,
    MappingValidation,
    SourceMapping,
)
from vss.loader import VssRegistry, get_registry
from vss.units import UnitError, resolve

logger = logging.getLogger(__name__)

BOOL_WORDS = {"true", "false", "yes", "no", "1", "0", "on", "off"}


def load_mapping(path: str | Path) -> SourceMapping:
    with open(path, "r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    return SourceMapping.model_validate(raw)


def load_profile_mappings(profile_dir: str | Path) -> dict[str, SourceMapping]:
    """Load every mapping file in a profile's mappings/ directory."""
    mappings_dir = Path(profile_dir) / "mappings"
    if not mappings_dir.is_dir():
        raise FileNotFoundError(f"No mappings directory at {mappings_dir}")

    found: dict[str, SourceMapping] = {}
    for file in sorted(mappings_dir.glob("*.y*ml")):
        mapping = load_mapping(file)
        found[mapping.source.name] = mapping
    logger.info("Loaded %d mapping(s) from %s", len(found), mappings_dir)
    return found


def _check_value_map(
    column: str, mapping, spec, issues: list[MappingIssue]
) -> None:
    """A value map has to produce something the VSS datatype can hold."""
    if not mapping.value_map:
        return

    values = list(mapping.value_map.values())
    if spec.datatype == "boolean":
        bad = [v for v in values if not isinstance(v, bool)]
        if bad:
            issues.append(MappingIssue(
                severity="error", column=column, vss_path=spec.path,
                message=f"{spec.path} is boolean but the map produces {bad!r}",
            ))
    elif spec.allowed:
        bad = [v for v in values if v not in spec.allowed]
        if bad:
            issues.append(MappingIssue(
                severity="error", column=column, vss_path=spec.path,
                message=(
                    f"{spec.path} only allows {spec.allowed}, "
                    f"but the map produces {bad!r}"
                ),
            ))
    elif spec.is_numeric:
        bad = [v for v in values if not isinstance(v, (int, float))]
        if bad:
            issues.append(MappingIssue(
                severity="error", column=column, vss_path=spec.path,
                message=f"{spec.path} is {spec.datatype} but the map produces {bad!r}",
            ))


def validate_mapping(
    mapping: SourceMapping, registry: VssRegistry | None = None
) -> MappingValidation:
    """Check one mapping file. Errors block ingest, warnings are advisory."""
    registry = registry or get_registry()
    issues: list[MappingIssue] = []
    seen_paths: dict[str, str] = {}

    for column, sig in mapping.signals.items():
        spec = registry.get(sig.vss_path)

        if spec is None:
            near = [s.path for s in registry.search(sig.vss_path.split(".")[-1])][:3]
            hint = f" Did you mean {near}?" if near else ""
            issues.append(MappingIssue(
                severity="error", column=column, vss_path=sig.vss_path,
                message=f"{sig.vss_path} is not a signal in VSS {registry.version}.{hint}",
            ))
            continue

        if spec.is_deprecated:
            issues.append(MappingIssue(
                severity="warning", column=column, vss_path=spec.path,
                message=f"{spec.path} is deprecated in VSS: {spec.deprecation}",
            ))

        if spec.path in seen_paths:
            issues.append(MappingIssue(
                severity="warning", column=column, vss_path=spec.path,
                message=(
                    f"{spec.path} is already mapped from column "
                    f"{seen_paths[spec.path]!r}; the later column wins"
                ),
            ))
        seen_paths[spec.path] = column

        try:
            conversion = resolve(sig.unit, spec.unit)
        except UnitError as exc:
            issues.append(MappingIssue(
                severity="error", column=column, vss_path=spec.path,
                message=str(exc),
            ))
        else:
            if sig.unit is None and spec.unit is not None and spec.is_numeric:
                issues.append(MappingIssue(
                    severity="warning", column=column, vss_path=spec.path,
                    message=(
                        f"{spec.path} is measured in {spec.unit} but the mapping "
                        f"declares no unit; values will be taken as {spec.unit} as-is"
                    ),
                ))
            elif not conversion.is_identity:
                logger.debug(
                    "%s: %s -> %s (x%s%+g)",
                    spec.path, sig.unit, spec.unit,
                    conversion.factor, conversion.offset,
                )

        if spec.kind == "attribute":
            issues.append(MappingIssue(
                severity="warning", column=column, vss_path=spec.path,
                message=(
                    f"{spec.path} is a VSS attribute, meaning it is static for the "
                    f"vehicle; it will be written to the asset record rather than "
                    f"to the time series"
                ),
            ))

        _check_value_map(column, sig, spec, issues)

    return MappingValidation(
        source_name=mapping.source.name,
        ok=not any(i.severity == "error" for i in issues),
        signal_count=len(mapping.signals),
        issues=issues,
    )


def validate_profile(profile_dir: str | Path) -> list[MappingValidation]:
    registry = get_registry()
    return [
        validate_mapping(m, registry)
        for m in load_profile_mappings(profile_dir).values()
    ]


def default_profile_dir() -> Path:
    """profiles/<PROFILE>/ relative to the repo root."""
    root = Path(__file__).resolve().parents[2]
    return root / "profiles" / os.getenv("PROFILE", "leafy")
