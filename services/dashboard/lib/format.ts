/**
 * Tiny display-side formatting helpers (Slice 5.0c-f).
 */

/**
 * Human-readable "X ago" string. Returns "just now" under a minute,
 * "N min ago" under an hour, "N hr ago" under a day, "N days ago" under
 * a week, then falls back to a locale date string. Always pass the full
 * ISO string into a `title=` attribute alongside so the absolute time
 * is one hover away.
 */
export function relativeTime(iso: string, now: number = Date.now()): string {
  const then = new Date(iso).getTime();
  if (!Number.isFinite(then)) return "";
  const ago = Math.max(0, Math.floor((now - then) / 1000));
  if (ago < 5) return "just now";
  if (ago < 60) return `${ago}s ago`;
  if (ago < 3600) return `${Math.floor(ago / 60)} min ago`;
  if (ago < 86_400) return `${Math.floor(ago / 3600)} hr ago`;
  const days = Math.floor(ago / 86_400);
  if (days < 7) return `${days} day${days === 1 ? "" : "s"} ago`;
  return new Date(iso).toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

/**
 * Human-readable "in X" string for a FUTURE ISO timestamp — the mirror of
 * relativeTime for the forward direction (e.g. a time-limited grant's
 * expiry). Returns "expired" once the moment has passed. Phase 6.5-f.
 */
export function timeUntil(iso: string, now: number = Date.now()): string {
  const then = new Date(iso).getTime();
  if (!Number.isFinite(then)) return "";
  const secs = Math.floor((then - now) / 1000);
  if (secs <= 0) return "expired";
  if (secs < 60) return `in ${secs}s`;
  if (secs < 3600) return `in ${Math.floor(secs / 60)} min`;
  if (secs < 86_400) return `in ${Math.floor(secs / 3600)} hr`;
  const days = Math.floor(secs / 86_400);
  return `in ${days} day${days === 1 ? "" : "s"}`;
}

/**
 * Absolute ISO → friendly tooltip ("Thu, 30 May 2026, 14:32:01 UTC")
 * for the `title=` attribute on a relative-time chip.
 */
export function absoluteTimeTitle(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString(undefined, {
    weekday: "short",
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}
