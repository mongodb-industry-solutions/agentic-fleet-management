"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Badge from "@leafygreen-ui/badge";
import Banner from "@leafygreen-ui/banner";
import Button from "@leafygreen-ui/button";
import Card from "@leafygreen-ui/card";
import Icon from "@leafygreen-ui/icon";
import TextInput from "@leafygreen-ui/text-input";
import { palette } from "@leafygreen-ui/palette";
import { Body, H3, Overline, Subtitle } from "@leafygreen-ui/typography";

import AppShell from "@/components/AppShell";
import ClaimResults from "@/components/ClaimResults";
import { assessDamage, getClaimStats, getHealth, searchClaims } from "@/lib/api/client";
import { formatNumber } from "@/lib/format";
import { useBehindTheScenes } from "@/lib/context/BehindTheScenesContext";

const EXAMPLES = [
  "shattered windscreen",
  "deep scratch along a door panel",
  "dented rear bumper with paint damage",
  "kerbed alloy wheel",
  "cracked headlamp lens",
];

function Money({ label, value, hint, strong }) {
  return (
    <div>
      <div className="text-xs uppercase tracking-wide text-gray-500">{label}</div>
      <div
        className={strong ? "text-3xl font-semibold" : "text-xl font-medium"}
        style={{ color: strong ? palette.green.dark2 : palette.black }}
      >
        ${formatNumber(Math.round(value || 0))}
      </div>
      {hint && <div className="text-xs text-gray-500">{hint}</div>}
    </div>
  );
}

export default function ClaimsPage() {
  const [health, setHealth] = useState(null);
  const [stats, setStats] = useState(null);
  const [query, setQuery] = useState("shattered windscreen");
  const [result, setResult] = useState(null);
  const [assessment, setAssessment] = useState(null);
  const [compare, setCompare] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const fileInput = useRef(null);
  const { publish } = useBehindTheScenes();

  useEffect(() => {
    Promise.all([getHealth(), getClaimStats()])
      .then(([healthData, statsData]) => {
        setHealth(healthData);
        setStats(statsData);
      })
      .catch((err) => setError(err.message));
  }, []);

  const runSearch = useCallback(
    async (text) => {
      if (!text?.trim()) return;
      setBusy(true);
      setError(null);
      setAssessment(null);
      try {
        const data = await searchClaims(text, { limit: 6 });
        setResult(data);
        publish(data.explain, "Damage search");
      } catch (err) {
        setError(err.message);
      } finally {
        setBusy(false);
      }
    },
    [publish]
  );

  useEffect(() => {
    runSearch("shattered windscreen");
  }, [runSearch]);

  const onUpload = async (event) => {
    const file = event.target.files?.[0];
    if (!file) return;
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      const data = await assessDamage(file);
      setAssessment(data);
      publish(data.explain, "Damage assessment");
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  const precedent = assessment?.assessedCost || result?.precedent;
  const matches = assessment?.matches || result?.results;

  return (
    <AppShell health={health}>
      <div className="flex flex-col gap-4">
        <div className="flex items-end justify-between gap-4 flex-wrap">
          <div>
            <Overline>Claims</Overline>
            <H3>Damage assessment</H3>
            <Body className="text-gray-600 text-sm mt-1">
              A car comes back with a mark on it. Find the closest matches among
              claims already settled, and price this one against what those
              actually cost.
            </Body>
          </div>
          {stats && (
            <div className="flex gap-2 items-center flex-wrap">
              <Badge variant="darkgray">
                {formatNumber(stats.stats.claims)} settled claims
              </Badge>
              <Badge variant="lightgray">{stats.models.embedding}</Badge>
              <Badge variant="lightgray">{stats.models.rerank}</Badge>
            </div>
          )}
        </div>

        {error && <Banner variant="danger">{error}</Banner>}

        <Card className="p-4 flex flex-col gap-3">
          <div className="flex gap-2 items-end flex-wrap">
            <div className="flex-1 min-w-[280px]">
              <TextInput
                label="Describe the damage"
                aria-label="Describe the damage"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                onKeyDown={(event) => event.key === "Enter" && runSearch(query)}
              />
            </div>
            <Button
              variant="primary"
              onClick={() => runSearch(query)}
              disabled={busy}
            >
              {busy ? "Searching..." : "Search"}
            </Button>
            <Button
              leftGlyph={<Icon glyph="Upload" />}
              onClick={() => fileInput.current?.click()}
              disabled={busy}
            >
              Assess a photo
            </Button>
            <input
              ref={fileInput}
              type="file"
              accept="image/*"
              className="hidden"
              onChange={onUpload}
            />
          </div>

          <div className="flex gap-1.5 flex-wrap">
            {EXAMPLES.map((example) => (
              <button
                key={example}
                type="button"
                onClick={() => {
                  setQuery(example);
                  runSearch(example);
                }}
                className="px-2.5 py-1 rounded-full text-xs border bg-white hover:border-gray-400"
                style={{ borderColor: palette.gray.light1, color: palette.gray.dark1 }}
              >
                {example}
              </button>
            ))}
          </div>
        </Card>

        {assessment?.labels && (
          <Card className="p-4">
            <Subtitle>What the model read off the photograph</Subtitle>
            <div className="flex gap-2 flex-wrap mt-2">
              <Badge variant="red">{assessment.labels.damageType}</Badge>
              <Badge variant="darkgray">{assessment.labels.panel}</Badge>
              <Badge variant="yellow">{assessment.labels.severity}</Badge>
              <Badge variant="lightgray">{assessment.labels.angle}</Badge>
              {assessment.labels.bodyColour && (
                <Badge variant="lightgray">{assessment.labels.bodyColour}</Badge>
              )}
            </div>
            <Body className="text-sm text-gray-700 mt-2">
              {assessment.labels.description}
            </Body>
          </Card>
        )}

        {precedent?.basis > 0 && (
          <Card className="p-4">
            <div className="flex items-start justify-between gap-6 flex-wrap">
              <div className="flex gap-8 flex-wrap items-end">
                <Money
                  label="Assessed from precedent"
                  value={precedent.median}
                  hint={`median of ${precedent.basis} comparable claims`}
                  strong
                />
                <Money label="Parts" value={precedent.medianParts} />
                <Money label="Labour" value={precedent.medianLabour} />
                <div>
                  <div className="text-xs uppercase tracking-wide text-gray-500">
                    Range
                  </div>
                  <div className="text-xl font-medium">
                    ${formatNumber(Math.round(precedent.low))} to $
                    {formatNumber(Math.round(precedent.high))}
                  </div>
                </div>
              </div>
            </div>
            <Body className="text-xs text-gray-500 mt-3">
              Settlement figures in this corpus are generated, because the source
              images carry no cost data. The method is the point: an estimate
              grounded in your own settled claims is defensible when a renter
              disputes it, and a model&apos;s guess is not.
            </Body>
          </Card>
        )}

        {result?.reranked && (
          <div className="flex items-center gap-3">
            <Button size="small" onClick={() => setCompare(!compare)}>
              {compare ? "Hide" : "Show"} what vector search alone returned
            </Button>
            {result.timings && (
              <span className="text-xs text-gray-500">
                embed {result.timings.embedMs} ms, search{" "}
                {result.timings.vectorSearchMs} ms, rerank{" "}
                {result.timings.rerankMs} ms
              </span>
            )}
          </div>
        )}

        {compare && result?.vectorOnly && (
          <Card className="p-4">
            <Subtitle>Vector search only</Subtitle>
            <Body className="text-gray-600 text-sm mt-1 mb-3">
              The first pass is recall. It is cheap and approximate, and it will
              return things that merely look similar.
            </Body>
            <ClaimResults results={result.vectorOnly} showRanks={false} compact />
          </Card>
        )}

        <Card className="p-4">
          <div className="flex items-baseline justify-between">
            <Subtitle>
              {assessment ? "Comparable settled claims" : "Matches"}
            </Subtitle>
            {result?.reranked && (
              <span className="text-xs text-gray-500">
                reordered by {stats?.models.rerank}
              </span>
            )}
          </div>
          <div className="mt-3">
            <ClaimResults results={matches} />
          </div>
        </Card>
      </div>
    </AppShell>
  );
}
