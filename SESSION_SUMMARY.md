# Session summary: from "docker command not found" to a patched goal loop

This is the narrative version of everything in this repo's newer
subfolders — one continuous working session on a real, running Hermes
Agent + Ollama + Docker deployment, told in the order it actually
happened. Each per-topic subfolder has its own diagrams and technical
detail; this document is the connective tissue between them, for anyone
who wants the whole arc rather than jumping in cold to one piece of it.

Every claim below was checked against a live system during the session
— `docker exec`, `journalctl`, real API calls, real test suites — not
inferred from documentation or memory. Where something turned out to be
intentional design rather than a bug, that's called out explicitly
rather than "fixed."

---

## 1. The starting question: "why can't Hermes see Docker?"

Hermes Agent's `terminal.backend: docker` runs every shell/file tool
call *inside* a sandbox container, not on the host directly — that's
the isolation model from the original `hermes-agent-blast-radius`
incident writeup. Asked to inspect Docker containers, the agent
reported it couldn't, and reasoned (wrongly, as it turned out to be a
recurring pattern) that it had no access at all.

Root cause: the sandbox genuinely has no `docker` CLI and no
`/var/run/docker.sock` — by design. Mounting the host's Docker socket
into a sandboxed container is a well-known root-equivalent privilege
escalation (any socket-holder can start a `--privileged` container with
the host filesystem bind-mounted in), a materially bigger grant than
anything else this deployment does. Left closed on purpose. Full
reasoning: [`system_architecture.md`](system_architecture.md) and
[diagram 16](diagrams/16_sandbox_docker_in_docker_gap.svg).

## 2. Model tuning: fitting a 9B model fully into VRAM

Separately, `qwen3.5:9b` was observed running a 12%/88% CPU/GPU split
despite comfortable free VRAM. Root-caused via `journalctl`: not the KV
cache (already GPU-resident and quantized) but an oversized compute
buffer from a large batch size. Fixed with a derived Ollama tag
(`qwen3.5:9b-vram-fit`, `num_batch` and `num_ctx` tuned), verified
`offloaded 34/34 layers to GPU` at full native 262,144-token context.
This became the deployment's active chat model for the rest of the
session. (Not its own subfolder — this was infrastructure tuning that
enabled everything downstream, covered in project memory rather than a
dedicated writeup here.)

## 3. Building `HERMES_DOCKER_SANDBOX_REFERENCE.md` — and what it found

Asked to document the sandbox as a tool-spec-style reference (mirroring
an existing Hermes-tools reference doc), placed directly inside the
sandbox's own mounted workspace so the agent itself can read it. Writing
it surfaced real findings beyond what was already known:

- **A persistent container's reuse is keyed by a profile label fixed at
  creation time** — `docker_extra_args` changes (GPU passthrough, in
  this case) silently don't apply across a Hermes process restart, only
  an explicit container removal picks it up.
- **The runtime user's actual `$HOME` isn't the host directory it looks
  like it should be** — a `pip install --user` package landed somewhere
  that doesn't survive a container recreation.
- **`--cap-drop ALL` isn't zero capabilities** — `docker inspect` showed
  `CapAdd=[CAP_CHOWN CAP_DAC_OVERRIDE CAP_FOWNER]`, three capabilities
  added back, which turned out to matter a lot in the next phase.
- **Three RAM-backed tmpfs scratch mounts** (`/tmp`, `/var/tmp`, `/run`)
  the original filesystem picture had missed — and, later, a real
  correction: `/tmp`'s *actual* live mount is `noexec`, contradicting
  what `docker inspect`'s own config field reported.

Published as [`sandbox-reference/`](sandbox-reference/) — 7 diagrams,
the full reference doc, and a dedicated logo.

## 4. Hardware and language introspection: a pattern of wrong self-diagnosis

A live agent, asked about IRQ assignments and system logs, tried the
tools a bare-metal admin would reach for (`lspci`, `dmidecode`,
`/proc/interrupts`, `lnav`), hit dead ends, and concluded the whole
category of information was "hidden by the Docker VM abstraction." It
wasn't a VM, and most of the information was one directory away in
`/sys`. Similarly, asked about Nim, Go, and Rust support, and asked to
batch-summarize a set of PDFs, the agent's own generated code kept
breaking on basic syntax errors rather than reaching the actual task.

The pattern across all of these: **the tool or library was usually not
the problem — the agent's own diagnosis, or its own generated code, was.**
Fixes landed one at a time, published as
[`sandbox-reference/hardware-introspection/`](sandbox-reference/hardware-introspection/)
(7 diagrams):

| Finding | Fix |
|---|---|
| No `lspci`/`dmidecode`/`lshw`/`lnav` | Installed via `apt-get -o APT::Sandbox::User=root` — plain `apt-get` fails even as root under `--cap-drop ALL` because apt's own internal privilege drop needs `CAP_SETUID`/`CAP_SETGID` |
| `dmidecode` installed but still fails, even as root | Needs `CAP_SYS_RAWIO` for `/dev/mem` — correctly absent from the `CapAdd` list found in phase 3; a real capability wall, left closed |
| PDF batch-summarization producing empty `.md` files | The model's inline PyMuPDF extraction+writing code kept breaking; split into a reliable `pdftotext`/`pdfinfo`-based script plus leaving only the insight-writing to the model |
| "Can Nim access `asyncdispatch`/`asyncnet`/`illwill`/`memfiles`?" | Three of four were already Nim standard library — zero action needed; only `illwill` (a real third-party package) needed `nimble install` |
| Go/Rust/Nim binaries failing with `Permission denied` on their own fresh builds | `/tmp` is `noexec` in practice (not just in the earlier tmpfs finding's config read) — always build under `/workspace` instead |
| No `pandas`/`matplotlib` | `pip3 install --user` — verified with a real `DataFrame` and a real rendered PNG chart |

## 5. Two architectures for "get help when stuck"

Two follow-up architecture questions, both answered by reading Hermes
Agent's own source directly rather than guessing:

**"Can an agent call a frontier model for help?"** — Yes:
`delegate_task` spawns isolated subagent contexts, and
`delegation.provider`/`delegation.model` in `config.yaml` can pin those
children to a specific model instead of inheriting the parent's local
model. Pinned to Claude Sonnet 4.6, using credentials that were already
live for an unrelated auxiliary role — zero new setup. Verified via a
real delegation manifest, not just a config read-back. Documented the
honest trade-off: this is a global switch, every delegation now costs
real API money, not only the ones where the local model is genuinely
stuck. A follow-up added an `AGENTS.md` nudge telling the agent to
self-delegate proactively, and surfaced one more instance of a pattern
seen throughout this session: the config change didn't apply to an
already-running session, because Hermes caches its config once at
process start. Published as
[`delegate-to-frontier/`](delegate-to-frontier/) (4 diagrams).

**"What about a frontier model *guiding* the agent, not just being
delegated to?"** — Also yes, and already running: `/goal` mode attaches
a frontier judge model to the *same* session, automatically, every
turn. Reading `hermes_cli/goals.py` found a real gap: the judge's
diagnosis was computed every turn and then discarded, replaced with a
generic templated nudge — the frontier model's actual reasoning never
reached the agent trying to act on it. Patched locally, verified with a
real judge API call. **Before proposing this upstream, searched
Hermes Agent's own issue/PR tracker first** (its `CONTRIBUTING.md`
requires this) and found an already-open, unmerged PR doing the same
fix, independently, by another contributor — so no competing PR was
opened. That PR's own code review had caught a real edge case (judge
infrastructure failures leaking into the agent as fake task feedback)
that this local patch shared; closed locally before publishing
anything. Published as
[`goal-judge-guidance/`](goal-judge-guidance/) (4 diagrams), including
the real diff and the "search first, found a duplicate" story as a
diagram in its own right.

## 6. "Compaction fires too early" — a documented floor, not a bug

The last investigation of the session: a report that context compaction
was firing well before the window filled on two different local models.
Reading `agent/context_compressor.py` found a genuine, documented
safety mechanism — any model under a 512K-token context window has its
compaction trigger forced up to 75%, specifically to avoid a thrashing
failure mode the source comments describe in detail. Confirmed live in
the actual logs at exactly that ratio. Raised further to 92% on
request, verified against Hermes's own real config-resolution code, and
documented the honest cost: less reserved headroom means a higher
chance of a session hitting the provider's hard limit mid-compaction on
a large turn. Published as
[`compaction-threshold/`](compaction-threshold/) (2 diagrams).

---

## The throughline

A few things kept recurring across a session that touched Docker
internals, Ollama scheduling, Nim tooling, and Hermes's own Python
source:

- **A local model's own explanation for why something doesn't work is a
  hypothesis, not a fact.** "Hidden by Docker's VM abstraction," "Nim
  isn't accessible," and silently-broken PDF extraction code were all
  wrong or incomplete diagnoses that only got resolved by checking the
  actual system state directly.
- **Config on disk and a running process's actual behavior are two
  different facts**, repeatedly: Docker sandbox args, model defaults,
  delegation pinning, and an `AGENTS.md` nudge all needed the process
  restarted before they took effect, and a `docker inspect` config
  field once flatly disagreed with the real live mount.
- **Not everything that looks broken is broken.** The 75%
  small-context compaction floor and the `dmidecode`/`CAP_SYS_RAWIO`
  wall both looked like bugs from the outside and were deliberate,
  documented safety behavior once traced to source.
- **Before writing new code, check whether someone already did.** The
  goal-judge fix's own upstream search — required by the target
  project's contribution guidelines, not optional — turned up a
  duplicate in progress, with a real review catching a bug this
  session's own patch would otherwise have shipped too.

## Full subfolder index

| Subfolder | Diagrams | Topic |
|---|---|---|
| [`diagrams/`](diagrams/) | 16 | Core dual-GPU Hermes/Ollama/Docker architecture |
| [`workspace-persistence/`](workspace-persistence/) | 3 | A Docker-sandbox `cwd` bug, root-caused and fixed |
| [`sandbox-reference/`](sandbox-reference/) | 7 | Tool-spec-style sandbox reference + real findings |
| [`sandbox-reference/hardware-introspection/`](sandbox-reference/hardware-introspection/) | 7 | Wrong self-diagnosis → real fixes, one at a time |
| [`delegate-to-frontier/`](delegate-to-frontier/) | 4 | Pinning `delegate_task` to a frontier model |
| [`goal-judge-guidance/`](goal-judge-guidance/) | 4 | A real Hermes code fix + an upstream duplicate found |
| [`compaction-threshold/`](compaction-threshold/) | 2 | A documented anti-thrash floor, raised on request |

**43 diagrams total**, every one rendered from a real `.dot` source
alongside its own `.svg`/`.png`.

## License

MIT — see [`LICENSE`](LICENSE).
