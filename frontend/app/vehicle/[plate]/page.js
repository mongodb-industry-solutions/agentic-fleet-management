"use client";

import { use, useEffect, useState } from "react";
import Link from "next/link";
import Badge from "@leafygreen-ui/badge";
import Banner from "@leafygreen-ui/banner";
import Card from "@leafygreen-ui/card";
import Icon from "@leafygreen-ui/icon";
import { palette } from "@leafygreen-ui/palette";
import { Body, H3, Overline, Subtitle } from "@leafygreen-ui/typography";

import AppShell from "@/components/AppShell";
import FleetMap from "@/components/FleetMap";
import FusionPanel from "@/components/FusionPanel";
import { getHealth, getVehicle } from "@/lib/api/client";
import {
  SOURCE_LABELS,
  ACTIVITY_LABELS,
  SEVERITY_VARIANTS,
  STATUS_LABELS,
  STATUS_VARIANTS,
  TRUST_LABELS,
  TRUST_VARIANTS,
} from "@/lib/const/fleet";
import { formatNumber } from "@/lib/format";
import { useBehindTheScenes } from "@/lib/context/BehindTheScenesContext";
import { useMounted } from "@/lib/hooks/useMounted";

const time = (iso) =>
  iso
    ? new Date(iso).toLocaleTimeString("en-GB", {
        hour: "2-digit",
        minute: "2-digit",
      })
    : "";

function SignalTile({ signal }) {
  const value =
    typeof signal.value === "boolean"
      ? signal.value
        ? "On"
        : "Off"
      : typeof signal.value === "number"
      ? signal.value.toLocaleString("en-US")
      : String(signal.value);

  return (
    <div className="px-3 py-2 rounded border bg-white" title={signal.description}>
      <div className="text-xs uppercase tracking-wide text-gray-500">
        {signal.label}
      </div>
      <div className="text-xl font-semibold">
        {value}
        {signal.unit && (
          <span className="text-sm font-normal text-gray-500 ml-1">
            {signal.unit}
          </span>
        )}
      </div>
      <div className="font-mono text-[10px] text-gray-400 mt-0.5 truncate">
        {signal.vssPath}
      </div>
    </div>
  );
}

export default function VehiclePage({ params }) {
  const { plate } = use(params);
  const [health, setHealth] = useState(null);
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const { publish } = useBehindTheScenes();
  const mounted = useMounted();

  useEffect(() => {
    Promise.all([getHealth(), getVehicle(plate)])
      .then(([healthData, vehicleData]) => {
        setHealth(healthData);
        setData(vehicleData);
        publish(vehicleData.explain, `Vehicle ${plate}`);
      })
      .catch((err) => setError(err.message));
  }, [plate, publish]);

  const vehicle = data?.vehicle;

  return (
    <AppShell health={health}>
      {error && <Banner variant="danger">{error}</Banner>}

      {vehicle && (
        <div className="flex flex-col gap-4">
          <div>
            <Link
              href="/fleet"
              className="text-sm no-underline inline-flex items-center gap-1"
              style={{ color: palette.blue.base }}
            >
              <Icon glyph="ArrowLeft" size="small" /> Fleet
            </Link>
          </div>

          <div className="flex items-start justify-between gap-4 flex-wrap">
            <div>
              <Overline>{vehicle.depot}</Overline>
              <H3>
                {vehicle.plate}{" "}
                <span className="font-normal text-gray-500 text-xl">
                  {vehicle.make} {vehicle.model} {vehicle.year}
                </span>
              </H3>
              <Body className="text-gray-600 text-sm mt-1">
                VIN {vehicle.vin} &middot;{" "}
                {ACTIVITY_LABELS[vehicle.activity] || vehicle.activity}
                {vehicle.odometerKm != null && (
                  <> &middot; {formatNumber(vehicle.odometerKm)} km</>
                )}
                {vehicle.lastSeen && <> &middot; last report {time(vehicle.lastSeen)}</>}
              </Body>
            </div>
            <div className="flex gap-2 items-center flex-wrap">
              {(data.sources || []).map((source) => (
                <Badge key={source} variant="darkgray">
                  {SOURCE_LABELS[source] || source}
                </Badge>
              ))}
              <Badge variant={STATUS_VARIANTS[vehicle.status] || "lightgray"}>
                {STATUS_LABELS[vehicle.status] || vehicle.status}
              </Badge>
              <Badge variant={TRUST_VARIANTS[vehicle.trust] || "lightgray"}>
                {TRUST_LABELS[vehicle.trust]}
              </Badge>
            </div>
          </div>

          {vehicle.contract && (
            <Card className="p-3 flex items-center gap-6 flex-wrap text-sm">
              <div>
                <div className="text-xs uppercase tracking-wide text-gray-500">
                  Rental agreement
                </div>
                <div className="font-mono">{vehicle.contract}</div>
              </div>
              <div>
                <div className="text-xs uppercase tracking-wide text-gray-500">
                  Customer
                </div>
                <div>{vehicle.customer}</div>
              </div>
            </Card>
          )}

          {data.findings.length > 0 && (
            <Card className="p-4">
              <Subtitle>Needs attention</Subtitle>
              <div className="flex flex-col gap-3 mt-3">
                {data.findings.map((finding, index) => (
                  <div
                    key={index}
                    className="flex items-start gap-3 pb-3 border-b last:border-0 last:pb-0"
                  >
                    <Badge variant={SEVERITY_VARIANTS[finding.severity]}>
                      {finding.severity}
                    </Badge>
                    <div className="flex-1">
                      <div className="font-medium text-sm">{finding.title}</div>
                      <div className="text-sm text-gray-600">
                        {finding.consequence}
                      </div>
                      <div className="text-xs text-gray-500 mt-1">
                        {finding.action}
                        {finding.occurrences > 1 && (
                          <> &middot; seen {formatNumber(finding.occurrences)} times</>
                        )}
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            </Card>
          )}

          <div className="grid grid-cols-1 lg:grid-cols-5 gap-4">
            <Card className="lg:col-span-3 p-3">
              {mounted && (
                <FleetMap
                  vehicles={[vehicle]}
                  focus={vehicle.plate}
                  onSelect={() => {}}
                  height={360}
                />
              )}
            </Card>

            <Card className="lg:col-span-2 p-4">
              <Subtitle>Live signals</Subtitle>
              <Body className="text-gray-600 text-xs mt-1">
                Named and typed by COVESA VSS, converted into the units the
                specification declares.
              </Body>
              <div className="grid grid-cols-2 gap-2 mt-3">
                {data.signals.map((signal) => (
                  <SignalTile key={signal.vssPath} signal={signal} />
                ))}
              </div>
            </Card>
          </div>

          <FusionPanel
            provenance={data.provenance || []}
            sources={data.sources || []}
          />

          <Card className="p-4">
            <div className="flex items-baseline justify-between">
              <Subtitle>Trips</Subtitle>
              <span className="text-xs text-gray-500">
                Reconstructed from ignition transitions, not stored
              </span>
            </div>
            {data.trips.length === 0 ? (
              <Body className="text-sm text-gray-500 mt-2">
                No completed trips in this window.
              </Body>
            ) : (
              <table className="w-full text-sm mt-3">
                <thead>
                  <tr className="text-left text-xs uppercase tracking-wide text-gray-500 border-b">
                    <th className="py-2 pr-4">Started</th>
                    <th className="py-2 pr-4">Ended</th>
                    <th className="py-2 pr-4 text-right">Duration</th>
                    <th className="py-2 text-right">Distance</th>
                  </tr>
                </thead>
                <tbody>
                  {data.trips.map((trip, index) => (
                    <tr key={index} className="border-b last:border-0">
                      <td className="py-2 pr-4 tabular-nums">
                        {time(trip.startedAt)}
                      </td>
                      <td className="py-2 pr-4 tabular-nums">
                        {trip.ongoing ? (
                          <Badge variant="blue">In progress</Badge>
                        ) : (
                          time(trip.endedAt)
                        )}
                      </td>
                      <td className="py-2 pr-4 text-right tabular-nums">
                        {trip.durationMin} min
                      </td>
                      <td className="py-2 text-right tabular-nums">
                        {trip.distanceKm != null ? `${trip.distanceKm} km` : "—"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </Card>
        </div>
      )}
    </AppShell>
  );
}
