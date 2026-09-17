<p align="center">
  <img alt="diagrams" src="https://img.shields.io/badge/diagrams-7%20%C3%97%202%20formats-orange">
  <img alt="rendered-with" src="https://img.shields.io/badge/rendered%20with-Graphviz-2e8b57">
  <img alt="verified" src="https://img.shields.io/badge/every%20claim-live--verified-39d0ff">
  <img alt="capabilities" src="https://img.shields.io/badge/theme-Linux%20capabilities-8b5cf6">
</p>

# hardware-introspection

A live agent asked a simple question — "what does the IRQ assignment
look like on this machine?" — and its own containerized shell tool
answered wrong. Not because the information was actually hidden, but
because it tried the three tools a bare-metal admin would reach for
first (`lspci`, `dmidecode`, `/proc/interrupts`), hit a dead end on all
three, and concluded the whole category of information was walled off by
"the Docker VM abstraction." It isn't a VM. This subfolder is the
correction, plus a second real fix that came out of trying to unblock
the same tools properly: getting `lspci`/`dmidecode`/`lshw` actually
installed into a `--cap-drop ALL` sandbox, and discovering exactly which
one of the three still doesn't work even with root and the binary
present.

Sibling to [`../REFERENCE.md`](../REFERENCE.md) §6 and §9, which this
subfolder expands into standalone diagrams and a self-contained script.

## The diagrams

1. **[IRQ introspection gap and fix](diagrams/01_irq_introspection_gap_and_fix.svg)**
   — the failed tool chain, the wrong conclusion, and the real cause:
   `/proc/interrupts` is genuinely unreadable (Docker's *default* `runc`
   masked-paths list bind-mounts `/dev/null` over it for every
   container, an information-disclosure hardening measure with nothing
   sandbox-specific about it), while `/sys/bus/pci/devices/*`,
   `/sys/kernel/irq/<n>/actions`, and `/proc/irq/<n>/smp_affinity_list`
   are all fully readable and unmasked. [`scripts/irq_report.py`](scripts/irq_report.py)
   turns those three sources into one PCI-address → IRQ → driver-name →
   CPU-affinity table, no `lspci`/`dmidecode` required. Verified live on
   a real AMD Ryzen 9 7950X3D + dual-GPU + dual-NVMe box: 25 PCI devices
   with an assigned interrupt line, correct driver names (`nvidia`,
   `ahci[...]`, `AMD-Vi`, `PCIe PME`), real per-IRQ CPU affinity.
2. **[apt install under `--cap-drop ALL`](diagrams/02_apt_install_under_cap_drop_all.svg)**
   — why `apt-get install` fails not just as the normal uid-1000 runtime
   user (expected — no write access) but **even as root via
   `docker exec -u root`**: apt's own internal hardening drops privileges
   to an unprivileged `_apt` user for the download step, which itself
   needs `CAP_SETUID`/`CAP_SETGID` — both stripped by `--cap-drop ALL`
   regardless of which uid is asking. The working fix:
   `apt-get -o APT::Sandbox::User=root ...`, which tells apt to skip that
   internal privilege drop for the one-off install. A real, general
   technique for installing packages into any `--cap-drop ALL`
   container, not specific to this sandbox or these three packages.
3. **[The dmidecode capability wall](diagrams/03_dmidecode_capability_wall.svg)**
   — `lspci` and `lshw` came back working fine at the normal runtime uid
   once installed; `dmidecode` did not, even as root:
   `Can't read memory from /dev/mem`. It needs `CAP_SYS_RAWIO` to read
   raw physical memory for SMBIOS/DMI tables — a capability
   `--cap-drop ALL` correctly strips no matter who's asking. This is the
   sandbox's hardening doing exactly its job, not a bug to route around,
   and the diagram frames the not-taken next step (`--cap-add SYS_RAWIO`)
   as a real, bigger-tier privilege grant that would need explicit
   sign-off, not something to add as a side effect of "install some
   diagnostic tools."

4. **[lnav installed, but nothing to see (host logs declined)](diagrams/04_lnav_installed_but_nothing_to_see.svg)**
   — a follow-up question, "can you access the system's log files such as
   `lnav`?", where Hermes's own diagnosis was actually **correct** this
   time (container-local logs only, real host status needs host access)
   — unlike the IRQ question above. `lnav` itself was installed with the
   same `APT::Sandbox::User=root` technique, but there's little for it to
   browse: the container's own `/var/log` only has sparse package-manager
   history (no init system, no services running inside it). Real host
   logs exist (`/var/log/syslog` — group `adm` readable, real hostname in
   every line, full systemd/service activity; the systemd journal,
   root-only) but were **deliberately not mounted in** — a different
   exposure tier from read-only PCI/hardware facts, declined by the
   operator rather than added as a side effect of "install a log viewer."

5. **[PDF batch summarization: splitting extraction from insight](diagrams/05_pdf_batch_summarization_split.svg)**
   — asked to turn a batch of PDFs into per-paper `.md` summaries, the
   agent's `execute_code` calls hit repeated Python syntax errors trying
   to do text extraction *and* summary-writing in one fragile inline
   step, gave up, and shipped `.md` files with a title and nothing else.
   PyMuPDF itself worked fine when tested directly — the failure was the
   model's own ad-hoc code. Fix: install `poppler-utils`
   (`pdftotext`/`pdfinfo`, same `APT::Sandbox::User=root` technique as
   the rest of this subfolder) and
   [`scripts/pdf_summary_skeleton.py`](scripts/pdf_summary_skeleton.py),
   which does the reliable half (extract text, pull real title/author/
   page-count metadata, write a skeleton `.md`) and leaves only the part
   that actually needs the model — reading the extracted text and
   writing the insights — as an explicit `TODO`. Also detects and flags
   extraction failures instead of silently producing junk; caught a real
   case live (an old failed download saved as `.pdf` that was actually
   an HTML error page). Verified against 10 real astronomy papers already
   in the operator's workspace: 100% correct metadata extraction,
   45K–267K characters of real body text each.
6. **[Language toolchains and the /tmp noexec correction](diagrams/06_language_toolchains_and_tmp_noexec.svg)**
   — asked "can you access Nim lang?", the baseline was `gcc`/`cc`
   present but no `go`, `rustc`/`cargo`, or `nim`. Go and Rust are both
   packaged in Debian (same `APT::Sandbox::User=root` technique); Nim is
   **not packaged in Debian at all** and was installed via its official
   `choosenim` installer instead, then symlinked onto `PATH`. All three
   test builds failed with `Permission denied` when built under `/tmp` —
   turns out `/tmp`'s *actual* live mount is `noexec`
   (`mount` shows `rw,nosuid,nodev,noexec,relatime`), **contradicting**
   what `docker inspect .HostConfig.Tmpfs` reported (no `noexec` listed)
   — a real correction to
   [`../diagrams/07_tmpfs_scratch_mounts.svg`](../diagrams/07_tmpfs_scratch_mounts.svg),
   not just an addition (that diagram has been corrected in place too).
   `go run` has an extra wrinkle: it stages its build under `$TMPDIR`
   regardless of source location, so it fails even with source under
   `/workspace` unless `TMPDIR` is redirected there too. Fix confirmed
   for all three: build/run under `/workspace` instead.
7. **[Nim modules: stdlib vs. Nimble](diagrams/07_nim_modules_stdlib_vs_nimble.svg)**
   — asked to install `asyncdispatch`, `asyncnet`, `illwill`, and
   `memfiles`. Three of the four needed zero action: `asyncdispatch`,
   `asyncnet`, and `memfiles` are Nim standard library, shipping with
   the compiler itself — verified with real functional tests (a real
   async TCP echo round-trip over loopback, `memfiles` really
   memory-mapping a file), not just import checks. Only `illwill` (a
   third-party terminal-UI package) needed a real install,
   `nimble install illwill -y`, landing in `/home/pn/.nimble/pkgs2/` —
   confirmed it compiles and links correctly, though its raw-terminal
   functions weren't exercised (no real TTY in a `docker exec`/Hermes
   tool call).

## Key takeaways

- **"Not available" can mean two very different things** in a hardened
  container: a masked kernel interface (`/proc/interrupts` — data exists,
  path is deliberately blocked) versus a missing binary (`lspci` — tool
  absent, data was never blocked). Diagnosing which one you're looking at
  changes whether the right fix is "read somewhere else" or "install the
  tool."
- **A capability-dropped container can still install packages** — the
  usual blocker is apt's *own* internal privilege-drop machinery, not the
  package manager needing broad host access. `APT::Sandbox::User=root`
  is the general escape hatch, and it's much narrower than adding
  capabilities back to the container.
- **Not every "installed but broken" tool is a bug worth chasing.**
  `dmidecode` needing `CAP_SYS_RAWIO` for `/dev/mem` is a hard security
  boundary working as designed — the honest answer is "this needs a
  bigger, deliberate privilege grant," not "let me find a workaround."
- **A live agent's own confident wrong explanation is worth checking.**
  "Hidden by the Docker VM abstraction" sounded plausible and was
  entirely wrong — the fix here was cheaper and safer than the story the
  agent told itself.
- **Installing a viewer and granting it something to view are two
  separate decisions.** `lnav` is a genuinely useful, zero-risk addition
  on its own; piping real host logs into it is not the same tier of
  decision and deserves its own explicit yes/no, even when the tool
  request and the data-exposure request arrive in the same sentence.
- **When an agent's own generated code keeps failing, check whether the
  task should be split, not just retried.** A single step that mixes
  "extract structured data reliably" with "write free-form prose" pushes
  a model toward writing more code than it can reliably get right in one
  shot. Splitting the reliable, scriptable part out (as a real, tested
  tool) leaves the model only the part it's actually good at.
- **A Docker config field and the actual live mount can disagree.**
  `docker inspect`'s reported tmpfs flags for `/tmp` didn't include
  `noexec`; the real, running mount did. When a "this should work"
  operation fails inexplicably, check the live system state directly
  (`mount`, not just `docker inspect`) rather than trusting the config
  you already read.
- **"Install module X for language Y" often isn't a package-manager
  question at all.** Three of four requested Nim modules were already
  present as standard library — checking `import <module>` first (or
  the language's own stdlib docs) is cheaper and more reliable than
  reaching for the package manager by default.

## Related

- [`../REFERENCE.md`](../REFERENCE.md) §6 and §9 — the source sections
  this subfolder expands.
- [`../README.md`](../README.md) — the `sandbox-reference` narrative
  tour and its own 5 diagrams (filesystem mounts, container-reuse-
  ignores-config, sandbox-at-a-glance, PDF tooling, guardrail coverage).
- [`../../README.md`](../../README.md) — the parent repo.

## License

MIT — see [`../../LICENSE`](../../LICENSE) (this subfolder is covered by
the parent repo's license).
