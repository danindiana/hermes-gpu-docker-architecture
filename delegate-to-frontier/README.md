<p align="center">
  <img alt="diagrams" src="https://img.shields.io/badge/diagrams-4%20%C3%97%202%20formats-orange">
  <img alt="rendered-with" src="https://img.shields.io/badge/rendered%20with-Graphviz-2e8b57">
  <img alt="model" src="https://img.shields.io/badge/pinned%20model-Claude%20Sonnet%204.6-8b5cf6">
  <img alt="verified" src="https://img.shields.io/badge/every%20claim-live--verified-39d0ff">
</p>

# delegate-to-frontier

Every other subfolder in this repo documents a case where the local
9B-parameter chat model (`qwen3.5:9b-vram-fit`) got stuck on something —
wrong IRQ diagnosis, syntactically broken PDF-extraction code, confused
Nim/Go/Rust code. This subfolder documents the fix for the underlying
*pattern*, not one more instance of it: giving the agent a real way to
ask something more capable for help when it's stuck, instead of
retrying the same class of mistake against itself.

## The question that started this

> "So we have a common issue where an agent is unable to figure
> something out. Is there some way in the Hermes agent harness to allow
> an agent to make an API call to a frontier model for help?"

The honest first answer was **not quite, not by default** — but the
mechanism to make it true already existed in the codebase, unused.

## What `delegate_task` actually is

Hermes Agent ships a `delegate_task` tool: the model can spawn one or
more subagents, each with its own isolated conversation, terminal
session, and toolset, and only the child's final summary returns to the
parent. It's built for exactly this kind of situation — the tool's own
schema description (read directly from `tools/delegate_tool.py`) says:

> "USE FOR: reasoning-heavy subtasks, work that would flood your
> context with intermediate data, or independent parallel workstreams."

A model that's genuinely stuck on a hard problem is a textbook case for
this tool. The catch, also stated directly in that same tool
description:

> "Children inherit the parent model unless pinned via
> `delegation.provider` / `delegation.model` in config.yaml."

**Unpinned, a delegated child is just another instance of the same
model.** For this deployment, that meant: the local qwen3.5:9b agent's
own subagents were *also* qwen3.5:9b — same knowledge gaps, same
tendency toward the same category of mistake. Delegation existed, but
it wasn't actually buying a second opinion. See diagram
[01](diagrams/01_default_inherits_parent_model.svg).

**This wasn't a hypothetical.** At the moment this was being
investigated, the live agent session was independently sitting in a
`goal_judge: verdict=blocked` state, reason: "the agent is writing
Python code that mixes Python and Nim syntax incorrectly" — a real,
concurrent instance of the exact failure mode this fix targets.

## The fix

One config change, in `~/.hermes/config.yaml`:

```yaml
delegation:
  provider: anthropic
  model: claude-sonnet-4-6
```

That's the entire change. No new tool, no new plugin, no code change —
the routing logic and the credential-resolution path were already
there; they just weren't pointed anywhere.

### No new credentials needed

Hermes already had a live Anthropic credential
(`credential_pool.anthropic` in `~/.hermes/auth.json`), because
`auxiliary.goal_judge` — the background classifier that judges whether
a `/goal`-mode session has actually finished — was already calling
Anthropic's `claude-haiku-4-5-20251001` for every turn. `delegate_task`
resolves credentials through the same pool. Pinning delegation to
Anthropic cost zero setup. See diagram
[02](diagrams/02_pin_to_sonnet_and_reuse_credentials.svg).

### Why Claude Sonnet 4.6, specifically

Checked against Hermes's own model catalog
(`~/.hermes/models_dev_cache.json`) before configuring anything, rather
than assuming a model ID:

| Field | Value |
|---|---|
| Model ID | `claude-sonnet-4-6` |
| Display name | Claude Sonnet 4.6 |
| Context window | 1,000,000 tokens |
| Max output | 64,000 tokens |
| Cost | $3 / $15 per million tokens (input / output) |
| Reasoning effort levels | low, medium, high, max |
| Modalities | text, image, PDF in → text out |
| Release date | 2026-02-17 |

Its own catalog description: *"Claude workhorse for coding agents,
careful analysis, and production cost control."* — a solid fit for a
model whose whole job here is picking up reasoning-heavy subtasks a
smaller local model bounced off of.

## Verified live, not just config read-back

This repo's standing rule (see the three-layer context-window footgun
documented in the parent `README.md`) is that a value sitting in
`config.yaml` is a claim, not a fact, until something real confirms it.
A one-shot delegation was triggered specifically to force a real
`delegate_task` call and check what actually ran:

```
hermes -z "Use the delegate_task tool to spawn exactly one subagent
with goal: 'Reply with exactly the words: delegation test ok'."
```

This produced a real delegation manifest,
`~/.hermes/cache/delegation/live/deleg_<id>/manifest.json`:

```json
{
  "model": "claude-sonnet-4-6",
  "provider": "anthropic",
  "tasks": [{"goal": "Reply with exactly the words: delegation test ok",
             "status": "completed"}],
  ...
}
```

Completed in 1.27 seconds — a real round trip to Anthropic's API, not a
local Ollama call. Test artifact removed afterward.

## What this is *not*: a per-call "ask for help" button

This is the trade-off worth being explicit about, and it's the subject
of diagram [03](diagrams/03_global_switch_not_per_call.svg):

- The model still decides **whether** to call `delegate_task` at all —
  that part of its judgment is unchanged.
- The model does **not** decide **which** model handles a given
  delegation. `delegation.provider`/`model` is a fixed, operator-set
  config value, not a parameter exposed in the tool's schema. There is
  no way, today, for the agent to say "route just this one to a bigger
  model" versus "route this one locally."
- The practical consequence: **every** `delegate_task` call from now
  on — not only the ones where the local model is genuinely stuck —
  goes to Claude Sonnet 4.6 and costs real API money. A model that
  over-delegates for mechanical work (the tool description explicitly
  warns against this: "DO NOT USE FOR: mechanical multi-step work with
  no reasoning needed") now does so at API rates instead of for free
  on local hardware.
- A cheaper middle ground exists and wasn't chosen here:
  `delegation.model: claude-haiku-4-5-20251001`, the same tier already
  proven reliable for `auxiliary.goal_judge`. If delegation volume or
  cost ever becomes a real concern, that's the same config key to
  adjust — or clear the block entirely to fall back to inheriting the
  parent's local model again.

## Follow-up: telling the agent to actually use it

A pinned model is only half the fix — the agent still has to *decide*
to call `delegate_task` in the first place. The follow-up question was
direct: *"I have a model which is stumped, how do I tell it to use that
feature to call for help?"*

### The nudge: AGENTS.md at the workspace root

[`AGENTS.md.example`](AGENTS.md.example) — a real copy of the file now
placed at `/workspace`'s root in this deployment (the sandbox's own
bind-mounted, checkpointed directory; see
[`../sandbox-reference/REFERENCE.md`](../sandbox-reference/REFERENCE.md)
§2) — tells the agent to call `delegate_task` **proactively, without
being asked**, specifically when it notices:

- it's retried the same class of fix twice or more (the same category
  of syntax error, the same wrong assumption),
- it's about to guess at an API or system behavior instead of verifying
  it, or
- a `/goal`-mode judge (or the operator) has flagged the same
  underlying issue twice.

It's deliberately scoped to *not* fire for mechanical work with no real
reasoning gap — the delegation tool's own schema already says not to
use it there, and repeating that in the nudge would just encourage
over-delegation (real cost, per the trade-off above).

### The gotcha found while testing this: config caching, not a new bug

Restarting the stuck live session to test the nudge surfaced one more
instance of a pattern already documented elsewhere in this repo: **a
config value being correct on disk doesn't mean the running process has
it.** The session that had been stuck had started well *before* the
`delegation.provider`/`model` edit — checked directly in Hermes's own
`cli.py`: `CLI_CONFIG = load_cli_config()` is a plain module-level
assignment, executed once at process start and never re-read. Exactly
the same shape as the `terminal.docker_extra_args` /
`model.default` findings in
[`../sandbox-reference/`](../sandbox-reference/) — the fix is the same
in every case: restart the process, don't just edit the file. Full
diagram: [04](diagrams/04_agents_md_nudge_and_restart_gotcha.svg).

## Why this belongs in this repo

Everything else here documents *symptoms* of a small local model
working inside a hardened, tool-limited sandbox — real failures,
root-caused and fixed one at a time
([`sandbox-reference/hardware-introspection/`](../sandbox-reference/hardware-introspection/)
has several). This subfolder documents the point where that pattern
was addressed structurally instead: giving the agent itself a real
escape hatch to a more capable model, using infrastructure (the
credential pool, the delegation mechanism) that already existed and
just needed to be pointed somewhere.

## License

MIT — see [`../LICENSE`](../LICENSE) (this subfolder is covered by the
parent repo's license).
