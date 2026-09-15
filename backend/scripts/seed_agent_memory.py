"""Seed the agent's procedural and taxonomic memory.

Run once after loading the fleet:

    uv run python scripts/seed_agent_memory.py

The SOP and the vocabulary live in MongoDB rather than in the source, so an
operations lead can change how the agent works without a deploy. Re-running is
safe; it replaces what is there.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv

load_dotenv()

from db.mdb import get_mongo_client  # noqa: E402
from services.agent_memory import EPISODES, PROCEDURES, TAXONOMY, AgentMemory  # noqa: E402

RECALL_SOP = {
    "_id": "recall_response",
    "name": "Recall response",
    "owner": "Fleet operations",
    "version": 3,
    "goal": (
        "Turn a supplier advisory about a defective production batch into a plan "
        "an operations team can execute today, for a rental and mobility operator "
        "running about 5,000 vehicles across Brazil, North America and Europe."
    ),
    "steps": [
        {
            "title": "Scope it",
            "detail": (
                "Find every vehicle carrying a part from the batch, how many are "
                "out with a customer, and how many cannot be located. This is a "
                "graph traversal and takes no judgement."
            ),
            "tool": "scope_recall",
        },
        {
            "title": "Corroborate it",
            "detail": (
                "Check whether any affected vehicle already has collision-consistent "
                "damage. A precautionary recall and one where failures have started "
                "are different problems and deserve different urgency."
            ),
            "tool": "find_collision_claims",
        },
        {
            "title": "Cohort it",
            "detail": (
                "Group the vehicles by what should actually happen to each group. "
                "The rules give you rental status and data trust. You decide the "
                "trade-off between customer risk and operational disruption."
            ),
            "tool": "cohort_vehicles",
        },
        {
            "title": "Check the urgency policy",
            "detail": (
                "Urgency values are this operator's own policy and carry "
                "contractual response times. Look them up with define_terms "
                "before you assign any, so your plan matches what the operations "
                "team has actually committed to."
            ),
            "tool": "define_terms",
        },
        {
            "title": "Propose actions",
            "detail": (
                "One action per cohort, each with an urgency and a reason. Do not "
                "propose a physical action for a vehicle that cannot be located; "
                "call those out as a gap for the investigation to close."
            ),
            "tool": "propose_actions",
        },
    ],
    "constraints": [
        "Every figure you cite must have come from a tool result. Do not invent numbers.",
        "Be direct and brief. You are writing for a fleet operations manager who will "
        "act on this within the hour, not for a report.",
        "Look up any domain term you are unsure of rather than guessing at it.",
        "You propose. A human approves. Nothing you decide is executed automatically.",
    ],
    "updatedAt": datetime.now(timezone.utc),
}

INVESTIGATION_SOP = {
    "_id": "unlocatable_investigation",
    "name": "Unlocatable vehicle investigation",
    "owner": "Fleet operations",
    "version": 2,
    "goal": (
        "Establish why one vehicle in a safety recall cannot be located, so that "
        "recall logistics are not sent to a position that is wrong."
    ),
    "steps": [
        {
            "title": "Gather the evidence",
            "detail": (
                "Pull the vehicle's recent telemetry, its open findings and the "
                "trust assessment of its position."
            ),
            "tool": "vehicle_evidence",
        },
        {
            "title": "Check for a conflicting position",
            "detail": (
                "Look for another vehicle sitting where this one claims to be. That "
                "is strong evidence the tracker was refitted into a different car."
            ),
            "tool": "tracker_conflict",
        },
        {
            "title": "Conclude",
            "detail": (
                "Weigh the evidence rather than pattern matching on one signal. A "
                "unit degrading gradually with poor satellite quality looks different "
                "from one that goes dark cleanly."
            ),
        },
    ],
    "constraints": [
        "Conclude exactly one of: gps_failure, tracker_moved, tampering, unresolved.",
        "State a confidence between 0 and 1 and justify it in two sentences.",
    ],
    "updatedAt": datetime.now(timezone.utc),
}


def term(key, label, definition, category, aliases=None, seeAlso=None):
    return {
        "_id": key,
        "label": label,
        "definition": definition,
        "category": category,
        "aliases": aliases or [],
        "seeAlso": seeAlso or [],
    }


TAXONOMY_TERMS = [
    # Urgency ladder. Operator policy with contractual response times, which no
    # model can infer: it has to be looked up.
    term("immediate", "Urgency: immediate",
         "Act within 2 hours. Authorised to interrupt a live rental and "
         "dispatch recovery at the operator's cost. Reserved for critical "
         "safety defects where continued driving risks injury.",
         "urgency", ["immediate", "urgent", "now"]),
    term("today", "Urgency: today",
         "Act before end of business the same day. The vehicle may complete its "
         "current journey but must not be re-rented.",
         "urgency", ["today", "same day"]),
    term("this-week", "Urgency: this week",
         "Act within 5 working days. The vehicle stays in service and is "
         "scheduled into the next available workshop slot.",
         "urgency", ["this week", "this-week"]),
    term("next-service", "Urgency: next service",
         "Fold into the vehicle's next routine maintenance visit. No separate "
         "trip and no disruption to availability.",
         "urgency", ["next service", "next-service", "routine"]),

    # Rental and fleet operations
    term("on_rent", "On rent",
         "The vehicle is out with a customer under an open rental agreement. "
         "Any action on it means contacting that customer.",
         "rental_status", ["on rent", "rented", "out with a customer"]),
    term("available", "Available",
         "The vehicle is on a depot lot, rentable, and not currently with a "
         "customer. It can be grounded without disrupting anybody's journey.",
         "rental_status", ["on lot", "on the lot", "idle"]),
    term("in_service", "In service",
         "The vehicle is in a workshop for maintenance or repair. It is already "
         "off the road and already in the hands of a technician.",
         "rental_status", ["in workshop", "in the shop", "under maintenance"]),
    term("agreement", "Rental agreement",
         "The contract binding a vehicle to a customer for a period. Holds the "
         "customer identifier used to reach them.",
         "rental", ["rental agreement", "contract", "booking"]),
    term("depot", "Depot",
         "A physical site where vehicles are kept, cleaned and handed over. "
         "Recall logistics are organised around depot catchments.",
         "operations", ["branch", "lot", "station"]),

    # Data trust
    term("trust_good", "Data trust: good",
         "Both feeds agree, readings are fresh and validation passed cleanly. "
         "The recorded position can be acted on.",
         "data_trust", ["good trust", "trusted"]),
    term("trust_degraded", "Data trust: degraded",
         "Readings are stale or the sources disagree beyond tolerance. The "
         "position is probably right but should not be relied on alone.",
         "data_trust", ["degraded"]),
    term("trust_unreliable", "Data trust: unreliable",
         "The position cannot be believed at all. The vehicle is effectively "
         "unlocatable even though a coordinate exists in the record.",
         "data_trust", ["unreliable", "cannot be located", "unlocatable"]),
    term("trust_score", "Trust score",
         "reliability(sourceType, vssPath) x freshnessDecay(observedAt) x "
         "confidenceFactor(quality). Decides which source is believed for a "
         "given signal on a given vehicle.",
         "data_trust", ["trustscore", "fusion score"]),
    term("fusion", "Data fusion",
         "Reconciling two feeds that describe the same vehicle into one set of "
         "values, keeping a record of which source won each signal and why.",
         "data_trust", ["data fusion", "sensor fusion"]),

    # Rejection reasons
    term("null_island", "Null Island",
         "A position reported at or beside 0 degrees north, 0 degrees east, in "
         "the Gulf of Guinea. It means an uninitialised GPS module, not a "
         "vehicle in the Atlantic.",
         "reject_reason", ["null island", "0,0", "zero zero"]),
    term("sentinel_value", "Sentinel value",
         "A magic number a vendor sends in place of a missing reading, such as "
         "255 for fuel level or -999 for coolant temperature. It is an absence "
         "of data wearing the costume of a measurement.",
         "reject_reason", ["sentinel", "magic number", "placeholder value"]),
    term("no_gps_fix", "No GPS fix",
         "The tracker reported a position while telling us it had no satellite "
         "lock. The coordinate is a guess.",
         "reject_reason", ["no fix", "no satellite lock"]),
    term("impossible_motion", "Impossible motion",
         "The implied speed between two consecutive fixes exceeds what a road "
         "vehicle can do, so at least one of the two positions is wrong.",
         "reject_reason", ["impossible motion", "teleport", "jump"]),

    # Sources
    term("oem_connected", "Manufacturer feed",
         "Factory-fitted telematics, sent by the vehicle manufacturer. "
         "Calibrated and certified, reads sensors directly, but reports less "
         "often and covers only vehicles sold with connectivity.",
         "source", ["oem", "factory feed", "manufacturer feed", "oem_connected"]),
    term("calamp_tracker", "Aftermarket tracker",
         "A retrofitted telematics box installed by a technician after purchase. "
         "Covers the whole fleet and reports frequently, but infers some values "
         "indirectly and fails more often.",
         "source", ["tracker", "aftermarket", "retrofit", "calamp"]),

    # Parts, recall, graph
    term("lot", "Production batch",
         "A batch of parts manufactured together by one supplier. When a defect "
         "is found it is usually confined to a batch, which is why recalls are "
         "scoped this way.",
         "parts", ["batch", "production batch", "production lot"]),
    term("blast_radius", "Blast radius",
         "Everything downstream of a flagged batch: the parts, the vehicles "
         "carrying them, the rentals open on those vehicles, and the customers "
         "who must be called. Found with a single graph traversal.",
         "recall", ["blast radius", "scope", "exposure"]),
    term("criticality", "Criticality",
         "How dangerous a defective part is. Critical means a safety system "
         "such as brakes or steering, where continued use risks injury.",
         "recall", ["severity", "criticality"]),
    term("stop_drive", "Stop-drive",
         "The most disruptive recall action: the vehicle must not be driven "
         "again until repaired, even if that strands a customer. Reserved for "
         "critical safety defects.",
         "recall", ["stop drive", "do not drive", "ground"]),

    # Damage
    term("collision_consistent", "Collision-consistent damage",
         "Damage of a type and location that matches an impact rather than wear: "
         "front or rear bumper, glass, moderate or severe. Used to test whether "
         "a suspected failure has already caused a crash.",
         "damage", ["collision damage", "impact damage"]),
    term("panel", "Panel",
         "The named body area a damage claim refers to, such as front-bumper or "
         "rear-quarter-right.",
         "damage", ["body panel"]),

    # Investigation conclusions
    term("gps_failure", "GPS failure",
         "The tracker cannot get a satellite fix. The vehicle is almost "
         "certainly where the records say; we are simply blind to it.",
         "investigation", ["gps failure", "no fix"]),
    term("tracker_moved", "Tracker moved",
         "The telematics unit has been refitted into a different vehicle. Its "
         "reported position belongs to that other car, so logistics sent there "
         "would reach the wrong vehicle.",
         "investigation", ["tracker moved", "unit swapped", "refitted"]),
    term("tampering", "Tampering",
         "The unit stopped reporting cleanly while other evidence shows the "
         "vehicle still in use, which indicates somebody disconnected it.",
         "investigation", ["tampering", "disconnected", "unplugged"]),

    # Standards
    term("vss", "COVESA VSS",
         "Vehicle Signal Specification. An open standard naming and typing every "
         "signal a vehicle can report, arranged as a tree under Vehicle. Both "
         "feeds are mapped onto it so their readings can be compared.",
         "standard", ["vss", "covesa", "vehicle signal specification"]),
    term("vss_path", "VSS path",
         "The dotted name of a signal in the VSS tree, such as "
         "Powertrain.FuelSystem.RelativeLevel. Vehicle-relative, so the leading "
         "Vehicle is implied.",
         "standard", ["vss path", "signal path"]),
]


def main() -> None:
    # --forget clears episodic memory so the next run is cold again, which is
    # how you show the before and after in a demo.
    if "--forget" in sys.argv:
        client = get_mongo_client()
        db = client[os.getenv("DATABASE_NAME", "fleet")]
        removed = AgentMemory(db).forget_investigations()
        print(f"Forgot {removed} investigation(s). The next run will be cold.")
        return

    client = get_mongo_client()
    if client is None:
        raise SystemExit("No MONGODB_URI set.")
    db = client[os.getenv("DATABASE_NAME", "fleet")]
    memory = AgentMemory(db)

    for sop in (RECALL_SOP, INVESTIGATION_SOP):
        db[PROCEDURES].replace_one({"_id": sop["_id"]}, sop, upsert=True)
    print(f"{PROCEDURES}: {db[PROCEDURES].estimated_document_count()} procedures")

    for row in TAXONOMY_TERMS:
        db[TAXONOMY].replace_one({"_id": row["_id"]}, row, upsert=True)
    print(f"{TAXONOMY}: {db[TAXONOMY].estimated_document_count()} terms")

    memory.ensure_indexes()
    print(f"{EPISODES}: {db[EPISODES].count_documents({})} episodes")
    print("\nSOP as the agent will read it:\n")
    print(memory.procedure_prompt("recall_response")[:600])


if __name__ == "__main__":
    main()
