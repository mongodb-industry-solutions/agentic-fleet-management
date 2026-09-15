"use client";

import { useEffect, useState } from "react";

/**
 * True once the component has mounted in the browser.
 *
 * Some LeafyGreen components allocate random element ids, which differ between
 * the server render and the client render and show up as a hydration mismatch.
 * Nothing on this page is useful before its data has been fetched in the
 * browser, so waiting for mount costs nothing.
 */
export function useMounted() {
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);
  return mounted;
}
