// Phase 5.4 — editable PLAN.md (the conductor's living plan). Edits BEFORE a run
// are safe; edits MID-RUN write the signed PLAN.md, which the harness re-reads on
// its next turn (pre_llm_call re-injection) and re-plans against. A diff-before-
// apply summary keeps edits honest; the raw editor is intentional (a schema-aware
// form is the Phase 6 /config surface).
import { useEffect, useState } from "react";
import { api } from "../../lib/api";
import { SkeletonRows, ErrorState, EmptyMoment } from "../ui";

export function PlanEditor({ cwd }: { cwd: string | null }) {
  const [loaded, setLoaded] = useState<string | null>(null);
  const [text, setText] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [savedAt, setSavedAt] = useState<number | null>(null);
  const [exists, setExists] = useState(true);

  useEffect(() => {
    if (!cwd) return;
    setLoaded(null); setErr(null);
    api.readPlan(cwd)
      .then((r) => {
        if (!r.ok) { setErr(r.error || "couldn't read PLAN.md"); return; }
        setLoaded(r.content); setText(r.content); setExists(r.exists);
      })
      .catch((e) => setErr((e as Error).message));
  }, [cwd]);

  if (!cwd) return <EmptyMoment icon="◇" title="No working directory" hint="Open a run with a known directory to edit its PLAN.md." />;
  if (err) return <ErrorState title="Couldn't load PLAN.md" detail={err} onRetry={() => setText((t) => t)} />;
  if (loaded === null) return <div className="pt-4"><SkeletonRows rows={10} /></div>;

  const dirty = text !== loaded;
  const { added, removed } = lineDiff(loaded, text);

  async function save() {
    if (!cwd) return;
    setSaving(true); setErr(null);
    try {
      const r = await api.writePlan(cwd, text);
      if (!r.ok) { setErr(r.error || "write failed"); return; }
      setLoaded(text); setExists(true); setSavedAt(Date.now());
    } catch (e) { setErr((e as Error).message); }
    finally { setSaving(false); }
  }

  return (
    <div className="flex h-full flex-col gap-2">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2 text-xs text-mist-400">
          <span className="font-mono text-mist-200">PLAN.md</span>
          {!exists && <span className="text-warn">· will be created</span>}
          {dirty && <span className="text-accent">· {added} added · {removed} removed</span>}
          {savedAt && !dirty && <span className="text-good">· saved</span>}
        </div>
        <div className="flex items-center gap-2">
          <button type="button" onClick={() => setText(loaded)} disabled={!dirty}
            className="rounded-md border border-ink-700 px-2.5 py-1 text-xs text-mist-300 disabled:opacity-40 hover:bg-ink-850">revert</button>
          <button type="button" onClick={save} disabled={!dirty || saving}
            className="rounded-md bg-accent px-3 py-1 text-xs font-medium text-ink-950 disabled:opacity-40 hover:opacity-90">
            {saving ? "saving…" : "Save"}
          </button>
        </div>
      </div>
      <textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        spellCheck={false}
        className="min-h-0 flex-1 resize-none rounded-lg border border-ink-800 bg-ink-input p-3 font-mono text-xs leading-relaxed text-mist-100 outline-none focus:border-accent"
        placeholder="# Plan&#10;&#10;- [ ] step one&#10;- [ ] step two"
      />
      <p className="text-[11px] text-mist-500">
        Mid-run edits write the signed PLAN.md; the harness re-reads it on its next turn and re-plans.
      </p>
    </div>
  );
}

// A tiny line-level diff summary (added/removed counts) — diff-before-apply.
function lineDiff(a: string, b: string): { added: number; removed: number } {
  const A = new Set(a.split("\n"));
  const B = new Set(b.split("\n"));
  let added = 0, removed = 0;
  for (const l of B) if (!A.has(l)) added++;
  for (const l of A) if (!B.has(l)) removed++;
  return { added, removed };
}
