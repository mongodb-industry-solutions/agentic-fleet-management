"""Generate the parts catalogue and build the ontology and graph.

Runs after scripts/load.py, because the ABox reads the fleet record that load
puts in place.

    uv run python scripts/load_graph.py
"""

from __future__ import annotations

import argparse
import logging
import os
import random
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from db.mdb import get_mongo_client  # noqa: E402
from db.schema import ASSET_SHADOW  # noqa: E402
from services.graph_service import (  # noqa: E402
    GraphService,
    build_abox,
    ensure_graph_collections,
    load_tbox,
)
from sim.parts import build_catalogue, fit_parts, pick_recall_lot  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(message)s")
logging.getLogger("pymongo").setLevel(logging.WARNING)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    client = get_mongo_client()
    if client is None:
        print("No MONGODB_URI set. Add it to backend/.env first.")
        return 1

    db = client[os.getenv("DATABASE_NAME", "fleet")]
    plates = db[ASSET_SHADOW].distinct("_id")
    if not plates:
        print("No vehicles loaded. Run scripts/load.py first.")
        return 1

    rng = random.Random(args.seed)
    now = datetime.now(timezone.utc)

    actions = ensure_graph_collections(db)
    tbox = load_tbox(db)

    suppliers, lots = build_catalogue(rng, now)
    fitments = fit_parts(plates, lots, rng, now)
    recall = pick_recall_lot(fitments, lots, rng)
    abox = build_abox(db, suppliers, lots, fitments, recall)

    print(f"\n  Vehicles in the fleet   {len(plates):,}")
    for name, action in actions.items():
        print(f"  {name:<14} {action}")

    print("\n  TBox")
    print(f"    classes             {tbox['classes']}")
    print(f"    relationships       {tbox['relationships']}")
    print(f"    business questions  {tbox['questions']}")

    print("\n  ABox")
    print(f"    suppliers           {abox['suppliers']:,}")
    print(f"    production batches  {abox['lots']:,}")
    print(f"    fitted parts        {abox['parts']:,}")
    print(f"    edges               {abox['edges']:,}")
    for edge_type, count in abox["byType"].items():
        print(f"      {edge_type:<16} {count:>9,}")
    print(f"    took                {abox['seconds']:,.1f}s")

    graph = GraphService(db)
    result = graph.blast_radius(recall.code)
    print(f"\n  Recall drill: {recall.code}")
    print(f"    {recall.part_name} from {recall.supplier}, {recall.criticality}")
    print(f"    traversed           {result['edgesTraversed']:,} edges in {result['queryMs']} ms")
    print(f"    vehicles affected   {len(result['vehicles']):,}")
    print(f"    open agreements     {len(result['agreements']):,}")
    print(f"    customers to call   {len(result['customers']):,}")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
