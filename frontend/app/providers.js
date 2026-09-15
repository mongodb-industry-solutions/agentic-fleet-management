"use client";

import LeafyGreenProvider from "@leafygreen-ui/leafygreen-provider";

import { BehindTheScenesProvider } from "@/lib/context/BehindTheScenesContext";

export function Providers({ children }) {
  return (
    <LeafyGreenProvider>
      <BehindTheScenesProvider>{children}</BehindTheScenesProvider>
    </LeafyGreenProvider>
  );
}
