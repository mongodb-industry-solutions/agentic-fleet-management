"use client";

import { palette } from "@leafygreen-ui/palette";

import { formatNumber } from "@/lib/format";

/**
 * The traversal, drawn.
 *
 * Each step is one relationship from the ontology and one hop of the
 * $graphLookup. Showing the count falling from parts to vehicles to open
 * agreements makes the shape of the answer legible: most of the fleet carries
 * the part, only some of those are out with a customer right now.
 */

const STEPS = [
  { key: "supplier", label: "Supplier", edge: null },
  { key: "lot", label: "Production batch", edge: "SUPPLIES" },
  { key: "parts", label: "Fitted parts", edge: "CONTAINS" },
  { key: "vehicles", label: "Vehicles", edge: "FITTED_TO" },
  { key: "agreements", label: "Open agreements", edge: "RENTED_UNDER" },
  { key: "customers", label: "Customers to call", edge: "HELD_BY" },
];

export default function RecallTrace({ lot, counts }) {
  const values = {
    supplier: 1,
    lot: 1,
    parts: counts.parts,
    vehicles: counts.vehicles,
    agreements: counts.agreements,
    customers: counts.customers,
  };

  return (
    <div className="flex items-stretch gap-1 flex-wrap">
      {STEPS.map((step, index) => (
        <div key={step.key} className="flex items-stretch gap-1">
          {step.edge && (
            <div className="flex flex-col justify-center px-1">
              <div className="text-[10px] font-mono text-gray-400 whitespace-nowrap">
                {step.edge}
              </div>
              <div className="text-gray-300 text-center leading-none">&rarr;</div>
            </div>
          )}
          <div
            className="px-3 py-2 rounded-lg border min-w-[110px]"
            style={{
              borderColor:
                index === STEPS.length - 1 ? palette.red.light1 : palette.gray.light2,
              backgroundColor:
                index === STEPS.length - 1 ? palette.red.light3 : "white",
            }}
          >
            <div className="text-[10px] uppercase tracking-wide text-gray-500">
              {step.label}
            </div>
            <div
              className="text-xl font-semibold tabular-nums"
              style={{
                color:
                  index === STEPS.length - 1 ? palette.red.dark2 : palette.black,
              }}
            >
              {formatNumber(values[step.key])}
            </div>
            {step.key === "supplier" && (
              <div className="text-[10px] text-gray-500 truncate">
                {lot.supplier}
              </div>
            )}
            {step.key === "lot" && (
              <div className="text-[10px] text-gray-500 truncate">
                {lot.partName}
              </div>
            )}
          </div>
        </div>
      ))}
    </div>
  );
}
