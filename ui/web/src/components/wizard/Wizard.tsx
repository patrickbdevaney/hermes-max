// The onboarding / edit wizard (PART III.1). First-run framing ("Welcome", linear,
// ends in "Continue to launch") vs edit framing ("Editing configuration", jump to
// any step, ends in "Done" → back to Run). Detect the profile, capture provider keys
// straight to the secret store (shared KeyManager — keys never touch the browser),
// test live, review.
import { useEffect, useState } from "react";
import { api } from "../../lib/api";
import { Badge, Dot } from "../ui";
import { ProviderKeyList } from "../providers/KeyManager";
import type { StatusPayload, KeysStatus, TestResult } from "../../types";

const STEPS = ["Profile", "Keys", "Test & review"] as const;

export function Wizard({ status, onDone, refreshStatus, firstRun = false }:
  {
    status: StatusPayload | null; onDone: () => void;
    refreshStatus: () => void; firstRun?: boolean;
  }) {
  const [step, setStep] = useState(0);

  return (
    <div className="mx-auto max-w-2xl space-y-5">
      <header>
        <h1 className="text-2xl font-semibold tracking-tight2 text-mist-100">
          {firstRun ? "Welcome to hermes-max" : "Editing configuration"}
        </h1>
        <p className="mt-1 text-sm text-mist-400">
          {firstRun
            ? "Three quick steps and you're ready to run."
            : "Change your profile, mode, or keys — jump to any step."}
        </p>
      </header>

      <Stepper step={step} setStep={setStep} editable={!firstRun} />

      {step === 0 && <ProfileStep status={status} refreshStatus={refreshStatus} />}
      {step === 1 && (
        <section className="rounded-lg border border-ink-800 bg-ink-900 p-5">
          <h2 className="text-lg font-medium text-mist-100">Provider keys</h2>
          <p className="mb-3 mt-1 text-sm text-mist-400">
            Optional — the local lane runs key-free. Add keys to unlock cloud rungs.
          </p>
          <ProviderKeyList onChange={refreshStatus} />
        </section>
      )}
      {step === 2 && <ReviewStep status={status} />}

      <div className="flex items-center justify-between">
        <button
          type="button"
          disabled={step === 0}
          onClick={() => setStep((s) => Math.max(0, s - 1))}
          className="rounded-md border border-ink-700 px-4 py-2 text-sm text-mist-200 transition-colors hover:bg-ink-800 disabled:opacity-30"
        >
          ← Back
        </button>
        {firstRun && (
          <button type="button" onClick={onDone} className="text-xs text-mist-400 hover:text-mist-200">
            skip setup
          </button>
        )}
        {step < STEPS.length - 1 ? (
          <button
            type="button"
            onClick={() => setStep((s) => s + 1)}
            className="rounded-md bg-accent px-4 py-2 text-sm font-medium text-ink-950 transition-opacity hover:opacity-90"
          >
            Next →
          </button>
        ) : (
          <button
            type="button"
            onClick={onDone}
            className="rounded-md bg-accent px-4 py-2 text-sm font-medium text-ink-950 transition-opacity hover:opacity-90"
          >
            {firstRun ? "Continue to launch →" : "Done"}
          </button>
        )}
      </div>
    </div>
  );
}

function Stepper({ step, setStep, editable }:
  { step: number; setStep: (n: number) => void; editable: boolean }) {
  return (
    <ol className="flex items-center gap-2 text-xs">
      {STEPS.map((label, i) => (
        <li key={label} className="flex items-center gap-2">
          <button
            type="button"
            disabled={!editable && i > step}
            onClick={() => (editable || i <= step) && setStep(i)}
            className={`flex h-6 w-6 items-center justify-center rounded-full border text-[11px] transition-colors ${
              i < step ? "border-good text-good"
                : i === step ? "border-accent bg-accent text-ink-950"
                : "border-ink-700 text-mist-400"} ${editable ? "cursor-pointer hover:border-accent" : ""}`}
          >
            {i < step ? "✓" : i + 1}
          </button>
          <span className={i === step ? "text-mist-100" : "text-mist-400"}>{label}</span>
          {i < STEPS.length - 1 && <span className="mx-1 h-px w-6 bg-ink-700" />}
        </li>
      ))}
    </ol>
  );
}

// ── Step 1: profile detect / recommend ───────────────────────────────────────
function ProfileStep({ status, refreshStatus }:
  { status: StatusPayload | null; refreshStatus: () => void }) {
  const [vllm, setVllm] = useState<TestResult | "probing" | null>(null);
  const [applying, setApplying] = useState<string | null>(null);

  useEffect(() => {
    setVllm("probing");
    api.testConnection("local_vllm").then(setVllm).catch(() => setVllm({ ok: false }));
  }, []);

  // The DRIVER is the truth, not raw GPU presence — a reachable remote endpoint IS a
  // live driver. Recommend from the detected driver state, never "no GPU".
  const driver = status?.driver;
  const driverState = driver?.state ?? "none";
  const recommended = driverState === "local" || driverState === "remote" ? "free"
    : driverState === "cloud" ? "full" : "full";

  async function applyMode(mode: string) {
    setApplying(mode);
    try { await api.applyConfig({ mode }); refreshStatus(); }
    finally { setApplying(null); }
  }

  return (
    <section className="rounded-lg border border-ink-800 bg-ink-900 p-5">
      <h2 className="text-lg font-medium text-mist-100">Your machine</h2>
      <div className="mt-4 grid gap-3 sm:grid-cols-2">
        <Detected
          label="Driver"
          tone={driverState === "none" ? "warn" : "good"}
          text={driver?.label ?? "detecting…"}
          sub={driver?.host ? `at ${driver.host}` : driver?.detail}
        />
        <Detected
          label="Local vLLM endpoint"
          tone={typeof vllm === "object" && vllm?.ok ? "good" : vllm === "probing" ? "muted" : "warn"}
          probing={vllm === "probing"}
          text={typeof vllm === "object" && vllm?.ok
            ? (vllm.model ? `reachable · ${vllm.model}` : "reachable")
            : "not reachable (set VLLM_BASE_URL if you run one)"}
        />
      </div>

      <div className="mt-5 rounded-lg border border-accent/30 bg-accent-soft/20 p-4">
        <div className="text-sm font-medium text-mist-100">
          {driverState === "remote" ? "Remote driver detected"
            : driverState === "local" ? "Local driver detected"
            : driverState === "cloud" ? "Cloud driver detected"
            : "No driver yet"}
        </div>
        <div className="mt-1 text-sm text-mist-300">
          {driverState === "remote" && "Your endpoint runs the agent on another machine — private, and $0 by default."}
          {driverState === "local" && "The local model executes; a free cloud model can plan. Private and $0 by default."}
          {driverState === "cloud" && "A cloud model drives. No GPU required."}
          {driverState === "none" && "Add a vLLM endpoint or a cloud key below to get a driver."}
        </div>
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <span className="text-xs text-mist-400">current mode:</span>
          <Badge tone="accent">{status?.mode ?? "—"}</Badge>
          <span className="mx-1 text-xs text-mist-400">recommended:</span>
          {["free", "full", "local"].map((m) => (
            <button
              key={m}
              type="button"
              disabled={applying != null || status?.mode === m}
              onClick={() => applyMode(m)}
              className={`rounded-md border px-3 py-1 text-xs transition-colors disabled:opacity-50 ${
                m === recommended ? "border-accent text-accent" : "border-ink-700 text-mist-300"
              } hover:bg-ink-800`}
            >
              {applying === m ? "applying…" : status?.mode === m ? `${m} ✓` : m}
              {m === recommended ? " ★" : ""}
            </button>
          ))}
        </div>
      </div>
    </section>
  );
}

function Detected({ label, tone, text, sub, probing }:
  { label: string; tone: "good" | "warn" | "muted"; text: string; sub?: string; probing?: boolean }) {
  return (
    <div className="rounded-lg border border-ink-800 bg-ink-850 p-3">
      <div className="text-xs uppercase tracking-wide text-mist-400">{label}</div>
      <div className="mt-1 flex items-center gap-2 text-sm text-mist-200">
        <Dot tone={tone} pulse={probing} />
        {probing ? "checking…" : text}
      </div>
      {sub && <div className="mt-0.5 text-[11px] text-mist-400">{sub}</div>}
    </div>
  );
}

// ── Step 3: review ────────────────────────────────────────────────────────────
function ReviewStep({ status }: { status: StatusPayload | null }) {
  const [ks, setKs] = useState<KeysStatus | null>(null);
  useEffect(() => { api.keysStatus().then(setKs).catch(() => void 0); }, []);
  const configured = ks?.providers.filter((p) => p.present) ?? [];

  return (
    <section className="rounded-lg border border-ink-800 bg-ink-900 p-5">
      <h2 className="text-lg font-medium text-mist-100">You're set</h2>
      <div className="mt-4 grid gap-3 sm:grid-cols-3">
        <SummaryCard label="Mode" value={status?.mode ?? "—"} />
        <SummaryCard label="Driver" value={status?.driver?.state ?? "none"} />
        <SummaryCard label="Providers ready" value={`${configured.length}`} />
      </div>
      <div className="mt-4 rounded-lg border border-ink-800 bg-ink-850 p-3">
        <div className="text-xs uppercase tracking-wide text-mist-400">Ready rungs</div>
        <div className="mt-2 flex flex-wrap gap-1.5">
          {configured.map((p) => (
            <Badge key={p.name} tone="good"><Dot tone="good" />{p.name}</Badge>
          ))}
          {configured.length === 0 && <span className="text-sm text-mist-400">local lane only (free, private)</span>}
        </div>
      </div>
      {ks && (
        <p className="mt-3 text-xs text-mist-400">
          Secrets stored in <span className="text-mist-200">{ks.backend_label}</span>.
        </p>
      )}
    </section>
  );
}

function SummaryCard({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-ink-800 bg-ink-850 p-3">
      <div className="text-xs uppercase tracking-wide text-mist-400">{label}</div>
      <div className="mt-1 font-mono text-base text-mist-100">{value}</div>
    </div>
  );
}
