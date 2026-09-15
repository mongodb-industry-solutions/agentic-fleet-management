/**
 * API client for the Agentic Fleet Management backend.
 */

// Empty string means use relative URLs, which is what the Docker setup relies on.
const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || "";

async function fetchAPI(endpoint, options = {}) {
  const url = `${API_BASE_URL}${endpoint}`;

  try {
    const response = await fetch(url, {
      ...options,
      headers: { "Content-Type": "application/json", ...options.headers },
    });

    if (!response.ok) {
      const error = await response
        .json()
        .catch(() => ({ detail: response.statusText }));
      throw new Error(error.detail || `HTTP ${response.status}`);
    }

    return await response.json();
  } catch (error) {
    console.error(`API Error (${endpoint}):`, error);
    throw error;
  }
}

/** Every vehicle in the fleet, with stats and the Behind the Scenes panels. */
export async function getFleet({
  status, trust, region, depot, needsAttention, limit,
} = {}) {
  const params = new URLSearchParams();
  if (status) params.append("status", status);
  if (trust) params.append("trust", trust);
  if (region) params.append("region", region);
  if (depot) params.append("depot", depot);
  if (needsAttention) params.append("needs_attention", "true");
  // The overview wants the counters only, and 5,000 vehicle documents to read
  // three of them is a slow way to draw a card.
  if (limit) params.append("limit", String(limit));
  const query = params.toString();
  return fetchAPI(`/api/fleet${query ? `?${query}` : ""}`);
}

/** One vehicle: registry, live signals, trips, findings and the raw document. */
export async function getVehicle(plate) {
  return fetchAPI(`/api/fleet/${encodeURIComponent(plate)}`);
}

/** The TBox: classes, relationships and the questions they exist to answer. */
export async function getOntology() {
  return fetchAPI("/api/graph/ontology");
}

/** Production batches, ranked by how widely they were fitted. */
export async function getLots(limit = 40) {
  return fetchAPI(`/api/graph/lots?limit=${limit}`);
}

/** Blast radius for one batch, enriched with live fleet state. */
export async function getRecall(lotCode, { region, onRentOnly } = {}) {
  const params = new URLSearchParams();
  if (region) params.append("region", region);
  if (onRentOnly) params.append("on_rent_only", "true");
  const query = params.toString();
  return fetchAPI(
    `/api/graph/recall/${encodeURIComponent(lotCode)}${query ? `?${query}` : ""}`
  );
}

/** Whether the agent can run, and the batch it would run on. */
export async function getAgentStatus() {
  return fetchAPI("/api/agent/status");
}

/** Record a human's approval of an agent run. Writes nothing to the fleet. */
export async function approveRun(runId) {
  return fetchAPI(`/api/agent/runs/${encodeURIComponent(runId)}/approve`, {
    method: "POST",
  });
}

/** Corpus statistics and a sample of settled claims. */
export async function getClaimStats() {
  return fetchAPI("/api/claims/stats");
}

/** Find settled claims matching a description. */
export async function searchClaims(
  query,
  { panel, damageType, severity, limit = 6, rerank = true } = {}
) {
  const params = new URLSearchParams({ q: query, limit: String(limit) });
  if (panel) params.append("panel", panel);
  if (damageType) params.append("damage_type", damageType);
  if (severity) params.append("severity", severity);
  if (!rerank) params.append("rerank", "false");
  return fetchAPI(`/api/claims/search?${params}`);
}

/** Read a returned vehicle's photo and price it against settled claims. */
export async function assessDamage(file, plate) {
  const body = new FormData();
  body.append("file", file);
  if (plate) body.append("plate", plate);

  const response = await fetch(`${API_BASE_URL}/api/claims/assess`, {
    method: "POST",
    body,
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: response.statusText }));
    throw new Error(error.detail || `HTTP ${response.status}`);
  }
  return response.json();
}

/** URL for a stored claim photograph. */
export function claimImageUrl(claimId) {
  return `${API_BASE_URL}/api/claims/${encodeURIComponent(claimId)}/image`;
}

/** What the collections actually weigh in Atlas, measured with collStats. */
export async function getStorage() {
  return fetchAPI("/api/fleet/storage");
}

/** Rebuild fleet state after the extract or mapping files change. */
/** Health, including the VSS version parsed at startup. */
export async function getHealth() {
  return fetchAPI("/api/health");
}

/** Counts by leaf kind and unit coverage for the loaded VSS release. */
export async function getRegistrySummary() {
  return fetchAPI("/api/registry/summary");
}

/**
 * Rank VSS signals against a phrase. Backs the mapping helper, where somebody
 * types what a vendor column means rather than a path.
 */
/** The VSS branch hierarchy, with leaf counts and what this profile maps. */
export async function getVssTree() {
  return fetchAPI("/api/registry/tree");
}

export async function searchSignals(query, limit = 15) {
  const params = new URLSearchParams({ q: query, limit: String(limit) });
  return fetchAPI(`/api/registry/search?${params}`);
}

/** Every vendor source in the active profile, with its mapping validation. */
export async function getSources() {
  return fetchAPI("/api/sources");
}

/** Re-read mapping files from disk after they have been edited. */
export async function reloadSources() {
  return fetchAPI("/api/sources/reload", { method: "POST" });
}

/** The data quality report for one source, running it if needed. */
export async function getQualityReport(sourceName) {
  return fetchAPI(`/api/quality/${sourceName}`);
}

/** Re-run a report, optionally against a different extract. */
export async function runQualityReport(sourceName, { path, limit } = {}) {
  const params = new URLSearchParams();
  if (path) params.append("path", path);
  if (limit) params.append("limit", String(limit));
  const query = params.toString();
  return fetchAPI(
    `/api/quality/run/${sourceName}${query ? `?${query}` : ""}`,
    { method: "POST" }
  );
}

/** Headline numbers across every source in the profile. */
export async function getQualitySummary() {
  return fetchAPI("/api/quality");
}
