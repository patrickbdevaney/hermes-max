/** @type {import('tailwindcss').Config} */
// PHASE 0 design system — now fully VAR-DRIVEN. Every colour resolves to
// `oklch(var(--TOKEN-c) / <alpha-value>)`, so (a) re-tuning a CSS var in
// index.css re-skins the whole UI, and (b) Tailwind opacity modifiers
// (`bg-accent/30`, `border-good/40`, `bg-ink-950/40`) just work — they inject
// the alpha into the OKLCH. The semantic names the components already use
// (ink / mist / accent / good / warn / bad / status) are preserved; two NEW
// identities — `conductor` (gold) and `executor` (slate) — carry the thesis.

// oklch channel-var helper: a colour that honours Tailwind's alpha modifier.
const c = (v) => `oklch(var(--${v}-c) / <alpha-value>)`;

export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}", "../ui/web/src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      fontFamily: {
        display: ["var(--font-display)"],
        sans: ["var(--font-sans)"],
        mono: ["var(--font-mono)"],
      },
      colors: {
        // Background layers (base → surface → overlay → recessed input).
        ink: {
          950: c("ink-950"), 900: c("ink-900"), 850: c("ink-850"),
          800: c("ink-800"), 700: c("ink-700"), 600: c("ink-600"),
          input: c("ink-input"),
        },
        // Semantic background aliases (the directive's names).
        bg: { base: c("ink-950"), surface: c("ink-900"), overlay: c("ink-850"), input: c("ink-input") },
        // Text hierarchy (mist scale: 100 brightest → 600 dimmest).
        mist: {
          100: c("mist-100"), 200: c("mist-200"), 300: c("mist-300"),
          400: c("mist-400"), 500: c("mist-500"), 600: c("mist-600"),
        },
        text: { primary: c("mist-100"), secondary: c("mist-300"), tertiary: c("mist-400"), disabled: c("mist-600") },
        // Accent — primary action + live/active state ONLY. `soft` mirrors
        // DEFAULT so `accent-soft/15` resolves to the intended low alpha.
        accent: { DEFAULT: c("accent"), hover: c("accent-hover"), soft: c("accent") },
        // Status (always paired with icon + label in the UI; never colour-alone).
        good: { DEFAULT: c("status-success"), soft: c("status-success") },
        warn: { DEFAULT: c("status-warning"), soft: c("status-warning") },
        bad:  { DEFAULT: c("status-error"),   soft: c("status-error") },
        status: {
          success: c("status-success"), warning: c("status-warning"),
          error: c("status-error"), info: c("status-info"),
        },
        // The two thesis identities (NEW).
        conductor: { DEFAULT: c("conductor"), hover: c("conductor-hover"), soft: c("conductor") },
        executor:  { DEFAULT: c("executor"), soft: c("executor") },
        code: { bg: c("ink-input"), text: c("code-text") },
      },
      borderRadius: {
        // Sharp — never > 8px.
        DEFAULT: "4px", sm: "2px", md: "4px", lg: "6px", xl: "8px", "2xl": "8px", full: "9999px",
      },
      spacing: {
        // 4 / 8 px scale (mirrors --space-*); no arbitrary values anywhere.
        1: "4px", 2: "8px", 3: "12px", 4: "16px", 5: "20px", 6: "24px", 8: "32px", 10: "40px",
      },
      letterSpacing: { tightish: "-0.01em", tight2: "-0.02em" },
      keyframes: {
        pulse2: { "0%,100%": { opacity: "0.35" }, "50%": { opacity: "1" } },
        risein: { "0%": { opacity: "0", transform: "translateY(6px)" },
                  "100%": { opacity: "1", transform: "translateY(0)" } },
        flash: { "0%": { backgroundColor: "transparent" },
                 "30%": { backgroundColor: "oklch(var(--accent-c) / 0.12)" },
                 "100%": { backgroundColor: "transparent" } },
        // marching-ants for the active flow edge (n8n/ComfyUI style).
        dash: { to: { strokeDashoffset: "-12" } },
        // conductor intervention pin drop (Phase 3); colour also encodes it.
        pindrop: { "0%": { opacity: "0", transform: "translateY(-8px)" },
                   "100%": { opacity: "1", transform: "translateY(0)" } },
      },
      animation: {
        pulse2: "pulse2 2.4s ease-in-out infinite",
        risein: "risein 0.2s ease-out",
        flash: "flash 1.1s ease-out",
        dash: "dash 0.6s linear infinite",
        pindrop: "pindrop 0.25s ease-out",
      },
    },
  },
  plugins: [],
};
