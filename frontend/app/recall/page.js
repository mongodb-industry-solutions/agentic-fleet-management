"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import Badge from "@leafygreen-ui/badge";
import Banner from "@leafygreen-ui/banner";
import Button from "@leafygreen-ui/button";
import Card from "@leafygreen-ui/card";
import { palette } from "@leafygreen-ui/palette";
import { Body, H3, Overline, Subtitle } from "@leafygreen-ui/typography";

import AppShell from "@/components/AppShell";
import FleetMap from "@/components/FleetMap";
import RecallTrace from "@/components/RecallTrace";
import { getHealth, getLots, getRecall } from "@/lib/api/client";
import { REGION_LABELS, STATUS_LABELS, STATUS_VARIANTS } from "@/lib/const/fleet";
import { formatNumber } from "@/lib/format";
import { useBehindTheScenes } from "@/lib/context/BehindTheScenesContext";
import { useMounted } from "@/lib/hooks/useMounted";

const CRITICALITY_VARIANT = {
  critical: "red",
  major: "yellow",
  minor: "lightgray",
};

export default function RecallPage() {
  const [health, setHealth] = useState(null);
  const [lots, setLots] = useState([]);
  const [selected, setSelected] = useState(null);
  const [result, setResult] = useState(null);
  const [region, setRegion] = useState(null);
  const [onRentOnly, setOnRentOnly] = useState(false);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const { publish } = useBehindTheScenes();
  const mounted = useMounted();

  useEffect(() => {
    Promise.all([getHealth(), getLots()])
      .then(([healthData, lotData]) => {
        setHealth(healthData);
        setLots(lotData.lots);
        setSelected(lotData.recalled?._id || lotData.lots[0]?._id);
      })
      .catch((err) => setError(err.message));
  }, []);

  useEffect(() => {
    if (!selected) return;
    let cancelled = false;
    setBusy(true);
    getRecall(selected, { region, onRentOnly })
      .then((data) => {
        if (cancelled) return;
        setResult(data);
        publish(data.explain, `Recall ${selected}`);
      })
      .catch((err) => !cancelled && setError(err.message))
      .finally(() => !cancelled && setBusy(false));
    return () => {
      cancelled = true;
    };
  }, [selected, region, onRentOnly, publish]);

  const lot = result?.lot;

  return (
    <AppShell health={health}>
      <div className="flex flex-col gap-4">
        <div>
          <Overline>Supplier advisory</Overline>
          <H3>Recall blast radius</H3>
          <Body className="text-gray-600 text-sm mt-1">
            A supplier flags a production batch. One traversal goes from the batch
            to the parts, to the vehicles carrying them, to the rentals open right
            now, to the customers who need calling.
          </Body>
        </div>

        {error && <Banner variant="danger">{error}</Banner>}

        <Card className="p-3 flex items-center gap-3 flex-wrap">
          <span className="text-xs uppercase tracking-wide text-gray-500">
            Batch
          </span>
          <select
            value={selected || ""}
            onChange={(event) => setSelected(event.target.value)}
            className="border rounded px-2 py-1 text-sm font-mono max-w-[340px]"
          >
            {lots.map((item) => (
              <option key={item._id} value={item._id}>
                {item._id} — {item.partName} ({item.fittedCount} fitted)
              </option>
            ))}
          </select>
          {lot && (
            <Badge variant={CRITICALITY_VARIANT[lot.criticality] || "lightgray"}>
              {lot.criticality}
            </Badge>
          )}
          <div className="flex-1" />
          <Button
            size="small"
            variant={onRentOnly ? "primary" : "default"}
            onClick={() => setOnRentOnly(!onRentOnly)}
          >
            Out with a customer
          </Button>
          {["BR", "NA", "EU"].map((code) => (
            <Button
              key={code}
              size="small"
              variant={region === code ? "primary" : "default"}
              onClick={() => setRegion(region === code ? null : code)}
            >
              {REGION_LABELS[code]}
            </Button>
          ))}
        </Card>

        {result && (
          <>
            <Card className="p-4 flex flex-col gap-3">
              <div className="flex items-baseline justify-between flex-wrap gap-2">
                <Subtitle>{lot.partName} from {lot.supplier}</Subtitle>
                <span className="text-xs text-gray-500">
                  {formatNumber(result.edgesTraversed)} edges traversed in{" "}
                  {result.queryMs} ms
                </span>
              </div>
              <RecallTrace lot={lot} counts={result.counts} />
            </Card>

            {result.counts.unreachable > 0 && (
              <Banner variant="warning">
                <strong>
                  {formatNumber(result.counts.unreachable)} of these vehicles cannot
                  be located.
                </strong>{" "}
                Their trackers are already flagged as unreliable, so the recall list
                includes cars nobody currently knows the position of.
              </Banner>
            )}

            <div className="flex gap-3 flex-wrap">
              {Object.entries(result.byRegion).map(([code, count]) => (
                <Card key={code} className="flex-1 min-w-[150px] p-4">
                  <div className="text-xs uppercase tracking-wide text-gray-500">
                    {REGION_LABELS[code] || code}
                  </div>
                  <div className="text-2xl font-semibold">{formatNumber(count)}</div>
                  <div className="text-xs text-gray-500">vehicles affected</div>
                </Card>
              ))}
              <Card className="flex-1 min-w-[150px] p-4">
                <div className="text-xs uppercase tracking-wide text-gray-500">
                  Part cost exposure
                </div>
                <div className="text-2xl font-semibold">
                  ${formatNumber(Math.round(result.estimatedPartCost))}
                </div>
                <div className="text-xs text-gray-500">
                  at ${lot.unitCost} per unit, parts only
                </div>
              </Card>
            </div>

            <div className="grid grid-cols-1 lg:grid-cols-5 gap-4">
              <Card className="lg:col-span-3 p-3">
                {mounted && (
                  <FleetMap vehicles={result.vehicles} height={460} />
                )}
              </Card>

              <Card className="lg:col-span-2 p-4 flex flex-col gap-2">
                <div className="flex items-baseline justify-between">
                  <Subtitle>Customers to call</Subtitle>
                  <span className="text-xs text-gray-500">
                    {busy ? "..." : `${formatNumber(result.vehicles.length)} shown`}
                  </span>
                </div>
                <div className="overflow-y-auto" style={{ maxHeight: 420 }}>
                  <table className="w-full text-sm">
                    <thead className="sticky top-0 bg-white">
                      <tr className="text-left text-xs uppercase tracking-wide text-gray-500 border-b">
                        <th className="py-2 pr-3">Vehicle</th>
                        <th className="py-2 pr-3">Customer</th>
                        <th className="py-2">Status</th>
                      </tr>
                    </thead>
                    <tbody>
                      {result.vehicles.slice(0, 150).map((vehicle) => (
                        <tr key={vehicle.plate} className="border-b last:border-0">
                          <td className="py-2 pr-3">
                            <Link
                              href={`/vehicle/${vehicle.plate}`}
                              className="font-mono text-xs no-underline"
                              style={{ color: palette.black }}
                            >
                              {vehicle.plate}
                            </Link>
                            <div className="text-xs text-gray-500">
                              {vehicle.make} {vehicle.model}
                            </div>
                          </td>
                          <td className="py-2 pr-3 text-xs">
                            {vehicle.customer || (
                              <span className="text-gray-400">At the depot</span>
                            )}
                            {vehicle.contract && (
                              <div className="font-mono text-[10px] text-gray-400">
                                {vehicle.contract}
                              </div>
                            )}
                          </td>
                          <td className="py-2">
                            <div className="flex flex-col gap-1 items-start">
                              <Badge
                                variant={STATUS_VARIANTS[vehicle.status] || "lightgray"}
                              >
                                {STATUS_LABELS[vehicle.status] || vehicle.status}
                              </Badge>
                              {vehicle.trust === "unreliable" && (
                                <Badge variant="red">cannot locate</Badge>
                              )}
                            </div>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </Card>
            </div>
          </>
        )}
      </div>
    </AppShell>
  );
}
