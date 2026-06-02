// The workshop (v2 Phase 1). Studio now OWNS the launch (via the Rust control
// plane), so it knows the run_id and drives the studio bar entirely from the new
// Rust SSE→Channel stream — no more workshop.rs tailer, no dual-observer race.
//
// The run VIEW is still the web UI, shown in an iframe DEEP-LINKED to this run
// (#/run/<id>) — a TEMPORARY transition fallback (Hard Decision #2). Phase 3
// replaces it with a native render fed by the same Channel stream.
import { useEffect, useReducer, useRef, useState } from "react";
import { renameProject, openProjectFolder, type Project } from "../lib/projects";
import { startRunStream, stopRunStream, type StreamMsg } from "../lib/runstream";
import { runTask, continueRun, steerRun, pauseRun, resumeRun, interruptRun } from "../lib/control";
import { computeShadow, fmtMoney, fmtMultiple } from "../lib/shadow";
import { reduceFeed, initialFeed } from "@webui/lib/feed";
import { projectMemory, activeRuns, type ProjectMemory } from "../lib/project";
import { getDepth } from "../lib/settings";
import { StatusDot } from "../components/StatusDot";
import { CompletionCard } from "../components/CompletionCard";
import { CheckpointsPanel } from "../components/CheckpointsPanel";
import { RunView } from "../run/RunView";

export function Workshop({ project, onExit }: { project: Project; onExit: () => void }) {
  const [runId, setRunId] = useState<string | null>(null);
  const [msg, setMsg] = useState<StreamMsg | null>(null);
  const [name, setName] = useState(project.name);
  const [prompt, setPrompt] = useState("");
  const [launching, setLaunching] = useState(false);
  const [paused, setPaused] = useState(false);
  const [steer, setSteer] = useState("");
  const [receipt, setReceipt] = useState<{ cost_usd: number; tokens: number } | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const wasRunning = useRef(false);
  // The SHARED reducer (same as the web UI) folds the Channel's structured event
  // stream into the feed/flow/chrome the native RunView renders (Phase 3.2).
  const [feed, dispatch] = useReducer(reduceFeed, initialFeed);
  // Phase 4: project memory + thread-vs-fresh + checkpoints. Phase 5.4: depth.
  const [mem, setMem] = useState<ProjectMemory | null>(null);
  const [priorRunId, setPriorRunId] = useState<string | null>(null);
  const [thread, setThread] = useState(true);
  const [showCps, setShowCps] = useState(false);
  const depth = getDepth();

  useEffect(() => () => { stopRunStream().catch(() => void 0); }, []);

  // On entering a project, load what the agent "remembers" + the most recent run
  // in this directory (so a turn defaults to threading into the live session).
  useEffect(() => {
    projectMemory(project.dir).then(setMem).catch(() => void 0);
    activeRuns()
      .then((r) => {
        const mine = r.runs.filter((x) => x.cwd === project.dir).sort(() => -1);
        setPriorRunId(mine[0]?.run_id ?? null);
      })
      .catch(() => void 0);
  }, [project.dir]);

  const chrome = msg?.chrome;
  const running = !!chrome?.running;
  const hasMemory = !!(mem?.plan_present || priorRunId);

  function consume(m: StreamMsg) {
    setMsg(m);
    if (m.events && m.events.length) {
      const now = Date.now();
      dispatch({ type: "batch", events: m.events.map((e) => ({ evt: e.event as any, data: e.data, now })) });
    }
    if (wasRunning.current && !m.chrome.running && m.chrome.done) {
      setReceipt({ cost_usd: m.chrome.cost_usd, tokens: m.chrome.tokens });
    }
    wasRunning.current = m.chrome.running;
  }

  async function launch(text: string) {
    const t = text.trim();
    if (!t) return;
    setLaunching(true); setErr(null);
    try {
      if (runId && !running) {
        await continueRun(runId, t);                 // turn 2+ on the same run
      } else if (!runId && thread && priorRunId) {
        // thread into the existing session — keep the plan + warm memory (4.2)
        setRunId(priorRunId);
        await continueRun(priorRunId, t);
        await startRunStream(priorRunId, consume);
      } else {
        dispatch({ type: "reset", userText: t });     // clear the feed for a fresh run
        const h = await runTask(project.dir, t);       // fresh run — Studio owns the id
        setRunId(h.run_id);
        await startRunStream(h.run_id, consume);
      }
      setPrompt(""); setReceipt(null);
    } catch (e) { setErr((e as Error).message); }
    finally { setLaunching(false); }
  }

  function exit() {
    if (running && !confirm("A build is still running. Leave the workshop anyway?")) return;
    stopRunStream().catch(() => void 0);
    onExit();
  }
  function commitName() {
    const n = name.trim();
    if (n && n !== project.name) renameProject(project.id, n).catch(() => void 0);
  }
  function doPause() { if (runId) { pauseRun(runId).catch(() => void 0); setPaused(true); } }
  function doResume() { if (runId) { resumeRun(runId).catch(() => void 0); setPaused(false); } }
  function doInterrupt() { if (runId) interruptRun(runId).catch(() => void 0); }
  function sendSteer() { const s = steer.trim(); if (runId && s) { steerRun(runId, s).catch(() => void 0); setSteer(""); } }

  const cost = chrome?.cost_usd ?? 0;
  const shadow = computeShadow(cost, chrome?.tokens ?? 0);
  const phrase = running ? (chrome?.phrase || "Working…") : chrome?.done ? "All done ✓" : "Ready when you are";

  return (
    <div className="flex h-screen flex-col bg-bg-base">
      {/* studio bar — driven entirely by the Rust Channel stream */}
      <div className="flex h-9 shrink-0 items-center gap-3 border-b border-ink-800 px-3 text-xs">
        <button type="button" onClick={exit} className="shrink-0 text-mist-300 hover:text-mist-100">← Projects</button>
        <StatusDot tone={running ? "accent" : chrome?.done ? "good" : "muted"} pulse={running} />
        <input value={name} onChange={(e) => setName(e.target.value)} onBlur={commitName}
          onKeyDown={(e) => { if (e.key === "Enter") (e.target as HTMLInputElement).blur(); }}
          className="w-40 shrink-0 truncate bg-transparent font-medium text-mist-100 outline-none focus:text-accent" aria-label="project name" />
        <span className="text-mist-600">•</span>
        <span className="min-w-0 flex-1 truncate text-mist-300">{paused ? "Pause requested — finishing current step…" : phrase}</span>
        {chrome && chrome.total > 0 && <span className="shrink-0 font-mono text-mist-500">{chrome.step}/{chrome.total}</span>}
        {running && (
          <span className="flex shrink-0 items-center gap-1">
            {paused
              ? <button type="button" onClick={doResume} className="rounded border border-accent/40 px-1.5 text-accent hover:bg-accent-soft/15" title="resume">▶</button>
              : <button type="button" onClick={doPause} className="rounded border border-ink-700 px-1.5 text-mist-200 hover:bg-ink-850" title="cooperative pause">⏸</button>}
            <button type="button" onClick={doInterrupt} className="rounded border border-bad/40 px-1.5 text-bad hover:bg-bad/10" title="interrupt">⏹</button>
          </span>
        )}
        <button type="button" onClick={() => setShowCps(true)} className="shrink-0 rounded border border-ink-700 px-1.5 text-mist-300 hover:bg-ink-850" title="checkpoints">⎇</button>
        <span className="shrink-0 font-mono text-mist-300" title={shadow.savedUsd > 0 ? `saved ${fmtMoney(shadow.savedUsd)} (${fmtMultiple(shadow.multiple)}) vs premium AI` : undefined}>{fmtMoney(cost)}</span>
      </div>

      {/* steer strip (only while running) */}
      {running && (
        <div className="flex items-center gap-2 border-b border-ink-800 bg-ink-900 px-3 py-1.5">
          <input value={steer} onChange={(e) => setSteer(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") sendSteer(); }}
            placeholder="nudge the agent… (non-destructive, applied at the next step)"
            className="flex-1 rounded-md border border-ink-700 bg-ink-input px-2 py-1 text-xs text-mist-100 outline-none focus:border-accent" />
          <button type="button" onClick={sendSteer} disabled={!steer.trim()} className="rounded-md border border-accent/40 px-2.5 py-1 text-xs text-accent hover:bg-accent-soft/15 disabled:opacity-40">Steer →</button>
        </div>
      )}

      {/* run view — rendered NATIVELY from the Channel via the shared reducer
          (Phase 3.2). The iframe is gone: one origin, one reducer, Cmd-K works. */}
      {runId ? (
        <div className="min-h-0 flex-1"><RunView feed={feed} live={running} depth={depth} /></div>
      ) : (
        <div className="flex min-h-0 flex-1 items-center justify-center px-6">
          <div className="w-full max-w-xl space-y-3 text-center">
            <h1 className="font-display text-2xl font-semibold tracking-tight2 text-mist-100">What should I build?</h1>
            {/* what the agent remembers about this project (Phase 4.3) — plain words */}
            {hasMemory && (
              <p className="text-xs text-mist-400">
                Working in <span className="text-mist-200">{project.name}</span>
                {mem && mem.file_count > 0 && <> · {mem.file_count.toLocaleString()} files indexed</>}
                {mem?.plan_present && <> · the plan ({mem.plan_steps} step{mem.plan_steps === 1 ? "" : "s"})</>}
              </p>
            )}
            <textarea value={prompt} onChange={(e) => setPrompt(e.target.value)} rows={3} autoFocus
              onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); launch(prompt); } }}
              placeholder="e.g. A todo app with user accounts and a passing test suite"
              className="w-full resize-none rounded-lg border border-ink-700 bg-ink-900 p-3 text-sm text-mist-100 outline-none focus:border-accent" />
            {/* thread-vs-fresh (Phase 4.2) — defaults to continuing, reversible */}
            {hasMemory && (
              <p className="text-[11px] text-mist-500">
                {thread
                  ? <>Continuing in {project.name} — keeping the plan &amp; memory · <button type="button" onClick={() => setThread(false)} className="text-accent hover:underline">Start fresh instead</button></>
                  : <>Starting fresh · <button type="button" onClick={() => setThread(true)} className="text-accent hover:underline">Continue {project.name} instead</button></>}
              </p>
            )}
            {err && <p className="text-xs text-bad">{err}</p>}
            <button type="button" onClick={() => launch(prompt)} disabled={launching || !prompt.trim()}
              className="rounded-md bg-accent px-4 py-2 text-sm font-medium text-ink-950 hover:opacity-90 disabled:opacity-40">
              {launching ? "Starting…" : "Let's go →"}
            </button>
          </div>
        </div>
      )}

      {showCps && <CheckpointsPanel cwd={project.dir} onClose={() => setShowCps(false)} />}
      {receipt && (
        <CompletionCard name={project.name} status={receipt}
          onClose={() => setReceipt(null)} onOpenFolder={() => openProjectFolder(project.dir)} />
      )}
    </div>
  );
}
