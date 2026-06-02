// A trivial SVG sparkline — a single <polyline> over a capped sample series
// (lib/feed keeps the ring at MAX_SERIES). Shows the TREND, not just the
// instantaneous number, in the persistent chrome. Pure SVG, ~no cost, no deps.
// State here is purely decorative trend; the live number beside it carries the
// meaning, so reduced-motion / colour-blindness lose nothing.

export function Sparkline({ data, width = 56, height = 18, stroke = "var(--accent)", fill }:
  { data: number[]; width?: number; height?: number; stroke?: string; fill?: string }) {
  if (data.length < 2) {
    // honest empty: a flat baseline rather than a misleading zigzag
    return (
      <svg width={width} height={height} aria-hidden className="block">
        <line x1={0} y1={height - 1} x2={width} y2={height - 1} stroke="var(--edge)" strokeWidth={1} />
      </svg>
    );
  }
  const max = Math.max(...data);
  const min = Math.min(...data);
  const span = max - min || 1;
  const n = data.length;
  const pts = data.map((v, i) => {
    const x = (i / (n - 1)) * (width - 2) + 1;
    const y = height - 1 - ((v - min) / span) * (height - 2);
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  });
  const last = pts[pts.length - 1].split(",");
  return (
    <svg width={width} height={height} aria-hidden className="block overflow-visible">
      {fill && (
        <polygon
          points={`1,${height - 1} ${pts.join(" ")} ${width - 1},${height - 1}`}
          fill={fill} stroke="none"
        />
      )}
      <polyline points={pts.join(" ")} fill="none" stroke={stroke} strokeWidth={1.25}
        strokeLinejoin="round" strokeLinecap="round" />
      <circle cx={last[0]} cy={last[1]} r={1.5} fill={stroke} />
    </svg>
  );
}
