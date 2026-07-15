#!/usr/bin/env python3
"""Prepare a Debian/Ubuntu workstation for private Codex administration.

The default mode is a non-mutating preview. System changes require --apply.
Traditional OpenSSH runs over Tailscale; Tailscale SSH is intentionally not used.
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import hashlib
import ipaddress
import json
import os
import pathlib
import platform
import re
import shlex
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
from typing import Any, Callable, Dict, List, Optional, Sequence


VERSION = "0.1.0"
DEFAULT_ADMIN_USER = "codex-admin"
TAILSCALE_INSTALL_URL = "https://tailscale.com/install.sh"
TAILSCALE_IPV4_NETWORK = ipaddress.ip_network("100.64.0.0/10")
BACKUP_ROOT = pathlib.Path("/var/backups/connetlinux")
STATE_PATH = pathlib.Path("/var/lib/connetlinux/bootstrap-state.json")
SSHD_MAIN_PATH = pathlib.Path("/etc/ssh/sshd_config")
SSHD_DROPIN_PATH = pathlib.Path(
    "/etc/ssh/sshd_config.d/00-connetlinux.conf"
)
SUDOERS_PATH = pathlib.Path(
    "/etc/sudoers.d/90-connetlinux-codex-admin"
)
SLEEP_TARGETS = (
    "sleep.target",
    "suspend.target",
    "hibernate.target",
    "hybrid-sleep.target",
)
USERNAME_PATTERN = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")


class BootstrapError(RuntimeError):
    """A safe, user-facing bootstrap failure."""


class CommandRunner:
    """Run commands in apply mode and only print them in preview mode."""

    def __init__(
        self,
        apply: bool,
        executor: Callable[..., subprocess.CompletedProcess] = subprocess.run,
    ) -> None:
        self.apply = apply
        self.executor = executor

    def run(
        self,
        args: Sequence[str],
        *,
        check: bool = True,
        capture_output: bool = False,
        env: Optional[Dict[str, str]] = None,
        timeout: Optional[int] = None,
    ) -> subprocess.CompletedProcess:
        printable = shlex.join([str(item) for item in args])
        print(f"$ {printable}")
        if not self.apply:
            empty = "" if capture_output else None
            return subprocess.CompletedProcess(args, 0, empty, empty)
        return self.executor(
            [str(item) for item in args],
            check=check,
            capture_output=capture_output,
            text=True,
            env=env,
            timeout=timeout,
        )


def parse_os_release(text: str) -> Dict[str, str]:
    parsed: Dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        parsed[key] = value.strip().strip('"').strip("'")
    return parsed


def read_os_release() -> Dict[str, str]:
    path = pathlib.Path("/etc/os-release")
    if not path.is_file():
        raise BootstrapError("/etc/os-release is missing; cannot identify Linux distribution")
    return parse_os_release(path.read_text(encoding="utf-8", errors="replace"))


def require_supported_os(os_release: Dict[str, str]) -> None:
    distro_id = os_release.get("ID", "").lower()
    distro_like = set(os_release.get("ID_LIKE", "").lower().split())
    if distro_id not in {"ubuntu", "debian"} and "debian" not in distro_like:
        raise RuntimeError(
            "This installer currently supports only Ubuntu/Debian systems; "
            f"detected ID={distro_id or 'unknown'}"
        )


def validate_admin_user(username: str) -> str:
    if username == "root" or not USERNAME_PATTERN.fullmatch(username):
        raise ValueError(
            "Admin user must be a non-root Linux username using lowercase letters, "
            "digits, underscores, or hyphens"
        )
    return username


def _read_ssh_string(blob: bytes, offset: int) -> tuple[bytes, int]:
    if offset + 4 > len(blob):
        raise ValueError("truncated SSH public key")
    length = int.from_bytes(blob[offset : offset + 4], "big")
    start = offset + 4
    end = start + length
    if end > len(blob):
        raise ValueError("truncated SSH public key")
    return blob[start:end], end


def _decode_ed25519_blob(encoded: str) -> bytes:
    try:
        blob = base64.b64decode(encoded.encode("ascii"), validate=True)
    except (ValueError, UnicodeEncodeError) as exc:
        raise ValueError("invalid base64 in SSH public key") from exc

    algorithm, offset = _read_ssh_string(blob, 0)
    public_bytes, offset = _read_ssh_string(blob, offset)
    if algorithm != b"ssh-ed25519" or len(public_bytes) != 32 or offset != len(blob):
        raise ValueError("invalid Ed25519 SSH public key payload")
    return blob


def validate_public_key(text: str) -> str:
    cleaned = text.lstrip("\ufeff")
    if "PRIVATE KEY" in cleaned.upper():
        raise ValueError("a private key was supplied; provide only the .pub file")

    lines = [line.strip() for line in cleaned.splitlines() if line.strip()]
    if len(lines) != 1:
        raise ValueError("public key file must contain exactly one key")

    parts = lines[0].split()
    if len(parts) < 2 or parts[0] != "ssh-ed25519":
        raise ValueError("only ssh-ed25519 public keys are accepted")
    _decode_ed25519_blob(parts[1])
    return " ".join(parts)


def public_key_fingerprint(public_key: str) -> str:
    parts = validate_public_key(public_key).split()
    digest = hashlib.sha256(_decode_ed25519_blob(parts[1])).digest()
    encoded = base64.b64encode(digest).decode("ascii").rstrip("=")
    return f"SHA256:{encoded}"


def render_sshd_config(admin_user: str) -> str:
    username = validate_admin_user(admin_user)
    return (
        "# Managed by connetlinux/bootstrap_workstation.py.\n"
        "# OpenSSH remains the SSH implementation; Tailscale is the network boundary.\n"
        "PermitRootLogin no\n"
        "PubkeyAuthentication yes\n"
        "PasswordAuthentication no\n"
        "KbdInteractiveAuthentication no\n"
        "AuthenticationMethods publickey\n"
        f"AllowUsers {username}@100.64.0.0/10 "
        f"{username}@fd7a:115c:a1e0::/48\n"
        "LoginGraceTime 30\n"
        "MaxAuthTries 3\n"
        "ClientAliveInterval 60\n"
        "ClientAliveCountMax 3\n"
        "X11Forwarding no\n"
        "AllowAgentForwarding no\n"
        "AllowTcpForwarding yes\n"
        "GatewayPorts no\n"
        "PermitTunnel no\n"
    )


def render_sudoers(admin_user: str) -> str:
    username = validate_admin_user(admin_user)
    return f"{username} ALL=(ALL:ALL) NOPASSWD: ALL\n"


def effective_allowusers_are_tailscale_only(
    effective_config: str, admin_user: str
) -> bool:
    username = validate_admin_user(admin_user)
    actual = set()
    for raw_line in effective_config.lower().splitlines():
        parts = raw_line.split(None, 1)
        if len(parts) == 2 and parts[0] == "allowusers":
            actual.add(parts[1])
    expected = {
        f"{username}@100.64.0.0/10",
        f"{username}@fd7a:115c:a1e0::/48",
    }
    return actual == expected


def ensure_root() -> None:
    if os.geteuid() != 0:
        raise BootstrapError("--apply and --rollback must be run with sudo")


def command_path(name: str, fallback: Optional[str] = None) -> str:
    found = shutil.which(name)
    if found:
        return found
    if fallback and pathlib.Path(fallback).exists():
        return fallback
    raise BootstrapError(f"required command is missing: {name}")


def capture_command(args: Sequence[str], timeout: int = 10) -> Dict[str, Any]:
    try:
        result = subprocess.run(
            [str(item) for item in args],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return {
            "command": [str(item) for item in args],
            "returncode": result.returncode,
            "stdout": result.stdout.strip(),
            "stderr": result.stderr.strip(),
        }
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return {
            "command": [str(item) for item in args],
            "returncode": None,
            "stdout": "",
            "stderr": str(exc),
        }


def secure_write_bytes(
    path: pathlib.Path,
    content: bytes,
    mode: int,
    uid: int = 0,
    gid: int = 0,
) -> None:
    if path.is_symlink():
        raise BootstrapError(f"refusing to replace symlink: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    temporary_path = pathlib.Path(temporary_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_path, mode)
        os.chown(temporary_path, uid, gid)
        os.replace(temporary_path, path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def secure_write_text(
    path: pathlib.Path,
    content: str,
    mode: int,
    uid: int = 0,
    gid: int = 0,
) -> None:
    secure_write_bytes(path, content.encode("utf-8"), mode, uid, gid)


def secure_write_json(path: pathlib.Path, payload: Dict[str, Any]) -> None:
    rendered = json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    secure_write_text(path, rendered, 0o600)


def snapshot_regular_file(path: pathlib.Path, backup_dir: pathlib.Path) -> Dict[str, Any]:
    if path.is_symlink():
        raise BootstrapError(f"refusing to manage symlink: {path}")
    entry: Dict[str, Any] = {"path": str(path), "existed": path.exists()}
    if not path.exists():
        return entry
    if not path.is_file():
        raise BootstrapError(f"managed path is not a regular file: {path}")

    metadata = path.stat()
    relative_backup = pathlib.Path("files") / str(path).lstrip("/")
    backup_path = backup_dir / relative_backup
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, backup_path)
    entry.update(
        {
            "backup": str(relative_backup),
            "mode": stat.S_IMODE(metadata.st_mode),
            "uid": metadata.st_uid,
            "gid": metadata.st_gid,
        }
    )
    return entry


def user_record(username: str) -> Optional[Dict[str, Any]]:
    import pwd

    try:
        record = pwd.getpwnam(username)
    except KeyError:
        return None
    return {
        "name": record.pw_name,
        "uid": record.pw_uid,
        "gid": record.pw_gid,
        "home": record.pw_dir,
        "shell": record.pw_shell,
    }


def user_home_path(username: str) -> pathlib.Path:
    record = user_record(username)
    return pathlib.Path(record["home"] if record else f"/home/{username}")


def create_backup(admin_user: str) -> pathlib.Path:
    timestamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_dir = BACKUP_ROOT / timestamp
    suffix = 1
    while backup_dir.exists():
        backup_dir = BACKUP_ROOT / f"{timestamp}-{suffix}"
        suffix += 1
    backup_dir.mkdir(parents=True, mode=0o700)
    os.chmod(backup_dir, 0o700)

    authorized_keys = user_home_path(admin_user) / ".ssh" / "authorized_keys"
    managed_paths = (SSHD_DROPIN_PATH, SUDOERS_PATH, authorized_keys)
    observed_paths = (SSHD_MAIN_PATH,)
    files = []
    for path in managed_paths:
        entry = snapshot_regular_file(path, backup_dir)
        entry["managed"] = True
        files.append(entry)
    for path in observed_paths:
        entry = snapshot_regular_file(path, backup_dir)
        entry["managed"] = False
        files.append(entry)

    pre_state = {
        "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "platform": platform.platform(),
        "admin_user": user_record(admin_user),
        "commands": {
            "ssh_active": capture_command(["systemctl", "is-active", "ssh"]),
            "tailscaled_active": capture_command(
                ["systemctl", "is-active", "tailscaled"]
            ),
            "tailscale_ip": capture_command(["tailscale", "ip", "-4"]),
            "ufw": capture_command(["ufw", "status", "numbered"]),
            "disk": capture_command(["df", "-h", "/"]),
            "memory": capture_command(["free", "-h"]),
        },
        "sleep_targets": {
            target: capture_command(["systemctl", "is-enabled", target])
            for target in SLEEP_TARGETS
        },
    }
    manifest = {
        "schema_version": "1.0",
        "files": files,
        "pre_state": pre_state,
    }
    secure_write_json(backup_dir / "manifest.json", manifest)
    secure_write_json(
        backup_dir / "changes.json",
        {
            "created_admin_user": False,
            "ssh_units_temporarily_masked": [],
            "ufw_rule_added": False,
            "sleep_targets_newly_masked": [],
        },
    )
    return backup_dir


def update_changes(backup_dir: pathlib.Path, **updates: Any) -> None:
    path = backup_dir / "changes.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload.update(updates)
    secure_write_json(path, payload)


def require_sshd_dropin_support() -> None:
    if not SSHD_MAIN_PATH.is_file():
        raise BootstrapError("OpenSSH server did not create /etc/ssh/sshd_config")
    meaningful = []
    for raw_line in SSHD_MAIN_PATH.read_text(
        encoding="utf-8", errors="replace"
    ).splitlines():
        line = raw_line.strip()
        if line and not line.startswith("#"):
            meaningful.append(line)
    include = "Include /etc/ssh/sshd_config.d/*.conf"
    if not meaningful or meaningful[0].lower() != include.lower():
        raise BootstrapError(
            "sshd_config does not load /etc/ssh/sshd_config.d/*.conf before other "
            "directives; refusing to rewrite the distribution-owned main file"
        )


def restore_file_entry(entry: Dict[str, Any], backup_dir: pathlib.Path) -> None:
    path = pathlib.Path(entry["path"])
    if path.is_symlink():
        raise BootstrapError(f"refusing to restore over symlink: {path}")
    if entry.get("existed"):
        backup_path = backup_dir / entry["backup"]
        if not backup_path.is_file():
            raise BootstrapError(f"backup file is missing: {backup_path}")
        secure_write_bytes(
            path,
            backup_path.read_bytes(),
            int(entry["mode"]),
            int(entry["uid"]),
            int(entry["gid"]),
        )
    elif path.exists():
        if not path.is_file():
            raise BootstrapError(f"refusing to remove non-file during rollback: {path}")
        path.unlink()


def parse_tailscale_ipv4(text: str) -> Optional[str]:
    for raw_line in text.splitlines():
        candidate = raw_line.strip()
        try:
            address = ipaddress.ip_address(candidate)
        except ValueError:
            continue
        if address.version == 4 and address in TAILSCALE_IPV4_NETWORK:
            return str(address)
    return None


class WorkstationInstaller:
    def __init__(self, admin_user: str, public_key: str) -> None:
        self.admin_user = validate_admin_user(admin_user)
        self.public_key = validate_public_key(public_key)
        self.runner = CommandRunner(apply=True)
        self.backup_dir: Optional[pathlib.Path] = None

    def apply(self) -> Dict[str, Any]:
        self.backup_dir = create_backup(self.admin_user)
        print(f"Backup and pre-change state: {self.backup_dir}")
        try:
            self.install_base_packages()
            tailscale_ipv4 = self.ensure_tailscale()
            self.ensure_admin_account()
            self.install_authorized_key()
            self.install_sudoers()
            self.configure_sshd()
            self.configure_firewall()
            self.disable_sleep()
            host_fingerprints = self.verify(tailscale_ipv4)
            state = self.write_state(tailscale_ipv4, host_fingerprints)
        except Exception:
            print(
                "Bootstrap stopped. Existing local account access was not changed.\n"
                f"Rollback command: sudo python3 {pathlib.Path(__file__).name} "
                f"--rollback {self.backup_dir}",
                file=sys.stderr,
            )
            raise
        self.print_completion(state)
        return state

    def install_base_packages(self) -> None:
        environment = os.environ.copy()
        environment["DEBIAN_FRONTEND"] = "noninteractive"
        assert self.backup_dir is not None
        manifest = json.loads(
            (self.backup_dir / "manifest.json").read_text(encoding="utf-8")
        )
        ssh_was_active = (
            manifest["pre_state"]["commands"]["ssh_active"].get("stdout", "").strip()
            == "active"
        )
        temporarily_masked = []
        if not ssh_was_active and shutil.which("sshd") is None:
            for unit in ("ssh.service", "ssh.socket"):
                state = capture_command(["systemctl", "is-enabled", unit])
                if state.get("stdout", "").strip() != "masked":
                    self.runner.run(["systemctl", "mask", unit], check=False)
                    temporarily_masked.append(unit)
            update_changes(
                self.backup_dir,
                ssh_units_temporarily_masked=temporarily_masked,
            )
        self.runner.run(["apt-get", "update"], env=environment)
        self.runner.run(
            [
                "apt-get",
                "install",
                "-y",
                "--no-install-recommends",
                "openssh-server",
                "tmux",
                "curl",
                "ca-certificates",
            ],
            env=environment,
        )

    def ensure_tailscale(self) -> str:
        if shutil.which("tailscale") is None:
            fd, installer_name = tempfile.mkstemp(
                prefix="tailscale-install-", suffix=".sh"
            )
            os.close(fd)
            installer_path = pathlib.Path(installer_name)
            try:
                self.runner.run(
                    [
                        "curl",
                        "--proto",
                        "=https",
                        "--tlsv1.2",
                        "-fsSL",
                        TAILSCALE_INSTALL_URL,
                        "-o",
                        str(installer_path),
                    ]
                )
                if installer_path.stat().st_size < 100:
                    raise BootstrapError("downloaded Tailscale installer is unexpectedly small")
                self.runner.run(["/bin/sh", str(installer_path)])
            finally:
                installer_path.unlink(missing_ok=True)

        self.runner.run(["systemctl", "enable", "--now", "tailscaled"])
        address = self.current_tailscale_ipv4()
        if address is None:
            print(
                "Tailscale authentication is required. Open the URL printed below "
                "and finish login in your browser."
            )
            self.runner.run(["tailscale", "up"])
            address = self.current_tailscale_ipv4()
        if address is None:
            raise BootstrapError("Tailscale login completed without a usable 100.64.0.0/10 address")
        self.runner.run(["tailscale", "set", "--ssh=false"])
        return address

    def current_tailscale_ipv4(self) -> Optional[str]:
        result = self.runner.run(
            ["tailscale", "ip", "-4"], check=False, capture_output=True
        )
        return parse_tailscale_ipv4(result.stdout or "")

    def ensure_admin_account(self) -> None:
        import grp

        existing = user_record(self.admin_user)
        if existing is None:
            self.runner.run(
                [
                    "useradd",
                    "--create-home",
                    "--shell",
                    "/bin/bash",
                    self.admin_user,
                ]
            )
            if self.backup_dir:
                update_changes(self.backup_dir, created_admin_user=True)

        groups = ["sudo"]
        try:
            grp.getgrnam("docker")
        except KeyError:
            pass
        else:
            groups.append("docker")
        self.runner.run(
            ["usermod", "--append", "--groups", ",".join(groups), self.admin_user]
        )
        # A leading "!" locks the whole account on Linux and can block public-key
        # SSH. *NP* is deliberately not a valid password hash, while leaving the
        # account available for key authentication as documented by sshd(8).
        self.runner.run(["usermod", "--password", "*NP*", self.admin_user])

    def install_authorized_key(self) -> None:
        import pwd

        record = pwd.getpwnam(self.admin_user)
        home = pathlib.Path(record.pw_dir)
        ssh_directory = home / ".ssh"
        if ssh_directory.is_symlink():
            raise BootstrapError(f"refusing to use symlinked SSH directory: {ssh_directory}")
        ssh_directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(ssh_directory, 0o700)
        os.chown(ssh_directory, record.pw_uid, record.pw_gid)

        authorized_keys = ssh_directory / "authorized_keys"
        if authorized_keys.is_symlink():
            raise BootstrapError(f"refusing to use symlinked key file: {authorized_keys}")
        existing_lines: List[str] = []
        if authorized_keys.is_file():
            existing_lines = authorized_keys.read_text(
                encoding="utf-8", errors="strict"
            ).splitlines()

        identity = tuple(self.public_key.split()[:2])
        known_identities = {
            tuple(line.split()[:2])
            for line in existing_lines
            if len(line.split()) >= 2
        }
        if identity not in known_identities:
            existing_lines.append(self.public_key)
        content = "\n".join(existing_lines).rstrip() + "\n"
        secure_write_text(
            authorized_keys,
            content,
            0o600,
            record.pw_uid,
            record.pw_gid,
        )

    def install_sudoers(self) -> None:
        previous = SUDOERS_PATH.read_bytes() if SUDOERS_PATH.is_file() else None
        previous_stat = SUDOERS_PATH.stat() if previous is not None else None
        secure_write_text(SUDOERS_PATH, render_sudoers(self.admin_user), 0o440)
        try:
            visudo = command_path("visudo", "/usr/sbin/visudo")
            self.runner.run([visudo, "-cf", str(SUDOERS_PATH)])
        except Exception:
            if previous is None:
                SUDOERS_PATH.unlink(missing_ok=True)
            else:
                assert previous_stat is not None
                secure_write_bytes(
                    SUDOERS_PATH,
                    previous,
                    stat.S_IMODE(previous_stat.st_mode),
                    previous_stat.st_uid,
                    previous_stat.st_gid,
                )
            raise

    def configure_sshd(self) -> None:
        require_sshd_dropin_support()
        previous = SSHD_DROPIN_PATH.read_bytes() if SSHD_DROPIN_PATH.is_file() else None
        previous_stat = SSHD_DROPIN_PATH.stat() if previous is not None else None
        secure_write_text(SSHD_DROPIN_PATH, render_sshd_config(self.admin_user), 0o644)
        try:
            sshd = command_path("sshd", "/usr/sbin/sshd")
            self.runner.run([sshd, "-t"])
            effective = self.runner.run(
                [
                    sshd,
                    "-T",
                    "-C",
                    f"user={self.admin_user},host=workstation,addr=100.64.0.1",
                ],
                capture_output=True,
            ).stdout.lower()
            required = (
                "permitrootlogin no",
                "pubkeyauthentication yes",
                "passwordauthentication no",
                "kbdinteractiveauthentication no",
                "authenticationmethods publickey",
            )
            missing = [setting for setting in required if setting not in effective]
            if missing:
                raise BootstrapError(
                    "effective sshd configuration is not secure: " + ", ".join(missing)
                )
            if not effective_allowusers_are_tailscale_only(
                effective, self.admin_user
            ):
                raise BootstrapError("effective sshd AllowUsers is not Tailscale-only")
            assert self.backup_dir is not None
            changes = json.loads(
                (self.backup_dir / "changes.json").read_text(encoding="utf-8")
            )
            temporarily_masked = changes.get("ssh_units_temporarily_masked", [])
            if temporarily_masked:
                self.runner.run(["systemctl", "unmask", *temporarily_masked])
                update_changes(
                    self.backup_dir,
                    ssh_units_temporarily_masked=[],
                )
            self.runner.run(["systemctl", "enable", "--now", "ssh"])
        except Exception:
            if previous is None:
                SSHD_DROPIN_PATH.unlink(missing_ok=True)
            else:
                assert previous_stat is not None
                secure_write_bytes(
                    SSHD_DROPIN_PATH,
                    previous,
                    stat.S_IMODE(previous_stat.st_mode),
                    previous_stat.st_uid,
                    previous_stat.st_gid,
                )
            subprocess.run(
                ["systemctl", "reload", "ssh"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            raise

    def configure_firewall(self) -> None:
        if shutil.which("ufw") is None:
            print("UFW is not installed; sshd AllowUsers still limits login to Tailscale sources.")
            return
        status = self.runner.run(
            ["ufw", "status", "numbered"], check=False, capture_output=True
        ).stdout
        if not re.search(r"^Status:\s+active\s*$", status, flags=re.MULTILINE | re.I):
            print("UFW is inactive; it will not be enabled automatically.")
            return
        if "connetlinux-ssh" in status:
            print("Existing connetlinux UFW rule found; no duplicate rule added.")
            return
        self.runner.run(
            [
                "ufw",
                "allow",
                "in",
                "on",
                "tailscale0",
                "to",
                "any",
                "port",
                "22",
                "proto",
                "tcp",
                "comment",
                "connetlinux-ssh",
            ]
        )
        if self.backup_dir:
            update_changes(self.backup_dir, ufw_rule_added=True)

    def disable_sleep(self) -> None:
        assert self.backup_dir is not None
        manifest = json.loads(
            (self.backup_dir / "manifest.json").read_text(encoding="utf-8")
        )
        newly_masked = []
        for target in SLEEP_TARGETS:
            state = manifest["pre_state"]["sleep_targets"][target]
            if state.get("stdout", "").strip() != "masked":
                newly_masked.append(target)
        self.runner.run(["systemctl", "mask", *SLEEP_TARGETS])
        update_changes(
            self.backup_dir, sleep_targets_newly_masked=newly_masked
        )

    def verify(self, tailscale_ipv4: str) -> List[str]:
        self.runner.run(["systemctl", "is-active", "--quiet", "ssh"])
        self.runner.run(["systemctl", "is-active", "--quiet", "tailscaled"])
        try:
            with socket.create_connection((tailscale_ipv4, 22), timeout=5):
                pass
        except OSError as exc:
            raise BootstrapError(
                f"sshd is not reachable on {tailscale_ipv4}:22: {exc}"
            ) from exc

        fingerprints = []
        ssh_keygen = command_path("ssh-keygen")
        for public_host_key in sorted(
            pathlib.Path("/etc/ssh").glob("ssh_host_*_key.pub")
        ):
            result = self.runner.run(
                [ssh_keygen, "-lf", str(public_host_key)], capture_output=True
            )
            if result.stdout.strip():
                fingerprints.append(result.stdout.strip())
        if not fingerprints:
            raise BootstrapError("no SSH host-key fingerprints were found")
        return fingerprints

    def write_state(
        self, tailscale_ipv4: str, host_fingerprints: List[str]
    ) -> Dict[str, Any]:
        assert self.backup_dir is not None
        changes = json.loads(
            (self.backup_dir / "changes.json").read_text(encoding="utf-8")
        )
        state = {
            "schema_version": "1.0",
            "installed_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "installer_version": VERSION,
            "admin_user": self.admin_user,
            "tailscale_ipv4": tailscale_ipv4,
            "admin_public_key_fingerprint": public_key_fingerprint(self.public_key),
            "ssh_host_key_fingerprints": host_fingerprints,
            "backup_dir": str(self.backup_dir),
            "changes": changes,
        }
        secure_write_json(STATE_PATH, state)
        return state

    def print_completion(self, state: Dict[str, Any]) -> None:
        print("\nCONNETLINUX_ACCESS_BEGIN")
        print(f"host={state['tailscale_ipv4']}")
        print(f"user={state['admin_user']}")
        print(f"key_fingerprint={state['admin_public_key_fingerprint']}")
        for fingerprint in state["ssh_host_key_fingerprints"]:
            print(f"host_key={fingerprint}")
        print(f"backup={state['backup_dir']}")
        print("CONNETLINUX_ACCESS_END")
        print(
            "\nKeep this local console open until the first Windows SSH login succeeds. "
            "Send only the CONNETLINUX_ACCESS block to the maintainer; it contains no secret."
        )


def delete_ufw_rule_by_comment(comment: str) -> None:
    if shutil.which("ufw") is None:
        return
    result = subprocess.run(
        ["ufw", "status", "numbered"],
        check=False,
        capture_output=True,
        text=True,
    )
    numbers = []
    for line in result.stdout.splitlines():
        if comment not in line:
            continue
        match = re.match(r"^\[\s*(\d+)\]", line.strip())
        if match:
            numbers.append(int(match.group(1)))
    for number in sorted(numbers, reverse=True):
        subprocess.run(["ufw", "--force", "delete", str(number)], check=True)


def rollback(backup_directory: str) -> None:
    ensure_root()
    backup_dir = pathlib.Path(backup_directory).resolve()
    backup_root = BACKUP_ROOT.resolve()
    try:
        backup_dir.relative_to(backup_root)
    except ValueError as exc:
        raise BootstrapError(f"rollback path must be inside {backup_root}") from exc

    manifest_path = backup_dir / "manifest.json"
    changes_path = backup_dir / "changes.json"
    if not manifest_path.is_file() or not changes_path.is_file():
        raise BootstrapError("rollback manifest is incomplete")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    changes = json.loads(changes_path.read_text(encoding="utf-8"))

    for entry in manifest["files"]:
        if entry.get("managed"):
            restore_file_entry(entry, backup_dir)

    if changes.get("ufw_rule_added"):
        delete_ufw_rule_by_comment("connetlinux-ssh")
    newly_masked = changes.get("sleep_targets_newly_masked", [])
    if newly_masked:
        subprocess.run(["systemctl", "unmask", *newly_masked], check=True)
    temporarily_masked = changes.get("ssh_units_temporarily_masked", [])
    if temporarily_masked:
        subprocess.run(["systemctl", "unmask", *temporarily_masked], check=True)
    subprocess.run(["systemctl", "daemon-reload"], check=True)

    sshd = shutil.which("sshd") or "/usr/sbin/sshd"
    if pathlib.Path(sshd).exists():
        subprocess.run([sshd, "-t"], check=True)
        subprocess.run(["systemctl", "reload", "ssh"], check=False)
    print(
        "Managed SSH, sudoers, authorized_keys, UFW, and sleep settings were restored.\n"
        "Installed packages and the locked codex-admin account were intentionally retained "
        "to avoid destructive rollback."
    )


def print_plan(
    os_release: Dict[str, str], admin_user: str, public_key: str
) -> None:
    print("connetlinux workstation bootstrap preview (no system changes)\n")
    print(f"OS: {os_release.get('PRETTY_NAME', os_release.get('ID', 'unknown'))}")
    print(f"Admin account: {admin_user}")
    print(f"Public key fingerprint: {public_key_fingerprint(public_key)}")
    print(
        "\nPlanned changes:\n"
        "  1. Record system state and back up managed configuration.\n"
        "  2. Install OpenSSH server, tmux, curl, and CA certificates.\n"
        "  3. Install/login Tailscale using its official Linux installer.\n"
        "  4. Create a no-password codex-admin account with the supplied public key.\n"
        "  5. Grant that account passwordless sudo and Docker group access if available.\n"
        "  6. Permit SSH public-key login only for codex-admin from Tailscale ranges.\n"
        "  7. Add a Tailscale-only UFW rule only when UFW is already active.\n"
        "  8. Mask sleep/hibernate targets and verify SSH on the Tailscale address.\n"
        "\nNot changed: Docker workloads, CUDA/NVIDIA, project files, disk data, public NAT."
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Prepare an Ubuntu/Debian workstation for key-only OpenSSH over Tailscale. "
            "Without --apply, only show the plan."
        )
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="apply system changes")
    mode.add_argument(
        "--rollback",
        metavar="BACKUP_DIR",
        help="restore managed settings from a recorded backup",
    )
    parser.add_argument(
        "--admin-user", default=DEFAULT_ADMIN_USER, help="dedicated remote admin user"
    )
    parser.add_argument(
        "--admin-public-key-file",
        default="codex-admin.pub",
        help="path to the Ed25519 public key file",
    )
    parser.add_argument("--version", action="version", version=VERSION)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.rollback:
            rollback(args.rollback)
            return 0
        if platform.system() != "Linux":
            raise BootstrapError("this installer must run on the target Linux workstation")
        os_release = read_os_release()
        require_supported_os(os_release)
        admin_user = validate_admin_user(args.admin_user)
        key_path = pathlib.Path(args.admin_public_key_file)
        if not key_path.is_file():
            raise BootstrapError(f"public key file not found: {key_path}")
        public_key = validate_public_key(key_path.read_text(encoding="utf-8"))
        if not args.apply:
            print_plan(os_release, admin_user, public_key)
            print(
                "\nTo apply: sudo python3 bootstrap_workstation.py --apply "
                f"--admin-public-key-file {shlex.quote(str(key_path))}"
            )
            return 0
        ensure_root()
        WorkstationInstaller(admin_user, public_key).apply()
        return 0
    except (BootstrapError, ValueError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
