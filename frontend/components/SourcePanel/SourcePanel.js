"use client";

import Badge from "@leafygreen-ui/badge";
import Banner from "@leafygreen-ui/banner";
import Card from "@leafygreen-ui/card";
import { Body, Subtitle } from "@leafygreen-ui/typography";

import { formatNumber } from "@/lib/format";

/**
 * The mapping file, rendered.
 *
 * Naming a VSS path is the whole integration: the target unit, the datatype, the
 * valid range and the allowed values all come from vss.json. This panel shows
 * what a fleet engineer wrote against what the specification says, which is where
 * mistakes become obvious.
 */
export default function SourcePanel({ source, registrySummary }) {
  if (!source) return null;

  const { validation } = source;

  return (
    <div className="flex flex-col gap-4">
      <Card className="p-4">
        <div className="flex items-start justify-between gap-4 flex-wrap">
          <div>
            <Subtitle>{source.name}</Subtitle>
            <Body className="text-gray-600 text-sm mt-1">{source.description}</Body>
          </div>
          <div className="flex gap-2 items-center">
            <Badge variant="darkgray">{source.sourceType}</Badge>
            <Badge variant={validation.ok ? "green" : "red"}>
              {validation.ok ? "Mapping valid" : "Mapping has errors"}
            </Badge>
          </div>
        </div>

        <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 mt-4 text-sm">
          <div>
            <div className="text-xs uppercase tracking-wide text-gray-500">Identity</div>
            <div className="font-mono text-xs mt-1">{source.identity.column}</div>
            <div className="text-xs text-gray-500">{source.identity.kind}</div>
          </div>
          <div>
            <div className="text-xs uppercase tracking-wide text-gray-500">Timestamp</div>
            <div className="font-mono text-xs mt-1">{source.timestampColumn}</div>
            <div className="text-xs text-gray-500">{source.timezone || "UTC"}</div>
          </div>
          <div>
            <div className="text-xs uppercase tracking-wide text-gray-500">Signals</div>
            <div className="text-xs mt-1">{source.signalCount} mapped</div>
          </div>
          <div>
            <div className="text-xs uppercase tracking-wide text-gray-500">
              Specification
            </div>
            <div className="text-xs mt-1">
              COVESA VSS {registrySummary?.version || "?"}
            </div>
            <div className="text-xs text-gray-500">
              {registrySummary ? `${formatNumber(registrySummary.leaves)} signals` : ""}
            </div>
          </div>
        </div>
      </Card>

      {validation.errors.length > 0 && (
        <Banner variant="danger">
          <strong>Ingest is blocked until these are fixed.</strong>
          <ul className="list-disc ml-5 mt-1 text-sm">
            {validation.errors.map((issue, index) => (
              <li key={index}>
                <span className="font-mono text-xs">{issue.column}</span> {issue.message}
              </li>
            ))}
          </ul>
        </Banner>
      )}

      {validation.warnings.length > 0 && (
        <Banner variant="warning">
          <strong>Worth checking.</strong>
          <ul className="list-disc ml-5 mt-1 text-sm">
            {validation.warnings.map((issue, index) => (
              <li key={index}>
                <span className="font-mono text-xs">{issue.column}</span> {issue.message}
              </li>
            ))}
          </ul>
        </Banner>
      )}

      <Card className="p-4">
        <Subtitle>Column mapping</Subtitle>
        <Body className="text-gray-600 text-sm mt-1">
          This is the only file a fleet operator writes to get their own data through
          the pipeline.
        </Body>
        <div className="overflow-x-auto mt-3">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs uppercase tracking-wide text-gray-500 border-b">
                <th className="py-2 pr-4">Vendor column</th>
                <th className="py-2 pr-4">VSS signal</th>
                <th className="py-2 pr-4">Declared unit</th>
                <th className="py-2">Handling</th>
              </tr>
            </thead>
            <tbody>
              {source.signals.map((signal) => (
                <tr key={signal.column} className="border-b last:border-0">
                  <td className="py-2 pr-4 font-mono text-xs">{signal.column}</td>
                  <td className="py-2 pr-4 font-mono text-xs">{signal.vssPath}</td>
                  <td className="py-2 pr-4 text-xs">{signal.declaredUnit || "—"}</td>
                  <td className="py-2">
                    <div className="flex gap-1 flex-wrap">
                      {signal.hasValueMap && <Badge variant="blue">value map</Badge>}
                      {signal.sentinels?.length > 0 && (
                        <Badge variant="yellow">
                          sentinel {signal.sentinels.join(", ")}
                        </Badge>
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
  );
}
