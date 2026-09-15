import { palette } from "@leafygreen-ui/palette";

/** Rental status: what an operator scans the map for. */
export const STATUS_COLOURS = {
  on_rent: palette.blue.base,
  available: palette.green.base,
  in_service: palette.gray.base,
  unknown: palette.gray.light1,
};

export const STATUS_LABELS = {
  on_rent: "On rent",
  available: "Available",
  in_service: "In service",
  unknown: "Unknown",
};

export const STATUS_VARIANTS = {
  on_rent: "blue",
  available: "green",
  in_service: "lightgray",
  unknown: "lightgray",
};

/** How much the data about a vehicle can be believed. Drawn as the marker ring. */
export const TRUST_COLOURS = {
  good: palette.gray.light1,
  degraded: palette.yellow.dark2,
  unreliable: palette.red.base,
};

export const TRUST_LABELS = {
  good: "Data good",
  degraded: "Data degraded",
  unreliable: "Data unreliable",
};

export const TRUST_VARIANTS = {
  good: "green",
  degraded: "yellow",
  unreliable: "red",
};

export const SEVERITY_VARIANTS = {
  critical: "red",
  warning: "yellow",
  info: "lightgray",
};

export const ACTIVITY_LABELS = {
  moving: "Moving",
  idling: "Idling",
  parked: "Parked",
  unknown: "No recent report",
};

export const REGION_LABELS = {
  BR: "Brazil",
  NA: "North America",
  EU: "Europe",
};

/** How each feed is described on screen. */
export const SOURCE_LABELS = {
  OEM_CONNECTED: "Manufacturer feed",
  CALAMP_TRACKER: "Aftermarket tracker",
};
