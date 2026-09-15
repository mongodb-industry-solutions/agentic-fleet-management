"use client";

import { createContext, useCallback, useContext, useMemo, useState } from "react";

/**
 * Holds whatever the current screen wants to reveal about MongoDB.
 *
 * Each API response carries an `explain` list naming the collection, the query
 * and the capability behind what the operator is looking at. A page publishes
 * that list here and the drawer renders it, so the technical story stays
 * attached to the screen that motivates it.
 */
const BehindTheScenesContext = createContext(null);

export function BehindTheScenesProvider({ children }) {
  const [open, setOpen] = useState(false);
  const [panels, setPanels] = useState([]);
  const [context, setContext] = useState("");

  const publish = useCallback((nextPanels, nextContext = "") => {
    setPanels(nextPanels || []);
    setContext(nextContext);
  }, []);

  const value = useMemo(
    () => ({ open, setOpen, panels, context, publish }),
    [open, panels, context, publish]
  );

  return (
    <BehindTheScenesContext.Provider value={value}>
      {children}
    </BehindTheScenesContext.Provider>
  );
}

export function useBehindTheScenes() {
  const ctx = useContext(BehindTheScenesContext);
  if (!ctx) {
    throw new Error("useBehindTheScenes must be used inside BehindTheScenesProvider");
  }
  return ctx;
}
