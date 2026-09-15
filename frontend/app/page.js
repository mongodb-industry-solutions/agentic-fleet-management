"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import Badge from "@leafygreen-ui/badge";
import Button from "@leafygreen-ui/button";
import Card from "@leafygreen-ui/card";
import Icon from "@leafygreen-ui/icon";
import { palette } from "@leafygreen-ui/palette";
import { Body, H2, H3, Overline } from "@leafygreen-ui/typography";

import AppShell from "@/components/AppShell";
import { formatNumber } from "@/lib/format";
import {
  getAgentStatus,
  getClaimStats,
  getFleet,
  getHealth,
  getQualitySummary,
} from "@/lib/api/client";

/**
 * The overview, which is the demo's argument in four steps.
 *
 * A map on the landing page asks the viewer to work out for themselves what
 * they are looking at. Four claims, each carrying the number that backs it and
 * a way through to the screen that proves it, tells them instead.
 */

function Stat({ value, label }) {
  return (
    <div>
      <div className="text-xl font-semibold tabular-nums leading-tight">
        {value}
      </div>
      <div className="text-xs text-gray-500">{label}</div>
    </div>
  );
}

function Step({ index, title, children, stats, actions, accent }) {
  return (
    <Card className="p-0 overflow-hidden">
      <div className="flex">
        <div
          className="w-1.5 shrink-0"
          style={{ backgroundColor: accent }}
          aria-hidden
        />
        <div className="flex-1 p-5">
          <div className="flex items-start gap-4 flex-wrap">
            <div className="flex-1 min-w-[420px]">
              <Overline style={{ color: accent }}>Step {index}</Overline>
              <H3 className="mt-0.5">{title}</H3>
              <Body className="text-gray-600 text-sm mt-2 max-w-[92ch] leading-relaxed">
                {children}
              </Body>
            </div>

            <div className="flex gap-7 items-start pt-1 shrink-0">
              {stats.map((stat) => (
                <Stat key={stat.label} {...stat} />
              ))}
            </div>
          </div>

          <div className="flex gap-2 mt-4 flex-wrap">
            {actions.map((action, position) => (
              <Link key={action.href} href={action.href} className="no-underline">
                <Button
                  size="small"
                  variant={position === 0 ? "primary" : "default"}
                  rightGlyph={<Icon glyph="ArrowRight" />}
                >
                  {action.label}
                </Button>
              </Link>
            ))}
          </div>
        </div>
      </div>
    </Card>
  );
}

const DASH = "—";

export default function OverviewPage() {
  const [health, setHealth] = useState(null);
  const [quality, setQuality] = useState(null);
  const [fleet, setFleet] = useState(null);
  const [claims, setClaims] = useState(null);
  const [agent, setAgent] = useState(null);

  useEffect(() => {
    getHealth().then(setHealth).catch(() => {});
    getQualitySummary().then(setQuality).catch(() => {});
    getFleet({ limit: 1 }).then(setFleet).catch(() => {});
    getClaimStats().then(setClaims).catch(() => {});
    getAgentStatus().then(setAgent).catch(() => {});
  }, []);

  const totals = quality?.totals;
  const fleetStats = fleet?.stats;
  const claimStats = claims?.stats;
  const lot = agent?.recalledLot;

  const number = (value) => (value == null ? DASH : formatNumber(value));

  return (
    <AppShell health={health}>
      <div className="flex flex-col gap-4">
        <div className="pt-2 pb-1">
          <H2>Next-gen agentic fleet management, powered by MongoDB</H2>
          <Body className="text-gray-600 mt-2 max-w-[104ch] leading-relaxed">
            This solution shows {formatNumber(fleetStats?.total ?? 5000)}{" "}
            connected vehicles across Brazil, North America, and Europe
            reporting data through two separate feeds, and those feeds disagree
            on units, on schema, and sometimes on whether a reading is even
            real. The fleet management software runs the four steps below in
            sequence, to clean up the data, comply it to Vehicle Signal
            Specification (VSS) schema and then power agentic workflows on it.
          </Body>
        </div>

        <Step
          index={1}
          accent={palette.red.base}
          title="Catching bad sensor data stream"
          stats={[
            { value: number(totals?.observationsRead), label: "readings ingested" },
            { value: number(totals?.observationsRejected), label: "rejected" },
            {
              value:
                totals == null
                  ? DASH
                  : `${(totals.rejectRate * 100).toFixed(2)}%`,
              label: "reject rate",
            },
          ]}
          actions={[{ href: "/data-quality", label: "See what was rejected" }]}
        >
          Every incoming reading gets checked against the COVESA VSS definition
          for that signal, and the rule that catches a bad reading gets logged
          alongside the rejection. This enables traceability and auditability.
          As an example, we might be receiving -999 degrees from an unplugged
          coolant sensor because the firmware adds a placeholder value or a
          tracker that loses its GPS fix parks a car at zero degrees north, zero
          degrees east, out in the ocean off West Africa.
        </Step>

        <Step
          index={2}
          accent={palette.blue.base}
          title="Resolving conflicting readings from two systems by data fusion and trust scoring"
          stats={[
            { value: number(fleetStats?.total), label: "vehicles" },
            {
              value: number(fleetStats?.byTrust?.good),
              label: "trusted position",
            },
            {
              value: number(
                (fleetStats?.byTrust?.degraded ?? 0) +
                  (fleetStats?.byTrust?.unreliable ?? 0) || null
              ),
              label: "degraded or worse",
            },
          ]}
          actions={[
            { href: "/fleet", label: "Open the live fleet" },
            { href: "/data-quality", label: "How the score is built" },
          ]}
        >
          Many rental vehicles with a factory telematics connection also carry
          a retrofitted tracker installed after the fact, so fuel level, GPS
          position, and ignition state may arrive twice, and the two readings
          sometimes don&apos;t match. A trust score decides which reading wins, based
          on how reliable each source has been for that specific signal, how
          recent the reading is, and how cleanly it passed validation on the way
          in. The factory system wins on fuel almost every time, since the
          tracker infers fuel indirectly while the factory system reads it
          straight off the tank sensor. The tracker wins on position almost
          every time, since it updates far more often than the factory system
          does. Either way, you can see the exact reason behind the decision
          instead of just a final number.
        </Step>

        <Step
          index={3}
          accent={palette.purple.base}
          title="Searching parts and damage together with MongoDB"
          stats={[
            { value: number(claimStats?.claims), label: "damage claims" },
            {
              value: claimStats == null ? DASH : `$${number(Math.round(claimStats.avgCost))}`,
              label: "average repair",
            },
            { value: number(fleetStats?.openFindings), label: "open findings" },
          ]}
          actions={[
            { href: "/claims", label: "Search the damage photos" },
            { href: "/recall", label: "Trace a part batch" },
          ]}
        >
          When a supplier flags a bad batch of brake calipers, finding every
          vehicle that received one means tracing the parts tree back through
          each vehicle&apos;s service history. Finding which of those vehicles
          already show damage matching the failure means searching the damage
          photos directly, and a search for a shattered tail light needs to rank
          broken glass above a dent sitting two panels over, even though both
          show up as similar shapes in the image data. This step can be
          performed using MongoDB search features such as semantic search and
          reranking.
        </Step>

        <Step
          index={4}
          accent={palette.green.dark1}
          title="Agentic workflows actioning on a recall notice"
          stats={[
            {
              value: lot ? lot.criticality : DASH,
              label: lot ? "flagged batch" : "no batch flagged",
            },
            {
              value: agent?.available ? "ready" : "no credentials",
              label: "supervisor",
            },
            { value: number(agent?.recentRuns?.length), label: "runs recorded" },
          ]}
          actions={[{ href: "/agent", label: "Run the recall response" }]}
        >
          A recall notice comes in and someone has a few hours to turn it into a
          work plan. The agent pulls the affected batch, checks whether any of
          those vehicles already show signs of the failure, and sorts the rest
          into groups based on what each group actually needs done. For any
          vehicle it cannot currently locate, it opens a small investigation to
          track it down. The agent proposes the plan, a supervisor reviews and
          approves it and only then the actions are performed and logged in
          MongoDB.
        </Step>

        <Card className="p-4">
          <div className="flex items-center gap-3 flex-wrap">
            <Badge variant="darkgray">One database</Badge>
            <Body className="text-sm text-gray-600 flex-1 min-w-[320px]">
              Time series telemetry, the validated rejects, the parts graph, the
              damage photographs and their embeddings, and the agent&apos;s runs
              all sit in MongoDB. The button in the header opens the query
              behind whatever is on screen.
            </Body>
            {health && (
              <Badge variant="lightgray">
                VSS {health.vssVersion}, {formatNumber(health.signals)} signals
              </Badge>
            )}
          </div>
        </Card>
      </div>
    </AppShell>
  );
}
