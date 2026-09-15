"use client";

import Badge from "@leafygreen-ui/badge";
import { palette } from "@leafygreen-ui/palette";
import { Body } from "@leafygreen-ui/typography";

import { formatNumber } from "@/lib/format";

/**
 * The trace, which is the product rather than a debug panel.
 *
 * An operator watching the agent scope, corroborate and cohort is being shown
 * the reasoning. A spinner followed by an answer asks them to take it on faith,
 * which is the opposite of what this demo argues for.
 */

const TOOL_LABELS = {
  scope_recall: "Scope the recall",
  find_collision_claims: "Look for corroborating damage",
  cohort_vehicles: "Group the vehicles",
  propose_actions: "Propose actions",
  vehicle_evidence: "Gather evidence",
  tracker_conflict: "Check for a conflicting position",
  define_terms: "Recall what the terms mean",
};

// Which tools are pure queries and which involve the model deciding something.
const IS_RULE = {
  scope_recall: true,
  cohort_vehicles: true,
  find_collision_claims: true,
  vehicle_evidence: true,
  tracker_conflict: true,
  define_terms: true,
  propose_actions: false,
};

const CONCLUSION_VARIANT = {
  gps_failure: "yellow",
  tracker_moved: "red",
  tampering: "red",
  unresolved: "lightgray",
};

const CONCLUSION_LABELS = {
  gps_failure: "GPS module failing",
  tracker_moved: "Tracker in another vehicle",
  tampering: "Possible tampering",
  unresolved: "Unresolved",
};

function Rail({ colour }) {
  return (
    <div className="flex flex-col items-center w-6 shrink-0">
      <span
        className="w-2 h-2 rounded-full mt-2"
        style={{ backgroundColor: colour }}
      />
      <span className="flex-1 w-px" style={{ backgroundColor: palette.gray.light2 }} />
    </div>
  );
}

function Entry({ colour, children }) {
  return (
    <div className="flex gap-2">
      <Rail colour={colour} />
      <div className="flex-1 pb-3 min-w-0">{children}</div>
    </div>
  );
}

export default function AgentTrace({ events }) {
  if (!events.length) return null;

  return (
    <div className="flex flex-col">
      {events.map((event, index) => {
        if (event.type === "text") {
          return (
            <Entry key={index} colour={palette.green.base}>
              <Body className="text-sm whitespace-pre-wrap leading-relaxed">
                {event.content.trim()}
              </Body>
            </Entry>
          );
        }

        if (event.type === "tool_pending") {
          return (
            <Entry key={index} colour={palette.gray.light1}>
              <div className="flex items-center gap-2 flex-wrap">
                <span className="text-sm font-medium text-gray-500">
                  {TOOL_LABELS[event.name] || event.name}
                </span>
                <span className="text-[11px] text-gray-400">writing...</span>
              </div>
            </Entry>
          );
        }

        if (event.type === "tool_call") {
          return (
            <Entry key={index} colour={palette.blue.light1}>
              <div className="flex items-center gap-2 flex-wrap">
                <span className="text-sm font-medium">
                  {TOOL_LABELS[event.name] || event.name}
                </span>
                <Badge
                  variant={
                    event.name === "define_terms"
                      ? "yellow"
                      : IS_RULE[event.name]
                        ? "lightgray"
                        : "blue"
                  }
                >
                  {event.name === "define_terms"
                    ? "memory"
                    : IS_RULE[event.name]
                      ? "query"
                      : "decision"}
                </Badge>
              </div>
              {Object.keys(event.input || {}).length > 0 && (
                <div
                  className="font-mono text-[11px] text-gray-500 mt-0.5 truncate"
                  title={JSON.stringify(event.input)}
                >
                  {/* The proposals arrive as a page of JSON; the cards below
                      render them properly, so the call only needs its shape. */}
                  {JSON.stringify(event.input).slice(0, 160)}
                  {JSON.stringify(event.input).length > 160 ? "..." : ""}
                </div>
              )}
            </Entry>
          );
        }

        if (event.type === "tool_result") {
          const claims = event.data?.claims;
          const terms = event.data?.terms;
          return (
            <Entry key={index} colour={palette.blue.base}>
              <div className="flex items-start gap-2">
                <Body className="text-sm text-gray-700 flex-1">{event.summary}</Body>
                <span className="text-[11px] text-gray-400 tabular-nums shrink-0">
                  {event.ms} ms
                </span>
              </div>
              {terms?.length > 0 && (
                <div className="mt-2 flex flex-col gap-1">
                  {terms.map((row) => (
                    <div
                      key={row.term}
                      className="text-xs px-2 py-1 rounded"
                      style={{ backgroundColor: palette.yellow.light3 }}
                    >
                      <span className="font-semibold">{row.label}</span>
                      <span className="text-gray-600"> {row.definition}</span>
                    </div>
                  ))}
                </div>
              )}
              {claims?.length > 0 && (
                <div className="mt-2 flex flex-col gap-1">
                  {claims.slice(0, 5).map((claim) => (
                    <div
                      key={claim._id}
                      className="text-xs flex items-center gap-2 px-2 py-1 rounded"
                      style={{ backgroundColor: palette.red.light3 }}
                    >
                      <span className="font-mono">{claim.plate}</span>
                      <span className="text-gray-600 flex-1 truncate">
                        {claim.description}
                      </span>
                      <span className="tabular-nums">
                        ${formatNumber(Math.round(claim.cost || 0))}
                      </span>
                    </div>
                  ))}
                </div>
              )}
            </Entry>
          );
        }

        if (event.type === "investigations_start") {
          return (
            <Entry key={index} colour={palette.purple.base}>
              <div className="text-sm font-medium">
                Investigating {event.count} vehicles that cannot be located
              </div>
              <div className="text-xs text-gray-500 mt-0.5">
                One agent each, running concurrently on{" "}
                {event.model.split(".").pop()}. These are the only cases where
                per-vehicle reasoning beats a rule.
              </div>
            </Entry>
          );
        }

        if (event.type === "investigation_pending") {
          return (
            <Entry key={index} colour={palette.gray.light1}>
              <div className="flex items-center gap-2">
                <span className="font-mono text-xs font-semibold text-gray-500">
                  {event.plate}
                </span>
                <span className="text-[11px] text-gray-400">investigating...</span>
              </div>
            </Entry>
          );
        }

        if (event.type === "investigation") {
          return (
            <Entry key={index} colour={palette.purple.light2}>
              <div className="flex items-center gap-2 flex-wrap">
                <span className="font-mono text-xs font-semibold">{event.plate}</span>
                <Badge variant={CONCLUSION_VARIANT[event.conclusion] || "lightgray"}>
                  {CONCLUSION_LABELS[event.conclusion] || event.conclusion}
                </Badge>
                <span className="text-[11px] text-gray-400">
                  confidence {event.confidence} &middot; {Math.round(event.ms)} ms
                </span>
                {event.fromMemory && (
                  <Badge variant="yellow">from memory</Badge>
                )}
              </div>
              <Body className="text-xs text-gray-600 mt-1">{event.reasoning}</Body>
              {event.recommendedAction && (
                <Body className="text-xs text-gray-500 mt-0.5">
                  {event.recommendedAction}
                </Body>
              )}
            </Entry>
          );
        }

        if (event.type === "error") {
          return (
            <Entry key={index} colour={palette.red.base}>
              <Body className="text-sm" style={{ color: palette.red.dark2 }}>
                {event.message}
              </Body>
            </Entry>
          );
        }

        return null;
      })}
    </div>
  );
}

export { CONCLUSION_LABELS, CONCLUSION_VARIANT };
