// The launch screen shown until the Rust side emits `stack-ready`. A calm,
// centered wordmark with a soft pulse — NOT a spinner, NOT log output. It
// resolves to first-run or the project list within a few seconds.
export function Loading({ detail }: { detail?: string }) {
  return (
    <div className="flex h-screen flex-col items-center justify-center gap-4 bg-bg-base">
      <div className="flex items-center gap-3">
        <span className="inline-block h-3 w-3 animate-pulse2 rounded-full bg-conductor" aria-hidden />
        <span className="font-display text-2xl font-semibold tracking-tight2 text-mist-100">Hermes Studio</span>
      </div>
      <p className="text-xs text-mist-500">{detail ?? "Warming up the workshop…"}</p>
    </div>
  );
}
