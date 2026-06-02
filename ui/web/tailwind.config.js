/** @type {import('tailwindcss').Config} */
// PART II design system — the Vercel/Linear discipline applied as tokens. The
// existing semantic names (ink/mist/accent/good/warn/bad) are remapped to the
// directive's exact values so the whole UI adopts the premium palette without a
// component rewrite; new tokens (bg-*, border-*, text-*, status-*) are added too.
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      fontFamily: {
        sans: ["Inter", "Geist", "system-ui", "-apple-system", "Segoe UI", "sans-serif"],
        mono: ["Geist Mono", "JetBrains Mono", "ui-monospace", "SF Mono", "Menlo", "monospace"],
      },
      colors: {
        // Background layers (base → surface → overlay → recessed input).
        ink: {
          950: "#0a0a0b",  // bg-base
          900: "#131316",  // bg-surface (cards, panels)
          850: "#1c1c21",  // bg-overlay (popovers, raised)
          800: "#202027",  // hairline borders / badge fills
          700: "#2a2a30",
          600: "#3a3a42",
        },
        // Semantic background aliases (the directive's names).
        bg: { base: "#0a0a0b", surface: "#131316", overlay: "#1c1c21", input: "#0e0e10" },
        // Text hierarchy (mist scale: 100 brightest → 400 dimmest).
        mist: {
          100: "#ededef",  // primary (headings, key values)
          200: "#cfcfd6",
          300: "#a1a1aa",  // secondary (body, labels)
          400: "#6b6b73",  // tertiary (captions, hints, cost)
          500: "#52525a",  // quaternary (timestamps, faint meta)
          600: "#3f3f46",  // faintest (gutter rules, disabled)
        },
        text: { primary: "#ededef", secondary: "#a1a1aa", tertiary: "#6b6b73", disabled: "#3f3f46" },
        // Accent — primary action + live/active state ONLY.
        accent: { DEFAULT: "#4d8dff", hover: "#6fa3ff", soft: "rgba(77,141,255,0.12)" },
        // Status (always paired with icon + label in the UI; never colour-alone).
        good: { DEFAULT: "#3fb950", soft: "rgba(63,185,80,0.15)" },
        warn: { DEFAULT: "#d29922", soft: "rgba(210,153,34,0.15)" },
        bad: { DEFAULT: "#f85149", soft: "rgba(248,81,73,0.15)" },
        status: { success: "#3fb950", warning: "#d29922", error: "#f85149", info: "#58a6ff" },
        code: { bg: "#0e0e10", text: "#c9d1d9" },
      },
      borderRadius: {
        // Sharp — never > 8px. Remap the keys components already use.
        DEFAULT: "4px", sm: "2px", md: "4px", lg: "4px", xl: "4px", "2xl": "6px", full: "9999px",
      },
      letterSpacing: { tightish: "-0.01em", tight2: "-0.02em" },
      keyframes: {
        pulse2: { "0%,100%": { opacity: "0.35" }, "50%": { opacity: "1" } },
        risein: { "0%": { opacity: "0", transform: "translateY(6px)" },
                  "100%": { opacity: "1", transform: "translateY(0)" } },
        flash: { "0%": { backgroundColor: "transparent" },
                 "30%": { backgroundColor: "rgba(77,141,255,0.12)" },
                 "100%": { backgroundColor: "transparent" } },
        // marching-ants for the active flow edge (n8n/ComfyUI style): the dash
        // pattern scrolls along the stroke. State is also conveyed by colour, so
        // reduced-motion (which freezes this) loses nothing.
        dash: { to: { strokeDashoffset: "-12" } },
      },
      animation: {
        pulse2: "pulse2 2.4s ease-in-out infinite",
        risein: "risein 0.2s ease-out",
        flash: "flash 1.1s ease-out",
        dash: "dash 0.6s linear infinite",
      },
    },
  },
  plugins: [],
};
