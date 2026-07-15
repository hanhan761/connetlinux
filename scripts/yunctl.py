#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import datetime as dt
import hashlib
import json
import os
import pathlib
import re
import secrets
import shlex
import subprocess
import sys
import tempfile
from typing import Any, Sequence


SKILL_ROOT = pathlib.Path(__file__).resolve().parents[1]
RUNNER_PATH = pathlib.Path(__file__).with_name("yun_job_runner.sh")
DEFAULT_REGISTRY_PATH = pathlib.Path.home() / ".config" / "yun" / "targets.json"
REGISTRY_ENV = "YUN_TARGETS_FILE"
JOB_ROOT = ".yun/jobs"
JOB_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
TARGET_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}[a-z0-9]$|^[a-z0-9]$")
HOST_FINGERPRINT_PATTERN = re.compile(r"^SHA256:[A-Za-z0-9+/]{43}$")
SINGLE_TOKEN_PATTERN = re.compile(r"^[^\s\x00-\x1f]+$")
SSH_ALIAS_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
ALLOWED_ROLES = {"server", "compute"}
ALLOWED_TARGET_KEYS = {
    "description",
    "ssh_alias",
    "expected_hostname",
    "expected_user",
    "expected_host_key_sha256",
    "roles",
    "protected",
    "compute_backend",
    "job_root",
}


class YunError(RuntimeError):
    pass


def printable(argv: Sequence[str]) -> str:
    return shlex.join([str(item) for item in argv])


def run_external(
    argv: Sequence[str],
    *,
    capture: bool = False,
    dry_run: bool = False,
    display: str | None = None,
) -> subprocess.CompletedProcess[str]:
    print(f"+ {display or printable(argv)}", file=sys.stderr)
    if dry_run:
        return subprocess.CompletedProcess(list(argv), 0, "", "")
    try:
        return subprocess.run(
            [str(item) for item in argv],
            check=False,
            capture_output=capture,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except FileNotFoundError as exc:
        raise YunError(f"required command is missing: {argv[0]}") from exc


def registry_path() -> pathlib.Path:
    configured = os.environ.get(REGISTRY_ENV)
    path = pathlib.Path(configured) if configured else DEFAULT_REGISTRY_PATH
    resolved = path.expanduser().resolve()
    try:
        resolved.relative_to(SKILL_ROOT)
    except ValueError:
        return resolved
    raise YunError("registry must remain outside the installed skill directory")


def empty_registry() -> dict[str, Any]:
    return {"schema_version": 1, "targets": {}}


def validate_target_name(name: str) -> str:
    if not TARGET_NAME_PATTERN.fullmatch(name):
        raise YunError("target name must use 1-64 lowercase letters, digits, or hyphens")
    return name


def validate_target(name: str, target: Any) -> dict[str, Any]:
    validate_target_name(name)
    if not isinstance(target, dict):
        raise YunError(f"target {name!r} must be an object")
    unknown = set(target) - ALLOWED_TARGET_KEYS
    if unknown:
        raise YunError(f"target {name!r} has unknown fields: {', '.join(sorted(unknown))}")

    required_strings = (
        "description",
        "ssh_alias",
        "expected_hostname",
        "expected_user",
        "expected_host_key_sha256",
    )
    for field in required_strings:
        value = target.get(field)
        if not isinstance(value, str) or not value.strip():
            raise YunError(f"target {name!r} requires nonempty {field}")
    description = str(target["description"])
    if len(description) > 200 or any(character in description for character in "\r\n\x00"):
        raise YunError(f"target {name!r} has invalid description")
    for field in ("ssh_alias", "expected_hostname", "expected_user"):
        value = str(target[field])
        if value.startswith("-") or not SINGLE_TOKEN_PATTERN.fullmatch(value):
            raise YunError(f"target {name!r} has invalid {field}")
    if not SSH_ALIAS_PATTERN.fullmatch(str(target["ssh_alias"])):
        raise YunError(f"target {name!r} has invalid ssh_alias")
    if not HOST_FINGERPRINT_PATTERN.fullmatch(str(target["expected_host_key_sha256"])):
        raise YunError(f"target {name!r} has invalid SHA-256 host fingerprint")
    if not isinstance(target.get("protected"), bool):
        raise YunError(f"target {name!r} requires boolean protected")

    roles = target.get("roles")
    if (
        not isinstance(roles, list)
        or not roles
        or any(not isinstance(role, str) or role not in ALLOWED_ROLES for role in roles)
        or len(set(roles)) != len(roles)
    ):
        raise YunError(f"target {name!r} has invalid roles")
    if "compute" in roles:
        if target.get("compute_backend") != "tmux" or target.get("job_root") != JOB_ROOT:
            raise YunError(f"target {name!r} compute contract must use tmux and {JOB_ROOT}")
    elif target.get("compute_backend") is not None or target.get("job_root") is not None:
        raise YunError(f"target {name!r} has compute fields without the compute role")
    return target


def validate_registry(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict) or set(payload) != {"schema_version", "targets"}:
        raise YunError("registry must contain only schema_version and targets")
    if payload.get("schema_version") != 1 or not isinstance(payload.get("targets"), dict):
        raise YunError("invalid targets registry schema")
    for name, target in payload["targets"].items():
        validate_target(name, target)
    return payload


def load_registry_payload() -> dict[str, Any]:
    path = registry_path()
    if not path.is_file():
        raise YunError(f"registry is missing: {path}; run 'python scripts/yunctl.py init'")
    return validate_registry(json.loads(path.read_text(encoding="utf-8")))


def load_registry() -> dict[str, dict[str, Any]]:
    return load_registry_payload()["targets"]


def restrict_local_file(path: pathlib.Path) -> None:
    if os.name == "nt":
        username = os.environ.get("USERNAME")
        if not username:
            raise YunError("USERNAME is unavailable for Windows ACL restriction")
        result = run_external(
            ["icacls", str(path), "/inheritance:r", "/grant:r", f"{username}:(F)"]
        )
        if result.returncode != 0:
            raise YunError(f"failed to restrict ACL: {path}")
    else:
        path.chmod(0o600)


def write_registry(payload: dict[str, Any], *, dry_run: bool = False) -> None:
    validate_registry(payload)
    path = registry_path()
    if dry_run:
        print(f"would_write_registry={path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    handle, temporary_name = tempfile.mkstemp(prefix=".targets-", suffix=".json", dir=path.parent)
    temporary = pathlib.Path(temporary_name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        if os.name != "nt":
            temporary.chmod(0o600)
        os.replace(temporary, path)
        restrict_local_file(path)
    finally:
        if temporary.exists():
            temporary.unlink()


def get_target(name: str) -> dict[str, Any]:
    target = load_registry().get(name)
    if target is None:
        raise YunError(f"unregistered target: {name}")
    return validate_target(name, target)


def effective_ssh_config(alias: str) -> dict[str, str]:
    result = run_external(["ssh", "-G", alias], capture=True)
    if result.returncode != 0:
        raise YunError(result.stderr.strip() or f"ssh -G failed for {alias}")
    config: dict[str, str] = {}
    for raw_line in result.stdout.splitlines():
        parts = raw_line.split(None, 1)
        if len(parts) == 2 and parts[0].lower() not in config:
            config[parts[0].lower()] = parts[1].strip()
    return config


def known_hosts_paths(value: str) -> list[pathlib.Path]:
    try:
        words = shlex.split(value, posix=os.name != "nt")
    except ValueError as exc:
        raise YunError("invalid UserKnownHostsFile value in effective SSH config") from exc
    paths: list[pathlib.Path] = []
    for word in words:
        unquoted = word.strip('"').replace("%d", str(pathlib.Path.home()))
        paths.append(pathlib.Path(unquoted).expanduser())
    return paths


def fingerprints_from_known_hosts(output: str) -> set[str]:
    fingerprints: set[str] = set()
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        fields = line.split()
        offset = 1 if fields and fields[0].startswith("@") else 0
        if len(fields) < offset + 3:
            continue
        try:
            key_bytes = base64.b64decode(fields[offset + 2], validate=True)
        except (ValueError, base64.binascii.Error):
            continue
        digest = base64.b64encode(hashlib.sha256(key_bytes).digest()).decode("ascii")
        fingerprints.add("SHA256:" + digest.rstrip("="))
    return fingerprints


def pinned_host_fingerprints(config: dict[str, str]) -> set[str]:
    configured_alias = config.get("hostkeyalias", "").strip()
    hostname = config.get("hostname", "").strip()
    if configured_alias and configured_alias.lower() != "none":
        lookup = configured_alias
    else:
        port = config.get("port", "22")
        lookup = hostname if port == "22" else f"[{hostname}]:{port}"
    path_value = config.get("userknownhostsfile", "")
    if not lookup or not path_value:
        raise YunError("effective SSH config lacks host lookup or UserKnownHostsFile")

    fingerprints: set[str] = set()
    for path in known_hosts_paths(path_value):
        if not path.is_file():
            continue
        result = run_external(
            ["ssh-keygen", "-F", lookup, "-f", str(path)], capture=True
        )
        if result.returncode not in (0, 1):
            raise YunError(result.stderr.strip() or f"known-host lookup failed: {path}")
        fingerprints.update(fingerprints_from_known_hosts(result.stdout))
    return fingerprints


def verify_target_payload(name: str, target: dict[str, Any]) -> dict[str, Any]:
    validate_target(name, target)
    alias = str(target["ssh_alias"])
    config = effective_ssh_config(alias)
    expected_hostname = str(target["expected_hostname"]).lower().rstrip(".")
    actual_hostname = config.get("hostname", "").lower().rstrip(".")
    if actual_hostname != expected_hostname:
        raise YunError(
            f"SSH hostname mismatch for {name}: {actual_hostname!r} != {expected_hostname!r}"
        )
    if config.get("user", "") != str(target["expected_user"]):
        raise YunError(f"SSH user mismatch for {name}")
    if config.get("stricthostkeychecking", "").lower() not in {"yes", "true"}:
        raise YunError(f"StrictHostKeyChecking must be yes/true for {name}")
    if config.get("identitiesonly", "").lower() not in {"yes", "true"}:
        raise YunError(f"IdentitiesOnly must be yes/true for {name}")
    expected_fingerprint = str(target["expected_host_key_sha256"])
    if expected_fingerprint not in pinned_host_fingerprints(config):
        raise YunError(f"pinned host-key fingerprint mismatch for {name}")
    return target


def verify_target(name: str) -> dict[str, Any]:
    return verify_target_payload(name, get_target(name))


def require_role(target: dict[str, Any], role: str) -> None:
    if role not in target.get("roles", []):
        raise YunError(f"target does not provide role: {role}")


def require_protected_confirmation(
    name: str, target: dict[str, Any], confirmation: str | None
) -> None:
    if target.get("protected") and confirmation != name:
        raise YunError(f"protected target requires --confirm-target {name}")


def ssh_run(
    target: dict[str, Any],
    remote_command: str,
    *,
    capture: bool = False,
    dry_run: bool = False,
) -> subprocess.CompletedProcess[str]:
    alias = str(target["ssh_alias"])
    return run_external(
        ["ssh", "-o", "BatchMode=yes", alias, remote_command],
        capture=capture,
        dry_run=dry_run,
        display=f"ssh {alias} <remote-command>",
    )


def validate_job_id(job_id: str) -> str:
    if not JOB_ID_PATTERN.fullmatch(job_id):
        raise YunError("invalid job ID")
    return job_id


def job_paths(target: dict[str, Any], job_id: str) -> tuple[str, str]:
    if target.get("job_root") != JOB_ROOT:
        raise YunError("invalid registered job root")
    validate_job_id(job_id)
    return f"$HOME/{JOB_ROOT}/{job_id}", f"~/{JOB_ROOT}/{job_id}"


def slug(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return (normalized or "job")[:24].rstrip("-")


def new_job_id(name: str) -> str:
    timestamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{timestamp}-{slug(name)}-{secrets.token_hex(3)}"


def redact(text: str) -> str:
    patterns = (
        (re.compile(r"(?i)(authorization:\s*bearer\s+)\S+"), r"\1[REDACTED]"),
        (re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b"), "[REDACTED_KEY]"),
        (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "[REDACTED_AWS_KEY]"),
        (
            re.compile(r"(?i)((?:api[_-]?key|token|password|secret)\s*[=:]\s*)\S+"),
            r"\1[REDACTED]",
        ),
    )
    for pattern, replacement in patterns:
        text = pattern.sub(replacement, text)
    return text


def cmd_init(args: argparse.Namespace) -> int:
    path = registry_path()
    if path.exists():
        load_registry_payload()
        print(f"registry={path}")
        print("state=existing")
        return 0
    write_registry(empty_registry(), dry_run=args.dry_run)
    print(f"registry={path}")
    print("state=would-create" if args.dry_run else "state=created")
    return 0


def cmd_registry_path(args: argparse.Namespace) -> int:
    del args
    print(registry_path())
    return 0


def cmd_targets(args: argparse.Namespace) -> int:
    targets = load_registry()
    if args.json:
        print(json.dumps(targets, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    if not targets:
        print("no targets registered")
        return 0
    for name, target in sorted(targets.items()):
        roles = ",".join(target["roles"])
        protection = "protected" if target["protected"] else "standard"
        print(
            f"{name}\t{target['ssh_alias']}\t{roles}\t{protection}\t"
            f"{target['description']}"
        )
    return 0


def cmd_register(args: argparse.Namespace) -> int:
    payload = load_registry_payload()
    name = validate_target_name(args.name)
    roles = list(dict.fromkeys(args.role))
    target: dict[str, Any] = {
        "description": args.description or name,
        "ssh_alias": args.ssh_alias,
        "expected_hostname": args.hostname,
        "expected_user": args.user,
        "expected_host_key_sha256": args.host_fingerprint,
        "roles": roles,
        "protected": bool(args.protected),
        "compute_backend": "tmux" if "compute" in roles else None,
        "job_root": JOB_ROOT if "compute" in roles else None,
    }
    validate_target(name, target)
    if name in payload["targets"] and args.confirm_replace != name:
        raise YunError(f"replacing target requires --confirm-replace {name}")
    verify_target_payload(name, target)
    payload["targets"][name] = target
    write_registry(payload, dry_run=args.dry_run)
    print(f"registered={name}")
    print(f"registry={registry_path()}")
    return 0


def cmd_keygen(args: argparse.Namespace) -> int:
    key_directory = pathlib.Path(args.directory).expanduser().resolve()
    key_name = slug(args.name)
    if args.format == "ed25519":
        private_key = key_directory / f"yun_{key_name}_ed25519"
        key_args = ["-t", "ed25519", "-a", "100"]
    else:
        private_key = key_directory / f"yun_{key_name}_rsa.pem"
        key_args = ["-t", "rsa", "-b", "4096", "-m", "PEM"]
    public_key = pathlib.Path(f"{private_key}.pub")
    if private_key.exists() or public_key.exists():
        raise YunError(f"refusing to overwrite existing key pair: {private_key}")
    if args.automation_key and not args.confirm_unencrypted:
        raise YunError("automation key requires --confirm-unencrypted")

    comment = f"yun:{key_name}:{dt.date.today().isoformat()}"
    command = ["ssh-keygen", "-q", *key_args, "-f", str(private_key), "-C", comment]
    if args.automation_key:
        command.extend(["-N", ""])
    if args.dry_run:
        run_external(command, dry_run=True)
        print(f"private_key={private_key}")
        print(f"public_key={public_key}")
        return 0

    key_directory.mkdir(parents=True, exist_ok=True)
    result = run_external(command)
    if result.returncode != 0:
        return result.returncode
    if not private_key.is_file() or not public_key.is_file():
        raise YunError("ssh-keygen completed without both key files")
    restrict_local_file(private_key)
    if os.name != "nt":
        public_key.chmod(0o644)

    fingerprint = run_external(
        ["ssh-keygen", "-lf", str(public_key), "-E", "sha256"], capture=True
    )
    if fingerprint.returncode != 0:
        raise YunError(fingerprint.stderr.strip() or "fingerprint generation failed")
    print(f"private_key={private_key}")
    print(f"public_key={public_key}")
    print(f"public_fingerprint={fingerprint.stdout.strip()}")
    return 0


def cmd_probe(args: argparse.Namespace) -> int:
    target = verify_target(args.target)
    remote = r"""
set -u
printf 'YUN_PROBE_OK\n'
printf 'hostname='; hostname
printf 'user='; id -un
printf 'kernel='; uname -sr
printf 'uptime='; uptime -p
printf 'root_disk='; df -h / | awk 'NR==2 {print $2","$3","$4","$5}'
printf 'memory='; LANG=C free -h | awk '/^Mem:/ {print $2","$3","$7}'
printf 'scheduler='; if command -v tmux >/dev/null 2>&1 && command -v setsid >/dev/null 2>&1; then echo tmux; else echo none; fi
printf 'gpu='; if command -v nvidia-smi >/dev/null 2>&1; then if gpu_info=$(nvidia-smi --query-gpu=name,memory.total --format=csv,noheader 2>/dev/null); then printf '%s\n' "$gpu_info" | paste -sd';' -; else echo unavailable; fi; else echo none; fi
""".strip()
    return ssh_run(target, remote, dry_run=args.dry_run).returncode


def cmd_exec(args: argparse.Namespace) -> int:
    target = verify_target(args.target)
    require_role(target, "server")
    if args.write:
        require_protected_confirmation(args.target, target, args.confirm_target)
    command = list(args.remote_command)
    if command and command[0] == "--":
        command.pop(0)
    if not command:
        raise YunError("remote command is required after --")
    return ssh_run(target, shlex.join(command), dry_run=args.dry_run).returncode


def valid_remote_path(value: str) -> str:
    if not value or value.startswith("-") or any(character in value for character in "\r\n\x00"):
        raise YunError("invalid remote path")
    return value


def cmd_upload(args: argparse.Namespace) -> int:
    target = verify_target(args.target)
    require_role(target, "server")
    require_protected_confirmation(args.target, target, args.confirm_target)
    local = pathlib.Path(args.local).expanduser().resolve()
    if not local.is_file():
        raise YunError(f"local upload file is missing: {local}")
    remote = valid_remote_path(args.remote)
    alias = str(target["ssh_alias"])
    return run_external(
        ["scp", str(local), f"{alias}:{remote}"], dry_run=args.dry_run
    ).returncode


def cmd_download(args: argparse.Namespace) -> int:
    target = verify_target(args.target)
    require_role(target, "server")
    remote = valid_remote_path(args.remote)
    destination = pathlib.Path(args.local).expanduser().resolve()
    if not args.dry_run:
        destination.parent.mkdir(parents=True, exist_ok=True)
    alias = str(target["ssh_alias"])
    return run_external(
        ["scp", f"{alias}:{remote}", str(destination)], dry_run=args.dry_run
    ).returncode


def cmd_submit(args: argparse.Namespace) -> int:
    target = verify_target(args.target)
    require_role(target, "compute")
    require_protected_confirmation(args.target, target, args.confirm_target)
    if target.get("compute_backend") != "tmux":
        raise YunError("this CLI submits only to registered tmux targets")
    script = pathlib.Path(args.script).expanduser().resolve()
    if not script.is_file():
        raise YunError(f"job script is missing: {script}")
    if script.stat().st_size > 10 * 1024 * 1024:
        raise YunError("job script exceeds 10 MiB")
    if not RUNNER_PATH.is_file():
        raise YunError("bundled remote runner is missing")

    job_id = new_job_id(args.name or script.stem)
    shell_path, scp_path = job_paths(target, job_id)
    create = f'''set -eu
umask 077
root="{shell_path}"
test ! -e "$root"
mkdir -p "$root/results"
'''
    result = ssh_run(target, create, dry_run=args.dry_run)
    if result.returncode != 0:
        return result.returncode

    alias = str(target["ssh_alias"])
    for source, name in ((script, "job.sh"), (RUNNER_PATH, "runner.sh")):
        result = run_external(
            ["scp", str(source), f"{alias}:{scp_path}/{name}"],
            dry_run=args.dry_run,
        )
        if result.returncode != 0:
            return result.returncode

    session = f"yun-{job_id}"
    start = f'''set -eu
root="{shell_path}"
session={shlex.quote(session)}
command -v tmux >/dev/null
command -v setsid >/dev/null
chmod 700 "$root/job.sh" "$root/runner.sh"
test ! -e "$root/status"
tmux new-session -d -s "$session" "bash \"$root/runner.sh\" \"$root\""
'''
    result = ssh_run(target, start, capture=True, dry_run=args.dry_run)
    if result.returncode != 0:
        if result.stderr:
            print(result.stderr, file=sys.stderr, end="")
        return result.returncode
    print(job_id)
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    target = verify_target(args.target)
    require_role(target, "compute")
    shell_path, _ = job_paths(target, args.job_id)
    session = f"yun-{args.job_id}"
    remote = f'''set -eu
root="{shell_path}"
test -d "$root"
for field in status started_at finished_at exit_code runner_pid child_pid; do
  if test -f "$root/$field"; then printf '%s=' "$field"; cat "$root/$field"; fi
done
if tmux has-session -t {shlex.quote(session)} 2>/dev/null; then echo session=running; else echo session=absent; fi
printf 'size='; du -sh "$root" | awk '{{print $1}}'
'''
    return ssh_run(target, remote, dry_run=args.dry_run).returncode


def cmd_jobs(args: argparse.Namespace) -> int:
    target = verify_target(args.target)
    require_role(target, "compute")
    remote = f'''set -eu
base="$HOME/{JOB_ROOT}"
test -d "$base" || exit 0
find "$base" -mindepth 1 -maxdepth 1 -type d -printf '%f\n' | sort | while IFS= read -r job; do
  case "$job" in *[!A-Za-z0-9._-]*|'') continue;; esac
  state=$(cat "$base/$job/status" 2>/dev/null || echo unknown)
  started=$(cat "$base/$job/started_at" 2>/dev/null || echo -)
  printf '%s\t%s\t%s\n' "$job" "$state" "$started"
done
'''
    return ssh_run(target, remote, dry_run=args.dry_run).returncode


def cmd_logs(args: argparse.Namespace) -> int:
    target = verify_target(args.target)
    require_role(target, "compute")
    shell_path, _ = job_paths(target, args.job_id)
    lines = max(1, min(args.lines, 5000))
    remote = f'''set -eu
root="{shell_path}"
test -d "$root"
printf '%s\n' '--- stdout ---'
test ! -f "$root/stdout.log" || tail -n {lines} "$root/stdout.log"
printf '%s\n' '--- stderr ---'
test ! -f "$root/stderr.log" || tail -n {lines} "$root/stderr.log"
'''
    result = ssh_run(target, remote, capture=True, dry_run=args.dry_run)
    if result.stdout:
        print(redact(result.stdout), end="")
    if result.stderr:
        print(redact(result.stderr), file=sys.stderr, end="")
    return result.returncode


def cmd_cancel(args: argparse.Namespace) -> int:
    target = verify_target(args.target)
    require_role(target, "compute")
    require_protected_confirmation(args.target, target, args.confirm_target)
    shell_path, _ = job_paths(target, args.job_id)
    session = f"yun-{args.job_id}"
    remote = f'''set -eu
root="{shell_path}"
test -d "$root"
state=$(cat "$root/status" 2>/dev/null || echo unknown)
case "$state" in succeeded|failed|cancelled) printf 'already_terminal=%s\n' "$state"; exit 0;; esac
: > "$root/cancel_requested"
if tmux has-session -t {shlex.quote(session)} 2>/dev/null; then
  tmux send-keys -t {shlex.quote(session)} C-c
  sleep 1
  tmux kill-session -t {shlex.quote(session)} 2>/dev/null || true
fi
if test -s "$root/child_pid"; then
  pid=$(cat "$root/child_pid")
  case "$pid" in *[!0-9]*|'') ;; *)
    sid=$(ps -o sid= -p "$pid" 2>/dev/null | tr -d ' ')
    if test "$sid" = "$pid"; then kill -TERM -- "-$pid" 2>/dev/null || true; fi
  esac
fi
printf 'cancel_requested=%s\n' {shlex.quote(args.job_id)}
'''
    return ssh_run(target, remote, dry_run=args.dry_run).returncode


def cmd_fetch(args: argparse.Namespace) -> int:
    target = verify_target(args.target)
    require_role(target, "compute")
    _, scp_path = job_paths(target, args.job_id)
    check = ssh_run(
        target,
        f'test -d "$HOME/{JOB_ROOT}/{args.job_id}/results"',
        dry_run=args.dry_run,
    )
    if check.returncode != 0:
        return check.returncode
    destination = pathlib.Path(args.destination).expanduser().resolve() / args.job_id
    if not args.dry_run:
        destination.parent.mkdir(parents=True, exist_ok=True)
    alias = str(target["ssh_alias"])
    return run_external(
        ["scp", "-r", f"{alias}:{scp_path}/results", str(destination)],
        dry_run=args.dry_run,
    ).returncode


def cmd_cleanup(args: argparse.Namespace) -> int:
    target = verify_target(args.target)
    require_role(target, "compute")
    require_protected_confirmation(args.target, target, args.confirm_target)
    validate_job_id(args.job_id)
    if args.confirm_job != args.job_id:
        raise YunError(f"cleanup requires --confirm-job {args.job_id}")
    shell_path, _ = job_paths(target, args.job_id)
    session = f"yun-{args.job_id}"
    remote = f'''set -eu
base=$(readlink -f "$HOME/{JOB_ROOT}")
job=$(readlink -f "{shell_path}")
test -d "$job"
case "$job" in "$base"/*) ;; *) echo 'unsafe job path' >&2; exit 65;; esac
test "${{job##*/}}" = {shlex.quote(args.job_id)}
state=$(cat "$job/status" 2>/dev/null || echo unknown)
case "$state" in succeeded|failed|cancelled) ;; *) echo "job is not terminal: $state" >&2; exit 66;; esac
if tmux has-session -t {shlex.quote(session)} 2>/dev/null; then echo 'job session is still active' >&2; exit 67; fi
rm -rf -- "$job"
test ! -e "$job"
printf 'cleaned=%s\n' {shlex.quote(args.job_id)}
'''
    return ssh_run(target, remote, dry_run=args.dry_run).returncode


def add_protected_confirmation(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--confirm-target")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="云：strict registered SSH control and durable remote jobs"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="print external actions without running them"
    )
    subparsers = parser.add_subparsers(dest="command_name", required=True)

    initialize = subparsers.add_parser("init", help="create the user-local registry")
    initialize.set_defaults(func=cmd_init)

    registry = subparsers.add_parser("registry-path", help="print the registry path")
    registry.set_defaults(func=cmd_registry_path)

    targets = subparsers.add_parser("targets", help="list registered targets")
    targets.add_argument("--json", action="store_true")
    targets.set_defaults(func=cmd_targets)

    register = subparsers.add_parser("register", help="validate and register one target")
    register.add_argument("name")
    register.add_argument("--ssh-alias", required=True)
    register.add_argument("--hostname", required=True)
    register.add_argument("--user", required=True)
    register.add_argument("--host-fingerprint", required=True)
    register.add_argument(
        "--role", action="append", choices=sorted(ALLOWED_ROLES), required=True
    )
    register.add_argument("--description")
    register.add_argument("--protected", action="store_true")
    register.add_argument("--confirm-replace")
    register.set_defaults(func=cmd_register)

    keygen = subparsers.add_parser("keygen", help="generate a per-target SSH key pair")
    keygen.add_argument("name")
    keygen.add_argument("--directory", default=str(pathlib.Path.home() / ".ssh"))
    keygen.add_argument("--format", choices=("ed25519", "rsa-pem"), default="ed25519")
    keygen.add_argument("--automation-key", action="store_true")
    keygen.add_argument("--confirm-unencrypted", action="store_true")
    keygen.set_defaults(func=cmd_keygen)

    probe = subparsers.add_parser("probe", help="run a read-only identity/readiness probe")
    probe.add_argument("target")
    probe.set_defaults(func=cmd_probe)

    execute = subparsers.add_parser("exec", help="run a declared remote command")
    execute.add_argument("target")
    intent = execute.add_mutually_exclusive_group(required=True)
    intent.add_argument("--read-only", action="store_true")
    intent.add_argument("--write", action="store_true")
    add_protected_confirmation(execute)
    execute.add_argument("remote_command", nargs="+")
    execute.set_defaults(func=cmd_exec)

    upload = subparsers.add_parser("upload", help="upload one bounded file")
    upload.add_argument("target")
    upload.add_argument("local")
    upload.add_argument("remote")
    add_protected_confirmation(upload)
    upload.set_defaults(func=cmd_upload)

    download = subparsers.add_parser("download", help="download one bounded file")
    download.add_argument("target")
    download.add_argument("remote")
    download.add_argument("local")
    download.set_defaults(func=cmd_download)

    submit = subparsers.add_parser("submit", help="submit a durable tmux compute job")
    submit.add_argument("target")
    submit.add_argument("script")
    submit.add_argument("--name")
    add_protected_confirmation(submit)
    submit.set_defaults(func=cmd_submit)

    status = subparsers.add_parser("status", help="show durable job state")
    status.add_argument("target")
    status.add_argument("job_id")
    status.set_defaults(func=cmd_status)

    jobs = subparsers.add_parser("jobs", help="list durable jobs on a target")
    jobs.add_argument("target")
    jobs.set_defaults(func=cmd_jobs)

    logs = subparsers.add_parser("logs", help="show redacted job log tails")
    logs.add_argument("target")
    logs.add_argument("job_id")
    logs.add_argument("--lines", type=int, default=100)
    logs.set_defaults(func=cmd_logs)

    cancel = subparsers.add_parser("cancel", help="cancel a running compute job")
    cancel.add_argument("target")
    cancel.add_argument("job_id")
    add_protected_confirmation(cancel)
    cancel.set_defaults(func=cmd_cancel)

    fetch = subparsers.add_parser("fetch", help="download a job results directory")
    fetch.add_argument("target")
    fetch.add_argument("job_id")
    fetch.add_argument("destination")
    fetch.set_defaults(func=cmd_fetch)

    cleanup = subparsers.add_parser(
        "cleanup", help="remove one confirmed terminal job directory"
    )
    cleanup.add_argument("target")
    cleanup.add_argument("job_id")
    cleanup.add_argument("--confirm-job", required=True)
    add_protected_confirmation(cleanup)
    cleanup.set_defaults(func=cmd_cleanup)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = build_parser().parse_args(argv)
        return int(args.func(args))
    except (YunError, OSError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
