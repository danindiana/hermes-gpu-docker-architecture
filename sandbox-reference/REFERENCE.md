# Hermes Docker Sandbox Reference

This document explains the Docker container that backs `terminal.backend:
docker` — the environment every `terminal`, `execute_code`, `write_file`,
and `patch` tool call actually runs inside. Read it the way you'd read a
tool spec: each section below is one thing you might need to know about
*where your shell commands actually execute*, not about Hermes's own
tools.

It's a live copy of the same file kept in this operator's Hermes
workspace (`HERMES_DOCKER_SANDBOX_REFERENCE.md`), where Hermes itself can
`read_file` it directly, alongside a general Hermes-tools reference doc.
Publishing it here is about the underlying mechanism — most of which
applies to anyone running Hermes Agent (or any similarly-designed
containerized agent) with a Docker-backed terminal tool, not just this
one machine.

**This sandbox is unrelated to model serving.** `model.provider: custom` /
`model.base_url` talk directly from the Hermes host process to Ollama at
`localhost:11434` — nothing about the model you're running goes through
this container. This document only covers the shell/file-tool sandbox.

See [`../README.md`](../README.md) for the diagrams and the full
narrative context this subfolder builds on.

---

## 1. What this container is

- Image: `hermes-sandbox:graphviz`.
- One persistent container per active Hermes **profile** (this one is
  profile `default`), reused across Hermes process restarts — not
  recreated per session. See §6, and diagram
  [02](diagrams/02_container_reuse_ignores_config.svg).
- Runs as a real Docker container on the host (`docker_image` under
  `terminal:` in `~/.hermes/config.yaml`), started with
  `--cap-drop ALL --user 1000:1000 --security-opt no-new-privileges`
  (confirmed via `docker inspect`) — non-root, no capability escalation.

## 2. Filesystem: what's mounted, what's not

See diagram [01](diagrams/01_filesystem_mount_truth_table.svg) for the
full picture.

- **`/workspace`** — the only writable bind mount to the real host
  filesystem (`~/Documents/hermes-sessions` on this operator's box). Your
  `cwd` inside the sandbox is `/workspace`. Anything written here is
  real, persists after the container is removed, and is covered by
  `checkpoints.enabled` (git-style snapshots, `max_snapshots: 20`).
- **Container's own `$HOME`** (`/home/pn` — the actual `$HOME` env var for
  the runtime user; `whoami` reports `pn`, uid 1000) — sandbox-only
  scratch space living in the container's own writable layer, **not**
  bind-mounted to the host at all (confirmed via `docker inspect`'s mount
  list — it's absent). It persists only as long as this specific
  container keeps running; it is wiped the moment the container is ever
  removed/recreated (§6/§4). **Anything installed here — e.g. a `pip
  install --user` python package — does not survive a container
  recreation and is not checkpointed.** Prefer `/workspace` for anything
  that matters, including packages you want to survive a GPU-passthrough
  fix (§4) or any other config-driven recreation. Real example: diagram
  [04](diagrams/04_pdf_tooling_gap_and_fix.svg).
- A separate host directory, `~/.hermes/sandboxes/docker/<profile>/home`,
  *is* bind-mounted — but to `/root` inside the container, a different
  path from the runtime user's actual `$HOME` (`/home/pn`). Don't assume
  writing to `$HOME` lands there.
- **The real host `$HOME`** (the operator's actual home directory) is
  **not mounted** at all. An attempt to reach it fails with Permission
  denied — confirmed live.

## 3. Network access

- `docker_network: true` → the container runs in normal Docker bridge
  mode with working DNS (`1.1.1.1`, `8.8.8.8`) and outbound internet.
- No egress proxy is active for this container (`hermes-egress: off`
  label) — traffic goes out directly, not through a credential-injection
  proxy. (Hermes supports one; it's just not configured here.)
- Verified live: general internet, `arxiv.org`, and the
  `export.arxiv.org` search API all reachable with `curl`/`wget`; a real
  PDF (`arxiv.org/pdf/...`) downloads cleanly into `/workspace`.
- To go air-gapped instead, set `terminal.docker_network: false` — this
  swaps in `--network=none` for every future container (again, only
  takes effect on a **new** container — see §6).

## 4. GPU passthrough — configured, but currently NOT live

Full mechanism diagram: [02](diagrams/02_container_reuse_ignores_config.svg).

- `terminal.docker_extra_args` includes `--gpus=all` — in principle this
  makes `nvidia-smi` and CUDA device nodes visible inside the sandbox via
  the NVIDIA Container Toolkit, no image rebuild needed.
- **As of this writing, the currently-running persistent container does
  NOT have it** — `nvidia-smi` inside the live container still returns
  "executable file not found". Confirmed via `docker exec`, twice, across
  two separate Hermes process restarts.
- **Why:** container reuse is keyed by a profile label, not by the
  current `docker_extra_args`/`docker_volumes`/`docker_image` value
  (`tools/environments/docker.py::_attach_existing_container`, "the reuse
  identity is captured at start and never changes for the container's
  lifetime"). Restarting the **Hermes process** does not recreate the
  container — it just reattaches to the same one. Only removing the
  container explicitly forces a fresh `docker run` with the current
  config:
  ```
  docker rm -f hermes-<hash>   # find the name via: docker ps --filter name=hermes
  ```
  The next terminal/execute_code call from Hermes will then create a new
  container with whatever `docker_extra_args` are in the config *right
  now*. **This is the general rule for any `terminal:` config change**
  (network mode, volumes, image, resource limits, extra args) — not
  Hermes-restart-sensitive, container-removal-sensitive.

## 5. Docker-in-Docker gap — deliberate, not a bug

- No `docker` CLI binary and no `/var/run/docker.sock` inside the
  sandbox. A shell command like `docker ps` run by Hermes fails inside
  the container even though the host's own Docker daemon is completely
  healthy.
- **Not fixed on purpose**: mounting the host's Docker socket into the
  sandbox would grant root-equivalent access to the whole host (a
  documented Docker privilege-escalation pattern — any socket-holder can
  start a `--privileged` container with the host filesystem bind-mounted
  in). Unlike GPU passthrough, there's no narrow version of this grant.
  Full reasoning and diagram: [`../system_architecture.md`](../system_architecture.md)
  and [diagram 16](../diagrams/16_sandbox_docker_in_docker_gap.svg) in the
  parent repo.
- Container/host Docker management stays a job for the operator on the
  real host shell (or a non-sandboxed agent), not for Hermes's own shell
  tool calls.

## 6. Persistence & container lifecycle

- `container_persistent: true` → the container's filesystem survives
  across Hermes sessions; it is **not** torn down when a session or the
  Hermes process ends.
- Reuse is scoped **per Hermes profile** (label `hermes-profile`) — each
  profile gets and keeps its own container. Switching profiles gets you
  a different sandbox filesystem and container, not a shared one.
- The orphan reaper only removes containers with Docker status `exited`
  and older than its age threshold — a running container is never
  auto-removed just because it's old or idle.
- `terminal.lifetime_seconds` is unrelated to the container's own life:
  it's how long Hermes keeps its *in-process handle* to an inactive
  terminal env before reaping it — the underlying container keeps
  running either way.

## 7. Resource limits

- `container_cpu` / `container_memory` — enforced via cgroups (`--cpus`,
  `--memory`) if the kernel/cgroup config supports it.
- `container_disk` (MB) — enforced via `--storage-opt size=...`, but
  **only** if the Docker storage driver is overlay2-on-XFS with pquota;
  otherwise Docker logs a warning and runs without a disk quota. Worth
  checking `docker info` if disk limits matter to you.
- `docker_shm_size` defaults to `1g`, raised from Docker's 64 MB
  default, which silently breaks headless Chromium/Playwright and
  PyTorch DataLoader workers.
- `terminal.timeout` — max seconds a single shell command inside the
  sandbox is allowed to run before Hermes kills it.

## 8. Tools available inside (confirmed live)

See diagram [03](diagrams/03_sandbox_at_a_glance.svg) for the full
present/absent cheat sheet.

Present: `curl`, `wget`, `python3` + `pip3`, `node` + `npm`, `git`, `dot`
(Graphviz — the image tag is literally `hermes-sandbox:graphviz`).
Absent: `docker` (see §5), `nvidia-smi` (see §4, config exists but not
live yet), `jq`.

**PDF processing:** only `pypdf` (pure-Python) ships in the image.
`fitz`/PyMuPDF, `PyPDF2`, `pdfplumber`, `pdfminer` are all absent, and
there's no CLI PDF tooling at all — no `pdftotext`, no `poppler-utils`,
no `ghostscript`, no `mupdf`, no `qpdf`. No root/`sudo` inside the
container, so `apt`/`apt-get` can't install anything (present as
binaries, but can't write to system dirs as uid 1000). `pip3 install
--user <pkg>` does work (PyPI reachable) — PyMuPDF was installed this way
(`pip3 install --user pymupdf`, confirmed `import fitz` works, though it
now warns to use `import pymupdf` instead — same package, newer preferred
import name). **Caveat:** this landed in `/home/pn/.local`, the
container's own non-persisted writable layer (see §2) — it survives
Hermes process restarts (same container keeps running) but is lost if
the container is ever removed/recreated. If PyMuPDF stops importing
after a container recreation (e.g. once the GPU-passthrough fix in §4
happens), just re-run the `pip3 install --user pymupdf` command. Full
sequence: diagram [04](diagrams/04_pdf_tooling_gap_and_fix.svg).

## 9. Guardrails layered on top of the sandbox

See diagram [05](diagrams/05_guardrail_coverage.svg).

- `approvals.deny` blocks specific destructive patterns unconditionally
  regardless of approval mode, confirmed against a real command:
  `rm -rf */`, `rm -rf ~/Documents/*`.
- `checkpoints.enabled: true` (`max_snapshots: 20`) git-snapshots
  `/workspace` before risky operations — but **not** the container's own
  `$HOME` (see §2). Always operate under `/workspace` explicitly; don't
  rely on the default cwd if a one-shot session ever changes it.

---

**If something here looks wrong:** config changes to `terminal:` in
`~/.hermes/config.yaml` won't show up in the running container's actual
behavior until it's removed and recreated (§6) — check `docker inspect
<container>` against the current config before assuming a setting isn't
working.
