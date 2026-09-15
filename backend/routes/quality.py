"""Data quality routes.

Running a report needs no database and no AI credentials. That is deliberate:
a fleet operator can point this at a historical extract and get an answer before
granting anyone access to a production system.
"""

from fastapi import APIRouter, HTTPException, Query

from services.profile_service import get_profile_service

router = APIRouter(prefix="/api/quality", tags=["quality"])


@router.post("/run/{source_name}")
def run_quality(
    source_name: str,
    path: str | None = Query(default=None, description="Override the extract path"),
    limit: int | None = Query(default=None, description="Stop after N rows"),
):
    """Map, validate and summarise one vendor extract."""
    service = get_profile_service()
    try:
        # An explicit re-run means the operator wants the extract read again.
        report = service.run_quality(
            source_name, path=path, limit=limit, use_cache=False
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return report.to_dict()


@router.get("/{source_name}")
def get_quality(source_name: str):
    """The last report for a source, running one if none has been run yet."""
    service = get_profile_service()
    report = service.cached_report(source_name)
    if report is None:
        try:
            report = service.run_quality(source_name)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    return report.to_dict()


@router.get("")
def summary():
    """Headline numbers across every source in the profile."""
    service = get_profile_service()
    reports = service.run_all()
    if not reports:
        raise HTTPException(
            status_code=404,
            detail="No extracts found for any source in this profile",
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
