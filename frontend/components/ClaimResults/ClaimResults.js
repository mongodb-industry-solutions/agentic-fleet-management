"use client";

import Badge from "@leafygreen-ui/badge";
import { palette } from "@leafygreen-ui/palette";

import { claimImageUrl } from "@/lib/api/client";
import { formatNumber } from "@/lib/format";

/**
 * Retrieved claims, with where each one sat before reranking.
 *
 * The rank change is the interesting number. A result that the vector index put
 * at 58 and the cross encoder put at 1 is the whole argument for the second
 * pass, and it is only visible if both orderings are kept.
 */

const SEVERITY_VARIANT = {
  cosmetic: "lightgray",
  minor: "blue",
  moderate: "yellow",
  severe: "red",
};

function RankMove({ from, to }) {
  if (from == null || to == null) return null;
  const moved = from - to;
  if (moved <= 0) return null;
  return (
    <span
      className="text-[10px] font-medium px-1.5 py-0.5 rounded"
      style={{ backgroundColor: palette.green.light3, color: palette.green.dark2 }}
      title={`Vector search ranked this ${from}, the reranker moved it to ${to}`}
    >
      #{from} &rarr; #{to}
    </span>
  );
}

export default function ClaimResults({ results, showRanks = true, compact = false }) {
  if (!results?.length) {
    return (
      <div className="py-8 text-sm text-gray-500 text-center">
        No matching claims.
      </div>
    );
  }

  return (
    <div
      className={`grid gap-3 ${
        compact ? "grid-cols-2 sm:grid-cols-3" : "grid-cols-1 sm:grid-cols-2 lg:grid-cols-3"
      }`}
    >
      {results.map((claim) => (
        <div key={claim._id} className="border rounded-lg overflow-hidden bg-white">
          <div className="relative bg-gray-100" style={{ aspectRatio: "4 / 3" }}>
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src={claimImageUrl(claim._id)}
              alt={claim.labels?.description || claim.damageType}
              className="w-full h-full object-cover"
              loading="lazy"
            />
            {showRanks && (
              <div className="absolute top-1.5 left-1.5 flex gap-1">
                <RankMove from={claim.vectorRank} to={claim.rerankRank} />
              </div>
            )}
          </div>

          <div className="p-2.5 flex flex-col gap-1.5">
            <div className="flex items-center gap-1.5 flex-wrap">
              <Badge variant="darkgray">{claim.damageType}</Badge>
              <Badge variant={SEVERITY_VARIANT[claim.severity] || "lightgray"}>
                {claim.severity}
              </Badge>
            </div>

            {/* The embedded text repeats the labels on purpose, which helps
                retrieval and reads badly. Show the assessor's sentence. */}
            <div className="text-xs text-gray-600 leading-snug line-clamp-2">
              {claim.labels?.description || claim.text}
            </div>

            <div className="flex items-baseline justify-between pt-1 border-t">
              <div>
                <div className="text-[10px] uppercase tracking-wide text-gray-400">
                  Settled
                </div>
                <div className="text-sm font-semibold tabular-nums">
                  ${formatNumber(Math.round(claim.cost?.total || 0))}
                </div>
              </div>
              <div className="text-right">
                <div className="text-[10px] text-gray-400">
                  {claim.panel?.replace(/-/g, " ")}
                </div>
                {claim.rerankScore != null ? (
                  <div className="text-[10px] text-gray-500 tabular-nums">
                    rerank {claim.rerankScore.toFixed(3)}
                  </div>
                ) : (
                  claim.score != null && (
                    <div className="text-[10px] text-gray-500 tabular-nums">
                      score {claim.score.toFixed(3)}
                    </div>
                  )
                )}
              </div>
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}
