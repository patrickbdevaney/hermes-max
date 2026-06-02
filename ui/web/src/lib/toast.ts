// Phase 6.3 — a tiny, zero-dependency toast store (sonner is the shadcn default,
// but a ~60-line emitter holds the bundle budget and the house style). Toasts
// are actionable (an optional onClick jumps to the event) and carry a tone that
// always pairs colour with an icon + label. An optional sound (off by default,
// opt-in via Settings) plays a soft WebAudio tone — high-signal for unwatched
// runs, never required to understand state.
export type ToastTone = "info" | "good" | "warn" | "bad" | "conductor";

export interface Toast {
  id: number;
  tone: ToastTone;
  title: string;
  detail?: string;
  actionLabel?: string;
  onAction?: () => void;
  ttl: number;
}

type Listener = (toasts: Toast[]) => void;

let _toasts: Toast[] = [];
let _id = 1;
const _listeners = new Set<Listener>();
const MAX = 5;

function emit() { for (const l of _listeners) l(_toasts); }

export function subscribeToasts(l: Listener): () => void {
  _listeners.add(l); l(_toasts);
  return () => { _listeners.delete(l); };
}

export function dismissToast(id: number) {
  _toasts = _toasts.filter((t) => t.id !== id);
  emit();
}

export function pushToast(t: Omit<Toast, "id" | "ttl"> & { ttl?: number }): number {
  const id = _id++;
  const toast: Toast = { ttl: t.onAction ? 9000 : 6000, ...t, id };
  _toasts = [..._toasts.slice(-(MAX - 1)), toast];
  emit();
  if (toast.ttl > 0) setTimeout(() => dismissToast(id), toast.ttl);
  return id;
}

// ── optional sound (opt-in) ──────────────────────────────────────────────────
let _ctx: AudioContext | null = null;
const TONES: Record<string, number> = { pass: 660, conductor: 392, complete: 523, fail: 247 };

export function soundEnabled(): boolean {
  try { return localStorage.getItem("hmx.sound") === "1"; } catch { return false; }
}

export function playCue(kind: keyof typeof TONES | string) {
  if (!soundEnabled()) return;
  try {
    _ctx = _ctx || new (window.AudioContext || (window as any).webkitAudioContext)();
    const osc = _ctx.createOscillator();
    const gain = _ctx.createGain();
    osc.frequency.value = TONES[kind] ?? 440;
    osc.type = "sine";
    gain.gain.setValueAtTime(0.0001, _ctx.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.06, _ctx.currentTime + 0.01);
    gain.gain.exponentialRampToValueAtTime(0.0001, _ctx.currentTime + 0.22);
    osc.connect(gain); gain.connect(_ctx.destination);
    osc.start(); osc.stop(_ctx.currentTime + 0.24);
  } catch { /* audio unavailable — silent, never throws */ }
}
