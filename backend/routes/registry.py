"""VSS registry routes.

The registry is generated from vss.json at startup and is read-only. Search backs
the mapping helper in the console: finding the right path among 1,382 leaves by
hand is tedious, so the UI offers candidates while somebody writes a mapping.
"""

from fastapi import APIRouter, HTTPException, Query

from services.profile_service import get_profile_service
from vss.loader import get_registry

router = APIRouter(prefix="/api/registry", tags=["registry"])


@router.get("/summary")
def summary():
    """Counts by leaf kind, unit coverage and the VSS version in use."""
    return get_registry().summary()


@router.get("/search")
def search(
    q: str = Query(min_length=1, description="Match against path and description"),
    limit: int = Query(default=25, le=200),
):
    registry = get_registry()
    hits = registry.search(q, limit=limit)
    return {
        "query": q,
        "count": len(hits),
        "results": [
            {
                "vssPath": s.path,
                "datatype": s.datatype,
                "kind": s.kind,
                "unit": s.unit,
                "description": s.description,
                "min": s.minimum,
                "max": s.maximum,
                "allowed": s.allowed,
                "deprecated": s.is_deprecated,
            }
            for s in hits
        ],
    }


@router.get("/signal/{vss_path:path}")
def signal(vss_path: str):
    registry = get_registry()
    spec = registry.get(vss_path)
    if spec is None:
        near = [s.path for s in registry.search(vss_path.split(".")[-1], limit=5)]
        raise HTTPException(
            status_code=404,
            detail=f"{vss_path} is not in VSS {registry.version}. Closest: {near}",
        )
    return spec.to_document()


@router.get("/tree")
def tree():
    """The whole VSS hierarchy as a nested tree, branches and leaves.

    The spec is 1,382 signals under 354 branches, which is unreadable as a flat
    list. Sent as the tree it actually is, with the paths this profile maps
    marked at every level, it explains both the standard and how small a slice
    of it any one fleet needs.
    """
    registry = get_registry()

    mapped: set[str] = set()
    try:
        for mapping in get_profile_service().mappings.values():
            mapped.update(mapping.mapped_paths)
    except Exception:  # noqa: BLE001
        pass

    root = {"name": "Vehicle", "path": "", "kind": "branch", "children": {}}

    for spec in registry.signals.values():
        parts = spec.path.split(".")
        node = root
        # Walk the branches, creating them on the way down.
        for level, part in enumerate(parts[:-1]):
            path = ".".join(parts[: level + 1])
            node = node["children"].setdefault(
                part,
                {"name": part, "path": path, "kind": "branch", "children": {}},
            )
        node["children"][parts[-1]] = {
            "name": parts[-1],
            "path": spec.path,
            "kind": spec.kind,
            "datatype": spec.datatype,
            "unit": spec.unit,
            "description": spec.description,
            "mapped": spec.path in mapped,
        }

    def collapse(node: dict) -> dict:
        """Turn the child dicts into sorted lists and total the leaves."""
        if node["kind"] != "branch":
            return node

        children = [collapse(child) for child in node["children"].values()]
        # Branches first, then leaves, each alphabetical.
        children.sort(key=lambda c: (c["kind"] != "branch", c["name"]))

        node["children"] = children
        node["leaves"] = sum(
            child.get("leaves", 1) if child["kind"] == "branch" else 1
            for child in children
        )
        node["mappedCount"] = sum(
            child.get("mappedCount", 0)
            if child["kind"] == "branch"
            else (1 if child.get("mapped") else 0)
            for child in children
        )
        return node

    return {
        "version": registry.version,
        "branches": len(registry.branches),
        "leaves": len(registry),
        "mappedLeaves": len(mapped),
        "tree": collapse(root),
    }


@router.get("/branch/{branch:path}")
def branch(branch: str, limit: int = Query(default=200, le=1500)):
    """Every signal at or below a branch, for browsing the tree."""
    signals = get_registry().under(branch)
    return {
        "branch": branch,
        "count": len(signals),
        "signals": [
            {"vssPath": s.path, "kind": s.kind, "unit": s.unit, "datatype": s.datatype}
            for s in signals[:limit]
        ],
    }
