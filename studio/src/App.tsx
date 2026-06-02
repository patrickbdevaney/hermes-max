// The Studio shell router. Two worlds (S architecture):
//   • SHELL mode — first-run / project list / settings (this React app)
//   • WORKSHOP mode — a thin studio bar over the embedded Phase 0-7 web UI
// Switching to a project enters the workshop; ← Projects returns to the shell.
//
// On launch we show the Loading screen until the Rust side emits `stack-ready`
// (the Python sidecar is up), then probe capabilities to decide first-run vs the
// project list. A fallback timer guarantees we never hang on Loading, and in a
// plain browser (no Tauri) we drop straight to first-run so the shell is dev-able.
import { useEffect, useState } from "react";
import type { UnlistenFn } from "@tauri-apps/api/event";
import { IS_TAURI, listen } from "./lib/tauri";
import { probeCapabilities, type DetectResult } from "./lib/detect";
import type { Project } from "./lib/projects";
import { Loading } from "./screens/Loading";
import { FirstRun } from "./screens/FirstRun";
import { Projects } from "./screens/Projects";
import { Settings } from "./screens/Settings";
import { Workshop } from "./screens/Workshop";

type Screen = "loading" | "firstrun" | "projects" | "settings";

export default function App() {
  const [screen, setScreen] = useState<Screen>("loading");
  const [active, setActive] = useState<Project | null>(null);
  const [detect, setDetect] = useState<DetectResult | null>(null);

  useEffect(() => {
    let un: UnlistenFn | undefined;
    let settled = false;
    const decide = async () => {
      if (settled) return;
      settled = true;
      try {
        const d = await probeCapabilities();
        setDetect(d);
        setScreen(d.suggested_mode === "NeedsSetup" || !d.hermes_present ? "firstrun" : "projects");
      } catch {
        setScreen("firstrun"); // browser dev or backend not ready — keep the shell usable
      }
    };
    listen("stack-ready", () => decide()).then((u) => (un = u));
    const fb = setTimeout(decide, IS_TAURI ? 6000 : 300);
    return () => { if (un) un(); clearTimeout(fb); };
  }, []);

  // Tray "New Project…" brings us back to the project list (S4.2).
  useEffect(() => {
    let un: UnlistenFn | undefined;
    listen("tray-new-project", () => { setActive(null); setScreen("projects"); }).then((u) => (un = u));
    return () => { if (un) un(); };
  }, []);

  const refreshDetect = () => probeCapabilities().then(setDetect).catch(() => void 0);

  if (active) return <Workshop project={active} detect={detect} onExit={() => { setActive(null); setScreen("projects"); }} />;
  if (screen === "loading") return <Loading />;
  if (screen === "firstrun") return <FirstRun detect={detect} onReady={() => { refreshDetect(); setScreen("projects"); }} />;
  if (screen === "settings") return <Settings detect={detect} onBack={() => setScreen("projects")} onChanged={refreshDetect} />;
  return <Projects onOpen={setActive} onSettings={() => setScreen("settings")} />;
}
