"""Create or update the Atlas Search indexes, without touching the data.

`mongodump` does not carry Atlas Search or Vector Search index definitions: they
live in mongot's metadata rather than in the collection catalogue. So a database
moved with dump and restore arrives with every document and every ordinary index
intact, and `$vectorSearch` fails until the search index is rebuilt.

The embeddings themselves are stored in the claim documents, so nothing needs to
be re-embedded. This only rebuilds the index over vectors that are already there.

    uv run python scripts/ensure_search_indexes.py
    uv run python scripts/ensure_search_indexes.py --wait
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv  # noqa: E402

from db.mdb import get_mongo_client  # noqa: E402
from services.claims import CLAIMS, VECTOR_INDEX, vector_index_definition  # noqa: E402

load_dotenv()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--wait",
        action="store_true",
        help="Block until the index reports itself queryable",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=600,
        help="Seconds to wait for the build (default 600)",
    )
    args = parser.parse_args()

    client = get_mongo_client()
    if client is None:
        print("  No MONGODB_URI set. Nothing to do.")
        return 1

    db = client[os.getenv("DATABASE_NAME", "fleet")]
    claims = db[CLAIMS]

    documents = claims.estimated_document_count()
    if not documents:
        print(
            f"  {CLAIMS} is empty. Restore the data first, or run "
            "scripts/load_claims.py."
        )
        return 1

    try:
        existing = {index["name"] for index in claims.list_search_indexes()}
    except Exception as exc:  # noqa: BLE001
        print(f"  Could not list search indexes: {exc}")
        print("  Atlas Search is not available on this deployment.")
        return 1

    if VECTOR_INDEX in existing:
        claims.update_search_index(VECTOR_INDEX, vector_index_definition()["definition"])
        action = "updated"
    else:
        claims.create_search_index(model=vector_index_definition())
        action = "created"

    print(f"  Vector index {VECTOR_INDEX} {action} over {documents:,} claims")

    if not args.wait:
        print("  Building in the background. Re-run with --wait to block on it.")
        return 0

    deadline = time.time() + args.timeout
    while time.time() < deadline:
        index = next(
            (i for i in claims.list_search_indexes() if i["name"] == VECTOR_INDEX),
            None,
        )
        if index and index.get("queryable"):
            print(f"  {VECTOR_INDEX} is queryable, status {index.get('status')}")
            return 0
        status = index.get("status") if index else "missing"
        print(f"  waiting, status {status}")
        time.sleep(5)

    print(f"  Still not queryable after {args.timeout}s. Check the Atlas UI.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
