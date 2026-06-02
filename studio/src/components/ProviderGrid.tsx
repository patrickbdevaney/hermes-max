// A grid of cloud AI providers for key entry. Clicking one selects it (the
// FirstRun screen then shows a single password field). Groq is flagged free.
import { PROVIDERS, type Provider } from "../lib/firstrun";

export function ProviderGrid({ selected, onSelect }:
  { selected?: string; onSelect: (p: Provider) => void }) {
  return (
    <div className="grid grid-cols-3 gap-2">
      {PROVIDERS.map((p) => (
        <button key={p.id} type="button" onClick={() => onSelect(p)}
          className={`flex flex-col items-start gap-0.5 rounded-md border px-3 py-2 text-left transition-colors ${
            selected === p.id ? "border-accent bg-accent-soft/15" : "border-ink-700 hover:bg-ink-850"}`}>
          <span className="text-sm font-medium text-mist-100">{p.name}</span>
          {p.free && <span className="text-[10px] text-good">free tier available</span>}
        </button>
      ))}
    </div>
  );
}
