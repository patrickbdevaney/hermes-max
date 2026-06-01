// A minimal popover: a trigger button + a panel that closes on outside-click or
// Escape. No portal/positioning lib — anchored relative to the trigger, right-
// aligned by default (the chrome popovers all open from the top-right).
import { useEffect, useRef, useState } from "react";
import type React from "react";

export function Popover({ trigger, children, align = "right", width = 320 }:
  {
    trigger: (open: boolean) => React.ReactNode;
    children: (close: () => void) => React.ReactNode;
    align?: "left" | "right";
    width?: number;
  }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <div ref={ref} className="relative">
      <button type="button" onClick={() => setOpen((o) => !o)} className="block">
        {trigger(open)}
      </button>
      {open && (
        <div
          className={`absolute top-[calc(100%+8px)] z-30 rounded-lg border border-ink-700 bg-ink-850 p-2 shadow-xl shadow-black/40 ${
            align === "right" ? "right-0" : "left-0"}`}
          style={{ width }}
          role="dialog"
        >
          {children(() => setOpen(false))}
        </div>
      )}
    </div>
  );
}
