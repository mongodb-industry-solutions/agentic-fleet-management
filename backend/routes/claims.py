"""Damage claim routes: search by meaning, and price from precedent.

The assess endpoint is the workflow with money attached. A car comes back with a
mark on it, the photo is read, similar settled claims are retrieved, and the
charge is quoted against what those actually cost rather than against somebody's
judgement.
"""

from __future__ import annotations

import io
import os

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response

from db.mdb import get_mongo_client
from services.claims import CLAIMS, VECTOR_INDEX
from services.claims_service import CANDIDATES, ClaimsService
from services.embeddings import EMBED_MODEL, RERANK_MODEL
from services.vision import get_labeller, to_jpeg

router = APIRouter(prefix="/api/claims", tags=["claims"])


def _explain(title, capability, collection, code, note, **extra) -> dict:
    return {
        "title": title, "capability": capability, "collection": collection,
        "code": code, "note": note, **extra,
    }


def _service() -> ClaimsService:
    client = get_mongo_client()
    if client is None:
        raise HTTPException(status_code=503, detail="No database connection.")
    service = ClaimsService(client[os.getenv("DATABASE_NAME", "fleet")])
    if not service.ready:
        raise HTTPException(
            status_code=503,
            detail="No claims loaded. Run: uv run python scripts/load_claims.py",
        )
    return service


def _search_explain(result: dict, filters: dict) -> list[dict]:
    filter_text = (
        "\n  filter: " + str(filters).replace("'", '"') if filters else ""
    )
    panels = [
        _explain(
            "Searching by meaning, not by filename",
            "Atlas Vector Search",
            CLAIMS,
            "db.damage_claims.aggregate([\n"
            "  { $vectorSearch: {\n"
            f'      index: "{VECTOR_INDEX}",\n'
            '      path: "embedding",\n'
            "      queryVector: <1024 floats>,\n"
            f"      numCandidates: {CANDIDATES},\n"
            f"      limit: {CANDIDATES}{filter_text}\n"
            "  }}\n"
            "])",
            f"{EMBED_MODEL} puts images and text in one space, so a typed "
            "description and a photograph are directly comparable. An image "
            "encoder cannot do that whatever distance metric you put on top, "
            "which is the reason to use a multimodal model rather than a "
            "classifier's output layer.",
            embedMs=result["timings"]["embedMs"],
            vectorSearchMs=result["timings"]["vectorSearchMs"],
            candidates=result["candidates"],
        ),
    ]

    if result.get("reranked"):
        moved = [
            {
                "text": (row.get("text") or "")[:80],
                "from": row["vectorRank"],
                "to": row["rerankRank"],
            }
            for row in result["reranked"]
            if row["vectorRank"] - row["rerankRank"] >= 3
        ]
        panels.append(_explain(
            "Recall finds it, precision orders it",
            "Voyage rerank-2.5",
            CLAIMS,
            f"// {CANDIDATES} candidates from the vector index\n"
            "// reordered by a cross encoder against the query\n\n"
            f"voyage.rerank(query, documents, model=\"{RERANK_MODEL}\")",
            "Vector search is approximate and cheap, so it is asked for a wide "
            "net. The cross encoder reads the query and each candidate together "
            "and decides the order, which is where topically adjacent becomes "
            "actually relevant. Asking the vector index directly for the six "
            "results you intend to show skips the step that fixes its mistakes.",
            rerankMs=result["timings"]["rerankMs"],
            promoted=moved[:4],
        ))

    if filters:
        panels.append(_explain(
            "Filters in the vector index",
            "Pre-filtered vector search",
            CLAIMS,
            "// index definition\n"
            "fields: [\n"
            '  { type: "vector", path: "embedding", numDimensions: 1024 },\n'
            '  { type: "filter", path: "panel" },\n'
            '  { type: "filter", path: "damageType" },\n'
            '  { type: "filter", path: "severity" }\n'
            "]",
            "Comparing a bumper photograph against wheel photographs is noise. "
            "Filtering inside the vector search rather than afterwards keeps the "
            "candidate pool full of things worth ranking. It only works because "
            "the labels come from a fixed vocabulary, which is why the vision "
            "pass is constrained rather than free text.",
        ))

    return panels


@router.get("/stats")
def stats():
    service = _service()
    return {
        "stats": service.stats(),
        "sample": service.sample(12),
        "models": {"embedding": EMBED_MODEL, "rerank": RERANK_MODEL},
    }


@router.get("/{claim_id}/image")
def claim_image(claim_id: str):
    """The stored photograph. Kept in the document alongside its vector."""
    service = _service()
    data = service.image_bytes(claim_id)
    if data is None:
        raise HTTPException(status_code=404, detail=f"No claim {claim_id!r}")
    return Response(content=data, media_type="image/jpeg",
                    headers={"Cache-Control": "public, max-age=86400"})


@router.get("/search")
def search(
    q: str = Query(min_length=2, description="What the damage looks like"),
    panel: str | None = Query(default=None),
    damage_type: str | None = Query(default=None),
    severity: str | None = Query(default=None),
    limit: int = Query(default=6, le=24),
    rerank: bool = Query(default=True),
):
    """Find settled claims matching a description."""
    service = _service()
    result = service.search(
        query=q, panel=panel, damage_type=damage_type,
        severity=severity, limit=limit, rerank=rerank,
    )
    result["precedent"] = service.precedent(result["results"])
    result["explain"] = _search_explain(result, result["filters"])
    return result


@router.post("/assess")
async def assess(
    file: UploadFile = File(...),
    plate: str | None = Form(default=None),
    limit: int = Form(default=6),
):
    """Read a returned vehicle's photo, then price it against settled claims."""
    service = _service()
    labeller = get_labeller()

    from PIL import Image

    raw = await file.read()
    try:
        image = Image.open(io.BytesIO(raw))
        jpeg = to_jpeg(image)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=422, detail=f"Not a readable image: {exc}") from exc

    labels = labeller.label(jpeg) if labeller.available else None

    # Search by the photograph itself, filtered to the panel the model read off
    # it. Image similarity finds the look, the filter keeps it on the right part
    # of the car.
    panel = (labels or {}).get("panel")
    result = service.search(
        query=(labels or {}).get("description") or None,
        image=Image.open(io.BytesIO(jpeg)),
        panel=panel if panel and panel != "unknown" else None,
        limit=limit,
        rerank=bool((labels or {}).get("description")),
    )
    precedent = service.precedent(result["results"])

    explain = [
        _explain(
            "Reading the photograph",
            f"Claude on Bedrock, {labeller.model_id}",
            None,
            "bedrock.converse(\n"
            f'  modelId="{labeller.model_id}",\n'
            '  messages=[{ "role": "user", "content": [\n'
            '      { "image": { "format": "jpeg", "source": { "bytes": ... } } },\n'
            '      { "text": "damage type, panel, severity, angle, description" }\n'
            "  ]}]\n"
            ")",
            "The vocabulary is fixed rather than free text, because a label that "
            "cannot be used as an index filter is only decoration, and a model "
            "left to invent categories produces forty ways of saying scratch. "
            "This pass is how a fleet operator onboards their own claim photos "
            "without hand-typing metadata for several hundred images.",
            labels=labels,
        ),
        *_search_explain(result, result["filters"]),
        _explain(
            "Priced from what similar damage actually settled at",
            "Aggregation over retrieved claims",
            CLAIMS,
            "// the retrieved claims carry their settled cost\n"
            "median(parts + labour) across the matches",
            "An estimate grounded in your own settled claims is defensible in a "
            "way a model's guess is not. When a renter disputes the charge, the "
            "answer is the photographs of the comparable repairs and what each "
            "of them cost.",
            precedent=precedent,
        ),
    ]

    return {
        "labels": labels,
        "assessedCost": precedent,
        "matches": result["results"],
        "vectorOnly": result["vectorOnly"],
        "timings": result["timings"],
        "plate": plate,
        "explain": explain,
    }
