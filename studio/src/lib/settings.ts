// Studio-local UI settings (synchronous, localStorage-backed). Progressive depth
// (Phase 5.4): the appliance default hides everything that needs a system noun;
// developer mode unlocks the full surface.
export type Depth = "appliance" | "standard" | "developer";

export function getDepth(): Depth {
  try {
    const d = localStorage.getItem("hmx.depth") as Depth | null;
    return d === "appliance" || d === "developer" ? d : "standard";
  } catch { return "standard"; }
}

export function setDepth(d: Depth) {
  try { localStorage.setItem("hmx.depth", d); } catch { /* quota */ }
}
