"use client";

import { useEffect, useMemo, useState } from "react";
import Badge from "@leafygreen-ui/badge";
import Banner from "@leafygreen-ui/banner";
import Card from "@leafygreen-ui/card";
import { palette } from "@leafygreen-ui/palette";
import TextInput from "@leafygreen-ui/text-input";
import { Body, H3, Subtitle } from "@leafygreen-ui/typography";

import AppShell from "@/components/AppShell";
import FleetMap from "@/components/FleetMap";
import VehicleList from "@/components/VehicleList";
import { getFleet, getHealth } from "@/lib/api/client";
import {
  REGION_LABELS,
  STATUS_COLOURS,
  STATUS_LABELS,
  TRUST_COLOURS,
  TRUST_LABELS,
} from "@/lib/const/fleet";
import { formatNumber } from "@/lib/format";
import { useBehindTheScenes } from "@/lib/context/BehindTheScenesContext";
import { useMounted } from "@/lib/hooks/useMounted";

function Counter({ label, value, colour, active, onClick }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`flex-1 min-w-[130px] text-left px-4 py-3 rounded-lg border bg-white transition-colors ${
        active ? "border-gray-400" : "border-transparent hover:border-gray-300"
      }`}
    >
      <div className="flex items-center gap-2">
        {colour && (
          <span
            className="inline-block w-2.5 h-2.5 rounded-full"
            style={{ backgroundColor: colour }}
          />
        )}
        <span className="text-xs uppercase tracking-wide text-gray-500">{label}</span>
      </div>
      <div className="text-2xl font-semibold mt-0.5">{formatNumber(value)}</div>
    </button>
  );
}

export default function FleetPage() {
  const [health, setHealth] = useState(null);
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [statusFilter, setStatusFilter] = useState(null);
  const [trustFilter, setTrustFilter] = useState(null);
  const [regionFilter, setRegionFilter] = useState(null);
  const [selected, setSelected] = useState(null);
  const [search, setSearch] = useState("");
  const { publish } = useBehindTheScenes();
  const mounted = useMounted();

  useEffect(() => {
    Promise.all([getHealth(), getFleet()])
      .then(([healthData, fleetData]) => {
        setHealth(healthData);
        setData(fleetData);
        publish(fleetData.explain, "Fleet overview");
      })
      .catch((err) => setError(err.message));
  }, [publish]);

  const vehicles = useMemo(() => {
    if (!data) return [];
    // Registration, make, model or VIN, so an operator can type what they have.
    const term = search.trim().toLowerCase();
    return data.vehicles.filter(
      (v) =>
        (!statusFilter || v.status === statusFilter) &&
        (!trustFilter || v.trust === trustFilter) &&
        (!regionFilter || v.region === regionFilter) &&
        (!term ||
          [v.plate, v.make, v.model, v.vin]
            .filter(Boolean)
            .some((field) => String(field).toLowerCase().includes(term)))
    );
  }, [data, statusFilter, trustFilter, regionFilter, search]);

  const stats = data?.stats;
  const attention = data?.vehicles.filter((v) => v.trust !== "good") ?? [];

  return (
    <AppShell health={health}>
      {error && (
        <Banner variant="danger">
          {error}
          <div className="text-sm mt-1">
            If this is the first run, generate the sample fleet with{" "}
            <span className="font-mono text-xs">
              uv run python scripts/make_sample_data.py
            </span>{" "}
            in the backend directory.
          </div>
        </Banner>
      )}

      {stats && (
        <div className="flex flex-col gap-4">
          <div className="flex items-end justify-between gap-4 flex-wrap">
            <div>
              <H3>Fleet</H3>
              <Body className="text-gray-600 text-sm">
                {formatNumber(stats.total)} vehicles across{" "}
                {Object.keys(stats.byRegion || {}).length} regions
                {data?.queryMs != null && (
                  <span className="text-gray-400">
                    {" "}&middot; read from MongoDB in {data.queryMs} ms
                  </span>
                )}
              </Body>
            </div>
            {attention.length > 0 && (
              <Badge variant="red">{attention.length} vehicles need attention</Badge>
            )}
          </div>

          <div className="flex gap-1.5 flex-wrap items-center">
            <span className="text-xs uppercase tracking-wide text-gray-500 mr-1">
              Region
            </span>
            {Object.entries(stats.byRegion || {}).map(([code, count]) => (
              <button
                key={code}
                type="button"
                onClick={() =>
                  setRegionFilter(regionFilter === code ? null : code)
                }
                className="px-2.5 py-1 rounded-full text-xs border transition-colors"
                style={{
                  borderColor:
                    regionFilter === code ? palette.green.dark1 : palette.gray.light1,
                  backgroundColor:
                    regionFilter === code ? palette.green.light3 : "white",
                  color:
                    regionFilter === code ? palette.green.dark2 : palette.gray.dark1,
                }}
              >
                {REGION_LABELS[code] || code} {formatNumber(count)}
              </button>
            ))}
          </div>

          <div className="flex items-center gap-3 flex-wrap">
            <div className="w-[320px]">
              <span id="fleet-search-label" className="sr-only">
                Search the fleet
              </span>
              <TextInput
                aria-labelledby="fleet-search-label"
                placeholder="Registration, make, model or VIN"
                value={search}
                onChange={(event) => setSearch(event.target.value)}
              />
            </div>
            {search && (
              <span className="text-xs text-gray-500">
                {formatNumber(vehicles.length)} match &ldquo;{search}&rdquo;
              </span>
            )}
          </div>

          <div className="flex gap-2 flex-wrap">
            <Counter
              label="All"
              value={stats.total}
              active={!statusFilter && !trustFilter}
              onClick={() => {
                setStatusFilter(null);
                setTrustFilter(null);
              }}
            />
            {["on_rent", "available", "in_service"].map((status) => (
              <Counter
                key={status}
                label={STATUS_LABELS[status]}
                value={stats.byStatus[status] || 0}
                colour={STATUS_COLOURS[status]}
                active={statusFilter === status}
                onClick={() => {
                  setStatusFilter(statusFilter === status ? null : status);
                  setTrustFilter(null);
                }}
              />
            ))}
            {["degraded", "unreliable"].map((trust) => (
              <Counter
                key={trust}
                label={TRUST_LABELS[trust]}
                value={stats.byTrust[trust] || 0}
                colour={TRUST_COLOURS[trust]}
                active={trustFilter === trust}
                onClick={() => {
                  setTrustFilter(trustFilter === trust ? null : trust);
                  setStatusFilter(null);
                }}
              />
            ))}
          </div>

          <div className="grid grid-cols-1 lg:grid-cols-5 gap-4">
            <Card className="lg:col-span-3 p-3">
              {mounted && <FleetMap vehicles={vehicles} focus={selected} height={520} />}
            </Card>

            <Card className="lg:col-span-2 p-4 flex flex-col gap-2">
              <div className="flex items-baseline justify-between">
                <Subtitle>Vehicles</Subtitle>
                <span className="text-xs text-gray-500">
                  {formatNumber(vehicles.length)} shown
                </span>
              </div>
              <VehicleList
                vehicles={vehicles}
                focus={selected}
                onSelect={(plate) =>
                  setSelected((current) => (current === plate ? null : plate))
                }
              />
            </Card>
          </div>

          <Card className="p-4">
            <Subtitle>Fleet activity</Subtitle>
            <div className="flex items-center gap-8 mt-2 flex-wrap">
              <div>
                <div className="text-3xl font-semibold">{stats.averageFuel}%</div>
                <div className="text-xs text-gray-500">average fuel</div>
              </div>
              <div>
                <div className="text-3xl font-semibold">
                  {formatNumber(stats.totalTrips)}
                </div>
                <div className="text-xs text-gray-500">
                  trips, reconstructed from ignition transitions
                </div>
              </div>
              <div className="flex gap-6 flex-wrap text-sm">
                {Object.entries(stats.byActivity).map(([activity, count]) => (
                  <div key={activity}>
                    <div className="text-xs uppercase tracking-wide text-gray-500">
                      {activity}
                    </div>
                    <div
                      className="text-lg font-medium"
                      style={{ color: palette.black }}
                    >
                      {formatNumber(count)}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </Card>
        </div>
      )}
    </AppShell>
  );
}
