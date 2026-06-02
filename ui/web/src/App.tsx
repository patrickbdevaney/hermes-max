// The shell: a persistent left-nav + top chrome around a hash-routed content area
// (PART III.1). The run reducer + SSE stream are lifted here so the chrome's live
// cost and the Run view share one source of truth, and so a run keeps streaming in
// the background while you visit Cost/Providers/Activity. Five surfaces: Run /
// Activity / Providers / Cost / Setup.
import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import { api } from "./lib/api";
import { openStream } from "./lib/events";
import type { ConnState } from "./lib/events";
import { reduce, initialView } from "./state";
import { reduceFeed, initialFeed, BATCH_FLUSH_MS } from "./lib/feed";
import { useRoute, navigate } from "./lib/router";
import { journal } from "./lib/runjournal";
import type { StatusPayload } from "./types";
import { SideNav } from "./components/SideNav";
import { TopChrome } from "./components/TopChrome";
import { RunPage } from "./components/RunPage";
import { HistoryPage } from "./components/HistoryPage";
import { ReplayPage } from "./components/ReplayPage";
import { ActivityPage } from "./components/ActivityPage";
import { ProvidersPage } from "./components/ProvidersPage";
import { CostPage } from "./components/CostPage";
import { Wizard } from "./components/wizard/Wizard";

export default function App() {
  const route = useRoute();
  const [status, setStatus] = useState<StatusPayload | null>(null);
  const [activeRunId, setActiveRunId] = useState<string | null>(null);
  const [conn, setConn] = useState<ConnState>("connecting");
  const [view, dispatch] = useReducer(reduce, initialView);
  // Part-2 feed/flow/chrome state — fed by the SAME SSE stream but ingested in
  // batches (Fix D) so a fast run can't thrash React or grow the heap unbounded.
  const [feed, feedDispatch] = useReducer(reduceFeed, initialFeed);
  const feedBuf = useRef<{ evt: any; data: any; now: number }[]>([]);
  const [alive, setAlive] = useState(false);
  const [firstRun, setFirstRun] = useState(false);
  const [liveRuns, setLiveRuns] = useState(0);
  const autoRouted = useRef(false);

  const refreshStatus = useCallback(() => {
    api.status().then(setStatus).catch(() => void 0);
  }, []);

  // Poll status (mode / providers / driver / today's spend) periodically.
  useEffect(() => {
    let stop = false;
    const tick = () => api.status().then((s) => { if (!stop) setStatus(s); }).catch(() => void 0);
    tick();
    const id = setInterval(tick, 10_000);
    return () => { stop = true; clearInterval(id); };
  }, []);

  // Discover runs from any origin (terminal / hm dev / UI) for the nav live-dot —
  // a fast poll so a terminal-launched run shows up within ~1s (Fix 4).
  useEffect(() => {
    let stop = false;
    const tick = () => api.runs()
      .then((r) => { if (!stop) setLiveRuns(r.runs.filter((x) => x.active).length); })
      .catch(() => void 0);
    tick();
    const id = setInterval(tick, 2000);
    return () => { stop = true; clearInterval(id); };
  }, []);

  // First-run heuristic: if nothing cloud is configured, route to Setup once.
  useEffect(() => {
    api.keysStatus()
      .then((ks) => {
        const anyCloud = ks.providers.some((p) => !p.keyless && p.present);
        setFirstRun(!anyCloud);
        if (!anyCloud && !autoRouted.current && route.name === "run" && !route.runId) {
          autoRouted.current = true;
          navigate("setup");
        }
      })
      .catch(() => setFirstRun(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // A run in the URL becomes the active run (deep-link / Activity / back-button).
  useEffect(() => {
    if (route.name === "run" && route.runId && route.runId !== activeRunId) {
      setActiveRunId(route.runId);
    }
  }, [route, activeRunId]);

  // Open / close the SSE stream when the active run changes. Seed the first turn's
  // user message from the journal (so a deep-linked run still shows what was asked).
  useEffect(() => {
    if (!activeRunId) { setConn("connecting"); return; }
    const meta = journal.get(activeRunId);
    dispatch({ type: "reset", userText: meta?.prompt ?? null });
    feedDispatch({ type: "reset", userText: meta?.prompt ?? null });
    feedBuf.current = [];
    const stream = openStream(activeRunId, (t, d) => {
      dispatch({ type: "event", evt: t, data: d });
      feedBuf.current.push({ evt: t, data: d, now: Date.now() });
    }, setConn);
    // coalesce buffered frames into one feed reduction per BATCH_FLUSH_MS tick
    const flush = setInterval(() => {
      if (feedBuf.current.length) {
        const events = feedBuf.current;
        feedBuf.current = [];
        feedDispatch({ type: "batch", events });
      }
    }, BATCH_FLUSH_MS);
    return () => { stream.close(); clearInterval(flush); };
  }, [activeRunId]);

  // Calm "alive" pulse: true while events have arrived recently.
  useEffect(() => {
    const id = setInterval(() => setAlive(Date.now() - view.lastEventTs < 4000), 1000);
    return () => clearInterval(id);
  }, [view.lastEventTs]);

  // ── actuation ──
  const launch = useCallback(async (cwd: string, prompt: string) => {
    try {
      const run = await api.run(cwd, prompt, status?.mode);
      journal.add({ run_id: run.run_id, prompt, cwd, mode: run.mode ?? status?.mode ?? null, start_ts: Date.now() });
      setActiveRunId(run.run_id);
      navigate("run", run.run_id);
    } catch (e) {
      // Surface as a transient narration so the user isn't left guessing.
      dispatch({ type: "reset", userText: prompt });
      dispatch({ type: "event", evt: "narration", data: { run_id: "x", plain_text: `Couldn't launch: ${(e as Error).message}`, level: "warn" } });
    }
  }, [status?.mode]);

  const cont = useCallback(async (prompt: string) => {
    if (!activeRunId) return;
    dispatch({ type: "user_message", text: prompt });
    feedDispatch({ type: "user", text: prompt, now: Date.now() });
    journal.bumpTurn(activeRunId);
    try {
      await api.continueRun(activeRunId, prompt);
    } catch (e) {
      dispatch({ type: "event", evt: "narration", data: { run_id: activeRunId, plain_text: `Couldn't continue: ${(e as Error).message}`, level: "warn" } });
    }
  }, [activeRunId]);

  const newRun = useCallback(() => {
    setActiveRunId(null);
    navigate("run");
  }, []);

  const fillHeight = route.name === "run" || route.name === "runs" || route.name === "replay";

  return (
    <div className="flex h-screen overflow-hidden">
      <SideNav active={route.name} status={status} liveRuns={liveRuns} />
      <div className="flex min-w-0 flex-1 flex-col">
        <TopChrome
          view={view}
          status={status}
          conn={conn}
          alive={alive && !!activeRunId}
          hasRun={!!activeRunId}
          refreshStatus={refreshStatus}
        />
        <main className="min-h-0 flex-1 overflow-hidden">
          <div className={`mx-auto h-full max-w-5xl px-6 ${fillHeight ? "py-5" : "overflow-y-auto py-6"}`}>
            {route.name === "run" && (
              <RunPage
                runId={activeRunId}
                view={view}
                feed={feed}
                conn={conn}
                status={status}
                onLaunch={launch}
                onContinue={cont}
                onNewRun={newRun}
              />
            )}
            {route.name === "runs" && <HistoryPage />}
            {route.name === "replay" && <ReplayPage runId={route.runId} />}
            {route.name === "activity" && <ActivityPage />}
            {route.name === "providers" && <ProvidersPage status={status} refreshStatus={refreshStatus} />}
            {route.name === "cost" && <CostPage />}
            {route.name === "setup" && (
              <Wizard
                status={status}
                refreshStatus={refreshStatus}
                firstRun={firstRun}
                onDone={() => navigate("run")}
              />
            )}
          </div>
        </main>
      </div>
    </div>
  );
}
