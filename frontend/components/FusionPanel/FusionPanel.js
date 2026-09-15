"use client";

import { useState } from "react";
import Badge from "@leafygreen-ui/badge";
import Card from "@leafygreen-ui/card";
import { palette } from "@leafygreen-ui/palette";
import { Body, Subtitle } from "@leafygreen-ui/typography";

import { SOURCE_LABELS } from "@/lib/const/fleet";

/**
 * Which source was believed, and why.
 *
 * The score decomposition is the point. Showing that the manufacturer won on
 * fuel level while the tracker won on position, with the arithmetic visible,
 * is more convincing than a fixed precedence list and it is what makes a
 * disputed mileage charge settleable by looking rather than arguing.
 */

const shortPath = (path) => path.split(".").slice(-2).join(".");

function ScoreBar({ label, value }) {
  return (
    <div className="flex items-center gap-1.5" title={`${label} ${value}`}>
      <span className="text-[10px] uppercase tracking-wide text-gray-400 w-9">
        {label}
      </span>
      <div className="w-12 h-1 rounded-full bg-gray-200 overflow-hidden">
        <div
          className="h-full rounded-full"
          style={{
            width: `${Math.round(value * 100)}%`,
            backgroundColor: palette.gray.dark1,
          }}
        />
      </div>
      <span className="text-[10px] tabular-nums text-gray-500 w-8">
        {value.toFixed(2)}
      </span>
    </div>
  );
}

function Witness({ witness }) {
  const { score } = witness;
  return (
    <div
      className="flex items-start gap-3 py-2 px-2.5 rounded"
      style={{
        backgroundColor: witness.used ? palette.green.light3 : "transparent",
        border: `1px solid ${witness.used ? palette.green.light2 : palette.gray.light2}`,
      }}
    >
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-xs font-medium">
            {SOURCE_LABELS[witness.src] || witness.src}
          </span>
          {witness.used && <Badge variant="green">used</Badge>}
          {witness.rejected && <Badge variant="red">{witness.rejected}</Badge>}
        </div>
        <div className="text-sm font-mono mt-0.5">
          {typeof witness.v === "boolean"
            ? witness.v
              ? "true"
              : "false"
            : String(witness.v)}
        </div>
        <div className="flex gap-3 flex-wrap mt-1.5">
          <ScoreBar label="rel" value={score.reliability} />
          <ScoreBar label="fresh" value={score.freshness} />
          <ScoreBar label="conf" value={score.confidence} />
        </div>
      </div>
      <div className="text-right shrink-0">
        <div className="text-[10px] uppercase tracking-wide text-gray-400">
          trust
        </div>
        <div
          className="text-lg font-semibold tabular-nums"
          style={{ color: witness.used ? palette.green.dark2 : palette.gray.dark1 }}
        >
          {witness.trustScore.toFixed(3)}
        </div>
      </div>
    </div>
  );
}

export default function FusionPanel({ provenance = [], sources = [] }) {
  const [showAll, setShowAll] = useState(false);

  if (!provenance.length) {
    return (
      <Card className="p-4">
        <Subtitle>Sources</Subtitle>
        <Body className="text-gray-600 text-sm mt-1">
          Only one source reports on this vehicle, so there is nothing to
          reconcile. Every value is taken as given.
        </Body>
        <div className="flex gap-2 mt-2">
          {sources.map((source) => (
            <Badge key={source} variant="darkgray">
              {SOURCE_LABELS[source] || source}
            </Badge>
          ))}
        </div>
      </Card>
    );
  }

  const contested = provenance.filter(
    (row) => new Set(row.witnesses.map((w) => JSON.stringify(w.v))).size > 1
  );
  const shown = showAll ? provenance : contested;

  return (
    <Card className="p-4">
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div>
          <Subtitle>Which source was believed</Subtitle>
          <Body className="text-gray-600 text-sm mt-1">
            {sources.length} sources describe this vehicle.{" "}
            {contested.length} of {provenance.length} signals came back with
            different answers, so a trust score decided.
          </Body>
        </div>
        <button
          type="button"
          onClick={() => setShowAll(!showAll)}
          className="text-xs underline"
          style={{ color: palette.blue.base }}
        >
          {showAll ? "Only where they disagreed" : `All ${provenance.length} signals`}
        </button>
      </div>

      <div className="text-xs font-mono text-gray-500 mt-3 px-2.5 py-2 rounded bg-gray-50">
        trustScore = reliability(sourceType, vssPath) x freshnessDecay(observedAt) x
        confidenceFactor(quality)
      </div>

      <div className="flex flex-col gap-4 mt-3">
        {shown.map((row) => (
          <div key={row.vssPath}>
            <div className="flex items-baseline gap-2 mb-1.5">
              <span className="text-sm font-medium">{shortPath(row.vssPath)}</span>
              <span className="font-mono text-[10px] text-gray-400">
                {row.vssPath}
              </span>
              {row.chosen.strategy !== "highest_trust" && (
                <Badge variant="blue">{row.chosen.strategy}</Badge>
              )}
            </div>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
              {row.witnesses.map((witness, index) => (
                <Witness key={index} witness={witness} />
              ))}
            </div>
          </div>
        ))}
      </div>

      {shown.length === 0 && (
        <Body className="text-sm text-gray-500 mt-3">
          Both sources agreed on every signal for this vehicle.
        </Body>
      )}
    </Card>
  );
}
