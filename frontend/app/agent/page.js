"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Badge from "@leafygreen-ui/badge";
import Banner from "@leafygreen-ui/banner";
import Button from "@leafygreen-ui/button";
import Card from "@leafygreen-ui/card";
import Checkbox from "@leafygreen-ui/checkbox";
import Icon from "@leafygreen-ui/icon";
import { palette } from "@leafygreen-ui/palette";
import { Body, H3, Overline, Subtitle } from "@leafygreen-ui/typography";

import AgentTrace from "@/components/AgentTrace";
import AppShell from "@/components/AppShell";
import { approveRun, getAgentStatus, getHealth } from "@/lib/api/client";
import { formatNumber } from "@/lib/format";
import { useBehindTheScenes } from "@/lib/context/BehindTheScenesContext";

const URGENCY_VARIANT = {
  immediate: "red",
  today: "yellow",
  "this-week": "blue",
  "next-service": "lightgray",
};

export default function AgentPage() {
  const [health, setHealth] = useState(null);
  const [status, setStatus] = useState(null);
  const [events, setEvents] = useState([]);
  const [result, setResult] = useState(null);
  const [running, setRunning] = useState(false);
  const [approved, setApproved] = useState(false);
  const [error, setError] = useState(null);
  const [useMemory, setUseMemory] = useState(true);
  const source = useRef(null);
  const bottom = useRef(null);
  const { publish } = useBehindTheScenes();

  useEffect(() => {
    Promise.all([getHealth(), getAgentStatus()])
      .then(([healthData, statusData]) => {
        setHealth(healthData);
        setStatus(statusData);
        publish(statusData.explain, "Recall response agent");
      })
      .catch((err) => setError(err.message));
    return () => source.current?.close();
  }, [publish]);

  useEffect(() => {
    // Tokens arrive many times a second, and smooth scrolling each one fights
    // itself. Jump instead.
    bottom.current?.scrollIntoView({ block: "end" });
  }, [events]);

  const run = useCallback(() => {
    const lot = status?.recalledLot?._id;
    if (!lot) return;

    setEvents([]);
    setResult(null);
    setApproved(false);
    setError(null);
    setRunning(true);

    const stream = new EventSource(
      `/api/agent/recall/${encodeURIComponent(lot)}/stream` +
        `?investigations=6&memory=${useMemory}`
    );
    source.current = stream;

    stream.onmessage = (message) => {
      const event = JSON.parse(message.data);
      if (event.type === "done") {
        setResult(event);
        setRunning(false);
        stream.close();
        return;
      }
      if (event.type === "error") {
        setError(event.message);
        setRunning(false);
        stream.close();
        return;
      }
      if (event.type === "start") return;

      // Text arrives token by token. The first delta opens a block and the rest
      // append to it, so a paragraph types itself rather than appearing whole.
      if (event.type === "text_delta") {
        setEvents((previous) => {
          const last = previous[previous.length - 1];
          if (last?.type === "text" && last.id === event.id) {
            return [
              ...previous.slice(0, -1),
              { ...last, content: last.content + event.content },
            ];
          }
          return [
            ...previous,
            { type: "text", id: event.id, content: event.content },
          ];
        });
        return;
      }

      // The real call, once its arguments finish streaming, replaces the row
      // that went up when the model started writing them.
      if (event.type === "tool_call") {
        setEvents((previous) => {
          const index = previous.findIndex(
            (item) => item.type === "tool_pending" && item.name === event.name
          );
          if (index === -1) return [...previous, event];
          const next = [...previous];
          next[index] = event;
          return next;
        });
        return;
      }

      // A finished investigation replaces the pending row for that vehicle.
      if (event.type === "investigation") {
        setEvents((previous) => {
          const index = previous.findIndex(
            (item) =>
              item.type === "investigation_pending" && item.plate === event.plate
          );
          if (index === -1) return [...previous, event];
          const next = [...previous];
          next[index] = event;
          return next;
        });
        return;
      }

      if (event.type !== "text_start" && event.type !== "text_end") {
        setEvents((previous) => [...previous, event]);
      }
    };

    stream.onerror = () => {
      setRunning(false);
      stream.close();
    };
  }, [status, useMemory]);

  const approve = async () => {
    if (!result?.runId) return;
    await approveRun(result.runId);
    setApproved(true);
  };

  const lot = status?.recalledLot;

  return (
    <AppShell health={health}>
      <div className="flex flex-col gap-4">
        <div className="flex items-end justify-between gap-4 flex-wrap">
          <div>
            <Overline>Agent</Overline>
            <H3>Recall response</H3>
            <Body className="text-gray-600 text-sm mt-1">
              A supplier flags a batch. The agent scopes it, checks whether
              failures have already started, decides what each group of vehicles
              needs, and investigates the ones nobody can locate.
            </Body>
          </div>
          <div className="flex gap-2 items-center flex-wrap">
            {status && (
              <>
                <Badge variant="darkgray">sonnet-4-5 supervisor</Badge>
                <Badge variant="lightgray">haiku-4-5 investigators</Badge>
              </>
            )}
          </div>
        </div>

        {error && <Banner variant="danger">{error}</Banner>}
        {status && !status.available && (
          <Banner variant="warning">
            No AWS credentials for Bedrock. Add <code>AWS_PROFILE</code> to
            backend/.env to run the agent.
          </Banner>
        )}

        <Card className="p-4 flex items-center gap-4 flex-wrap">
          <div className="flex-1 min-w-[280px]">
            <div className="text-xs uppercase tracking-wide text-gray-500">
              Supplier advisory
            </div>
            {lot ? (
              <div className="text-sm mt-0.5">
                <span className="font-mono">{lot._id}</span>
                <span className="text-gray-600">
                  {" "}&middot; {lot.partName} from {lot.supplier}
                </span>
                <Badge variant="red" className="ml-2">
                  {lot.criticality}
                </Badge>
              </div>
            ) : (
              <Body className="text-sm text-gray-500">
                No flagged batch. Run scripts/load_graph.py first.
              </Body>
            )}
          </div>
          <div className="flex items-center gap-3 flex-wrap">
            <Checkbox
              checked={useMemory}
              disabled={running}
              onChange={(event) => setUseMemory(event.target.checked)}
              label="Use memory"
              description={
                status?.memory
                  ? `${status.memory.procedures} procedures, ` +
                    `${status.memory.taxonomy} terms, ` +
                    `${status.memory.episodes} past conclusions`
                  : "SOP, vocabulary and past conclusions from MongoDB"
              }
            />
            <Button
              variant="primary"
              leftGlyph={<Icon glyph="Play" />}
              onClick={run}
              disabled={running || !lot || !status?.available}
            >
              {running ? "Working..." : "Run the agent"}
            </Button>
          </div>
        </Card>

        <Card className="p-4">
          <Subtitle>What the rules already did</Subtitle>
          <Body className="text-gray-600 text-sm mt-1">
            Scoping the recall is a graph traversal. Deciding which vehicles
            cannot be located is a field on a document. Neither needs a model,
            and the trace labels every step so the agent is not credited with
            work it did not do.
          </Body>
        </Card>

        {(events.length > 0 || running) && (
          <Card className="p-4">
            <div className="flex items-baseline justify-between">
              <Subtitle>Trace</Subtitle>
              {running && (
                <span className="text-xs text-gray-500">running...</span>
              )}
            </div>
            <div className="mt-3">
              <AgentTrace events={events} />
              <div ref={bottom} />
            </div>
          </Card>
        )}

        {result && (
          <>
            <div className="flex gap-3 flex-wrap">
              {Object.entries(result.conclusions || {}).map(([key, count]) => (
                <Card key={key} className="flex-1 min-w-[150px] p-4">
                  <div className="text-xs uppercase tracking-wide text-gray-500">
                    {key.replace(/_/g, " ")}
                  </div>
                  <div className="text-2xl font-semibold">{formatNumber(count)}</div>
                  <div className="text-xs text-gray-500">vehicles investigated</div>
                </Card>
              ))}
              <Card className="flex-1 min-w-[150px] p-4">
                <div className="text-xs uppercase tracking-wide text-gray-500">
                  Elapsed
                </div>
                <div className="text-2xl font-semibold">{result.seconds}s</div>
                <div className="text-xs text-gray-500">start to proposal</div>
              </Card>
            </div>

            <Card className="p-4">
              <div className="flex items-start justify-between gap-4 flex-wrap">
                <div>
                  <Subtitle>Proposed actions</Subtitle>
                  <Body className="text-gray-600 text-sm mt-1">
                    Nothing is executed. Approving records the decision against
                    the run and writes nothing to the fleet.
                  </Body>
                </div>
                <Button
                  variant={approved ? "default" : "primary"}
                  onClick={approve}
                  disabled={approved}
                  leftGlyph={<Icon glyph={approved ? "Checkmark" : "Note"} />}
                >
                  {approved ? "Approved" : "Approve plan"}
                </Button>
              </div>

              <div className="flex flex-col gap-2 mt-3">
                {(result.proposals || []).map((action, index) => (
                  <div
                    key={index}
                    className="flex items-start gap-3 p-3 rounded border"
                    style={{ borderColor: palette.gray.light2 }}
                  >
                    <Badge variant={URGENCY_VARIANT[action.urgency] || "lightgray"}>
                      {action.urgency}
                    </Badge>
                    <div className="flex-1">
                      <div className="text-sm font-medium">
                        {action.cohort}
                        {action.vehicleCount != null && (
                          <span className="text-gray-500 font-normal">
                            {" "}&middot; {formatNumber(action.vehicleCount)} vehicles
                          </span>
                        )}
                      </div>
                      <div className="text-sm text-gray-700 mt-0.5">
                        {action.action}
                      </div>
                      <div className="text-xs text-gray-500 mt-1">
                        {action.reason}
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            </Card>
          </>
        )}
      </div>
    </AppShell>
  );
}
