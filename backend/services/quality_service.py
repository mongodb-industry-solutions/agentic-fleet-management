"""Build a data quality report from a run of the ingest pipeline.

This is what a fleet operator gets back from step one, before any agent, any
map and any database write. It answers questions most operators have never had
measured: how many position fixes land on Null Island, how often a vehicle
appears to teleport, which signals are dominated by sentinel values, and which
source is responsible.

The counts by themselves make the case. A firmware release that breaks a batch of
trackers shows up as a reject rate that moves.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable

from models.telemetry import REASON_TEXT, ReadingResult, RejectReason

# How many worked examples to keep per rejection reason. Enough to show a fleet
# engineer a real row, few enough to keep the report small.
SAMPLES_PER_REASON = 5
MAP_SAMPLE_LIMIT = 500


@dataclass
class QualityReport:
    source_name: str
    source_type: str

    rows_read: int = 0
    rows_rejected: int = 0
    observations_read: int = 0
    observations_rejected: int = 0

    assets_seen: set[str] = field(default_factory=set)
    first_observed: datetime | None = None
    last_observed: datetime | None = None

    row_reasons: Counter = field(default_factory=Counter)
    observation_reasons: Counter = field(default_factory=Counter)
    reasons_by_path: dict[str, Counter] = field(
        default_factory=lambda: defaultdict(Counter)
    )
    signal_counts: Counter = field(default_factory=Counter)
    samples: dict[str, list] = field(default_factory=lambda: defaultdict(list))

    positions_checked: int = 0
    positions_rejected: int = 0
    flagged_points: list[dict] = field(default_factory=list)
    accepted_points: list[dict] = field(default_factory=list)

    @property
    def rows_accepted(self) -> int:
        return self.rows_read - self.rows_rejected

    @property
    def observations_accepted(self) -> int:
        return self.observations_read - self.observations_rejected

    @property
    def reject_rate(self) -> float:
        if self.observations_read == 0:
            return 0.0
        return self.observations_rejected / self.observations_read

    def _record_sample(self, reason: str, payload: dict) -> None:
        bucket = self.samples[reason]
        if len(bucket) < SAMPLES_PER_REASON:
            bucket.append(payload)

    def add(self, result: ReadingResult) -> None:
        self.rows_read += 1

        if result.row_rejection is not None:
            self.rows_rejected += 1
            reason = result.row_rejection.reason.value
            self.row_reasons[reason] += 1
            self._record_sample(reason, {
                "row": result.row_number,
                "assetRef": result.asset_ref,
                "detail": result.row_rejection.detail,
            })
            return

        if result.asset_ref:
            self.assets_seen.add(result.asset_ref)

        for obs in result.observations:
            self.observations_read += 1
            self.signal_counts[obs.vss_path] += 1

            if self.first_observed is None or obs.observed_at < self.first_observed:
                self.first_observed = obs.observed_at
            if self.last_observed is None or obs.observed_at > self.last_observed:
                self.last_observed = obs.observed_at

            if obs.rejected is None:
                continue

            self.observations_rejected += 1
            reason = obs.rejected.reason.value
            self.observation_reasons[reason] += 1
            self.reasons_by_path[obs.vss_path][reason] += 1
            self._record_sample(reason, {
                "row": result.row_number,
                "assetRef": obs.asset_ref,
                "vssPath": obs.vss_path,
                "column": obs.raw_column,
                "rawValue": obs.raw_value,
                "observedAt": obs.observed_at.isoformat(),
                "detail": obs.rejected.detail,
            })

        if result.position is not None:
            self.positions_checked += 1
            point = {
                "assetRef": result.asset_ref,
                "lat": result.position.latitude,
                "lon": result.position.longitude,
                "observedAt": result.position.observed_at.isoformat(),
            }
            if result.position_rejection is not None:
                self.positions_rejected += 1
                if len(self.flagged_points) < MAP_SAMPLE_LIMIT:
                    point["reason"] = result.position_rejection.reason.value
                    point["detail"] = result.position_rejection.detail
                    self.flagged_points.append(point)
            elif len(self.accepted_points) < MAP_SAMPLE_LIMIT:
                self.accepted_points.append(point)

    def worst_signals(self, limit: int = 10) -> list[dict]:
        """Signals ranked by how much of their data is unusable."""
        out = []
        for path, reasons in self.reasons_by_path.items():
            total = self.signal_counts[path]
            rejected = sum(reasons.values())
            out.append({
                "vssPath": path,
                "observations": total,
                "rejected": rejected,
                "rejectRate": rejected / total if total else 0.0,
                "reasons": dict(reasons),
            })
        out.sort(key=lambda r: (-r["rejectRate"], -r["rejected"]))
        return out[:limit]

    def to_dict(self) -> dict:
        return {
            "source": {"name": self.source_name, "sourceType": self.source_type},
            "window": {
                "from": self.first_observed.isoformat() if self.first_observed else None,
                "to": self.last_observed.isoformat() if self.last_observed else None,
            },
            "totals": {
                "rowsRead": self.rows_read,
                "rowsAccepted": self.rows_accepted,
                "rowsRejected": self.rows_rejected,
                "observationsRead": self.observations_read,
                "observationsAccepted": self.observations_accepted,
                "observationsRejected": self.observations_rejected,
                "rejectRate": round(self.reject_rate, 5),
                "assetsSeen": len(self.assets_seen),
                "signalsSeen": len(self.signal_counts),
            },
            "positions": {
                "checked": self.positions_checked,
                "rejected": self.positions_rejected,
                "rejectRate": round(
                    self.positions_rejected / self.positions_checked, 5
                ) if self.positions_checked else 0.0,
            },
            "rowReasons": _reason_rows(self.row_reasons, self.rows_read),
            "observationReasons": _reason_rows(
                self.observation_reasons, self.observations_read
            ),
            "worstSignals": self.worst_signals(),
            "samples": {k: v for k, v in self.samples.items()},
            "map": {
                "flagged": self.flagged_points,
                "accepted": self.accepted_points,
            },
        }


def _reason_rows(counter: Counter, total: int) -> list[dict]:
    rows = []
    for reason, count in counter.most_common():
        try:
            text = REASON_TEXT[RejectReason(reason)]
        except ValueError:
            text = reason
        rows.append({
            "reason": reason,
            "label": text,
            "count": count,
            "share": round(count / total, 5) if total else 0.0,
        })
    return rows


def build_report(
    results: Iterable[ReadingResult], source_name: str, source_type: str
) -> QualityReport:
    report = QualityReport(source_name=source_name, source_type=source_type)
    for result in results:
        report.add(result)
    return report
