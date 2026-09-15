"use client";

import { useEffect, useMemo } from "react";
import { useRouter } from "next/navigation";
import { CircleMarker, MapContainer, Popup, TileLayer, useMap } from "react-leaflet";
import { palette } from "@leafygreen-ui/palette";

import { STATUS_COLOURS, STATUS_LABELS, TRUST_COLOURS } from "@/lib/const/fleet";
import { REASON_COLOURS, REASON_LABELS } from "@/lib/const/rejectReasons";

/**
 * The fleet on a map.
 *
 * Fill colour is rental status, which is what an operator scans for. The ring is
 * data trust, so a car whose position cannot be believed looks different from
 * one that can. Tiles come from OpenStreetMap, so there is no token to obtain
 * and anyone can clone this repo and see a map.
 */

function FitBounds({ points, focus, frameKey }) {
  const map = useMap();
  const key =
    frameKey ?? (focus || points.map((p) => p.plate ?? p.assetRef).join(","));

  useEffect(() => {
    if (focus) {
      const target = points.find((p) => p.plate === focus);
      if (target) {
        map.setView([target.lat, target.lon], 12, { animate: true });
        return;
      }
    }
    if (!points.length) return;

    const lats = points.map((p) => p.lat);
    const lons = points.map((p) => p.lon);
    const bounds = [
      [Math.min(...lats), Math.min(...lons)],
      [Math.max(...lats), Math.max(...lons)],
    ];

    // Every Null Island reading sits on the same coordinate, and fitting a
    // box with no area zooms to the maximum. Centre on it instead.
    const spread =
      Math.max(...lats) - Math.min(...lats) + (Math.max(...lons) - Math.min(...lons));
    if (spread < 0.01) {
      map.setView([lats[0], lons[0]], 5, { animate: true });
      return;
    }

    map.fitBounds(bounds, { padding: [50, 50], animate: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, map]);

  return null;
}

export default function FleetMap({
  vehicles = [],
  focus = null,
  onSelect = null,
  height = 520,
  accepted = null,
  flagged = null,
  activeReason = null,
}) {
  const router = useRouter();

  // Quality mode: accepted positions for context, rejected ones coloured by the
  // rule that caught them. Selecting a reason frames that reason's points.
  const quality = accepted != null || flagged != null;
  const acceptedPoints = useMemo(
    () => (accepted || []).filter((p) => p.lat != null && p.lon != null),
    [accepted]
  );
  const flaggedPoints = useMemo(
    () =>
      (flagged || [])
        .filter((p) => p.lat != null && p.lon != null)
        .filter((p) => !activeReason || p.reason === activeReason),
    [flagged, activeReason]
  );

  const points = useMemo(
    () => vehicles.filter((v) => v.lat != null && v.lon != null),
    [vehicles]
  );

  const centre = points.length ? [points[0].lat, points[0].lon] : [-15.79, -47.88];

  // With a reason selected, frame the rejects. Otherwise show everything.
  const framed = quality
    ? activeReason
      ? flaggedPoints
      : [...acceptedPoints, ...flaggedPoints]
    : points;

  const select = (plate) => {
    if (onSelect) onSelect(plate);
    else router.push(`/vehicle/${plate}`);
  };

  return (
    <MapContainer
      center={centre}
      zoom={3}
      scrollWheelZoom
      // Thousands of markers as SVG elements makes panning crawl. Canvas draws
      // the whole fleet in one surface instead.
      preferCanvas
      worldCopyJump
      style={{ height, width: "100%", borderRadius: 8 }}
    >
      <TileLayer
        attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
        url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
      />
      <FitBounds
        points={framed}
        focus={quality ? null : focus}
        frameKey={quality ? `${activeReason || "all"}:${framed.length}` : undefined}
      />

      {quality &&
        acceptedPoints.map((point, index) => (
          <CircleMarker
            key={`ok-${point.assetRef}-${index}`}
            center={[point.lat, point.lon]}
            radius={2.5}
            pathOptions={{
              color: palette.green.dark1,
              fillColor: palette.green.base,
              fillOpacity: activeReason ? 0.15 : 0.7,
              weight: 0,
            }}
          >
            <Popup>
              <div className="text-sm">
                <strong>{point.assetRef}</strong>
                <br />
                Accepted position
              </div>
            </Popup>
          </CircleMarker>
        ))}

      {quality &&
        flaggedPoints.map((point, index) => (
          <CircleMarker
            key={`bad-${point.assetRef}-${index}`}
            center={[point.lat, point.lon]}
            radius={6}
            pathOptions={{
              color: REASON_COLOURS[point.reason] || palette.red.base,
              fillColor: REASON_COLOURS[point.reason] || palette.red.base,
              fillOpacity: 0.55,
              weight: 1.5,
            }}
          >
            <Popup>
              <div className="text-sm">
                <strong>{point.assetRef}</strong>
                <br />
                {REASON_LABELS[point.reason] || point.reason}
                <br />
                <span className="text-gray-600">{point.detail}</span>
                <br />
                <span className="font-mono text-xs">
                  {point.lat.toFixed(5)}, {point.lon.toFixed(5)}
                </span>
              </div>
            </Popup>
          </CircleMarker>
        ))}

      {points.map((vehicle) => {
        const selected = focus === vehicle.plate;
        return (
          <CircleMarker
            key={vehicle.plate}
            center={[vehicle.lat, vehicle.lon]}
            radius={selected ? 11 : vehicle.trust === "good" ? 3.5 : 5.5}
            pathOptions={{
              color: TRUST_COLOURS[vehicle.trust] || palette.gray.base,
              fillColor: STATUS_COLOURS[vehicle.status] || palette.gray.base,
              fillOpacity: 0.85,
              weight: vehicle.trust === "good" ? 0.5 : 2,
            }}
            eventHandlers={{ click: () => select(vehicle.plate) }}
          >
            <Popup>
              <div className="text-sm">
                <strong>{vehicle.plate}</strong>
                <br />
                {vehicle.make} {vehicle.model} {vehicle.year}
                <br />
                {STATUS_LABELS[vehicle.status] || vehicle.status} &middot;{" "}
                {vehicle.activity}
                {vehicle.fuel != null && <> &middot; {vehicle.fuel}% fuel</>}
                {vehicle.topFinding && (
                  <>
                    <br />
                    <span style={{ color: palette.red.dark2 }}>
                      {vehicle.topFinding}
                    </span>
                  </>
                )}
              </div>
            </Popup>
          </CircleMarker>
        );
      })}
    </MapContainer>
  );
}
