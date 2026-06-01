// The one-time launch token travels in the URL `hm ui` opens (?token=…). We read
// it once into module memory and keep it there — NEVER in localStorage, cookies,
// or any persistent store. It is the CSRF synchronizer token for POSTs too.

const params = new URLSearchParams(window.location.search);
export const launchToken: string = params.get("token") ?? "";

// Cosmetic: drop the token from the visible address bar after reading it, so it
// doesn't sit in the URL bar / get copied accidentally. Reloads still work because
// the EventSource/fetch layer holds it in memory; a hard reload without the token
// will prompt re-open from the `hm ui` console line.
if (launchToken && window.history.replaceState) {
  params.delete("token");
  const q = params.toString();
  // Preserve the hash route (#/run/… etc.) when stripping the token from the URL.
  window.history.replaceState({}, "", window.location.pathname + (q ? `?${q}` : "") + (window.location.hash || ""));
}

export const hasToken = launchToken.length > 0;
