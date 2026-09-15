"""Searching settled claims, and pricing damage from precedent.

Three passes, and the demo is in the difference between them.

Vector search decides what is in the running. It is recall: cheap, approximate,
and it will happily return things that merely look similar.

Reranking decides the order. It is precision, and it is where topically adjacent
becomes actually relevant. A query about a cracked lens in cold weather should
not be answered with a general door-latch bulletin just because both mention
latches.

Precedent pricing is the point of the exercise. Retrieving three similar settled
claims and quoting their actual costs turns an assessment into something a
customer can be shown.
"""

from __future__ import annotations

import logging
import statistics
import time

from pymongo.database import Database

from services.claims import CLAIMS, VECTOR_INDEX
from services.embeddings import get_embedder

logger = logging.getLogger(__name__)

# Retrieve wide, then let the cross encoder cut it down. Asking the vector index
# for exactly what you want to show skips the step that fixes its mistakes.
CANDIDATES = 60
DEFAULT_LIMIT = 6

PROJECTION = {
    "labels": 1, "damageType": 1, "panel": 1, "severity": 1, "angle": 1,
    "text": 1, "vehicle": 1, "region": 1, "cost": 1, "outcome": 1,
    "settledOn": 1, "source": 1, "costIsSynthetic": 1,
    "score": {"$meta": "vectorSearchScore"},
}


class ClaimsService:
    def __init__(self, db: Database) -> None:
        self.db = db
        self.embedder = get_embedder()

    @property
    def ready(self) -> bool:
        return self.db[CLAIMS].estimated_document_count() > 0

    def stats(self) -> dict:
        """Corpus headline numbers in one round trip.

        Four separate groups over the same 128 documents cost four trips to a
        remote cluster, which is most of a second spent waiting rather than
        counting. One facet does the same work in one.
        """
        def bucket(field: str) -> list[dict]:
            return [
                {"$group": {"_id": f"${field}", "count": {"$sum": 1}}},
                {"$sort": {"count": -1}},
            ]

        pipeline = [
            {"$facet": {
                "summary": [
                    {"$group": {
                        "_id": None,
                        "claims": {"$sum": 1},
                        "avgCost": {"$avg": "$cost.total"},
                        "minCost": {"$min": "$cost.total"},
                        "maxCost": {"$max": "$cost.total"},
                        "bytes": {"$sum": "$imageBytes"},
                    }},
                ],
                "byDamageType": bucket("damageType"),
                "bySeverity": bucket("severity"),
                "byPanel": bucket("panel"),
            }},
        ]
        facets = (list(self.db[CLAIMS].aggregate(pipeline)) or [{}])[0]
        summary = (facets.get("summary") or [{}])[0]

        def tally(name):
            return {r["_id"]: r["count"] for r in facets.get(name, [])}

        return {
            "claims": summary.get("claims", 0),
            "avgCost": round(summary.get("avgCost") or 0, 2),
            "costRange": [
                round(summary.get("minCost") or 0, 2),
                round(summary.get("maxCost") or 0, 2),
            ],
            "imageMegabytes": round((summary.get("bytes") or 0) / 1024 / 1024, 1),
            "byDamageType": tally("byDamageType"),
            "bySeverity": tally("bySeverity"),
            "byPanel": tally("byPanel"),
        }

    def _vector_search(self, vector: list[float], filters: dict, limit: int) -> list[dict]:
        stage = {
            "index": VECTOR_INDEX,
            "path": "embedding",
            "queryVector": vector,
            "numCandidates": CANDIDATES,
            "limit": limit,
        }
        if filters:
            stage["filter"] = filters
        return list(self.db[CLAIMS].aggregate([
            {"$vectorSearch": stage},
            {"$project": PROJECTION},
        ]))

    def search(
        self,
        query: str | None = None,
        image=None,
        panel: str | None = None,
        damage_type: str | None = None,
        severity: str | None = None,
        limit: int = DEFAULT_LIMIT,
        rerank: bool = True,
    ) -> dict:
        """Find settled claims like this one, by text or by photograph."""
        if query is None and image is None:
            raise ValueError("Give either a text query or an image")

        filters: dict = {}
        if panel:
            filters["panel"] = {"$eq": panel}
        if damage_type:
            filters["damageType"] = {"$eq": damage_type}
        if severity:
            filters["severity"] = {"$eq": severity}

        began = time.perf_counter()
        vector = self.embedder.embed_query(image if image is not None else query)
        embed_ms = round((time.perf_counter() - began) * 1000, 1)

        began = time.perf_counter()
        candidates = self._vector_search(vector, filters, CANDIDATES)
        search_ms = round((time.perf_counter() - began) * 1000, 1)

        vector_order = [
            {**row, "vectorRank": index + 1} for index, row in enumerate(candidates)
        ]

        reranked = None
        rerank_ms = None
        if rerank and query and candidates:
            began = time.perf_counter()
            ordered = self.embedder.rerank(
                query, [row.get("text") or "" for row in candidates], top_k=limit
            )
            rerank_ms = round((time.perf_counter() - began) * 1000, 1)
            reranked = [
                {
                    **vector_order[item["index"]],
                    "rerankScore": round(item["score"], 4),
                    "rerankRank": position + 1,
                }
                for position, item in enumerate(ordered)
            ]

        return {
            "query": query,
            "byImage": image is not None,
            "filters": {k: v["$eq"] for k, v in filters.items()},
            "timings": {
                "embedMs": embed_ms,
                "vectorSearchMs": search_ms,
                "rerankMs": rerank_ms,
            },
            "candidates": len(candidates),
            # Both orderings are returned so the console can show them side by
            # side. The change between them is the argument for reranking.
            "vectorOnly": vector_order[:limit],
            "reranked": reranked,
            "results": reranked or vector_order[:limit],
        }

    def precedent(self, results: list[dict]) -> dict:
        """Price from what similar claims actually settled at."""
        costs = [r["cost"]["total"] for r in results if r.get("cost")]
        if not costs:
            return {"basis": 0}

        parts = [r["cost"]["parts"] for r in results if r.get("cost")]
        labour = [r["cost"]["labour"] for r in results if r.get("cost")]

        return {
            "basis": len(costs),
            "median": round(statistics.median(costs), 2),
            "mean": round(statistics.fmean(costs), 2),
            "low": round(min(costs), 2),
            "high": round(max(costs), 2),
            "medianParts": round(statistics.median(parts), 2),
            "medianLabour": round(statistics.median(labour), 2),
            "outcomes": {
                outcome: sum(1 for r in results if r.get("outcome") == outcome)
                for outcome in {r.get("outcome") for r in results if r.get("outcome")}
            },
            "isSynthetic": True,
        }

    def claim(self, claim_id: str) -> dict | None:
        return self.db[CLAIMS].find_one({"_id": claim_id}, {"embedding": 0, "image": 0})

    def image_bytes(self, claim_id: str) -> bytes | None:
        doc = self.db[CLAIMS].find_one({"_id": claim_id}, {"image": 1})
        return doc.get("image") if doc else None

    def sample(self, limit: int = 12) -> list[dict]:
        """A few claims to seed the UI with something to search for."""
        return list(self.db[CLAIMS].aggregate([
            {"$sample": {"size": limit}},
            {"$project": {k: 1 for k in (
                "damageType", "panel", "severity", "angle", "text",
                "vehicle", "cost", "outcome", "labels",
            )}},
        ]))
