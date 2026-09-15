/**
 * Deterministic formatting.
 *
 * Calling toLocaleString with no locale gives different output on the server and
 * in the browser, which shows up as a React hydration mismatch. Pinning the
 * locale keeps prerendered HTML and client render identical.
 */

const NUMBER = new Intl.NumberFormat("en-US");

export const formatNumber = (value) => NUMBER.format(value ?? 0);

export const formatPercent = (value, digits = 2) =>
  `${((value ?? 0) * 100).toFixed(digits)}%`;
