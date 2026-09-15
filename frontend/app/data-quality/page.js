"use client";

import { useCallback, useEffect, useState } from "react";
import Banner from "@leafygreen-ui/banner";
import Button from "@leafygreen-ui/button";
import { Tabs, Tab } from "@leafygreen-ui/tabs";
import { Body, H3 } from "@leafygreen-ui/typography";

import AppShell from "@/components/AppShell";
import QualityReport from "@/components/QualityReport";
import SignalExplorer from "@/components/SignalExplorer";
import VssTree from "@/components/VssTree";
import SourcePanel from "@/components/SourcePanel";
import {
  getHealth,
  getQualityReport,
  getRegistrySummary,
  getSources,
  runQualityReport,
} from "@/lib/api/client";
import { useBehindTheScenes } from "@/lib/context/BehindTheScenesContext";
import { useMounted } from "@/lib/hooks/useMounted";

const EXPLAIN = [
  {
    title: "Validation generated from the specification",
    capability: "COVESA VSS 6.1.0",
    collection: "signal_registry",
    code:
      '// Every leaf in vss.json carries what it needs to be checked\n' +
      '"RelativeLevel": {\n' +
      '  "datatype": "uint8",\n' +
      '  "type": "sensor",\n' +
      '  "unit": "percent",\n' +
      '  "min": 0, "max": 100\n' +
      "}",
    note:
      "1,382 signals, each with a unit, a datatype and where relevant a range, " +
      "an allowed set and a format pattern. Ranges, enums and patterns become " +
      "validators at build time, so a coolant reading of -999 C fails against a " +
      "bound nobody on this project typed.",
  },
  {
    title: "Nothing is thrown away",
    capability: "Document model",
    collection: "telemetry_raw",
    code:
      "db.telemetry_raw.insertOne({\n" +
      '  assetRef: "ABC1D23",\n' +
      '  sourceType: "CALAMP_TRACKER",\n' +
      '  vssPath: "CurrentLocation.Latitude",\n' +
      "  value: 0.0,\n" +
      "  observedAt: ISODate(...),\n" +
      '  rejected: { reason: "NULL_ISLAND", rule: "position.null_island" },\n' +
      "  raw: { col: \"latitude\", val: \"0.000000\" }\n" +
      "})",
    note:
      "Rejected readings are stored alongside accepted ones with the reason and " +
      "the untouched original payload. Provenance needs the losers as much as " +
      "the winners, and a firmware release that breaks a batch of trackers shows " +
      "up as a reject rate that moves.",
  },
  {
    title: "Position is checked as a pair",
    capability: "Geospatial",
    collection: "telemetry_raw",
    code:
      "// Latitude 0 and longitude 0 each pass their own range check\n" +
      "db.telemetry_raw.aggregate([\n" +
      '  { $match: { "rejected.reason": "NULL_ISLAND" } },\n' +
      '  { $group: { _id: "$assetRef", hits: { $sum: 1 } } },\n' +
      "  { $sort: { hits: -1 } }\n" +
      "])",
    note:
      "VSS keeps latitude and longitude as separate leaves, and both -90 to 90 " +
      "and -180 to 180 accept zero. A position is only meaningful as a pair, " +
      "which is why Null Island needs a rule of its own.",
  },
];

export default function DataQualityPage() {
  const [health, setHealth] = useState(null);
  const [registry, setRegistry] = useState(null);
  const [sources, setSources] = useState([]);
  const [activeSource, setActiveSource] = useState(null);
  const [report, setReport] = useState(null);
  const [tab, setTab] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const { publish } = useBehindTheScenes();
  const mounted = useMounted();

  useEffect(() => {
    publish(EXPLAIN, "Data quality");
    Promise.all([getHealth(), getRegistrySummary(), getSources()])
      .then(([healthData, registryData, sourceData]) => {
        setHealth(healthData);
        setRegistry(registryData);
        setSources(sourceData.sources);
        if (sourceData.sources.length) setActiveSource(sourceData.sources[0].name);
      })
      .catch((err) => setError(err.message));
  }, [publish]);

  useEffect(() => {
    if (!activeSource) return;
    let cancelled = false;
    setBusy(true);
    getQualityReport(activeSource)
      .then((data) => !cancelled && setReport(data))
      .catch((err) => !cancelled && setError(err.message))
      .finally(() => !cancelled && setBusy(false));
    return () => {
      cancelled = true;
    };
  }, [activeSource]);

  const rerun = useCallback(async () => {
    if (!activeSource) return;
    setBusy(true);
    try {
      setReport(await runQualityReport(activeSource));
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }, [activeSource]);

  const source = sources.find((s) => s.name === activeSource);

  return (
    <AppShell health={health}>
      <div className="flex flex-col gap-4">
        <div className="flex items-end justify-between gap-4 flex-wrap">
          <div>
            <H3>Data quality</H3>
            <Body className="text-gray-600 text-sm">
              What arrived from each vendor feed, what was kept, and what could not
              be believed.
            </Body>
          </div>
          <div className="flex items-center gap-2">
            {sources.map((item) => (
              <Button
                key={item.name}
                size="small"
                variant={item.name === activeSource ? "primary" : "default"}
                onClick={() => setActiveSource(item.name)}
              >
                {item.name}
              </Button>
            ))}
            <Button size="small" onClick={rerun} disabled={busy || !activeSource}>
              {busy ? "Running..." : "Re-run"}
            </Button>
          </div>
        </div>

        {error && <Banner variant="danger">{error}</Banner>}

        {mounted ? (
          <Tabs aria-label="Views" selected={tab} onChange={setTab} className="bg-transparent">
            <Tab name="Report">
              <div className="pt-4">
                {report ? (
                  <QualityReport report={report} />
                ) : (
                  <Body className="text-gray-500">
                    {busy ? "Reading the extract..." : "No report yet."}
                  </Body>
                )}
              </div>
            </Tab>
            <Tab name="Mapping">
              <div className="pt-4">
                <SourcePanel source={source} registrySummary={registry} />
              </div>
            </Tab>
            <Tab name="VSS signals">
              <div className="pt-4 grid grid-cols-1 xl:grid-cols-2 gap-4 items-start">
                <SignalExplorer />
                <VssTree />
              </div>
            </Tab>
          </Tabs>
        ) : (
          <Body className="text-gray-500 py-4">Loading...</Body>
        )}
      </div>
    </AppShell>
  );
}
