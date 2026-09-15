"""Embeddings and reranking on Voyage AI.

voyage-multimodal-3 puts images and text in one space, which is the whole reason
to use a multimodal model rather than an image encoder. A technician typing
"cracked rear tail light lens" and getting matching claim photos back needs the
query and the corpus to be comparable, and an ImageNet classifier's logits cannot
do that no matter what distance metric you put on top.

rerank-2.5 is a cross encoder. Vector search is recall, reranking is precision:
the first pass finds fifty candidates cheaply and the reranker orders the ones
that actually answer the question.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass

logger = logging.getLogger(__name__)

EMBED_MODEL = os.getenv("VOYAGE_EMBEDDING_MODEL", "voyage-multimodal-3")
RERANK_MODEL = os.getenv("VOYAGE_RERANK_MODEL", "rerank-2.5")

DIMENSIONS = 1024

# Voyage caps a multimodal request by payload size, so images go in small
# batches. Text is cheap and goes in larger ones.
IMAGE_BATCH = 8
TEXT_BATCH = 64


@dataclass
class EmbeddingUsage:
    requests: int = 0
    items: int = 0
    seconds: float = 0.0

    def record(self, items: int, seconds: float) -> None:
        self.requests += 1
        self.items += items
        self.seconds += seconds

    def to_dict(self) -> dict:
        return {
            "requests": self.requests,
            "items": self.items,
            "seconds": round(self.seconds, 2),
            "model": EMBED_MODEL,
            "dimensions": DIMENSIONS,
        }


class Embedder:
    """Wraps the Voyage client. Raises clearly when the key is missing."""

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key or os.getenv("VOYAGE_API_KEY", "")
        self._client = None
        self.usage = EmbeddingUsage()

    @property
    def available(self) -> bool:
        return bool(self.api_key) and not self.api_key.startswith("api-key")

    @property
    def client(self):
        if self._client is None:
            if not self.available:
                raise RuntimeError(
                    "VOYAGE_API_KEY is not set. Add it to backend/.env."
                )
            import voyageai

            self._client = voyageai.Client(api_key=self.api_key)
        return self._client

    def embed_images(self, images: list, input_type: str = "document") -> list[list[float]]:
        """Embed PIL images. Returns one vector per image, in order."""
        vectors: list[list[float]] = []
        for start in range(0, len(images), IMAGE_BATCH):
            batch = images[start:start + IMAGE_BATCH]
            began = time.perf_counter()
            result = self.client.multimodal_embed(
                inputs=[[image] for image in batch],
                model=EMBED_MODEL,
                input_type=input_type,
            )
            self.usage.record(len(batch), time.perf_counter() - began)
            vectors.extend(result.embeddings)
        return vectors

    def embed_text(self, texts: list[str], input_type: str = "document") -> list[list[float]]:
        """Embed text into the same space the images live in."""
        vectors: list[list[float]] = []
        for start in range(0, len(texts), TEXT_BATCH):
            batch = texts[start:start + TEXT_BATCH]
            began = time.perf_counter()
            result = self.client.multimodal_embed(
                inputs=[[text] for text in batch],
                model=EMBED_MODEL,
                input_type=input_type,
            )
            self.usage.record(len(batch), time.perf_counter() - began)
            vectors.extend(result.embeddings)
        return vectors

    def embed_query(self, query) -> list[float]:
        """One query, text or image, embedded as a query rather than a document."""
        if isinstance(query, str):
            return self.embed_text([query], input_type="query")[0]
        return self.embed_images([query], input_type="query")[0]

    def rerank(self, query: str, documents: list[str], top_k: int | None = None) -> list[dict]:
        """Reorder candidates with a cross encoder.

        Vector search decides what is in the running. This decides the order, and
        it is where the difference between topically adjacent and actually
        relevant gets settled.
        """
        if not documents:
            return []
        began = time.perf_counter()
        result = self.client.rerank(
            query=query,
            documents=documents,
            model=RERANK_MODEL,
            top_k=top_k or len(documents),
        )
        logger.debug("reranked %d docs in %.2fs", len(documents), time.perf_counter() - began)
        return [
            {"index": r.index, "score": r.relevance_score, "document": r.document}
            for r in result.results
        ]


_embedder: Embedder | None = None


def get_embedder() -> Embedder:
    global _embedder
    if _embedder is None:
        _embedder = Embedder()
    return _embedder
