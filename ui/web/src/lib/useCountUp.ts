// A reduced-motion-safe number tween: eases a displayed value toward a target
// over ~500ms via requestAnimationFrame. When the user prefers reduced motion
// (or on first paint) it snaps instantly — the number's MEANING is the value,
// the motion is decoration, so snapping loses nothing (the Phase-0 contract).
import { useEffect, useRef, useState } from "react";

const REDUCED = typeof window !== "undefined"
  && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;

export function useCountUp(target: number, ms = 500): number {
  const [val, setVal] = useState(target);
  const from = useRef(target);
  const raf = useRef(0);

  useEffect(() => {
    if (REDUCED || ms <= 0) { setVal(target); from.current = target; return; }
    const start = performance.now();
    const a = from.current;
    const tick = (t: number) => {
      const p = Math.min(1, (t - start) / ms);
      const eased = 1 - Math.pow(1 - p, 3); // easeOutCubic
      const v = a + (target - a) * eased;
      setVal(v);
      if (p < 1) raf.current = requestAnimationFrame(tick);
      else from.current = target;
    };
    raf.current = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf.current);
  }, [target, ms]);

  return val;
}
