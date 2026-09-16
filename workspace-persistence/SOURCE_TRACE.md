# Findings: source-level trace of the terminal.cwd bug

All citations are into the real, upstream [`NousResearch/hermes-agent`](https://github.com/NousResearch/hermes-agent)
source, cloned locally at `~/.hermes/hermes-agent` on this box (a normal,
writable clone of the actual upstream project — not a vendored copy).
File paths and line numbers below are to open-source code, safe to
publish as-is.

## 1. `terminal.cwd: .` is a placeholder, not a path

`gateway/cwd_placeholder.py:12`:

```python
CWD_PLACEHOLDERS = {".", "auto", "cwd"}
```

`resolve_placeholder_terminal_cwd()` (same file, lines 14-39) only maps
one of these placeholder strings to something real:

- For the `local` backend: resolves to `MESSAGING_CWD` or the host's
  `Path.home()`.
- For the `docker` backend: **only** when `docker_mount_cwd_to_workspace`
  is `true` *and* a host `messaging_cwd` value is actually present.

On this machine, `docker_mount_cwd_to_workspace: false` — so for every
`docker`-backend session, this function returns `None`.

## 2. The `/root` fallback

`tools/terminal_tool.py`, `_resolve_config_cwd()` (~line 586-608): when
the placeholder resolver returns `None`, it falls back to:

```python
_DEFAULT_CWD_BY_BACKEND.get(env_type, "/root")
```

The `docker` backend has **no entry** in `_DEFAULT_CWD_BY_BACKEND` — so
this always evaluates to the dict's own default, `/root`, unconditionally.
This has nothing to do with AGENTS.md, launch directory, or anything the
model does — it's decided before the model ever sees a prompt.

## 3. `docker_mount_cwd_to_workspace` is a separate mechanism from `docker_volumes`

The static `docker_volumes` bind mount (e.g.
`~/Documents/hermes-sessions:/workspace:rw` on this box) is unconditional
and always present. `docker_mount_cwd_to_workspace` is a *different*
switch that additionally binds whatever the host's current launch cwd is
to `/workspace`, and sets the container's initial working directory
there (`terminal_tool.py` ~lines 142, 509, 592-602). With it `false`, that
extra wiring is simply skipped — the static volume mount still exists and
is still writable, the container's cwd just doesn't start there.

## 4. `home_mode: auto` is unrelated — don't conflate it

`tools/terminal_scope.py:31` and `hermes_cli/config_defaults.py:294`:
`home_mode` controls the `HOME` environment variable handed to
*subprocess* tool invocations (relevant to CLI credential/config
visibility, e.g. whether a tool sees `~/.aws` or similar). In containers,
`auto` sets `HOME={HERMES_HOME}/home`. It has no effect on the terminal
tool's working directory. This is worth stating explicitly because it's
an easy but wrong guess at first glance.

## 5. AGENTS.md discovery never checks `$HOME`

`agent/prompt_builder.py:1526`, `_agents_md_directory_chain()` /
`_agents_md_candidates()`: walks from the git root down to the *resolved*
cwd (deepest directory wins), checking, per directory, in order:
`AGENTS.override.md` → `AGENTS.md` → `agents.md` (first non-empty file
wins). The cwd used here comes from
`agent.runtime_cwd.resolve_context_cwd()` (`runtime_cwd.py:85-94`) — the
host-configured `TERMINAL_CWD`, or `os.getcwd()` at launch.

**This chain never includes `$HOME` or `/root` as a candidate directory.**
Once cwd has already resolved to `/root` (per §1-2, unconditionally for
this config), no placement of `AGENTS.md` anywhere in the real workspace
tree can be discovered — the walk simply never reaches it. This is the
precise mechanism behind the partial result reported in
`hermes-agent-blast-radius`'s 5th addendum.

## 6. The fix

Setting `terminal.cwd: /workspace` (a literal absolute path) instead of
`.`:

- `cwd_placeholder.py:33` — `if configured_cwd and configured_cwd not in
  CWD_PLACEHOLDERS: return configured_cwd` — a non-placeholder value is
  returned unchanged, skipping the whole `docker_mount_cwd_to_workspace`
  branch entirely.
- `terminal_tool_config.py:98`, `_is_unusable_container_cwd()` — only
  rejects host-style paths (`/home/...`, `/Users/...`, Windows drive
  letters, or relative paths). `/workspace` is a valid container-native
  absolute path and passes.

Net effect: the container's cwd is unconditionally `/workspace`,
regardless of `docker_mount_cwd_to_workspace`, `home_mode`, or whether the
model reads/obeys AGENTS.md at all.

## 7. No existing write-location safety net

Checked `tools/file_tools_write_guards.py` and `tools/threat_patterns.py`
— both are real, active guard modules, but both cover command-injection
and dangerous-command detection (e.g. `rm -rf` patterns, as documented in
`hermes-agent-blast-radius`). Neither checks *where* a write lands
relative to the configured workspace mount. See `RESEARCH_PROPOSAL.md`'s
follow-on-work section.

## 8. Real verification performed

Applied the fix to the live config (`~/.hermes/config.yaml`, backed up
first), then ran a **one-shot** invocation — deliberately chosen because
`-z` spins up its own fresh, non-persistent sandbox container rather than
reusing the already-running persistent session's container (which
wouldn't pick up a config-file edit until it's recreated, and would have
produced a false pass either way):

```
$ hermes -z "run the shell command: pwd && ls -la" -m <a model with tool-calling+thinking support>
```

Real output:

```
/workspace

total 28
drwxrwxr-x 6 pn pn 4096 Sep 16 17:44 .
drwxr-xr-x 1 root root 4096 Sep 16 16:00 ..
-rw-rw-r-- 1 pn pn 872 Sep 15 20:42 AGENTS.md
drwxrwxr-x 2 pn pn 4096 Sep 15 20:42 ai_earning_avatar_research
drwxrwxr-x 2 pn pn 4096 Sep 16 06:18 new_frontier
drwxrwxr-x 3 pn pn 4096 Sep 15 17:32 research_repackage
drwxrwxr-x 3 pn pn 4096 Sep 16 17:44 session_1789580030
```

`pwd` resolved to `/workspace`, and the listing shows real, previously
-persisted directory contents (including the session recovered from the
ephemeral home earlier the same session) — not an empty fresh container.
Fix confirmed working against a real invocation, not just read from
source.

**Note:** this fix is not yet live on the pre-existing, already-running
persistent sandbox container from before the config change — that
container only picks up the new default the next time it's recreated
(the same trigger as any other config change to `docker_extra_args` or
similar).
