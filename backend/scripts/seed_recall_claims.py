"""Attach a few collision-consistent claims to vehicles in the recalled batch.

Brake failure leading to a front-end collision is the scenario a recall is
worried about. The claims corpus is attached to randomly sampled vehicles, so
without this the overlap with any one batch is down to chance and the
corroboration step has nothing to find.

This moves existing claims rather than inventing them.
"""

from __future__ import annotations

import argparse
import os
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from db.mdb import get_mongo_client  # noqa: E402
from services.agent_tools import COLLISION_PANELS, COLLISION_TYPES  # noqa: E402
from services.graph_service import GraphService, LOTS  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=5)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    client = get_mongo_client()
    db = client[os.getenv("DATABASE_NAME", "fleet")]
    rng = random.Random(args.seed)

    lot = db[LOTS].find_one({"recalled": True})
    if lot is None:
        print("No recalled batch. Run scripts/load_graph.py first.")
        return 1

    affected = GraphService(db).blast_radius(lot["_id"])["vehicles"]
    candidates = list(db["damage_claims"].find(
        {
            "damageType": {"$in": COLLISION_TYPES},
            "panel": {"$in": COLLISION_PANELS},
            "severity": {"$in": ["moderate", "severe"]},
        },
        {"_id": 1},
    ).limit(40))

    if not candidates:
        print("No collision-consistent claims in the corpus.")
        return 1

    chosen = rng.sample(candidates, min(args.count, len(candidates)))
    plates = rng.sample(affected, min(len(chosen), len(affected)))

    for claim, plate in zip(chosen, plates):
        vehicle = db.asset_shadow.find_one({"_id": plate}, {"registry": 1})
        registry = (vehicle or {}).get("registry", {})
        db["damage_claims"].update_one({"_id": claim["_id"]}, {"$set": {
            "vehicle": {
                "plate": plate,
                "make": registry.get("marca"),
                "model": registry.get("modelo"),
                "region": registry.get("regiao"),
                "depot": registry.get("deposito_nome"),
            },
            "region": registry.get("regiao"),
        }})

    print(f"  Attached {len(chosen)} collision claims to vehicles in {lot['_id']}")
    print(f"  Plates: {', '.join(plates)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
