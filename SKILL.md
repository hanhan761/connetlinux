---
name: yun
description: Connect and operate authorized Linux or Windows SSH servers from one self-describing RSA PEM per target, onboard or import that PEM, pin host identity, transfer bounded files, and submit, monitor, cancel, fetch, or clean durable Linux compute jobs. Use when the user invokes /yun or $yun, supplies a yun_*.pem, wants another agent to control a server without carrying registry/known-host/SSH-config files, needs to generate a PEM, or asks to run remote computation.
---

# 云

Use the bundled control plane. Treat a `YUN-BUNDLE-V1` PEM as the portable source
of truth and the user-local registry/known-hosts files as reproducible cache.
Never reconstruct a private connection from conversation history.

## Start every run

1. Resolve this skill directory. If the user supplied an exact self-describing
   PEM path, run `python scripts/yunctl.py import-pem PATH`; never search for it.
2. Otherwise run `python scripts/yunctl.py init`, then `targets`, and match the
   requested target.
3. If the target remains absent, take the onboarding branch. Otherwise run
   `python scripts/yunctl.py probe TARGET` before its first operation this turn.
4. Read the selected branch reference before acting. Read
   [references/registry.md](references/registry.md) as well only when creating,
   replacing, or diagnosing the local target registry.

Never invent or scan for a host. Never bypass an unknown host with
`StrictHostKeyChecking=no` or `accept-new`. Stop when the target, account, or
server fingerprint cannot be verified through an authorized channel.

## Onboarding branch

Read [references/onboarding.md](references/onboarding.md), then generate a
unique client key locally, install only its `.pub` half, verify and pin the
server's host key out of band, run `register`, accept with `probe`, and finish
with `bundle-pem TARGET`. Only the resulting PEM needs to travel with this Skill.

Complete onboarding only after strict non-interactive login, registered `probe`,
bundle validation, and an isolated `import-pem` all pass. An unbundled generated
PEM is only a key; a validated self-describing PEM is the portable connection.

## Compute branch

Read [references/compute.md](references/compute.md), then use `yunctl.py` for the
submit → status/logs → fetch or cancel → cleanup lifecycle.

Complete a job only after observing a terminal state and exit code, reviewing a
redacted log summary, and fetching or verifying every requested result. A job
ID proves submission, not completion.

## Server branch

Read [references/servers.md](references/servers.md), then use `probe`, declared
`exec` intent, and bounded `upload`/`download` operations.

Complete a mutation only after verifying the requested outcome. For deployment
or configuration work, preserve and identify a tested rollback path.

## Authority and safety

- Treat invocation as authority only for the requested target and outcome.
- Perform read-only inspection on a registered target. Require an explicit user
  request before writes, restarts, deployment, cancellation, billable compute,
  firewall/DNS changes, or public cutover.
- Pass `--confirm-target TARGET` for protected-target writes, uploads, job
  submission, cancellation, and cleanup. This verifies target selection; it
  does not replace user authority.
- Never open, print, copy into the Skill, or return private-key bodies,
  passphrases, populated `.env` files, API keys, cloud tokens, or secret-manager
  values. Let only `yunctl.py bundle-pem` stream a key locally into a restricted,
  validated candidate for atomic in-place replacement.
- Never put secrets in command arguments, submitted scripts, logs, target
  metadata, or summaries.
- Preserve unrelated workloads. Inspect identity, disk, ports, active jobs, and
  service ownership before a mutation.
- Prefer reversible operations. Refuse recursive deletion, destructive Git,
  disk formatting, account lockout, or key rotation without exact authority and
  verified path/target boundaries.

## Control-plane entry point

Run from the skill directory:

```text
python scripts/yunctl.py --help
```

The CLI accepts only imported or registered targets, verifies hostname, port,
user, exact PEM, client fingerprint, dedicated known-hosts cache, and pinned
host-key fingerprint before network use. It disables SSH config and ambient
agents on every command. The PEM embeds public connection metadata, never a
second credential; its RSA private body remains directly OpenSSH-compatible.

Ordinary server control requires only Python 3, the system OpenSSH client, a
reachable Linux sshd or Windows OpenSSH Server, and one bundled PEM. Windows
targets use explicit PowerShell and support server operations, while durable
compute remains Linux-only. Registry and known-hosts cache are
generated on import. Tailscale, cloud SDKs, MCP, and user SSH config are not
required by the Skill. The optional compute branch also requires remote Bash,
tmux, and setsid.
