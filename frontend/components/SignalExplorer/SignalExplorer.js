"use client";

import { useEffect, useState } from "react";
import Badge from "@leafygreen-ui/badge";
import Card from "@leafygreen-ui/card";
import TextInput from "@leafygreen-ui/text-input";
import { Body, Subtitle } from "@leafygreen-ui/typography";

import { searchSignals } from "@/lib/api/client";

const KIND_VARIANT = {
  sensor: "green",
  actuator: "blue",
  attribute: "darkgray",
};

/**
 * Search the VSS specification by meaning rather than by path.
 *
 * Finding the right leaf among 1,382 by hand is tedious, so somebody writing a
 * mapping types what a vendor column means and picks from the candidates.
 */
export default function SignalExplorer() {
  const [query, setQuery] = useState("fuel level");
  const [results, setResults] = useState([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!query.trim()) {
      setResults([]);
      return;
    }
    let cancelled = false;
    setLoading(true);
    const timer = setTimeout(async () => {
      try {
        const data = await searchSignals(query, 20);
        if (!cancelled) setResults(data.results);
      } catch {
        if (!cancelled) setResults([]);
      } finally {
        if (!cancelled) setLoading(false);
      }
    }, 200);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [query]);

  return (
    <Card className="p-4 flex flex-col gap-3">
      <div>
        <Subtitle id="signal-search-label">Find a signal</Subtitle>
        <Body className="text-gray-600 text-sm mt-1">
          Describe what a vendor column holds. Everything shown here comes from
          vss.json, including the unit a value has to be converted into and the
          range it has to fall inside.
        </Body>
      </div>

      <TextInput
        // LeafyGreen wants a label or aria-labelledby; aria-label alone trips
        // its accessibility check and prints an error on every render.
        aria-labelledby="signal-search-label"
        placeholder="coolant temperature, odometer, door open"
        value={query}
        onChange={(event) => setQuery(event.target.value)}
      />

      <div className="flex flex-col divide-y max-h-[420px] overflow-y-auto">
        {results.map((signal) => (
          <div key={signal.vssPath} className="py-2">
            <div className="flex items-center gap-2 flex-wrap">
              <span className="font-mono text-xs">{signal.vssPath}</span>
              <Badge variant={KIND_VARIANT[signal.kind] || "lightgray"}>
                {signal.kind}
              </Badge>
              {signal.unit && <Badge variant="lightgray">{signal.unit}</Badge>}
              <span className="text-xs text-gray-500">{signal.datatype}</span>
              {signal.deprecated && <Badge variant="red">deprecated</Badge>}
            </div>
            <div className="text-xs text-gray-600 mt-1">{signal.description}</div>
            {(signal.min !== null || signal.allowed) && (
              <div className="text-xs text-gray-500 mt-0.5">
                {signal.allowed
                  ? `allowed: ${signal.allowed.join(", ")}`
                  : `range: ${signal.min} to ${signal.max}`}
              </div>
            )}
          </div>
        ))}
        {!loading && query.trim() && results.length === 0 && (
          <Body className="text-sm text-gray-500 py-3">
            Nothing matched. VSS is written in English, so a Portuguese column name
            will not match directly.
          </Body>
        )}
      </div>
    </Card>
  );
}
