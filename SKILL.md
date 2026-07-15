---
name: yun
description: Connect and operate authorized Linux SSH servers with one dedicated RSA PEM per target, onboard and pin host identity, transfer bounded files, and submit, monitor, cancel, fetch, or clean durable remote compute jobs. Use when the user invokes /yun or $yun, wants another agent to control a Linux server without cloud/VPN/SSH-config coupling, needs to generate a PEM, or asks to run computation on a registered host.
---

# 云

Use the bundled control plane. Keep credentials in the operating system's SSH
store and operational target metadata in the user-local registry; never teach a
run to reconstruct a private connection from conversation history.

## Start every run

1. Resolve this skill directory and run `python scripts/yunctl.py init`.
2. Run `python scripts/yunctl.py targets` and match the requested target.
3. If the target is absent, take the onboarding branch. Otherwise run
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
server's host key out of band, and run `register` with the absolute PEM and
known-hosts paths.

Complete onboarding only after strict non-interactive login and the registered
`probe` both pass. A generated `.pem` file alone is not a connection.

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
- Never read, print, copy into the skill, or return private keys, passphrases,
  populated `.env` files, API keys, cloud tokens, or secret-manager values.
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

The CLI accepts only targets in the user-local registry, verifies the effective
hostname, port, user, exact PEM, dedicated known-hosts file, and pinned host-key
fingerprint before network use. It disables SSH config and ambient agents on
every command and never embeds credential material.

Ordinary server control requires only Python 3, the system OpenSSH client, a
reachable Linux sshd, and the registered PEM/known-host data. Tailscale, cloud
SDKs, MCP, and user SSH config are not required. The optional compute branch
also requires remote Bash, tmux, and setsid.
