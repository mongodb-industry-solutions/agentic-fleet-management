"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Badge from "@leafygreen-ui/badge";
import Button from "@leafygreen-ui/button";
import Card from "@leafygreen-ui/card";
import Icon from "@leafygreen-ui/icon";
import { palette } from "@leafygreen-ui/palette";
import { Body, Subtitle } from "@leafygreen-ui/typography";

import { getVssTree } from "@/lib/api/client";
import { formatNumber } from "@/lib/format";

/**
 * The whole VSS tree, drawn from the spec this instance actually loaded.
 *
 * COVESA publishes a diagram of the hierarchy, but a picture of somebody else's
 * tree says nothing about this fleet. Reading vss.json gives the same shape with
 * real leaf counts, and marking the paths this profile maps shows how small a
 * slice of a 1,382 signal standard any one operator needs.
 */

const DOCS = "https://covesa.github.io/vehicle_signal_specification/introduction/";

const KIND_VARIANT = {
  sensor: "blue",
  actuator: "red",
  attribute: "lightgray",
};

function Leaf({ node, depth }) {
  return (
    <div
      className="flex items-baseline gap-2 py-[3px] pr-2 rounded"
      style={{
        paddingLeft: depth * 14 + 22,
        backgroundColor: node.mapped ? palette.green.light3 : "transparent",
      }}
    >
      <span
        className="font-mono text-[12px] truncate"
        style={{
          color: node.mapped ? palette.green.dark2 : palette.gray.dark2,
          fontWeight: node.mapped ? 600 : 400,
        }}
        title={node.description}
      >
        {node.name}
      </span>

      <Badge variant={KIND_VARIANT[node.kind] || "lightgray"}>{node.kind}</Badge>
      {node.unit && (
        <span className="text-[11px] text-gray-500 font-mono">{node.unit}</span>
      )}
      <span className="text-[11px] text-gray-400 font-mono">{node.datatype}</span>

      {node.mapped && <Badge variant="green">mapped</Badge>}
    </div>
  );
}

function Node({ node, depth, open, onToggle, onlyMapped }) {
  if (node.kind !== "branch") {
    return <Leaf node={node} depth={depth} />;
  }

  // With the filter on, a branch that leads nowhere useful is not drawn.
  if (onlyMapped && !node.mappedCount) return null;

  const expanded = !!open[node.path];
  const children = onlyMapped
    ? node.children.filter((c) =>
        c.kind === "branch" ? c.mappedCount > 0 : c.mapped
      )
    : node.children;

  return (
    <div>
      <button
        type="button"
        onClick={() => onToggle(node.path)}
        className="w-full flex items-center gap-2 py-[3px] pr-2 rounded text-left hover:bg-gray-100"
        style={{
          paddingLeft: depth * 14 + 2,
          backgroundColor: node.mappedCount
            ? palette.green.light3
            : "transparent",
        }}
      >
        <Icon
          glyph={expanded ? "CaretDown" : "CaretRight"}
          size="small"
          fill={palette.gray.dark1}
        />
        <span
          className="font-mono text-[13px] flex-1 truncate"
          style={{
            color: node.mappedCount ? palette.green.dark2 : palette.gray.dark3,
            fontWeight: 600,
          }}
        >
          {node.name}
        </span>

        {node.mappedCount > 0 && (
          <Badge variant="green">{node.mappedCount} used</Badge>
        )}
        <span className="text-[11px] text-gray-500 tabular-nums w-12 text-right">
          {formatNumber(node.leaves)}
        </span>
      </button>

      {expanded &&
        children.map((child) => (
          <Node
            key={child.path}
            node={child}
            depth={depth + 1}
            open={open}
            onToggle={onToggle}
            onlyMapped={onlyMapped}
          />
        ))}
    </div>
  );
}

/** Every branch path that leads to a mapped signal, so they can start open. */
function branchesWithMapped(node, into = {}) {
  if (node.kind !== "branch") return into;
  if (node.mappedCount > 0 && node.path) into[node.path] = true;
  for (const child of node.children) branchesWithMapped(child, into);
  return into;
}

function allBranches(node, into = {}) {
  if (node.kind !== "branch") return into;
  if (node.path) into[node.path] = true;
  for (const child of node.children) allBranches(child, into);
  return into;
}

export default function VssTree() {
  const [data, setData] = useState(null);
  const [open, setOpen] = useState({});
  const [onlyMapped, setOnlyMapped] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    getVssTree()
      .then((payload) => {
        setData(payload);
        // The branches this profile touches start open, since they are the point.
        setOpen(branchesWithMapped(payload.tree));
      })
      .catch((err) => setError(err.message));
  }, []);

  const toggle = useCallback(
    (path) => setOpen((previous) => ({ ...previous, [path]: !previous[path] })),
    []
  );

  const roots = data?.tree?.children || [];
  const expandAll = () => setOpen(allBranches(data.tree));
  const collapseAll = () => setOpen({});

  const stats = useMemo(
    () =>
      data
        ? [
            { value: formatNumber(data.leaves), label: "signals in the spec" },
            { value: formatNumber(data.branches), label: "branches" },
            {
              value: formatNumber(data.mappedLeaves),
              label: "used by this demo",
              green: true,
            },
          ]
        : [],
    [data]
  );

  return (
    <Card className="p-4">
      <div className="flex items-baseline justify-between gap-3 flex-wrap">
        <Subtitle>The VSS tree</Subtitle>
        {data && <Badge variant="darkgray">VSS {data.version}</Badge>}
      </div>

      <Body className="text-gray-600 text-sm mt-1 leading-relaxed">
        COVESA defines one name and one unit for every signal a vehicle can
        report, arranged as a tree under <code>Vehicle</code>. Both vendor feeds
        are mapped onto these paths on the way in, which is what lets readings
        from a factory system and an aftermarket tracker be compared at all.
      </Body>

      {error && (
        <Body className="text-sm mt-3" style={{ color: palette.red.dark2 }}>
          {error}
        </Body>
      )}

      {data && (
        <>
          <div
            className="flex gap-6 mt-3 pb-3 border-b"
            style={{ borderColor: palette.gray.light2 }}
          >
            {stats.map((stat) => (
              <div key={stat.label}>
                <div
                  className="text-lg font-semibold tabular-nums leading-tight"
                  style={stat.green ? { color: palette.green.dark2 } : undefined}
                >
                  {stat.value}
                </div>
                <div className="text-xs text-gray-500">{stat.label}</div>
              </div>
            ))}
          </div>

          <div className="flex items-center gap-2 mt-3 flex-wrap">
            <Button size="xsmall" onClick={expandAll}>
              Expand all
            </Button>
            <Button size="xsmall" onClick={collapseAll}>
              Collapse all
            </Button>
            <Button
              size="xsmall"
              variant={onlyMapped ? "primary" : "default"}
              onClick={() => setOnlyMapped((value) => !value)}
            >
              {onlyMapped ? "Showing mapped only" : "Show mapped only"}
            </Button>
            <div className="flex-1" />
            <span className="text-[11px] uppercase tracking-wide text-gray-500">
              leaves
            </span>
          </div>

          <div
            className="mt-2 max-h-[560px] overflow-y-auto border-t pt-2"
            style={{ borderColor: palette.gray.light2 }}
          >
            <div className="font-mono text-[13px] font-semibold text-gray-500 pl-1 pb-1">
              Vehicle
            </div>
            {roots.map((node) => (
              <Node
                key={node.path}
                node={node}
                depth={0}
                open={open}
                onToggle={toggle}
                onlyMapped={onlyMapped}
              />
            ))}
          </div>

          <a
            href={DOCS}
            target="_blank"
            rel="noreferrer"
            className="text-xs mt-3 inline-block"
            style={{ color: palette.blue.base }}
          >
            COVESA Vehicle Signal Specification
          </a>
        </>
      )}
    </Card>
  );
}
