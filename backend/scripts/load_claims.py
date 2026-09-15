"""Build the settled claims corpus: download, compress, label, embed, index.

    uv run python scripts/load_claims.py                    # the whole corpus
    uv run python scripts/load_claims.py --limit 20         # a quick pass
    uv run python scripts/load_claims.py --folder ../photos # a local folder

Each image is downscaled and compressed, read by Claude on Bedrock for damage
type, panel, severity and a description, then embedded by voyage-multimodal-3
into a space shared with text. Repair costs are generated, because the source
images carry none.
"""

from __future__ import annotations

import argparse
import logging
import os
import random
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from db.mdb import get_mongo_client  # noqa: E402
from db.schema import ASSET_SHADOW  # noqa: E402
from services.claims import CLAIMS, VECTOR_INDEX, build_claim, vector_index_definition  # noqa: E402
from services.embeddings import get_embedder  # noqa: E402
from services.vision import get_labeller, to_jpeg  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(message)s")
for noisy in ("pymongo", "urllib3", "datasets", "httpx", "botocore", "boto3"):
    logging.getLogger(noisy).setLevel(logging.WARNING)

HF_DATASET = "chittaranjankhatua/car_damage_pub"


def load_images(dataset: str | None, folder: str | None, limit: int | None):
    """Yield (identifier, PIL image) from a Hugging Face dataset or a folder."""
    if folder:
        from PIL import Image

        paths = sorted(
            p for p in Path(folder).iterdir()
            if p.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp")
        )
        for index, path in enumerate(paths[:limit] if limit else paths):
            yield f"local-{index:04d}", Image.open(path)
        return

    from datasets import features, load_dataset

    schema = features.Features({
        "image": features.Image(decode=True),
        "label": features.Value("string"),
    })
    data = load_dataset(dataset, split="train", features=schema)
    total = min(limit, len(data)) if limit else len(data)
    for index in range(total):
        yield f"hf-{index:04d}", data[index]["image"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", default=HF_DATASET)
    parser.add_argument("--folder", default=None, help="Use a local folder instead")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--drop", action="store_true")
    parser.add_argument("--skip-labelling", action="store_true",
                        help="Embed without reading the images, for a cheap dry run")
    parser.add_argument("--workers", type=int, default=6,
                        help="Concurrent vision calls. Lower this if Bedrock throttles.")
    args = parser.parse_args()

    client = get_mongo_client()
    if client is None:
        print("No MONGODB_URI set. Add it to backend/.env first.")
        return 1

    db = client[os.getenv("DATABASE_NAME", "fleet")]
    embedder = get_embedder()
    labeller = get_labeller()

    if not embedder.available:
        print("No VOYAGE_API_KEY set. Add it to backend/.env first.")
        return 1
    if not labeller.available and not args.skip_labelling:
        print("No AWS credentials for Bedrock. Add AWS_PROFILE to backend/.env,")
        print("or pass --skip-labelling to embed without reading the images.")
        return 1

    if args.drop:
        db[CLAIMS].drop()

    rng = random.Random(args.seed)
    # Claims are attached to real vehicles so a match can link back to a car.
    fleet = list(db[ASSET_SHADOW].aggregate([
        {"$sample": {"size": 400}},
        {"$project": {
            "make": "$registry.marca", "model": "$registry.modelo",
            "region": "$registry.regiao", "depot": "$registry.deposito_nome",
        }},
    ]))

    source = args.folder or args.dataset
    print(f"\n  Source     {source}")
    print(f"  Embedder   voyage-multimodal-3")
    print(f"  Labeller   {labeller.model_id if not args.skip_labelling else 'skipped'}\n")

    started = time.perf_counter()

    loaded = [
        (claim_id, image, to_jpeg(image))
        for claim_id, image in load_images(args.dataset, args.folder, args.limit)
    ]
    raw_bytes = sum(len(jpeg) for _, _, jpeg in loaded)
    print(f"  Read {len(loaded)} images, {raw_bytes / 1024 / 1024:.1f} MB compressed")

    SKIPPED = {
        "damageType": "scratch", "panel": "unknown", "severity": "minor",
        "angle": "unknown", "description": "", "bodyColour": "",
        "confidence": 0.0, "labelledBy": "skipped",
    }

    if args.skip_labelling:
        all_labels = [dict(SKIPPED) for _ in loaded]
    else:
        # Each call is a few seconds of round trip, so they overlap. Bedrock
        # throttles rather than queues, so the pool stays small.
        print(f"  Labelling with {args.workers} concurrent calls...")
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            all_labels = list(pool.map(lambda row: labeller.label(row[2]), loaded))

    documents: list[dict] = []
    images: list = []
    for (claim_id, image, jpeg), labels in zip(loaded, all_labels):
        vehicle = rng.choice(fleet) if fleet else None
        if vehicle:
            vehicle = {
                "plate": vehicle["_id"], "make": vehicle.get("make"),
                "model": vehicle.get("model"), "region": vehicle.get("region"),
                "depot": vehicle.get("depot"),
            }
        documents.append(build_claim(claim_id, jpeg, labels, vehicle, rng, source))
        images.append(image)

    # Embed the image and its description together: the photo carries what it
    # looks like, the text carries what it means.
    print("  Embedding...")
    image_vectors = embedder.embed_images(images)
    for document, vector in zip(documents, image_vectors):
        document["embedding"] = vector

    if db[CLAIMS].estimated_document_count() and not args.drop:
        db[CLAIMS].delete_many({"_id": {"$in": [d["_id"] for d in documents]}})
    db[CLAIMS].insert_many(documents, ordered=False)

    db[CLAIMS].create_index([("panel", 1), ("severity", 1)])
    db[CLAIMS].create_index([("damageType", 1)])
    db[CLAIMS].create_index([("vehicle.plate", 1)])

    existing = [i["name"] for i in db[CLAIMS].list_search_indexes()]
    if VECTOR_INDEX in existing:
        db[CLAIMS].update_search_index(VECTOR_INDEX, vector_index_definition()["definition"])
        index_action = "updated"
    else:
        db[CLAIMS].create_search_index(model=vector_index_definition())
        index_action = "created"

    elapsed = time.perf_counter() - started

    from collections import Counter

    by_type = Counter(d["damageType"] for d in documents)
    by_severity = Counter(d["severity"] for d in documents)
    by_panel = Counter(d["panel"] for d in documents)
    costs = [d["cost"]["total"] for d in documents]

    print(f"\n  Stored {len(documents)} claims in {CLAIMS}")
    print(f"  Vector index {VECTOR_INDEX} {index_action}, building in the background")
    print(f"\n  Damage types    {dict(by_type.most_common(6))}")
    print(f"  Severity        {dict(by_severity.most_common())}")
    print(f"  Top panels      {dict(by_panel.most_common(6))}")
    print(f"  Settled cost    ${min(costs):,.0f} to ${max(costs):,.0f}, "
          f"median ${sorted(costs)[len(costs) // 2]:,.0f}")
    print(f"\n  Vision          {labeller.usage.to_dict()}")
    print(f"  Embeddings      {embedder.usage.to_dict()}")
    print(f"  Total           {elapsed:,.1f}s\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
