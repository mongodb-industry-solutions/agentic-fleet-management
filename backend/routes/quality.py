"""Data quality routes.

Reports are written to MongoDB by the load, because the answer only changes when
the extract does and reading 800,000 rows to redraw a tab is work nobody asked
for. The API therefore answers from `quality_reports` with a findOne.

Running a report still needs no database and no AI credentials, and that path is
kept intact: with no MONGODB_URI, or with a database that has no report stored
yet, the extract is read exactly as it was before. That is deliberate, because a
fleet operator can point this at a historical extract and get an answer before
granting anyone access to a production system.
"""

import logging
import os

from fastapi import APIRouter, HTTPException, Query

from db.mdb import get_mongo_client
from db.schema import QUALITY_REPORTS, QUALITY_SUMMARY_ID
from services.profile_service import get_profile_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/quality", tags=["quality"])


def _collection():
    """The stored reports, or None when this instance has no database."""
    client = get_mongo_client()
    if client is None:
        return None
    return client[os.getenv("DATABASE_NAME", "fleet")][QUALITY_REPORTS]


def _stored(report_id: str) -> dict | None:
    collection = _collection()
    if collection is None:
        return None
    try:
        document = collection.find_one({"_id": report_id})
    except Exception as exc:  # noqa: BLE001
        # A database that is unreachable is not a reason to fail: the extract is
        # still right there.
        logger.warning("Could not read the stored quality report: %s", exc)
        return None
    if document is None:
        return None
    document.pop("_id", None)
    document.pop("generatedAt", None)
    return document


def _run(source_name: str, path: str | None = None, limit: int | None = None,
         use_cache: bool = True) -> dict:
    """Read the extract, the way this always worked."""
    service = get_profile_service()
    try:
        report = service.run_quality(
            source_name, path=path, limit=limit, use_cache=use_cache
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail=(
                f"{exc} No report is stored in MongoDB for {source_name!r} either, "
                "so run: uv run python scripts/load.py"
            ),
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return report.to_dict()


@router.post("/run/{source_name}")
def run_quality(
    source_name: str,
    path: str | None = Query(default=None, description="Override the extract path"),
    limit: int | None = Query(default=None, description="Stop after N rows"),
):
    """Map, validate and summarise one vendor extract.

    An explicit re-run always reads the extract, cache disabled. It is the most
    expensive call in the API, which is why nothing reaches it by accident.
    """
    report = _run(source_name, path=path, limit=limit, use_cache=False)

    # A re-run is the operator saying the stored answer is stale, so replace it.
    collection = _collection()
    if collection is not None and limit is None:
        try:
            collection.replace_one(
                {"_id": source_name}, {**report, "_id": source_name}, upsert=True
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not store the re-run report: %s", exc)

    return report


@router.get("/{source_name}")
def get_quality(source_name: str):
    """One source's report: from MongoDB if the load wrote one, else the extract."""
    stored = _stored(source_name)
    if stored is not None:
        return stored

    cached = get_profile_service().cached_report(source_name)
    if cached is not None:
        return cached.to_dict()

    return _run(source_name)


@router.get("")
def summary():
    """Headline numbers across every source in the profile."""
    stored = _stored(QUALITY_SUMMARY_ID)
    if stored is not None:
        return stored

    service = get_profile_service()
    reports = service.run_all()
    if not reports:
        raise HTTPException(
            status_code=404,
            detail=(
                "No quality reports stored in MongoDB and no extracts found for any "
                "source in this profile. Run: uv run python scripts/load.py"
            ),
        )

    rows = sum(r.rows_read for r in reports.values())
    observations = sum(r.observations_read for r in reports.values())
    rejected = sum(r.observations_rejected for r in reports.values())
    assets: set[str] = set()
    for report in reports.values():
        assets |= report.assets_seen

    return {
        "profile": service.name,
        "sources": [
            {
                "name": name,
                "sourceType": report.source_type,
                "rowsRead": report.rows_read,
                "observationsRead": report.observations_read,
                "observationsRejected": report.observations_rejected,
                "rejectRate": round(report.reject_rate, 5),
                "assetsSeen": len(report.assets_seen),
                "topReasons": [
                    {"reason": reason, "count": count}
                    for reason, count in report.observation_reasons.most_common(3)
                ],
            }
            for name, report in reports.items()
        ],
        "totals": {
            "rowsRead": rows,
            "observationsRead": observations,
            "observationsRejected": rejected,
            "rejectRate": round(rejected / observations, 5) if observations else 0.0,
            "assetsSeen": len(assets),
        },
    }
