# 本机目标登记表

`yunctl.py` resolves the registry from `YUN_TARGETS_FILE` when set; otherwise it
uses `~/.config/yun/targets.json`. The file contains operational metadata, not
credentials, but should remain private and must not be committed.

Create or inspect it with:

```text
python scripts/yunctl.py init
python scripts/yunctl.py registry-path
python scripts/yunctl.py targets
```

Add targets only through `register`; it validates and writes the registry
atomically. Replacing an existing target requires its name twice:

```text
python scripts/yunctl.py register TARGET ... --confirm-replace TARGET
```

Each entry records:

- a local target name and description;
- direct hostname, port, and SSH user;
- one absolute `.pem` identity path for that target;
- one absolute dedicated known-hosts path;
- the independently verified `SHA256:` server host-key fingerprint;
- `server` and/or `compute` roles;
- whether writes need protected-target confirmation;
- the fixed detached-job backend/root when compute is enabled.

The registry never stores private-key bytes, key passphrases, passwords, cloud
tokens, populated environment variables, or application secrets. `targets
--json` may expose operational metadata, so return it only when the user asks.

Every operation disables SSH config with `-F none`, disables ambient agents,
and passes the registered PEM and known-hosts file explicitly. Moving either
file requires an explicitly confirmed target replacement. The registry rejects
one PEM path shared by two targets.

Use `YUN_TARGETS_FILE` for an isolated test registry or controlled automation;
do not point it at a repository path that will be committed.
