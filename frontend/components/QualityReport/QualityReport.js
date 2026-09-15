"use client";

import { useState } from "react";
import Badge from "@leafygreen-ui/badge";
import Card from "@leafygreen-ui/card";
import { palette } from "@leafygreen-ui/palette";
import { Body, H3, Subtitle } from "@leafygreen-ui/typography";

import FleetMap from "@/components/FleetMap";
import { REASON_COLOURS, REASON_LABELS } from "@/lib/const/rejectReasons";

import { formatNumber as number, formatPercent as percent } from "@/lib/format";

function Stat({ label, value, hint, tone = "default" }) {
  const colour =
    tone === "warn"
      ? palette.red.dark2
      : tone === "good"
      ? palette.green.dark2
      : palette.black;
  return (
    <Card className="flex-1 min-w-[160px] p-4">
      <div className="text-xs uppercase tracking-wide text-gray-500">{label}</div>
      <div className="text-2xl font-semibold mt-1" style={{ color: colour }}>
        {value}
      </div>
      {hint ? <div className="text-xs text-gray-500 mt-1">{hint}</div> : null}
    </Card>
  );
}

function ReasonRow({ row, active, onSelect, mappable }) {
  const colour = REASON_COLOURS[row.reason] || palette.gray.base;
  return (
    <button
      type="button"
      onClick={() => onSelect(active ? null : row.reason)}
      className={`w-full text-left px-3 py-2 rounded transition-colors ${
        active ? "bg-gray-100" : "hover:bg-gray-50"
      }`}
    >
      <div className="flex items-center gap-2">
        <span
          className="inline-block rounded-full shrink-0"
          style={{
            width: 10,
            height: 10,
            backgroundColor: mappable ? colour : palette.gray.light1,
          }}
        />
        <span className="font-medium text-sm flex-1">
          {REASON_LABELS[row.reason] || row.reason}
        </span>
        <span className="text-sm tabular-nums">{number(row.count)}</span>
        <span className="text-xs text-gray-500 w-14 text-right tabular-nums">
          {percent(row.share)}
        </span>
      </div>
      <div className="text-xs text-gray-500 pl-5 mt-0.5">{row.label}</div>
    </button>
  );
}

export default function QualityReport({ report }) {
  const [activeReason, setActiveReason] = useState(null);

  if (!report) return null;

  const { totals, positions, observationReasons, rowReasons, worstSignals, samples, map } =
    report;

  const mappableReasons = new Set(map.flagged.map((point) => point.reason));
  const sampleRows = activeReason ? samples[activeReason] || [] : [];

  return (
    <div className="flex flex-col gap-6">
      <div className="flex gap-3 flex-wrap">
        <Stat
          label="Records read"
          value={number(totals.rowsRead)}
          hint={`${number(totals.rowsRejected)} unusable`}
        />
        <Stat
          label="Observations"
          value={number(totals.observationsRead)}
          hint={`${number(totals.signalsSeen)} distinct signals`}
        />
        <Stat
          label="Rejected"
          value={number(totals.observationsRejected)}
          hint={percent(totals.rejectRate)}
          tone="warn"
        />
        <Stat
          label="Positions checked"
          value={number(positions.checked)}
          hint={`${number(positions.rejected)} flagged, ${percent(positions.rejectRate)}`}
          tone={positions.rejected > 0 ? "warn" : "good"}
        />
        <Stat label="Vehicles seen" value={number(totals.assetsSeen)} />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-5 gap-4">
        <Card className="lg:col-span-2 p-4 flex flex-col gap-3">
          <div>
            <Subtitle>Why data was rejected</Subtitle>
            <Body className="text-gray-600 text-sm mt-1">
              Select a reason to filter the map and see worked examples. Nothing is
              discarded, so every rejection stays traceable to the record it came
              from.
            </Body>
          </div>

          {rowReasons.length > 0 && (
            <div>
              <div className="text-xs uppercase tracking-wide text-gray-500 mb-1">
                Whole records
              </div>
              <div className="flex flex-col">
                {rowReasons.map((row) => (
                  <ReasonRow
                    key={row.reason}
                    row={row}
                    active={activeReason === row.reason}
                    onSelect={setActiveReason}
                    mappable={false}
                  />
                ))}
              </div>
            </div>
          )}

          <div>
            <div className="text-xs uppercase tracking-wide text-gray-500 mb-1">
              Individual readings
            </div>
            <div className="flex flex-col">
              {observationReasons.map((row) => (
                <ReasonRow
                  key={row.reason}
                  row={row}
                  active={activeReason === row.reason}
                  onSelect={setActiveReason}
                  mappable={mappableReasons.has(row.reason)}
                />
              ))}
            </div>
          </div>
        </Card>

        <Card className="lg:col-span-3 p-4 flex flex-col gap-3">
          <div className="flex items-baseline justify-between">
            <Subtitle>Where the fleet says it is</Subtitle>
            {activeReason && (
              <Badge variant="red">
                {REASON_LABELS[activeReason] || activeReason}
              </Badge>
            )}
          </div>
          <div style={{ height: 460 }}>
            <FleetMap
              accepted={map.accepted}
              flagged={map.flagged}
              activeReason={activeReason}
            />
          </div>
        </Card>
      </div>

      {sampleRows.length > 0 && (
        <Card className="p-4">
          <Subtitle>
            Examples: {REASON_LABELS[activeReason] || activeReason}
          </Subtitle>
          <div className="overflow-x-auto mt-3">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-xs uppercase tracking-wide text-gray-500 border-b">
                  <th className="py-2 pr-4">Record</th>
                  <th className="py-2 pr-4">Vehicle</th>
                  <th className="py-2 pr-4">Column</th>
                  <th className="py-2 pr-4">Raw value</th>
                  <th className="py-2">What went wrong</th>
                </tr>
              </thead>
              <tbody>
                {sampleRows.map((row, index) => (
                  <tr key={index} className="border-b last:border-0">
                    <td className="py-2 pr-4 tabular-nums text-gray-500">{row.row}</td>
                    <td className="py-2 pr-4 font-mono text-xs">{row.assetRef || "—"}</td>
                    <td className="py-2 pr-4 font-mono text-xs">{row.column || "—"}</td>
                    <td className="py-2 pr-4 font-mono text-xs">
                      {row.rawValue !== undefined ? String(row.rawValue) : "—"}
                    </td>
                    <td className="py-2 text-gray-700">{row.detail}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}

      {worstSignals.length > 0 && (
        <Card className="p-4">
          <Subtitle>Signals with the most unusable data</Subtitle>
          <Body className="text-gray-600 text-sm mt-1">
            Ranked by the share of readings that failed. Bounds, allowed values and
            formats all come from the VSS specification, so none of these rules were
            written by hand.
          </Body>
          <div className="overflow-x-auto mt-3">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-xs uppercase tracking-wide text-gray-500 border-b">
                  <th className="py-2 pr-4">VSS signal</th>
                  <th className="py-2 pr-4 text-right">Readings</th>
                  <th className="py-2 pr-4 text-right">Rejected</th>
                  <th className="py-2 pr-4 text-right">Rate</th>
                  <th className="py-2">Reasons</th>
                </tr>
              </thead>
              <tbody>
                {worstSignals.map((row) => (
                  <tr key={row.vssPath} className="border-b last:border-0">
                    <td className="py-2 pr-4 font-mono text-xs">{row.vssPath}</td>
                    <td className="py-2 pr-4 text-right tabular-nums">
                      {number(row.observations)}
                    </td>
                    <td className="py-2 pr-4 text-right tabular-nums">
                      {number(row.rejected)}
                    </td>
                    <td
                      className="py-2 pr-4 text-right tabular-nums font-medium"
                      style={{
                        color: row.rejectRate > 0.005 ? palette.red.dark2 : palette.black,
                      }}
                    >
                      {percent(row.rejectRate)}
                    </td>
                    <td className="py-2">
                      <div className="flex gap-1 flex-wrap">
                        {Object.entries(row.reasons).map(([reason, count]) => (
                          <Badge key={reason} variant="lightgray">
                            {REASON_LABELS[reason] || reason} {count}
                          </Badge>
                        ))}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}
    </div>
  );
}
