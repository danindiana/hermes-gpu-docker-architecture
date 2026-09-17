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
  recreated per session. See §7, and diagram
  [02](diagrams/02_container_reuse_ignores_config.svg).
- Runs as a real Docker container on the host (`docker_image` under
  `terminal:` in `~/.hermes/config.yaml`), started with
  `--cap-drop ALL --user 1000:1000 --security-opt no-new-privileges`
  (confirmed via `docker inspect`) — non-root, no capability escalation.
- **`--cap-drop ALL` isn't the whole capability story.** `docker inspect`
  also shows `CapAdd=[CAP_CHOWN CAP_DAC_OVERRIDE CAP_FOWNER]` — three
  specific capabilities added back on top of the drop-all baseline.
  These are why `dpkg` (via the `apt-get -o APT::Sandbox::User=root`
  technique in [`hardware-introspection/`](hardware-introspection/))
  could actually unpack packages and set file ownership cleanly, not
  just "run as root." `CAP_SYS_RAWIO` is conspicuously **not** in this
  list — the real reason `dmidecode` still fails even as root (see
  [`hardware-introspection/diagrams/03_dmidecode_capability_wall.svg`](hardware-introspection/diagrams/03_dmidecode_capability_wall.svg)).
  Full diagram: [06](diagrams/06_capabilities_added_back.svg). Never
  assume "`--cap-drop ALL`" means zero capabilities — check the actual
  `CapAdd` list.

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
  removed/recreated (§7/§4). **Anything installed here — e.g. a `pip
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
- **`/workspace` can only ever be whatever `docker_volumes` currently
  points at.** If files you expect aren't there, check
  `~/.hermes/config.yaml`'s `terminal.docker_volumes` before assuming
  anything was lost — content can end up outside this mount entirely if
  it was written by something other than a Hermes tool call (e.g. a
  human moving files around in a file manager on the host).
- **A third category: RAM-backed tmpfs scratch.** `/tmp` (512 MB,
  `nosuid`), `/var/tmp` (256 MB, `noexec,nosuid`), and `/run` (64 MB,
  `noexec,nosuid`) are tmpfs mounts — not bind mounts, not part of the
  container's persistent writable layer either. Confirmed actively used:
  Hermes's own `execute_code` tool writes its Python kernel runner
  scripts to `/tmp/hermes_rkernel_<id>/kernel_runner.py`. These are wiped
  on **any** container stop/restart, not just recreation — a tighter
  durability bar than `/home/pn` (§ above) or the container's root
  filesystem (§9's pip/apt installs). Never checkpointed. Full diagram:
  [07](diagrams/07_tmpfs_scratch_mounts.svg).

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
  takes effect on a **new** container — see §7).

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

## 6. Hardware introspection (PCI/IRQ) — lspci/dmidecode are gone, sysfs isn't

Full standalone writeup and 4 diagrams:
[`hardware-introspection/`](hardware-introspection/).

A real failure mode, hit live: asked "what does the IRQ assignment look
like on this machine," Hermes tried `lspci -vv`, `dmidecode`, and
`/proc/interrupts` in sequence — all three dead ends — and concluded IRQ
info was "hidden by the Docker VM abstraction." **That conclusion was
wrong.** This isn't a VM; it's a normal Linux container sharing the host
kernel, and most of the same information is directly readable elsewhere:

- `/proc/interrupts` — readable (`cat` exits 0) but **empty**. `ls -la`
  shows it as a character device `1,3` — Docker's default `runc` masked-
  paths list bind-mounts `/dev/null` over a handful of sensitive `/proc`
  entries for every container (this one included, no special config
  needed to trigger it) as an information-disclosure hardening measure.
  There is no scoped way to unmask just this one file without either
  disabling all masking (`--security-opt systempaths=unconfined` — this
  also exposes `/proc/kcore`, `/proc/keys`, and more; not worth it for
  this) or an explicit bind mount of that one host path.
- **`/sys/bus/pci/devices/*/`** — fully readable, not masked, not
  namespaced. Every PCI device on the host shows up here with real
  `vendor`, `device`, `class`, `numa_node`, and **`irq`** files.
- **`/sys/kernel/irq/<n>/actions`** and **`/proc/irq/<n>/smp_affinity_list`**
  — both fully readable per-IRQ, giving the device/driver name that owns
  each interrupt and which CPUs it's allowed to run on.

**Fix (part 1):** [`hardware-introspection/scripts/irq_report.py`](hardware-introspection/scripts/irq_report.py)
— a small, dependency-free Python script (stdlib only, no `subprocess`)
that walks `/sys/bus/pci/devices`, cross-references each device's `irq`
number against `/sys/kernel/irq/*/actions` and
`/proc/irq/*/smp_affinity_list`, and prints a full PCI-address → IRQ →
owning driver → CPU-affinity table. Confirmed live: 25 real PCI devices
with an assigned interrupt line, correct driver names (`nvidia`,
`ahci[...]`, `AMD-Vi`, `PCIe PME`), real CPU-affinity lists.

**Fix (part 2): `lspci`, `dmidecode`, `lshw`, `lnav` actually installed.**
Plain `apt-get install` fails under `--cap-drop ALL` even as root — apt's
download step tries to drop privileges to its internal `_apt` user, which
needs `CAP_SETUID`/`CAP_SETGID`, both stripped. Worked around from the
**host** (Hermes's own sandboxed shell can't do this — its terminal tool
always runs as uid 1000, no route to root) via:
```
docker exec -u root hermes-<hash> sh -c \
  'apt-get -o APT::Sandbox::User=root update && \
   apt-get -o APT::Sandbox::User=root install -y <packages>'
```
- **`lspci` / `lspci -vv`** — fully working at the normal runtime uid.
  Real vendor names, and the original ask
  (`Interrupt: pin A routed to IRQ 26`-style lines) works directly now.
- **`lshw -short`** — mostly working (full PCI device tree, real device
  names), missing only DMI-derived fields.
- **`dmidecode`** — installed but **non-functional even as root**:
  `Can't read memory from /dev/mem`. Needs `CAP_SYS_RAWIO`, stripped by
  `--cap-drop ALL` regardless of uid — a real capability boundary, not a
  bug. Fixing it for real (`--cap-add SYS_RAWIO` + `/dev/mem` access)
  would be a privilege grant on the same tier as GPU passthrough or
  bigger — not done here without explicit sign-off.
- **`lnav`** — installed for a follow-up "can you access system logs"
  question. Little to actually browse: the container's own `/var/log`
  is sparse (no init system, no services running inside it). Real host
  logs (`/var/log/syslog` — real hostname in every line, full
  systemd/service history; the journal) exist but were **declined** —
  a bigger exposure tier than read-only PCI/hardware facts, not granted
  as a side effect of "add a log viewer."
- **Same durability caveat as PyMuPDF (§9):** these packages live in the
  container's own root filesystem, not a bind-mounted path — survive
  Hermes process restarts, wiped if the container is ever
  removed/recreated (§4/§7).

## 7. Persistence & container lifecycle

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

## 8. Resource limits

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

## 9. Tools available inside (confirmed live)

See diagram [03](diagrams/03_sandbox_at_a_glance.svg) for the present/
absent cheat sheet as of the first sandbox-reference batch; §6 above
covers what changed since.

Present: `curl`, `wget`, `python3` + `pip3`, `node` + `npm`, `git`, `dot`
(Graphviz — the image tag is literally `hermes-sandbox:graphviz`),
`lspci`, `lshw`, `lnav`, `dmidecode` (installed but non-functional, §6).
Absent: `docker` (see §5), `nvidia-smi` (see §4, config exists but not
live yet), `jq`, `hwinfo`.

**PDF processing:** only `pypdf` (pure-Python) ships in the image.
`fitz`/PyMuPDF, `PyPDF2`, `pdfplumber`, `pdfminer` are all absent, and
there's no CLI PDF tooling at all — no `pdftotext`, no `poppler-utils`,
no `ghostscript`, no `mupdf`, no `qpdf`. No root/`sudo` inside the
container for the *normal* runtime shell, so `apt`/`apt-get` can't
install anything from inside a Hermes tool call itself (present as
binaries, but can't write to system dirs as uid 1000 — see §6 for the
host-side `docker exec -u root` workaround that *does* work). `pip3
install --user <pkg>` does work from inside the sandbox though (PyPI
reachable) — PyMuPDF was installed this way (`pip3 install --user
pymupdf`, confirmed `import fitz` works, though it now warns to use
`import pymupdf` instead — same package, newer preferred import name).
**Caveat:** this landed in `/home/pn/.local`, the container's own
non-persisted writable layer (see §2) — it survives Hermes process
restarts (same container keeps running) but is lost if the container is
ever removed/recreated. If PyMuPDF stops importing after a container
recreation (e.g. once the GPU-passthrough fix in §4 happens), just
re-run the `pip3 install --user pymupdf` command. Full sequence: diagram
[04](diagrams/04_pdf_tooling_gap_and_fix.svg).

## 10. Guardrails layered on top of the sandbox

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
behavior until it's removed and recreated (§7) — check `docker inspect
<container>` against the current config before assuming a setting isn't
working. And if a file you expect in `/workspace` isn't there, check
`docker_volumes` (§2) before assuming it was lost — it may simply be
living somewhere this mount doesn't reach.
