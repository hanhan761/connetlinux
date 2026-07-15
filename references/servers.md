# 已授权服务器操作闭环

## Inspect

Use only names returned by:

```text
python scripts/yunctl.py targets
```

Start with `probe`. Declare additional command intent explicitly:

```text
python scripts/yunctl.py exec TARGET --read-only -- hostname
python scripts/yunctl.py exec TARGET --read-only -- systemctl is-active SERVICE
```

An SSH config or host-fingerprint mismatch is an identity incident. Stop and
verify the change out of band; never weaken strict checking.

## Mutate

Use a write only for the user's requested outcome:

```text
python scripts/yunctl.py exec TARGET --write -- sudo systemctl restart SERVICE
python scripts/yunctl.py exec PROD --write --confirm-target PROD -- COMMAND
python scripts/yunctl.py upload PROD LOCAL REMOTE --confirm-target PROD
```

Before changing a service or deployment:

1. Capture target identity, current state, disk, ports, dependencies, and a
   rollback artifact or previous configuration.
2. Validate candidate syntax/configuration before activation when supported.
3. Apply the smallest bounded change.
4. Verify service state, health, logs, external behavior, persistence, and
   restart policy as applicable.
5. Roll back when acceptance fails and rollback remains data-safe.

Never combine unrelated cleanup with the requested mutation.

## Transfer

```text
python scripts/yunctl.py upload TARGET ./artifact.tar.gz /tmp/artifact.tar.gz
python scripts/yunctl.py download TARGET /remote/result.json ./downloads/result.json
```

Verify checksums before activation. Do not download or display private keys,
populated environment files, cloud credentials, or secret-store exports.

When the CLI lacks an operation, use the registered alias rather than rebuilding
connection flags. Preserve every authority, identity, secret, and verification
gate in `SKILL.md`.
