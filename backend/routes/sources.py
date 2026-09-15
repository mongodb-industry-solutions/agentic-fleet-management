"""Profile and mapping routes.

A source is one vendor feed described by one mapping file. Validation is exposed
so the console can show a fleet engineer that their mapping is wrong before they
wait for an ingest run to tell them.
"""

from fastapi import APIRouter, HTTPException

from services.profile_service import get_profile_service, reset_profile_service

router = APIRouter(prefix="/api/sources", tags=["sources"])


def _mapping_payload(mapping, validation) -> dict:
    return {
        "name": mapping.source.name,
        "sourceType": mapping.source.source_type,
        "description": mapping.source.description,
        "identity": {
            "column": mapping.identity.column,
            "kind": mapping.identity.kind,
        },
        "timestampColumn": mapping.timestamp.column,
        "timezone": mapping.timestamp.tz,
        "signalCount": len(mapping.signals),
        "signals": [
            {
                "column": column,
                "vssPath": sig.vss_path,
                "declaredUnit": sig.unit,
                "hasValueMap": sig.value_map is not None,
                "sentinels": sig.sentinels,
            }
            for column, sig in mapping.signals.items()
        ],
        "validation": {
            "ok": validation.ok,
            "errors": [i.model_dump() for i in validation.errors],
            "warnings": [i.model_dump() for i in validation.warnings],
        },
    }


@router.get("")
def list_sources():
    service = get_profile_service()
    validations = {v.source_name: v for v in service.validations()}
    return {
        "profile": service.settings(),
        "serviceArea": service.service_area.name if service.service_area else None,
        "sources": [
            _mapping_payload(m, validations[name])
            for name, m in service.mappings.items()
        ],
    }


@router.get("/{source_name}")
def get_source(source_name: str):
    service = get_profile_service()
    mapping = service.mappings.get(source_name)
    if mapping is None:
        raise HTTPException(
            status_code=404,
            detail=f"No source {source_name!r}. Available: {sorted(service.mappings)}",
        )
    validation = next(
        v for v in service.validations() if v.source_name == source_name
    )
    return _mapping_payload(mapping, validation)


@router.post("/reload")
def reload_profile():
    """Re-read mapping files from disk after they have been edited."""
    reset_profile_service()
    service = get_profile_service()
    return {
        "profile": service.name,
        "sources": sorted(service.mappings),
        "reloaded": True,
    }
