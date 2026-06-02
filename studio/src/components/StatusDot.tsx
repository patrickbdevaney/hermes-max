// A status dot — colour + (optional) pulse. Always paired with a text label by
// callers, never colour alone (the Phase 0 accessibility contract).
type Tone = "good" | "warn" | "bad" | "accent" | "muted";

const BG: Record<Tone, string> = {
  good: "bg-good", warn: "bg-warn", bad: "bg-bad", accent: "bg-accent", muted: "bg-mist-500",
};

export function StatusDot({ tone, pulse }: { tone: Tone; pulse?: boolean }) {
  return <span className={`inline-block h-2 w-2 rounded-full ${BG[tone]} ${pulse ? "animate-pulse2" : ""}`} aria-hidden />;
}
