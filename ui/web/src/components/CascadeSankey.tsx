// Phase 2.3 — the provider-cascade visualization. A hand-rolled SVG Sankey
// (no d3-sankey / visx dependency — holds the bundle budget) showing where the
// run's tokens actually routed: a single source fans out to PROVIDERS (band
// width ∝ token volume), then each provider flows into a FREE or PAID sink
// (a provider with $0 spend is free-tier — the asymmetry the thesis is about).
// Built only from real ledger data (CostReport.by_provider); nothing inferred
// beyond free⇔$0, which is honest.
import type { CostReport } from "../types";
import { fmtInt, fmtUsd } from "./ui";

const W = 560, H = 240, PAD = 8;
const X_SRC = 12, NODE_W = 14;
const X_PROV = W * 0.45, X_SINK = W - 12 - NODE_W;

function band(x1: number, y1a: number, y1b: number, x2: number, y2a: number, y2b: number): string {
  const mx = (x1 + x2) / 2;
  return [
    `M${x1},${y1a}`,
    `C${mx},${y1a} ${mx},${y2a} ${x2},${y2a}`,
    `L${x2},${y2b}`,
    `C${mx},${y2b} ${mx},${y1b} ${x1},${y1b}`,
    "Z",
  ].join(" ");
}

export function CascadeSankey({ report }: { report: CostReport }) {
  const provs = Object.entries(report.by_provider ?? {})
    .filter(([, b]) => b.tok > 0)
    .sort((a, b) => b[1].tok - a[1].tok);
  const total = provs.reduce((s, [, b]) => s + b.tok, 0);
  if (total <= 0) return null;

  const usable = H - PAD * 2;
  const gap = provs.length > 1 ? Math.min(10, (usable * 0.15) / (provs.length - 1)) : 0;
  const usableNet = usable - gap * (provs.length - 1);
  const sc = usableNet / total;

  // provider node rects (middle column)
  let y = PAD;
  const nodes = provs.map(([name, b]) => {
    const h = Math.max(2, b.tok * sc);
    const rect = { name, b, y, h, free: b.usd <= 0 };
    y += h + gap;
    return rect;
  });

  // sink totals
  const freeTok = nodes.filter((n) => n.free).reduce((s, n) => s + n.b.tok, 0);
  const paidTok = total - freeTok;
  const freeH = freeTok * sc;
  const paidH = paidTok * sc;

  // sink layout (free on top, paid below)
  const freeY = PAD;
  const paidY = PAD + (freeTok > 0 ? freeH + gap : 0);

  // running offsets for links arriving at each sink
  let freeCursor = freeY;
  let paidCursor = paidY;
  // running offset on the source node
  let srcCursor = PAD;

  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="block w-full" role="img"
      aria-label="provider cascade: tokens routed by provider, then free vs paid">
      {/* source node */}
      <rect x={X_SRC} y={PAD} width={NODE_W} height={usableNet + gap * (provs.length - 1)} rx={2} fill="var(--accent)" opacity={0.85} />
      <text x={X_SRC} y={PAD - 2} className="fill-mist-400" fontSize={9}>run tokens</text>

      {/* sink nodes */}
      {freeTok > 0 && <rect x={X_SINK} y={freeY} width={NODE_W} height={Math.max(2, freeH)} rx={2} fill="var(--status-success)" opacity={0.85} />}
      {paidTok > 0 && <rect x={X_SINK} y={paidY} width={NODE_W} height={Math.max(2, paidH)} rx={2} fill="var(--status-warning)" opacity={0.85} />}
      {freeTok > 0 && <text x={X_SINK + NODE_W} y={freeY + 10} textAnchor="end" className="fill-good" fontSize={9}>free {fmtInt(freeTok)}</text>}
      {paidTok > 0 && <text x={X_SINK + NODE_W} y={paidY + 10} textAnchor="end" className="fill-warn" fontSize={9}>paid {fmtInt(paidTok)}</text>}

      {/* links: source → provider → sink */}
      {nodes.map((n) => {
        const srcA = srcCursor, srcB = srcCursor + n.h; srcCursor = srcB + gap;
        const sink = n.free ? "free" : "paid";
        let sinkA: number;
        if (sink === "free") { sinkA = freeCursor; freeCursor += n.h; }
        else { sinkA = paidCursor; paidCursor += n.h; }
        const sinkB = sinkA + n.h;
        const stroke = n.free ? "var(--status-success)" : "var(--status-warning)";
        return (
          <g key={n.name}>
            {/* source → provider */}
            <path d={band(X_SRC + NODE_W, srcA, srcB, X_PROV, n.y, n.y + n.h)} fill="var(--accent)" opacity={0.14} />
            {/* provider → sink */}
            <path d={band(X_PROV + NODE_W, n.y, n.y + n.h, X_SINK, sinkA, sinkB)} fill={stroke} opacity={0.14} />
            {/* provider node */}
            <rect x={X_PROV} y={n.y} width={NODE_W} height={n.h} rx={2} fill="var(--executor)" />
            <title>{`${n.name} · ${fmtInt(n.b.tok)} tok · ${n.free ? "free" : fmtUsd(n.b.usd)} · ${n.b.calls} calls`}</title>
            {n.h > 11 && (
              <text x={X_PROV + NODE_W + 4} y={n.y + n.h / 2 + 3} className="fill-mist-300" fontSize={9}>
                {n.name}
              </text>
            )}
          </g>
        );
      })}
    </svg>
  );
}
