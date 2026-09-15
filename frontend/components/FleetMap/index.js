"use client";

import dynamic from "next/dynamic";

// Leaflet reaches for window on import, so the map only loads in the browser.
const FleetMap = dynamic(() => import("./FleetMap"), {
  ssr: false,
  loading: () => (
    <div
      style={{ height: "100%", width: "100%", borderRadius: 8 }}
      className="flex items-center justify-center bg-gray-100 text-sm text-gray-500"
    >
      Loading map...
    </div>
  ),
});

export default FleetMap;
export { FleetMap };
