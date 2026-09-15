"use client";

import Link from "next/link";
import Badge from "@leafygreen-ui/badge";
import { palette } from "@leafygreen-ui/palette";

import {
  ACTIVITY_LABELS,
  STATUS_LABELS,
  STATUS_VARIANTS,
  TRUST_COLOURS,
} from "@/lib/const/fleet";

function FuelBar({ percent }) {
  if (percent == null) {
    return <span className="text-xs text-gray-400">no reading</span>;
  }
  const colour =
    percent <= 15
      ? palette.red.base
      : percent <= 30
      ? palette.yellow.dark2
      : palette.green.base;
  return (
    <div className="flex items-center gap-2">
      <div className="w-10 h-1.5 rounded-full bg-gray-200 overflow-hidden">
        <div
          className="h-full rounded-full"
          style={{ width: `${percent}%`, backgroundColor: colour }}
        />
      </div>
      <span className="text-xs tabular-nums w-8">{percent}%</span>
    </div>
  );
}

const WINDOW = 150;

export default function VehicleList({ vehicles, focus, onSelect }) {
  // Vehicles arrive sorted with the most findings first, so the window shows
  // what needs attention rather than an arbitrary slice.
  const shown = vehicles.slice(0, WINDOW);

  return (
    <div className="overflow-y-auto" style={{ maxHeight: 520 }}>
      <table className="w-full text-sm">
        <thead className="sticky top-0 bg-white z-10">
          <tr className="text-left text-xs uppercase tracking-wide text-gray-500 border-b">
            <th className="py-2 pr-3">Vehicle</th>
            <th className="py-2 pr-3">Status</th>
            <th className="py-2 pr-3">Fuel</th>
            <th className="py-2 pr-2">Attention</th>
            <th className="py-2 w-[58px]" />
          </tr>
        </thead>
        <tbody>
          {shown.map((vehicle) => (
            <tr
              key={vehicle.plate}
              // Selecting is a click, not a hover. Sweeping the cursor down a
              // list should not send the map chasing after it.
              className="border-b last:border-0 hover:bg-gray-50 cursor-pointer"
              onClick={() => onSelect?.(vehicle.plate)}
              style={{
                backgroundColor:
                  focus === vehicle.plate ? palette.green.light3 : undefined,
              }}
            >
              <td className="py-2 pr-3">
                <div className="flex items-center gap-2">
                  <span
                    className="inline-block w-2 h-2 rounded-full shrink-0"
                    style={{
                      backgroundColor:
                        TRUST_COLOURS[vehicle.trust] || palette.gray.light1,
                    }}
                  />
                  <span className="font-mono text-xs font-semibold">
                    {vehicle.plate}
                  </span>
                </div>
                <div className="text-xs text-gray-500 pl-4">
                  {vehicle.make} {vehicle.model} &middot;{" "}
                  {ACTIVITY_LABELS[vehicle.activity] || vehicle.activity}
                </div>
              </td>
              <td className="py-2 pr-3">
                <Badge variant={STATUS_VARIANTS[vehicle.status] || "lightgray"}>
                  {STATUS_LABELS[vehicle.status] || vehicle.status}
                </Badge>
              </td>
              <td className="py-2 pr-3">
                <FuelBar percent={vehicle.fuel} />
              </td>
              <td className="py-2 pr-2 max-w-[150px]">
                {vehicle.topFinding ? (
                  <span
                    className="text-xs line-clamp-2"
                    title={vehicle.topFinding}
                    style={{
                      color:
                        vehicle.trust === "unreliable"
                          ? palette.red.dark2
                          : palette.yellow.dark2,
                    }}
                  >
                    {vehicle.topFinding}
                  </span>
                ) : (
                  <span className="text-xs text-gray-400">None</span>
                )}
              </td>
              <td className="py-2 w-[58px]">
                <Link
                  href={`/vehicle/${vehicle.plate}`}
                  onClick={(event) => event.stopPropagation()}
                  className="no-underline inline-block px-2 py-1 rounded border text-xs whitespace-nowrap"
                  style={{
                    borderColor: palette.gray.light1,
                    color: palette.green.dark2,
                    backgroundColor: palette.white,
                  }}
                >
                  Detail
                </Link>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {vehicles.length > WINDOW && (
        <div className="py-3 text-xs text-gray-500 text-center border-t">
          Showing the {WINDOW} vehicles with the most findings, of{" "}
          {vehicles.length.toLocaleString("en-US")} matching. Search or filter
          above to narrow it.
        </div>
      )}
      {vehicles.length === 0 && (
        <div className="py-6 text-sm text-gray-500 text-center">
          No vehicles match these filters.
        </div>
      )}
    </div>
  );
}
