<p align="center">
  <img alt="diagrams" src="https://img.shields.io/badge/diagrams-2%20%C3%97%202%20formats-orange">
  <img alt="rendered-with" src="https://img.shields.io/badge/rendered%20with-Graphviz-2e8b57">
  <img alt="verified" src="https://img.shields.io/badge/every%20claim-live--verified-39d0ff">
</p>

# compaction-threshold

A real "this looks like a bug" report — context compaction firing well
before the model's context window was full — that turned out to be a
deliberate, documented safety mechanism in Hermes Agent's own source,
plus a real config change and its honest trade-off once the operator
decided to override it anyway.

## The report

> "Context compaction seems to be firing before the context window limit
> is reached, leaving a lot of context still to be used. I've noticed
> this for the qwen3.5 model and the muse-glimmer models."

## What's actually happening: a raise-only small-context floor

Diagram [01](diagrams/01_small_context_floor_mechanism.svg).

`agent/context_compressor.py` has `_effective_threshold_percent()`: any
model with a context window under 512,000 tokens gets its compaction
trigger **forced up** to 75%, regardless of the configured
`compression.threshold` (default `0.50`). Both models in the report
qualify — `qwen3.5:9b-vram-fit` at 262,144 tokens and `muse-glimmer:30b`
at 131,072 tokens, both well under the 512K line.

Confirmed live, not just from reading the source — `agent.log` on the
deployment that reported this shows:

```
Pre-API compression: ~197,189 request tokens >= 196,608 threshold
(context=262,144, attempt=1/3)
```

196,608 / 262,144 = exactly 75.0%.

**Why the floor exists**, per the code's own comment: at 50% on a small
window, the protected/incompressible tail — system prompt, protected
recent messages, tool schemas — eats up most of whatever a single
compaction pass reclaims. The session would compact, immediately refill
past the threshold again, and thrash on repeated compaction every 1-2
turns. 75% leaves enough absolute headroom that one pass buys many
turns before firing again. What looked like "a lot of context left
unused" was actually the reserve that makes compaction worth doing at
all.

## Raising it anyway: the floor is raise-only

Diagram [02](diagrams/02_raising_it_and_the_tradeoff.svg).

The operator wanted more of the window used before compaction fires,
and asked to raise `compression.threshold` toward 0.90–0.95. The
floor's own logic supports this cleanly — it's explicitly *raise-only*:
`max(configured_threshold, 0.75)`. An explicit value above 0.85 is
honored as literal user intent and is **not** re-capped (confirmed
directly in the source comment for a separate internal cap that only
applies to the automatic floor, not an explicit user setting).

Set `compression.threshold: 0.92` in `~/.hermes/config.yaml`. Verified
**live, using Hermes's own real config-loading and context-resolution
functions** — not hand-typed arithmetic:

| Model | Context | New trigger | % of window | Headroom |
|---|---|---|---|---|
| `qwen3.5:9b-vram-fit` | 262,144 | 241,172 | 92.0% | 20,972 tokens |
| `muse-glimmer:30b` | 131,072 | 120,586 | 92.0% | 10,486 tokens |

## The honest trade-off

This is real, flagged, and **not yet observed** as an actual failure —
but the same deployment's own log showed a real compaction pass taking
**~118 seconds** to complete. At 92%, the reserved headroom (10.5K–21K
tokens depending on the model) is small enough that one large
tool-output turn arriving *during* that compaction window could push
the request past the provider's hard context limit before compaction
finishes — a "wedged" session, exactly the failure mode the 75% floor
exists to prevent in the first place, now reintroduced deliberately at
a smaller scale by choice.

If that happens: drop `compression.threshold` back down toward `0.85`.
The floor won't let it go below `0.75` for these window sizes regardless
of how low it's set.

Same restart-required caveat as every other config change documented in
this repo: an already-running Hermes session has the old threshold
resolved at process start and won't reflect a `config.yaml` change until
restarted.

## Related

- [`../goal-judge-guidance/`](../goal-judge-guidance/) — another case
  this session of reading Hermes's own source directly to separate
  "looks like a bug" from "documented, intentional design."

## License

MIT — see [`../LICENSE`](../LICENSE) (this subfolder is covered by the
parent repo's license).
