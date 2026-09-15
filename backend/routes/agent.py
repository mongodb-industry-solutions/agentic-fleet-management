"""Agent routes.

The run streams as it happens, because the trace is the product. An operator
watching the agent scope, corroborate and cohort is being shown the reasoning,
not a spinner followed by an answer.
"""

from __future__ import annotations

import json
import os

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

from db.mdb import get_mongo_client
from services.agent_memory import AgentMemory
from services.agents import AGENT_RUNS, RecallAgent, bedrock_available
from services.graph_service import LOTS

router = APIRouter(prefix="/api/agent", tags=["agent"])


def _jsonable(value):
    """Runs hold whole tool results, and a stray ObjectId anywhere in that tree
    fails the whole response. Coerce what JSON cannot carry on the way out."""
    from bson import ObjectId

    if isinstance(value, ObjectId):
        return str(value)
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    return value


def _db():
    client = get_mongo_client()
    if client is None:
        raise HTTPException(status_code=503, detail="No database connection.")
    return client[os.getenv("DATABASE_NAME", "fleet")]


@router.get("/status")
def status():
    """Whether the agent can run, and what it would run on."""
    db = _db()
    recalled = db[LOTS].find_one({"recalled": True})
    memory = AgentMemory(db)
    return _jsonable({
        "available": bedrock_available(),
        "recalledLot": recalled,
        "framework": "langgraph-react",
        "memory": memory.stats(),
        "recentRuns": list(
            db[AGENT_RUNS]
            .find({}, {"_id": 0, "narrative": 0, "scope.vehicles": 0, "cohorts": 0})
            .sort("startedAt", -1)
            .limit(5)
        ),
        "explain": [{
            "title": "What the agent is and is not doing",
            "capability": "Supervisor plus fan-out",
            "collection": AGENT_RUNS,
            "code": (
                "// memory, in MongoDB, read at the start of every run\n"
                "  agent_procedures   the SOP the supervisor follows\n"
                "  agent_taxonomy     what the operator's terms mean\n"
                "  agent_episodes     conclusions already reached\n"
                "  checkpoints        every reasoning step, as it happens\n\n"
                "// the rules decided these, before any model ran\n"
                "  which vehicles carry the part      $graphLookup\n"
                "  which cannot be located            health.trust\n"
                "  status, region, depot              asset_shadow\n\n"
                "// the agent decides these\n"
                "  what each cohort needs, and when\n"
                "  whether collision claims change the urgency\n"
                "  why each unlocatable vehicle is unlocatable\n"
                "  the wording for the operations team"
            ),
            "note": (
                "The SOP is a document, not a prompt in the source. Editing "
                "agent_procedures changes how the agent works without a deploy. "
                "The credibility of an agent rests on not claiming work the rules "
                "did. Scoping a recall is a traversal and takes no judgement. "
                "Deciding that a critical part on a car two hundred kilometres "
                "from a depot is worth the disruption of a stop-drive does."
            ),
        }],
    })


@router.get("/recall/{lot_code}/stream")
def stream_recall(
    lot_code: str,
    investigations: int = Query(default=8, le=20),
    memory: bool = Query(
        default=True,
        description="Give the agent its SOP, vocabulary and past conclusions.",
    ),
):
    """Run the recall response, streaming the trace."""
    if not bedrock_available():
        raise HTTPException(
            status_code=503,
            detail="No AWS credentials for Bedrock. Add AWS_PROFILE to backend/.env.",
        )

    db = _db()
    if db[LOTS].find_one({"_id": lot_code}) is None:
        raise HTTPException(status_code=404, detail=f"No batch {lot_code!r}")

    agent = RecallAgent(db)

    def events():
        try:
            for event in agent.run(
                lot_code,
                max_investigations=investigations,
                use_memory=memory,
            ):
                yield event.to_sse()
        except Exception as exc:  # noqa: BLE001
            yield f"data: {json.dumps({'type': 'error', 'message': str(exc)})}\n\n"

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/runs/{run_id}")
def get_run(run_id: str):
    db = _db()
    run = db[AGENT_RUNS].find_one({"runId": run_id})
    if run is None:
        raise HTTPException(status_code=404, detail=f"No run {run_id!r}")
    run.pop("_id", None)
    return _jsonable(run)


@router.post("/runs/{run_id}/approve")
def approve(run_id: str):
    """Record a human's approval. The demo still writes nothing to the fleet."""
    db = _db()
    result = db[AGENT_RUNS].update_one(
        {"runId": run_id},
        {"$set": {"approved": True, "approvedBy": "operations"}},
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail=f"No run {run_id!r}")
    return {
        "runId": run_id,
        "approved": True,
        "note": (
            "Approval is recorded against the run. Nothing is written to the "
            "fleet: every action stays a proposal."
        ),
    }
