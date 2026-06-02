// tauri.ts — the SINGLE seam to Tauri's API. Every invoke()/listen() in the
// shell goes through here and nowhere else, so the shell can run in a plain
// browser during development (the mock rejects/no-ops) and against the real app
// unchanged. This is the same discipline as the web UI's lib/api + lib/events.
import { invoke as tauriInvoke, Channel as TauriChannel } from "@tauri-apps/api/core";
import { listen as tauriListen, type EventCallback, type UnlistenFn } from "@tauri-apps/api/event";

export const IS_TAURI = typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;

// A Tauri Channel (ordered, high-throughput — the v2 run stream rides this, NOT
// emit). Returns an object to pass as a command arg; in a plain browser it's an
// inert stand-in so the shell stays dev-able.
export function makeChannel<T>(onMessage: (m: T) => void): unknown {
  if (!IS_TAURI) return {};
  const ch = new TauriChannel<T>();
  ch.onmessage = onMessage;
  return ch;
}

export function invoke<T = unknown>(cmd: string, args?: Record<string, unknown>): Promise<T> {
  if (!IS_TAURI) return Promise.reject(new Error(`mock invoke (no Tauri): ${cmd}`));
  return tauriInvoke<T>(cmd, args);
}

export function listen<T = unknown>(event: string, cb: EventCallback<T>): Promise<UnlistenFn> {
  if (!IS_TAURI) return Promise.resolve((() => {}) as UnlistenFn);
  return tauriListen<T>(event, cb);
}
