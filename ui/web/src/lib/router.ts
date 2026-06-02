// A tiny hash router (zero deps — the same sovereignty discipline as the stdlib
// backend). Hash routes keep the URL meaningful so Back, bookmark, and deep-link
// all work, without a server-side rewrite. The five product surfaces:
//
//   #/run            → the launcher / live conversational run (home, default)
//   #/run/:runId     → a specific run's visual flow (deep-linkable, replayable)
//   #/activity       → run history
//   #/providers      → provider rungs + key management
//   #/cost           → the ledger breakdown
//   #/setup          → the onboarding / edit wizard
import { useEffect, useState } from "react";

export type RouteName =
  | "run" | "runs" | "replay" | "activity" | "providers" | "cost" | "setup"
  | "services" | "skills" | "fabric" | "state" | "config" | "plan" | "settings";

// Routes that carry a trailing :runId segment.
const ID_ROUTES = new Set<RouteName>(["run", "replay", "plan", "state"]);

export interface Route {
  name: RouteName;
  runId: string | null;   // meaningful for ID_ROUTES
}

const NAMES: RouteName[] = [
  "run", "runs", "replay", "activity", "providers", "cost", "setup",
  "services", "skills", "fabric", "state", "config", "plan", "settings",
];

export function parseHash(hash: string): Route {
  // Strip the leading "#", tolerate "#/foo" and "#foo" and trailing slashes.
  const raw = hash.replace(/^#\/?/, "").replace(/\/+$/, "");
  const [head, ...rest] = raw.split("/");
  const name = (NAMES.includes(head as RouteName) ? head : "run") as RouteName;
  const runId = ID_ROUTES.has(name) && rest.length ? decodeURIComponent(rest.join("/")) : null;
  return { name, runId };
}

export function hrefFor(name: RouteName, runId?: string | null): string {
  if (ID_ROUTES.has(name) && runId) return `#/${name}/${encodeURIComponent(runId)}`;
  return `#/${name}`;
}

export function navigate(name: RouteName, runId?: string | null): void {
  const next = hrefFor(name, runId);
  if (window.location.hash !== next) window.location.hash = next;
}

// Subscribe to hashchange; returns the current parsed route. Seeds "#/run" on a
// bare load so the address bar always reflects a real surface.
export function useRoute(): Route {
  const [route, setRoute] = useState<Route>(() => parseHash(window.location.hash));
  useEffect(() => {
    if (!window.location.hash) window.history.replaceState({}, "", "#/run");
    const onChange = () => setRoute(parseHash(window.location.hash));
    window.addEventListener("hashchange", onChange);
    onChange();
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  return route;
}
