import { palette } from "@leafygreen-ui/palette";

/**
 * Presentation for rejection reasons, kept away from the map component so that
 * importing a colour does not drag Leaflet into server rendering.
 *
 * Only reasons that describe a position get a colour, since those are the ones
 * that can be drawn.
 */

export const REASON_COLOURS = {
  NULL_ISLAND: palette.red.base,
  IMPOSSIBLE_MOTION: palette.purple.base,
  NO_GPS_FIX: palette.yellow.dark2,
  COORD_OUT_OF_RANGE: palette.red.light1,
  OUTSIDE_SERVICE_AREA: palette.blue.base,
};

export const REASON_LABELS = {
  MISSING_IDENTITY: "No vehicle identifier",
  MISSING_TIMESTAMP: "No timestamp",
  BAD_TIMESTAMP: "Unparseable timestamp",
  TYPE_COERCION_FAILED: "Wrong datatype",
  UNIT_CONVERSION_FAILED: "Unit not convertible",
  OUT_OF_RANGE: "Out of range",
  NOT_ALLOWED_VALUE: "Not an allowed value",
  PATTERN_MISMATCH: "Wrong format",
  SENTINEL_VALUE: "Vendor sentinel",
  NO_GPS_FIX: "No GPS fix",
  NULL_ISLAND: "Null Island",
  COORD_OUT_OF_RANGE: "Coordinate out of range",
  OUTSIDE_SERVICE_AREA: "Outside service area",
  IMPOSSIBLE_MOTION: "Impossible motion",
};
