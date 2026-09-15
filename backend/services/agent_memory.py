"""Agent memory, held in MongoDB.

Three kinds, because they answer different questions and change at different
rates:

  procedural  How this job is done. The recall SOP, as documents rather than a
              string in the source, so changing how the agent works is an edit
              to a collection rather than a deploy.
  taxonomic   What the words mean. "On rent", NULL_ISLAND, blast radius. A
              closed vocabulary of a few hundred terms that a general model
              should not have to infer from context every run.
  episodic    What was concluded before. A vehicle investigated yesterday does
              not need investigating again unless its telemetry moved on, which
              makes the second run of a demo both fast and cheap.

Nothing here is a vector search. The terms are a closed set and lookups are by
key, so retrieval is exact, free and explainable on stage.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from pymongo import ASCENDING
from pymongo.database import Database

logger = logging.getLogger(__name__)

PROCEDURES = "agent_procedures"
TAXONOMY = "agent_taxonomy"
EPISODES = "agent_episodes"


class AgentMemory:
    """Read and write the agent's three memories."""

    def __init__(self, db: Database) -> None:
        self.db = db

    # -- procedural ---------------------------------------------------------

    def procedure(self, name: str = "recall_response") -> dict | None:
        return self.db[PROCEDURES].find_one({"_id": name})

    def procedure_prompt(self, name: str = "recall_response") -> str:
        """The SOP rendered for a system prompt.

        Returning empty when nothing is stored is deliberate: the caller keeps a
        built-in default, so an empty collection degrades to the old behaviour
        rather than an agent with no instructions.
        """
        doc = self.procedure(name)
        if not doc:
            return ""

        lines = [doc.get("goal", "").strip(), ""]
        for index, step in enumerate(doc.get("steps", []), start=1):
            lines.append(f"{index}. {step['title']}")
            lines.append(f"   {step['detail']}")
            if step.get("tool"):
                lines.append(f"   Tool: {step['tool']}")
            lines.append("")

        if doc.get("constraints"):
            lines.append("Constraints:")
            lines += [f"  - {c}" for c in doc["constraints"]]

        return "\n".join(lines).strip()

    # -- taxonomic ----------------------------------------------------------

    def define(self, terms: list[str]) -> list[dict]:
        """Look up domain terms by key or alias, exactly."""
        wanted = [t.strip().lower() for t in terms if t and t.strip()]
        if not wanted:
            return []
        rows = self.db[TAXONOMY].find(
            {"$or": [{"_id": {"$in": wanted}}, {"aliases": {"$in": wanted}}]},
            {"embedding": 0},
        )
        return list(rows)

    def vocabulary(self, limit: int = 40) -> list[str]:
        """Just the term names, so the agent knows what it can ask about."""
        return [
            row["_id"]
            for row in self.db[TAXONOMY].find({}, {"_id": 1}).limit(limit)
        ]

    # -- episodic -----------------------------------------------------------

    def recall_investigation(self, plate: str, last_reading_at) -> dict | None:
        """A previous verdict, if the vehicle has not reported since.

        Keying on the last reading rather than a clock means the memory expires
        exactly when the evidence changes, which is the only thing that could
        change the conclusion.
        """
        return self.db[EPISODES].find_one(
            {"_id": f"investigation:{plate}", "evidenceAt": last_reading_at}
        )

    def remember_investigation(self, plate: str, last_reading_at, verdict: dict) -> None:
        self.db[EPISODES].replace_one(
            {"_id": f"investigation:{plate}"},
            {
                "_id": f"investigation:{plate}",
                "kind": "investigation",
                "plate": plate,
                "evidenceAt": last_reading_at,
                "conclusion": verdict.get("conclusion"),
                "confidence": verdict.get("confidence"),
                "reasoning": verdict.get("reasoning"),
                "recommendedAction": verdict.get("recommendedAction"),
                "rememberedAt": datetime.now(timezone.utc),
            },
            upsert=True,
        )

    def forget_investigations(self) -> int:
        """Clear episodic memory, so a demo can show the cold run again."""
        return self.db[EPISODES].delete_many({"kind": "investigation"}).deleted_count

    # -- setup --------------------------------------------------------------

    def ensure_indexes(self) -> None:
        self.db[TAXONOMY].create_index([("aliases", ASCENDING)])
        self.db[EPISODES].create_index([("kind", ASCENDING), ("plate", ASCENDING)])

    def stats(self) -> dict:
        return {
            "procedures": self.db[PROCEDURES].estimated_document_count(),
            "taxonomy": self.db[TAXONOMY].estimated_document_count(),
            "episodes": self.db[EPISODES].count_documents({"kind": "investigation"}),
        }
