"""The recall response agent: ReAct on LangGraph, memory in MongoDB.

A supervisor reasons and acts in a loop. A smaller investigator runs once per
vehicle that cannot be located, concurrently, because those are the only cases
where per-vehicle reasoning is worth paying for. Everything else the rules
already decided, and the console says so.

Memory is three collections rather than a prompt:

  agent_procedures  The SOP the supervisor follows. Editing it changes how the
                    agent works with no deploy.
  agent_taxonomy    What the domain words mean, looked up on demand rather than
                    inferred from context every run.
  agent_episodes    Conclusions already reached. A vehicle whose telemetry has
                    not moved does not get investigated twice.

LangGraph's checkpointer writes every reasoning step to MongoDB as it happens,
so a run is inspectable and resumable rather than a black box that either
finishes or does not.
"""

from __future__ import annotations

import json
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterator

from langchain_core.messages import AIMessageChunk, HumanMessage
from langchain_core.tools import StructuredTool
from langgraph.checkpoint.mongodb import MongoDBSaver
from langgraph.prebuilt import create_react_agent
from pymongo.database import Database

from services.agent_memory import AgentMemory
from services.agent_tools import RecallTools, ToolResult, dispatch

logger = logging.getLogger(__name__)

SUPERVISOR_MODEL = os.getenv(
    "BEDROCK_MODEL_ID", "us.anthropic.claude-sonnet-4-5-20250929-v1:0"
)
INVESTIGATOR_MODEL = os.getenv(
    "BEDROCK_FANOUT_MODEL_ID", "us.anthropic.claude-haiku-4-5-20251001-v1:0"
)

AGENT_RUNS = "agent_runs"
CHECKPOINT_DB = os.getenv("AGENT_CHECKPOINT_DB", "fleet_agent_state")

MAX_TURNS = 12
INVESTIGATION_WORKERS = 6

# US dollars per million tokens, on-demand Bedrock. Override if your rates
# differ; the console reports whatever is set here.
PRICES = {
    SUPERVISOR_MODEL: (
        float(os.getenv("SUPERVISOR_INPUT_PER_MTOK", "3.00")),
        float(os.getenv("SUPERVISOR_OUTPUT_PER_MTOK", "15.00")),
    ),
    INVESTIGATOR_MODEL: (
        float(os.getenv("FANOUT_INPUT_PER_MTOK", "1.00")),
        float(os.getenv("FANOUT_OUTPUT_PER_MTOK", "5.00")),
    ),
}

# Used only when agent_procedures is empty, so a fresh database still runs.
FALLBACK_SOP = """Scope the recall, corroborate it against damage claims, cohort
the affected vehicles, then propose one action per cohort with an urgency and a
reason. Do not invent numbers."""

INVESTIGATOR_PROMPT = """You are investigating why one vehicle in a safety recall
cannot be located.

Gather the evidence, then conclude which of these is true:

  gps_failure   The tracker cannot get a fix. The vehicle is almost certainly
                fine and where the records say; we are simply blind to it.
  tracker_moved The unit has been refitted into a different vehicle. Its reported
                position belongs to that other car, so recall logistics sent to
                that position would go to the wrong place.
  tampering     The unit stopped reporting cleanly while other evidence shows the
                vehicle still in use. That is somebody disconnecting it.
  unresolved    The evidence does not support any of the above.

Weigh the evidence rather than pattern matching on one signal. A unit degrading
gradually with poor satellite quality looks different from one that goes dark
cleanly. Another vehicle sitting exactly where this one claims to be is strong
evidence the unit moved.

Reply with JSON only:
{"conclusion": "...", "confidence": 0.0-1.0, "reasoning": "two sentences",
 "recommendedAction": "one sentence"}"""


@dataclass
class AgentEvent:
    """One thing that happened, streamed to the console as it happens."""

    type: str
    payload: dict = field(default_factory=dict)

    def to_sse(self) -> str:
        return f"data: {json.dumps({'type': self.type, **self.payload}, default=str)}\n\n"


class Spend:
    """Tokens and dollars for one run, counted per model."""

    def __init__(self) -> None:
        self.by_model: dict[str, dict] = {}

    def add(self, model_id: str, usage: dict) -> None:
        if not usage:
            return
        row = self.by_model.setdefault(
            model_id, {"calls": 0, "inputTokens": 0, "outputTokens": 0}
        )
        row["calls"] += 1
        row["inputTokens"] += usage.get("input_tokens", usage.get("inputTokens", 0))
        row["outputTokens"] += usage.get("output_tokens", usage.get("outputTokens", 0))

    def summary(self) -> dict:
        models = []
        total = 0.0
        for model_id, row in self.by_model.items():
            rate_in, rate_out = PRICES.get(model_id, (0.0, 0.0))
            cost = (
                row["inputTokens"] * rate_in + row["outputTokens"] * rate_out
            ) / 1_000_000
            total += cost
            models.append({**row, "model": model_id, "usd": round(cost, 6)})
        return {
            "models": models,
            "inputTokens": sum(m["inputTokens"] for m in models),
            "outputTokens": sum(m["outputTokens"] for m in models),
            "usd": round(total, 4),
        }


def bedrock_available() -> bool:
    return bool(os.getenv("AWS_PROFILE") or os.getenv("AWS_ACCESS_KEY_ID"))


def _chat(model_id: str, max_tokens: int = 2000):
    from langchain_aws import ChatBedrockConverse

    return ChatBedrockConverse(
        model=model_id,
        region_name=os.getenv("AWS_REGION", "us-east-1"),
        credentials_profile_name=os.getenv("AWS_PROFILE") or None,
        max_tokens=max_tokens,
        temperature=0.2,
    )


def _parse_json(text: str) -> dict | None:
    import re

    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None


def _text_of(message) -> str:
    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content
    return "".join(
        part.get("text", "") for part in content if isinstance(part, dict)
    )


class ToolRecorder:
    """Runs the real tools and keeps what happened, for the trace.

    LangGraph hands the model a string. The console wants the summary, the
    timing and the structured payload, so the result is captured here on the way
    past rather than reconstructed from the transcript afterwards.
    """

    def __init__(
        self, tools: RecallTools, memory: AgentMemory, use_memory: bool = True
    ) -> None:
        self.tools = tools
        self.memory = memory
        self.use_memory = use_memory
        self.pending: list[dict] = []
        self.collected: dict[str, ToolResult] = {}
        self.terms_recalled: int = 0

    def run(self, name: str, arguments: dict) -> str:
        self.pending.append(
            {"type": "tool_call", "name": name, "input": _trim(arguments)}
        )
        result = dispatch(self.tools, name, arguments)
        self.collected[name] = result
        self.pending.append(result.to_event())
        return result.for_model()

    def define_terms(self, terms: list[str]) -> str:
        """Taxonomic memory, looked up by key rather than guessed at."""
        began = time.perf_counter()
        self.pending.append(
            {"type": "tool_call", "name": "define_terms", "input": {"terms": terms}}
        )
        rows = self.memory.define(terms)
        self.terms_recalled += len(rows)
        ms = round((time.perf_counter() - began) * 1000, 1)

        if rows:
            for_model = " ".join(
                f"{r['label']}: {r['definition']}" for r in rows
            )
            summary = (
                f"{len(rows)} term(s) recalled from taxonomic memory: "
                + ", ".join(r["label"] for r in rows)
            )
        else:
            for_model = f"No definition held for {', '.join(terms)}."
            summary = for_model

        self.pending.append({
            "type": "tool_result",
            "name": "define_terms",
            "summary": summary,
            "ms": ms,
            "data": {"terms": [
                {"term": r["_id"], "label": r["label"], "definition": r["definition"]}
                for r in rows
            ]},
        })
        return for_model

    def drain(self) -> list[dict]:
        events, self.pending = self.pending, []
        return events

    def supervisor_tools(self) -> list[StructuredTool]:
        tools = [
            StructuredTool.from_function(
                func=lambda lot_code: self.run("scope_recall", {"lot_code": lot_code}),
                name="scope_recall",
                description=(
                    "Find every vehicle carrying a part from a flagged production "
                    "batch, how many are out with a customer, and how many cannot "
                    "be located. Call this first."
                ),
            ),
            StructuredTool.from_function(
                func=lambda days=180: self.run("find_collision_claims", {"days": days}),
                name="find_collision_claims",
                description=(
                    "Of the vehicles already scoped, find those with "
                    "collision-consistent damage claims, to test whether the "
                    "failure has already started."
                ),
            ),
            StructuredTool.from_function(
                func=lambda: self.run("cohort_vehicles", {}),
                name="cohort_vehicles",
                description=(
                    "Split the scoped vehicles into groups by rental status and "
                    "whether their position can be trusted."
                ),
            ),
            StructuredTool.from_function(
                func=lambda actions: self.run("propose_actions", {"actions": actions}),
                name="propose_actions",
                description=(
                    "Propose one action per cohort. Each action is an object with "
                    "cohort, action, urgency (immediate, today, this-week or "
                    "next-service), reason and vehicleCount."
                ),
            ),
        ]
        if self.use_memory:
            tools.append(
                StructuredTool.from_function(
                    func=self.define_terms,
                    name="define_terms",
                    description=(
                        "Look up what a fleet management or vehicle data term means "
                        "in this operator's vocabulary. Use it rather than guessing."
                    ),
                )
            )
        return tools


def investigate_vehicle(
    tools: RecallTools, memory: AgentMemory, plate: str, use_memory: bool = True
) -> dict:
    """One vehicle, one small ReAct agent, its own tool loop.

    Checks episodic memory first. A conclusion reached while the vehicle's
    evidence was in exactly this state is still the right conclusion, so the
    second run of a demo is fast and nearly free.
    """
    began = time.perf_counter()

    evidence = dispatch(tools, "vehicle_evidence", {"plate": plate})
    last_seen = evidence.data.get("lastReadingAt")

    remembered = (
        memory.recall_investigation(plate, last_seen)
        if use_memory and last_seen else None
    )
    if remembered:
        return {
            "plate": plate,
            "conclusion": remembered["conclusion"],
            "confidence": remembered.get("confidence", 0.0),
            "reasoning": remembered.get("reasoning", ""),
            "recommendedAction": remembered.get("recommendedAction", ""),
            "fromMemory": True,
            "ms": round((time.perf_counter() - began) * 1000, 1),
            "model": "episodic memory",
            "usage": {},
        }

    recorder = ToolRecorder(tools, memory)
    investigator_tools = [
        StructuredTool.from_function(
            func=lambda: recorder.run("vehicle_evidence", {"plate": plate}),
            name="vehicle_evidence",
            description="Recent telemetry, findings and trust for this vehicle.",
        ),
        StructuredTool.from_function(
            func=lambda radius_km=2.0: recorder.run(
                "tracker_conflict", {"plate": plate, "radius_km": radius_km}
            ),
            name="tracker_conflict",
            description="Other vehicles sitting where this one claims to be.",
        ),
    ]

    usage: dict = {}
    text = ""
    try:
        agent = create_react_agent(
            _chat(INVESTIGATOR_MODEL, 900),
            investigator_tools,
            prompt=INVESTIGATOR_PROMPT,
        )
        state = agent.invoke(
            {"messages": [HumanMessage(
                f"Vehicle {plate} is in a safety recall and cannot be located. "
                f"Gather the evidence and conclude why."
            )]},
            {"recursion_limit": 12},
        )
        for message in state["messages"]:
            meta = getattr(message, "usage_metadata", None) or {}
            if meta:
                usage["input_tokens"] = usage.get("input_tokens", 0) + meta.get(
                    "input_tokens", 0
                )
                usage["output_tokens"] = usage.get("output_tokens", 0) + meta.get(
                    "output_tokens", 0
                )
        text = _text_of(state["messages"][-1])
    except Exception as exc:  # noqa: BLE001
        logger.warning("Investigation of %s failed: %s", plate, exc)

    verdict = _parse_json(text) or {
        "conclusion": "unresolved",
        "confidence": 0.0,
        "reasoning": (text or "No conclusion reached.").strip()[:300],
        "recommendedAction": "",
    }
    verdict.update({
        "plate": plate,
        "fromMemory": False,
        "ms": round((time.perf_counter() - began) * 1000, 1),
        "model": INVESTIGATOR_MODEL,
        "usage": usage,
    })

    if use_memory and last_seen and verdict["conclusion"] != "unresolved":
        memory.remember_investigation(plate, last_seen, verdict)

    return verdict


class RecallAgent:
    """Supervisor plus fan-out investigators, both ReAct on LangGraph."""

    def __init__(self, db: Database) -> None:
        self.db = db
        self.tools = RecallTools(db)
        self.memory = AgentMemory(db)

    def _system_prompt(self, use_memory: bool = True) -> str:
        """Procedural memory, read fresh so an SOP edit takes effect at once."""
        if not use_memory:
            # No SOP and no vocabulary: what the agent knows on its own.
            return (
                "You coordinate vehicle recalls for Leafy Mobility, a rental and "
                "mobility operator running about 5,000 vehicles across Brazil, "
                "North America and Europe.\n\n"
                f"{FALLBACK_SOP}"
            )
        sop = self.memory.procedure_prompt("recall_response") or FALLBACK_SOP
        vocabulary = self.memory.vocabulary()
        return (
            "You coordinate vehicle recalls for Leafy Mobility, a rental and "
            "mobility operator running about 5,000 vehicles across Brazil, North "
            "America and Europe.\n\n"
            "Follow this standard operating procedure, which your operations "
            "team maintains:\n\n"
            f"{sop}\n\n"
            "If you are unsure what a term means, call define_terms rather than "
            "guessing. Terms held in memory include: "
            f"{', '.join(vocabulary[:24])}."
        )

    def run(
        self,
        lot_code: str,
        max_investigations: int = 8,
        use_memory: bool = True,
    ) -> Iterator[AgentEvent]:
        """Stream the whole response as it happens.

        With use_memory off the agent gets no SOP, no term lookup and no past
        conclusions. That is the comparison the console offers: the same models
        and the same tools, with and without what the operator has taught it.
        """
        began = time.perf_counter()
        run_id = f"recall-{lot_code}-{int(time.time())}"
        spend = Spend()
        memory_stats = self.memory.stats() if use_memory else {}

        yield AgentEvent("start", {
            "runId": run_id, "lot": lot_code,
            "supervisor": SUPERVISOR_MODEL, "investigator": INVESTIGATOR_MODEL,
            "framework": "langgraph-react",
            "useMemory": use_memory,
            "memory": memory_stats,
        })

        recorder = ToolRecorder(self.tools, self.memory, use_memory)
        narrative: list[str] = []
        proposals: list[dict] = []
        state = {"block": 0, "open": None}

        def flush():
            """Tool events are recorded as the tools run; emit them in order."""
            for event in recorder.drain():
                if state["open"]:
                    yield AgentEvent("text_end", {"id": state["open"]})
                    state["open"] = None
                yield AgentEvent(event.pop("type"), event)

        with MongoDBSaver.from_conn_string(
            os.environ["MONGODB_URI"], CHECKPOINT_DB
        ) as checkpointer:
            agent = create_react_agent(
                _chat(SUPERVISOR_MODEL),
                recorder.supervisor_tools(),
                prompt=self._system_prompt(use_memory),
                checkpointer=checkpointer,
            )
            config = {
                "configurable": {"thread_id": run_id},
                "recursion_limit": MAX_TURNS * 2,
            }
            question = HumanMessage(
                f"Supplier advisory received for production batch {lot_code}. "
                f"Work the recall response procedure."
            )

            for chunk, _meta in agent.stream(
                {"messages": [question]}, config, stream_mode="messages"
            ):
                yield from flush()

                usage = getattr(chunk, "usage_metadata", None)
                if usage:
                    spend.add(SUPERVISOR_MODEL, usage)

                if not isinstance(chunk, AIMessageChunk):
                    continue
                piece = _text_of(chunk)
                if not piece:
                    continue
                if state["open"] is None:
                    state["block"] += 1
                    state["open"] = f"{run_id}-{state['block']}"
                    yield AgentEvent("text_start", {"id": state["open"]})
                narrative.append(piece)
                yield AgentEvent(
                    "text_delta", {"id": state["open"], "content": piece}
                )

            yield from flush()
            if state["open"]:
                yield AgentEvent("text_end", {"id": state["open"]})

        collected = recorder.collected
        if "propose_actions" in collected:
            proposals = collected["propose_actions"].data.get("actions", [])

        # The fan-out. Only the vehicles nobody can locate, because those are the
        # only ones where per-vehicle reasoning beats a rule.
        scope = collected.get("scope_recall")
        unlocatable = (
            scope.data.get("unlocatable", []) if scope else []
        )[:max_investigations]
        investigations: list[dict] = []

        if unlocatable:
            yield AgentEvent("investigations_start", {
                "count": len(unlocatable), "plates": unlocatable,
                "model": INVESTIGATOR_MODEL,
            })
            for plate in unlocatable:
                yield AgentEvent("investigation_pending", {"plate": plate})

            with ThreadPoolExecutor(max_workers=INVESTIGATION_WORKERS) as pool:
                futures = {
                    pool.submit(
                        investigate_vehicle,
                        self.tools, self.memory, plate, use_memory,
                    ): plate
                    for plate in unlocatable
                }
                for future in as_completed(futures):
                    verdict = future.result()
                    spend.add(INVESTIGATOR_MODEL, verdict.pop("usage", {}))
                    investigations.append(verdict)
                    yield AgentEvent("investigation", verdict)

        elapsed = round(time.perf_counter() - began, 1)
        cost = spend.summary()
        summary = {
            "runId": run_id,
            "lot": lot_code,
            "seconds": elapsed,
            "cost": cost,
            "framework": "langgraph-react",
            "useMemory": use_memory,
            "proposals": proposals,
            "investigations": investigations,
            "narrative": "".join(narrative),
            "scope": scope.data if scope else {},
            "collision": collected.get("find_collision_claims", ToolResult("", "")).data,
            "cohorts": collected.get("cohort_vehicles", ToolResult("", "")).data,
            "memory": memory_stats,
            "startedAt": datetime.now(timezone.utc),
            "supervisorModel": SUPERVISOR_MODEL,
            "investigatorModel": INVESTIGATOR_MODEL,
            "approved": False,
        }
        self.db[AGENT_RUNS].insert_one(dict(summary))

        yield AgentEvent("done", {
            "runId": run_id, "seconds": elapsed, "cost": cost,
            "useMemory": use_memory,
            "termsRecalled": recorder.terms_recalled,
            "proposals": proposals,
            "investigations": investigations,
            "conclusions": _tally(investigations),
            "recalledFromMemory": sum(
                1 for row in investigations if row.get("fromMemory")
            ),
        })


def _tally(investigations: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in investigations:
        key = row.get("conclusion", "unresolved")
        counts[key] = counts.get(key, 0) + 1
    return counts


def _trim(payload: dict, limit: int = 6) -> dict:
    """Arrays of five hundred registrations make the trace unreadable."""
    out = {}
    for key, value in payload.items():
        if isinstance(value, list) and len(value) > limit:
            out[key] = value[:limit] + [f"... {len(value) - limit} more"]
        else:
            out[key] = value
    return out
