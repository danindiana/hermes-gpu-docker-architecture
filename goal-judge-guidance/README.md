<p align="center">
  <img alt="diagrams" src="https://img.shields.io/badge/diagrams-4%20%C3%97%202%20formats-orange">
  <img alt="rendered-with" src="https://img.shields.io/badge/rendered%20with-Graphviz-2e8b57">
  <img alt="status" src="https://img.shields.io/badge/upstream-duplicate%20PR%20found-8b5cf6">
  <img alt="verified" src="https://img.shields.io/badge/every%20claim-live--verified-39d0ff">
</p>

# goal-judge-guidance

A follow-up to [`../delegate-to-frontier/`](../delegate-to-frontier/):
that subfolder documents *model-initiated, on-demand* help from a
frontier model (`delegate_task`). This one is about the other shape of
the same instinct — a frontier model **automatically supervising** a
running local-model session, turn by turn — and a real gap found in how
Hermes Agent already implements it.

## The question that started this

> "Instead of a sub-agent that gets delegated a task, what if we make
> the Hermes agent harness / model the *slave* and a frontier model or
> other the *master* model which guides the agent to completion of a
> goal or goals? Is there such functionality in Hermes?"

## What already exists: `/goal` mode + `auxiliary.goal_judge`

Diagram [01](diagrams/01_two_existing_supervision_patterns.svg).

Yes — and it's not hypothetical, it's already running in this
deployment. `/goal` mode attaches a frontier auxiliary model (currently
`claude-haiku-4-5-20251001`) to the **same session** — no isolated
subagent context, no spawn/summarize round trip. After every turn, the
judge evaluates the agent's last response against the standing goal and
returns one of four verdicts: `done`, `blocked`, `continue`, `wait`.
Architecturally, this is the closer match to "master guides slave":
same session, automatic, frontier-powered, no per-call opt-in needed
from the agent.

## The gap, found by reading the source directly

Diagram [02](diagrams/02_gap_and_fix.svg).

`hermes_cli/goals.py`'s `next_continuation_prompt()` — the function that
builds the message sent back to the agent after a `continue` verdict —
returned one of three **fixed, generic templates**:

> "Continue working toward this goal. Take the next concrete step. If
> you believe the goal is complete, state so explicitly and stop..."

The judge's actual one-sentence `reason` — computed fresh every turn,
stored in `state.last_reason` — was used **only** for the human-facing
status line and the log message. It never reached the agent. The
frontier model would correctly diagnose *why* the goal wasn't done yet;
that diagnosis was thrown away before the agent's next turn began.

**Real precedent, in the same file, that the fix is sound**: the kanban
goal-loop variant, `KANBAN_GOAL_CONTINUATION_TEMPLATE`, already includes
`Reason: {reason}` verbatim for its worker. This isn't a novel idea —
it's an existing, trusted pattern in this codebase that just wasn't
wired into the default `/goal` path.

## The fix

One new template constant, and `next_continuation_prompt()` refactored
from three early returns into building one `prompt` variable and
conditionally appending guidance before the final return. Full diff:
[`goals.py.patch`](goals.py.patch).

```python
_JUDGE_GUIDANCE_TEMPLATE = (
    "\n\nThe goal judge's note from its last review (use this to correct "
    "course, not just to confirm you're still working):\n{reason}"
)
```

**Deliberately left unchanged**: the `blocked` verdict still pauses and
asks the human, per the code's own comment — *"BLOCKED is NOT done:
pause so the user sees the judge's reason and can re-scope or override,
instead of burning turns on an unachievable goal."* That's a safety
valve, not the gap being fixed here.

## Before proposing this upstream: search first — and it mattered

Diagram [03](diagrams/03_search_first_found_a_duplicate.svg).

Hermes Agent's own `CONTRIBUTING.md` is explicit about this, ahead of
anything else in the PR process:

> "Search both open *and* merged PRs and issues for your topic or
> error symptom... If an open PR already addresses it, consider
> reviewing or improving that one instead of opening a competing
> duplicate."

Running that search (`gh search prs --repo NousResearch/hermes-agent
"goal judge"` and related terms) turned up
[**PR #93521, "fix(goals): feed judge feedback into continuation"**](https://github.com/NousResearch/hermes-agent/pull/93521)
— open, unmerged, filed 2026-08-24 by another contributor, doing
**the same fix**, independently found. **No competing PR was opened
here.**

That PR had already received a real code review, and the review caught
something worth knowing about even without submitting anything:
`state.last_reason` isn't only set on a genuine judge evaluation — the
judge-response parser sets it to **infrastructure error text** on a
parse or transport failure too (`"judge reply was not JSON: ..."`,
`"judge returned empty response"`). An unguarded version of this fix
would feed that error text to the agent as if it were real task
feedback after a judge outage — confusing at best, and exactly the
failure mode this local patch reproduced in its own first (broken test
environment) run, independently, before this search even happened.

**Both counters that discriminate this already exist on `GoalState`**
(`consecutive_parse_failures`, `consecutive_transport_failures`, both
reset to `0` on any clean evaluation) — so the local patch here gates
the appended guidance on both being zero, and additionally skips the
`"no reason provided"` placeholder fallback, closing the gap the
upstream review flagged before it ever shipped anywhere.

## Verification

- **Unit level**: 5 synthetic cases — a real reason (appended), a
  transport-failure reason (suppressed), a parse-failure reason
  (suppressed), the `"no reason provided"` placeholder (suppressed),
  and no reason at all (byte-identical to the pre-patch behavior). All
  pass.
- **End-to-end, real API round trip**: called `judge_goal()` directly
  through the actual runtime venv with a real (incomplete) task
  response. The live Claude Haiku judge returned a genuine, specific
  diagnosis — *"The agent has not yet written the haiku or created the
  file; it only confirmed the file doesn't exist..."* — and the patched
  `next_continuation_prompt()` correctly carried that exact text into
  the resulting prompt.
- **Regression check**: Hermes's own existing test suite for this file
  (`tests/hermes_cli/test_goals.py`, 45 tests) still passes in full.

## Scope and the real cost of a local patch

Diagram [04](diagrams/04_scope_boundary_and_patch_risk.svg).

`~/.hermes/hermes-agent` (this deployment's install) is a real local
`git` clone of `github.com/NousResearch/hermes-agent`, clean tree,
tracking `origin/main` — not a vendored blob. The fix is committed
**locally only**, not pushed anywhere, given the duplicate PR found
above. There's no supported plugin hook for this specific behavior, so
this is a genuine local patch to a third-party tree: a future `hermes
update` may overwrite it, and it would need to be reapplied or rebased.
That's a known, flagged, unresolved trade-off — not a solved problem.
If PR #93521 (or a successor with the same fix) merges upstream, this
local patch becomes redundant on the next update, which is the intended
outcome, not a failure of taking this approach.

Same restart-required caveat as every other config/code change
documented in this repo: the already-running interactive session has
the old module loaded in process memory and won't reflect this until
restarted.

## What about contributing `delegate-to-frontier` upstream too?

Also considered, and the honest answer is: there's less to contribute
there than it might look like. `delegation.provider`/`delegation.model`
already exist in Hermes as first-class config — nothing in
[`../delegate-to-frontier/`](../delegate-to-frontier/) is new
*functionality*, it's a configuration pattern applied to capability that
already fully works. Per `CONTRIBUTING.md`'s own priority list, that
puts a possible contribution at the bottom (**documentation** — "fixes,
clarifications, new examples"), not alongside the bug-fix-priority work
in this subfolder. Nothing was opened for it.

## Related

- [`../delegate-to-frontier/`](../delegate-to-frontier/) — the
  model-initiated, on-demand counterpart to this same-session,
  automatic supervision pattern.
- [`../sandbox-reference/`](../sandbox-reference/) — where several of
  the real local-model failures that motivated this investigation were
  originally root-caused.

## License

MIT — see [`../LICENSE`](../LICENSE) (this subfolder is covered by the
parent repo's license).
