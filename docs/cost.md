# Cost — the ledger and `hm cost`

hermes-max records **every** model call to a ledger and prints an honest spend view
with `hm cost`. The whole point of the design is that you can *see* what the free
constellation saves you and prove the paid tiers stay sparing.

## What `hm cost` shows

The ledger records tokens + USD at **`$0.000000` precision** (six decimals — fan-out
costs live in the 4th–6th). Free providers record real token counts at `$0.000000`,
so you see *volume* even at zero cost.

```bash
hm cost            # month-to-date
hm cost today      # a narrower window
hm cost week
```

It breaks down by:

- **totals** for today / week / month, by provider, model, and role;
- a **free-vs-paid split** — tokens served at $0 vs tokens that cost money, i.e. how
  much the free constellation is saving you;
- **remaining daily free budget** per free model (from the bucket tracker), so you
  know how much fan-out budget is left today.

The bucket tracker parses Groq / OpenRouter / Cerebras rate-limit headers into a
unified remaining-RPM/TPM/RPD view, and the router **pre-checks** it so a rung is
skipped *before* sending — you never absorb a 429.

## What each mode costs

| Mode | Typical monthly | What you're paying for |
|---|---|---|
| `local` / `free` | $0.00 | nothing — local + free tiers only |
| `full-local` | ~$1.50 | V4-Pro planning (~$0.05/day); local execution free |
| `full` | ~$17 | V4-Pro plans + V4-Flash drives, both API, no rate limits |
| `frontier-local` | ~$45 | Opus planning + local execution |
| `frontier` | ~$60 | Opus planning + V4-Flash execution |

Measured anchors from development: V4-Pro **~$0.0035/brief**, V4-Flash
**~$0.00022/brief**; an Opus frontier call **~$0.08–1.25** (~$0.10 cached). See
[providers.md](providers.md) for per-token prices.

## The frontier tier is deliberately rare

`hm cost` proves the Opus rung stays sparing: it shows the month-to-date per-tier
spend, the Opus call count + cost against the sparing target (≤ ~15/month), the
frontier-mode total versus a Claude Code flat subscription, and a **warning if Opus
drifts over target** — which means either the difficulty gate is too loose, or the
work is genuinely blue-ocean and a Claude Code subscription may fit you better
(reported honestly). A hard monthly/daily frontier USD cap blocks and falls back to
V4-Pro when hit.

## The bigger economic picture

For a dispassionate, fully-worked comparison of the local-sovereign, BYOK-API, and
commercial-subscription options — including hardware amortization and the cases
where a commercial subscription is genuinely the right answer — see
**[local-vs-subscription.md](local-vs-subscription.md)**.

The mechanics of the ledger and routing live in
[architecture.md](architecture.md) §14.
