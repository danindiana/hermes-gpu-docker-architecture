# Research Proposal: Deterministic Workspace Persistence for a Containerized LLM Agent

## Problem statement

A Hermes Agent session, running with `terminal.backend: docker` (the
Docker-sandboxed terminal tool documented in the sibling
[`hermes-agent-blast-radius`](https://github.com/danindiana/hermes-agent-blast-radius)
repo), was asked to create a session folder and write a summary document.
It reported success, and it wasn't lying — the files were real. But they
landed at `/root/hermes-sessions/session_.../` **inside the sandbox
container**, not at the persistent, host-mounted `/workspace`. That
`/root` is bind-mounted from an ephemeral per-sandbox scratch directory on
the host, wiped every time the persistent container is recreated (model
switches, config changes, `docker_extra_args` updates — all of which
recreate the container). The agent's own self-report ("I cannot open
folders using dolphin... you can access the folder at
`/root/hermes-sessions/...`") was accurate about its own constraints, and
inaccurate only in the sense that the path it gave would stop existing the
next time its own sandbox got rebuilt.

This is silent, intermittent data loss with no error at write time.

## Prior mitigation and why it wasn't enough

`hermes-agent-blast-radius` documents a related, earlier attempt at this
exact problem (its 5th addendum): placing an `AGENTS.md` file at the real
launch working directory, telling the agent to always operate under
`/workspace`. That was a real, measured improvement — but the same
writeup honestly reported a gap: "a byproduct copy still briefly appears
under `$HOME`." It relied on the agent choosing to read and follow a text
instruction, which only works if the agent's working directory happens to
already be somewhere that instruction can be discovered from in the first
place.

## Root cause

See [`SOURCE_TRACE.md`](SOURCE_TRACE.md) for the full source-level trace. In
short: `terminal.cwd: .` in `~/.hermes/config.yaml` is not a literal path,
it's a placeholder — and for the `docker` backend, with
`docker_mount_cwd_to_workspace: false`, that placeholder resolves to
`None`, which then falls back to a hardcoded default of `/root`. This
happens before AGENTS.md discovery ever runs, and AGENTS.md discovery
never looks at `$HOME` in the first place — so no amount of correct
AGENTS.md placement could have closed this gap. The two mitigations were
addressing different layers of the same problem: one is a soft
instruction to the model, the other is a hard default in the code path
that decides where the model's tools actually operate before the model
gets a say.

## Proposed fix

Set `terminal.cwd: /workspace` explicitly — a literal absolute in-container
path, not a placeholder. This is accepted by the same validation that
already exists (`_is_unusable_container_cwd()` only rejects host-style
paths, not container-native ones like `/workspace`), and it makes the
resolved cwd unconditional: independent of `docker_mount_cwd_to_workspace`,
independent of `home_mode`, and independent of whether the model reads or
obeys any instruction file. This closes the gap the AGENTS.md mitigation
left open, without contradicting or replacing it — AGENTS.md placement
remains good defense-in-depth for narrative/instructional guidance, it's
just no longer load-bearing for *where writes physically land*.

## Proposed follow-on work (not implemented here)

There is currently no safety-net guard anywhere in the hermes-agent
terminal/file-tool code that rejects or even flags a write landing outside
`/workspace`. The existing guard modules
(`tools/file_tools_write_guards.py`, `tools/threat_patterns.py`) cover
injection and dangerous-command detection, not write-location policy. A
natural next step — proposed, not built, since it requires patching the
upstream `hermes-agent` source rather than a config change — would be a
lightweight check in the terminal/file tool path: if the resolved
effective path for a write falls outside the configured workspace mount,
warn (or block, depending on `approvals` posture) instead of silently
succeeding. This would catch the *next* variant of this bug, whatever
causes it, rather than only the one root-caused here.

## Related finding (unrelated bug, noted for completeness)

While verifying the fix, Hermes's one-shot (`-z`) invocation mode was
found to reject requests against `devstral-small-2:24b-196k` with
`HTTP 400: "devstral-small-2:24b-196k" does not support thinking`. That
model's Ollama-reported capabilities are `completion, vision, tools` —
no `thinking` — yet Hermes appears to send a thinking/reasoning-related
parameter regardless of the active model's declared capabilities. This is
a separate, reproducible issue, flagged here for future investigation and
explicitly **not** addressed by this proposal.

## Evaluation / verification plan

1. Apply the config change to a real, working `~/.hermes/config.yaml`
   (backed up first).
2. Run a one-shot invocation that spins up its own fresh, non-persistent
   sandbox container (so the already-running persistent session's
   container, unaffected by a config file edit until it's recreated,
   can't produce a false pass) — confirm `pwd` resolves to `/workspace`
   and that a directory listing shows real, previously-persisted content
   rather than an empty fresh container.
3. Confirm no other terminal-tool behavior regressed (approvals, checkpoints,
   the existing mount scoping) — this change touches only the *default*
   cwd resolution, not the mount list or capability set.

This was executed, not just planned — see `SOURCE_TRACE.md` for the real
command output.
