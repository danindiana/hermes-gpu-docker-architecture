<p align="center">
  <img src="assets/logo.svg" alt="sandbox-reference logo" width="480">
</p>

<p align="center">
  <img alt="diagrams" src="https://img.shields.io/badge/diagrams-14%20%C3%97%202%20formats-orange">
  <img alt="rendered-with" src="https://img.shields.io/badge/rendered%20with-Graphviz-2e8b57">
  <img alt="format" src="https://img.shields.io/badge/format-tool--spec%20style-8b5cf6">
  <img alt="verified" src="https://img.shields.io/badge/every%20claim-live--verified-39d0ff">
</p>

# sandbox-reference

The Docker sandbox that backs Hermes Agent's `terminal.backend: docker`
does more, and less, than it looks like it should. This subfolder is a
compact, tool-spec-style reference to what that container actually does
— filesystem mounts, network, GPU access, the docker-in-docker gap,
lifecycle, and a couple of gaps that only showed up by testing, not by
reading config. Every claim here was checked live (`docker inspect`,
`docker exec`, `journalctl`, a real `rm -rf` against `approvals.deny`,
a real PDF download) against a running Hermes Agent deployment — nothing
here is inferred from the config schema alone.

**Read [`REFERENCE.md`](REFERENCE.md) for the full doc.** This README is
the narrative tour; the reference doc is the thing meant to be consulted
like documentation (and, in the source deployment, the thing the agent
itself can read directly out of its own mounted workspace).

## Why this exists as its own subfolder

The parent repo's diagrams 3, 4, and 16 already cover the sandbox's
general isolation model, GPU passthrough, and the docker-in-docker gap.
This subfolder isn't a re-telling of those — it's what came out of
actually *using* the sandbox for a while afterward and hitting two things
config alone wouldn't have told you:

1. **A config change to the sandbox doesn't apply just because Hermes
   restarts.** GPU passthrough had been "added" to the config, the
   Hermes process had restarted twice since, and `nvidia-smi` still
   didn't work — because container reuse is keyed by a profile label
   that's fixed at creation time, not re-evaluated against current
   config. This is the standout finding here (diagram 2).
2. **The runtime user's actual `$HOME` isn't the host directory it looks
   like it should be.** A `pip install --user` landed somewhere that
   doesn't survive a container recreation — a gap that only a real
   `docker inspect` of the mount table and a real install-and-check
   caught (diagram 1, diagram 4).

Both are the kind of thing that's invisible from reading
`~/.hermes/config.yaml` alone, and both generalize past this one
operator's machine to anyone running a containerized coding agent with
config-driven `docker run` flags and a persistent sandbox filesystem.

## The diagrams

All 7 diagrams live in [`diagrams/`](diagrams/) as Graphviz `.dot`
sources, each rendered to both `.svg` and `.png` (same dark-neon style as
the rest of this repo).

1. **[Filesystem mount truth table](diagrams/01_filesystem_mount_truth_table.svg)**
   — `/workspace` (the only real bind mount, checkpointed), the
   container's actual `$HOME` (`/home/pn`, **not** mounted at all — the
   container's own throwaway layer), a *different* host directory that
   **is** mounted but lands at `/root` (a path the runtime user never
   touches), and the real host `$HOME` (never mounted, permission-denied
   on contact).
2. **[Container reuse ignores config changes](diagrams/02_container_reuse_ignores_config.svg)**
   — the mechanism, traced directly in the agent's own source: a
   persistent container's reuse identity is a profile label captured
   once at creation and never re-checked. Two real Hermes process
   restarts, same container both times, `--gpus=all` still not live.
   Only an explicit `docker rm -f` plus the framework's own
   "no such container" recovery path actually applies new config.
3. **[Sandbox at a glance](diagrams/03_sandbox_at_a_glance.svg)** — a
   cheat-sheet grid: identity/hardening facts, network facts, tools
   confirmed present, tools confirmed absent (including things you might
   assume are there and aren't, like `jq` or a working `apt-get`).
4. **[PDF tooling gap and fix](diagrams/04_pdf_tooling_gap_and_fix.svg)**
   — a real failure end-to-end: an extraction task blocked on missing
   `fitz`/PyMuPDF, the dead-end (`apt-get`, no root), the working fix
   (`pip3 install --user`), and the same non-persistence caveat from
   diagram 1 biting a second time in practice.
5. **[Guardrail coverage](diagrams/05_guardrail_coverage.svg)** — what
   `approvals.deny` and `checkpoints.enabled` actually protect
   (production-tested against a real destructive command) versus a real,
   confirmed gap: the container's own `$HOME` isn't checkpointed, only
   `/workspace` is.
6. **[Capabilities added back](diagrams/06_capabilities_added_back.svg)**
   — `--cap-drop ALL` isn't the whole story: `docker inspect` shows
   `CapAdd=[CAP_CHOWN CAP_DAC_OVERRIDE CAP_FOWNER]`, three capabilities
   added back on top of the drop-all baseline. These are why the
   `hardware-introspection/` apt-install technique actually worked
   cleanly, not just "ran as root" — and `CAP_SYS_RAWIO`'s conspicuous
   absence from this list is the real reason `dmidecode` still fails even
   as root.
7. **[tmpfs scratch mounts](diagrams/07_tmpfs_scratch_mounts.svg)** — a
   third filesystem category diagram 1 didn't cover: `/tmp` (512 MB),
   `/var/tmp` (256 MB), and `/run` (64 MB) are RAM-backed tmpfs, not bind
   mounts. Confirmed actively used (Hermes's own `execute_code` tool
   writes its Python kernel runner here) and wiped on **any** container
   stop/restart — a tighter durability bar than `/home/pn` or an
   apt/pip-installed package.

## Key takeaways

- **"The config was updated" and "the running container reflects the
  config" are two different facts.** For any Docker-backed persistent
  sandbox, check what the *live* container was actually started with
  (`docker inspect`) before trusting what a YAML file currently says.
- **A container's `$HOME` env var and its bind-mounted paths can silently
  diverge.** Don't assume "the container's home directory persists"
  without checking which literal path `$HOME` resolves to *and* whether
  that specific path is in the mount list — both facts, checked
  separately.
- **Non-root sandboxing (no `sudo`, `cap-drop ALL`) is real hardening,
  but it also means the obvious fix (`apt-get install`) silently isn't
  available** — `pip install --user` (or your language's equivalent
  user-scope package manager) is usually still open, worth checking
  before concluding a sandbox is fully locked down for that purpose.
- **"`--cap-drop ALL`" is a starting point people quote, not the full
  configuration.** Check the actual `CapAdd` list before reasoning about
  what a hardened container can or can't do — a small, deliberate set of
  capabilities added back can matter more than the drop-all headline.
- **Checkpointing scoped to one directory (`/workspace`) is not the same
  as checkpointing the whole sandbox.** If an agent's default working
  directory can ever drift from that directory, your safety net doesn't
  follow it.

## Follow-up round: hardware introspection (lspci/dmidecode/lshw/lnav)

A second real round of findings, from actually using the sandbox for
diagnostic questions ("what's the IRQ assignment", "can you access
system logs"), lives in its own subfolder:
[`hardware-introspection/`](hardware-introspection/) — a real diagnosis
failure (wrong conclusion: "hidden by the Docker VM abstraction") turned
into a working sysfs-based fix, a general technique for installing
packages into a `--cap-drop ALL` container, a genuine `CAP_SYS_RAWIO`
capability wall around `dmidecode` that stays closed on purpose, and a
log-viewer install that was deliberately *not* paired with host log
access, and a batch-PDF-summarization failure fixed by splitting extraction from insight-writing, and a /tmp noexec correction found while adding Go/Rust/Nim toolchains, and finding most requested Nim modules were already stdlib. 7 diagrams.

## Related

- [`../README.md`](../README.md) — the parent repo: full dual-GPU
  Hermes/Ollama/Docker architecture, 19 diagrams.
- [`../system_architecture.md`](../system_architecture.md) — the
  docker-in-docker gap this subfolder builds on top of.
- [`../workspace-persistence/`](../workspace-persistence/) — a related,
  separate real bug (files written to the wrong path entirely) and its
  fix, root-caused in the same source tree.
- [`hermes-agent-blast-radius`](https://github.com/danindiana/hermes-agent-blast-radius)
  — the original incident that led to this sandbox existing at all.

## License

MIT — see [`../LICENSE`](../LICENSE) (this subfolder is covered by the
parent repo's license).
