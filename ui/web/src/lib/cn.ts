// cn() — class-name composition (clsx-style), zero-dependency to preserve the
// build's sovereignty discipline. Accepts strings, arrays and conditional
// objects; flattens, drops falsy, joins. A full tailwind-merge (last-wins
// conflict resolution) isn't pulled in — the components compose deliberately
// rather than override, so a clsx-grade join is sufficient and ~0 bytes.
export type ClassValue = string | number | false | null | undefined | ClassValue[] | Record<string, boolean>;

export function cn(...parts: ClassValue[]): string {
  const out: string[] = [];
  for (const p of parts) {
    if (!p) continue;
    if (typeof p === "string" || typeof p === "number") out.push(String(p));
    else if (Array.isArray(p)) { const s = cn(...p); if (s) out.push(s); }
    else for (const k in p) if (p[k]) out.push(k);
  }
  return out.join(" ");
}
