# System architecture note: sandbox / host Docker boundary

This note is a deep dive on one specific edge of the stack documented in the main
[README](README.md): why **Hermes Agent's own shell tool cannot see or manage Docker containers**,
even though Docker runs fine on the host, and even though the sandbox that shell tool runs inside
of is itself a Docker container. It supplements diagram
[16](diagrams/16_sandbox_docker_in_docker_gap.svg) with the full investigation and reasoning.

## Symptom

Asked (via chat) to inspect or manage Docker containers on the-box, Hermes reported:

> "I cannot see or manage Docker containers because the docker command is not available in my
> current shell environment."

## Two different "Docker"s in this stack

It's easy to conflate two separate things once Hermes's terminal tool is itself Docker-backed:

| | Host Docker | Sandbox's view of Docker |
|---|---|---|
| What it is | `dockerd` running on the-box via systemd | Whatever binaries/sockets exist *inside* `hermes-sandbox:graphviz` |
| Controls | Every container on the-box, including the sandbox's own persistent container | Nothing — no client, no socket |
| Confirmed live | `docker --version` → `29.1.3`; `systemctl is-active docker` → `active` | `docker exec <sandbox> which docker` → exit 2, "executable file not found in $PATH" |

`terminal.backend: docker` in `~/.hermes/config.yaml` means every shell tool call Hermes makes is
executed with `docker exec` *into* a persistent sandbox container (`hermes-sandbox:graphviz`), not
run directly on the host. That's the entire isolation mechanism documented in diagram 3 of the main
README, and the reason a hostile or buggy `rm -rf` from the agent can only destroy the bind-mounted
`/workspace`, never the real host filesystem — see
[hermes-agent-blast-radius](https://github.com/danindiana/hermes-agent-blast-radius) for that
incident and fix.

The consequence, not previously called out on its own: **the sandbox image has neither a `docker`
CLI binary nor `/var/run/docker.sock` mounted in.** So a shell command like `docker ps`, run by
Hermes, is attempted *inside* the sandbox, where it simply doesn't exist. This has nothing to do
with the host's Docker installation, which is healthy and unaffected.

## Live verification

```
$ docker --version
Docker version 29.1.3, build 29.1.3-0ubuntu3~22.04.2
$ systemctl is-active docker
active
$ docker ps -a --filter "name=hermes"
CONTAINER ID   IMAGE                     COMMAND            CREATED       STATUS       PORTS     NAMES
bcc2abff49d2   hermes-sandbox:graphviz   "sleep infinity"   2 hours ago   Up 2 hours             hermes-0260093a

$ docker exec bcc2abff49d2 which docker
OCI runtime exec failed: exec failed: unable to start container process:
exec: "docker": executable file not found in $PATH
$ docker exec bcc2abff49d2 ls -la /var/run/docker.sock
ls: cannot access '/var/run/docker.sock': No such file or directory
```

Same pattern already surfaced once before for a different binary: `nvidia-smi` was also missing
inside the sandbox until `--gpus=all` was deliberately added to `docker_extra_args` (see
[hermes-sandbox-gpu-passthrough](diagrams/04_gpu_passthrough.svg)). GPU passthrough was judged a
reasonable, narrowly-scoped exception because the NVIDIA Container Toolkit injects only
driver/runtime libraries and a read/monitor CLI — it doesn't hand the sandbox control over
anything on the host beyond the GPU device nodes it's given.

Docker-in-Docker is a different risk class entirely, covered next.

## Why this isn't "fixed" the same way GPU passthrough was

The mechanical fix for Docker access would look similar to the GPU one on paper: mount
`/var/run/docker.sock` into the sandbox and install a `docker` client binary in the image. In
practice this is a well-known privilege-escalation pattern, not a scoped capability grant:

- Any process with access to the Docker socket can ask the daemon to start a **new** container
  with `--privileged` and a host bind mount of `/`, then `chroot` into it — i.e. full host root,
  trivially, no exploit required, it's a documented Docker feature working as designed.
- It would also let the sandbox manage (stop, exec into, delete) **its own** persistent container
  from the inside, and any other container on the-box — collapsing exactly the isolation boundary
  the blast-radius hardening built.
- Unlike GPU passthrough, there's no narrower variant of "give it the raw socket" — the socket is
  an all-or-nothing root-equivalent API. A safer middle ground would require fronting it with a
  read-only or scoped proxy (e.g. a `docker-socket-proxy`-style allowlist of specific API verbs),
  which is a real option but a materially bigger piece of infrastructure than a one-line
  `docker_extra_args` addition — and wasn't built here.

## Current posture (as of this writeup)

No config change was made. The sandbox remains docker-blind by design. Container management on
the-box (starting/stopping services, inspecting other containers, pruning images, etc.) stays a
task for the operator directly on the host, or for a host-level agent (e.g. Claude Code) that
isn't running inside the sandbox — not something delegated to Hermes's own shell tool calls.

If a scoped Docker-management capability is added later (most likely via a socket-proxy rather
than a raw socket mount), this document and diagram 16 should be updated to reflect the new
boundary, and the risk tradeoff re-evaluated explicitly rather than assumed safe by analogy to the
GPU passthrough precedent.
