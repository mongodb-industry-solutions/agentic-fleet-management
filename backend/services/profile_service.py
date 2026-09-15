"""Load a profile and run quality reports against the files it points at.

A profile is everything that differs between fleet operators, kept in one place:
mapping files, the operating territory, trust seeds and business questions. The
core never needs editing, so adopting the demo means adding a directory rather
than forking the codebase.

Reports are cached in memory by source name. Running one needs no database.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import pickle
from pathlib import Path

import yaml

from ingest.mapping_loader import load_profile_mappings, validate_mapping
from ingest.pipeline import read_csv, run
from ingest.validation import ServiceArea
from models.mapping import MappingValidation, SourceMapping
from services.quality_service import QualityReport, build_report
from vss.loader import get_registry

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]

# Validating 7.4 million observations takes the better part of a minute, and the
# answer only changes when the extract does. Keep it across restarts.
CACHE_DIR = Path(__file__).resolve().parents[1] / "data" / "cache"


class ProfileService:
    """Holds one profile's mappings, service area and cached reports."""

    def __init__(self, name: str | None = None) -> None:
        self.name = name or os.getenv("PROFILE", "leafy")
        self.directory = REPO_ROOT / "profiles" / self.name
        if not self.directory.is_dir():
            raise FileNotFoundError(
                f"No profile at {self.directory}. Create it, or set PROFILE to one "
                f"of: {[p.name for p in (REPO_ROOT / 'profiles').iterdir() if p.is_dir()]}"
            )
        self.mappings: dict[str, SourceMapping] = load_profile_mappings(self.directory)
        self.service_area = self._load_service_area()
        self._reports: dict[str, QualityReport] = {}

    def _load_service_area(self) -> ServiceArea | None:
        """profile/service_area.geojson bounds where vehicles are expected to be."""
        path = self.directory / "service_area.geojson"
        if not path.exists():
            return None
        with path.open(encoding="utf-8") as fh:
            raw = json.load(fh)
        geometry = raw.get("geometry", raw)
        if raw.get("type") == "FeatureCollection" and raw.get("features"):
            geometry = raw["features"][0]["geometry"]
        return ServiceArea(name=raw.get("name", self.name), geometry=geometry)

    def settings(self) -> dict:
        """Optional profile/profile.yaml for display name and branding."""
        path = self.directory / "profile.yaml"
        if not path.exists():
            return {"name": self.name, "displayName": self.name.title()}
        with path.open(encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}

    def validations(self) -> list[MappingValidation]:
        registry = get_registry()
        return [validate_mapping(m, registry) for m in self.mappings.values()]

    def resolve_data_file(self, mapping: SourceMapping, override: str | None = None) -> Path:
        """Find the extract for a source.

        Looked for in order: an explicit override, profiles/<name>/data/<source>.csv,
        then backend/data/sample/<source>.csv for the bundled sample.
        """
        if override:
            candidate = Path(override)
            if not candidate.is_absolute():
                candidate = REPO_ROOT / candidate
            if candidate.exists():
                return candidate
            raise FileNotFoundError(f"No file at {candidate}")

        for candidate in (
            self.directory / "data" / f"{mapping.source.name}.csv",
            REPO_ROOT / "backend" / "data" / "sample" / f"{mapping.source.name}.csv",
        ):
            if candidate.exists():
                return candidate

        raise FileNotFoundError(
            f"No extract found for source {mapping.source.name!r}. Put a CSV at "
            f"profiles/{self.name}/data/{mapping.source.name}.csv, or generate the "
            f"sample with: uv run python scripts/make_sample_data.py"
        )

    def _cache_file(self, source_name: str, data_file: Path) -> Path:
        """One cache file per extract, keyed so a changed extract misses."""
        stat = data_file.stat()
        signature = hashlib.md5(
            f"{data_file}:{stat.st_mtime_ns}:{stat.st_size}".encode()
        ).hexdigest()[:12]
        return CACHE_DIR / f"quality-{source_name}-{signature}.pickle"

    def run_quality(
        self,
        source_name: str,
        path: str | None = None,
        limit: int | None = None,
        use_cache: bool = True,
    ) -> QualityReport:
        mapping = self.mappings.get(source_name)
        if mapping is None:
            raise KeyError(
                f"No source {source_name!r} in profile {self.name!r}. "
                f"Available: {sorted(self.mappings)}"
            )

        validation = validate_mapping(mapping, get_registry())
        if not validation.ok:
            messages = "; ".join(i.message for i in validation.errors)
            raise ValueError(f"Mapping {source_name!r} has errors: {messages}")

        data_file = self.resolve_data_file(mapping, path)

        cache_file = self._cache_file(source_name, data_file) if limit is None else None
        if use_cache and cache_file and cache_file.exists():
            try:
                report = pickle.loads(cache_file.read_bytes())
                self._reports[source_name] = report
                logger.info("Quality report for %s read from cache", source_name)
                return report
            except Exception as exc:  # noqa: BLE001
                logger.warning("Ignoring unreadable cache %s: %s", cache_file, exc)

        logger.info("Running quality report for %s over %s", source_name, data_file)

        results = run(
            read_csv(data_file, mapping),
            mapping,
            registry=get_registry(),
            service_area=self.service_area,
            limit=limit,
        )
        report = build_report(results, mapping.source.name, mapping.source.source_type)
        self._reports[source_name] = report

        if cache_file:
            try:
                CACHE_DIR.mkdir(parents=True, exist_ok=True)
                cache_file.write_bytes(pickle.dumps(report))
            except OSError as exc:
                logger.warning("Could not cache %s: %s", cache_file, exc)

        return report

    def cached_report(self, source_name: str) -> QualityReport | None:
        return self._reports.get(source_name)

    def run_all(self, limit: int | None = None) -> dict[str, QualityReport]:
        """Report on every source, reusing anything already computed.

        A report reads the whole extract, so running one for each source costs
        the better part of a minute over 800,000 rows. The extracts do not
        change while the process is up, which makes a cache hit the right answer
        for a page that just wants the headline. POST /run/{source} re-runs.
        """
        for name in self.mappings:
            if limit is None and name in self._reports:
                continue
            try:
                self.run_quality(name, limit=limit)
            except (FileNotFoundError, ValueError) as exc:
                logger.warning("Skipping %s: %s", name, exc)
        return dict(self._reports)


_service: ProfileService | None = None


def get_profile_service() -> ProfileService:
    global _service
    if _service is None:
        _service = ProfileService()
    return _service


def reset_profile_service() -> None:
    """Drop the cached profile so a changed mapping file is picked up."""
    global _service
    _service = None
