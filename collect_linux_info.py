#!/usr/bin/env python3
"""Collect read-only facts needed to configure a Linux compute workstation."""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import getpass
import glob
import json
import os
import pathlib
import platform
import re
import shlex
import shutil
import socket
import subprocess
import sys
import time
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple


COLLECTOR_VERSION = "0.2.0"
SCHEMA_VERSION = "1.0"
COMMAND_TIMEOUT_SECONDS = 8
MAX_COMMAND_OUTPUT_CHARS = 32_000
GIB = 1024 ** 3


@dataclasses.dataclass(frozen=True)
class CommandResult:
    found: bool
    returncode: Optional[int] = None
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False

    @property
    def succeeded(self) -> bool:
        return self.found and not self.timed_out and self.returncode == 0

    @property
    def output(self) -> str:
        return self.stdout.strip() or self.stderr.strip()


def run_command(
    command: Sequence[str],
    *,
    timeout: int = COMMAND_TIMEOUT_SECONDS,
) -> CommandResult:
    executable = command[0] if command else ""
    if not executable or shutil.which(executable) is None:
        return CommandResult(found=False)

    environment = os.environ.copy()
    environment["LC_ALL"] = "C"
    environment["LANG"] = "C"
    try:
        completed = subprocess.run(
            list(command),
            capture_output=True,
            check=False,
            errors="replace",
            text=True,
            timeout=timeout,
            env=environment,
        )
    except subprocess.TimeoutExpired as exc:
        return CommandResult(
            found=True,
            stdout=_truncate(_coerce_text(exc.stdout)),
            stderr=_truncate(_coerce_text(exc.stderr)),
            timed_out=True,
        )
    except OSError:
        return CommandResult(found=True, returncode=None)

    return CommandResult(
        found=True,
        returncode=completed.returncode,
        stdout=_truncate(completed.stdout),
        stderr=_truncate(completed.stderr),
    )


def _coerce_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _truncate(value: str, limit: int = MAX_COMMAND_OUTPUT_CHARS) -> str:
    if len(value) <= limit:
        return value
    return value[:limit] + "\n[output truncated]"


def read_text(path: pathlib.Path, limit: int = MAX_COMMAND_OUTPUT_CHARS) -> str:
    try:
        return _truncate(path.read_text(encoding="utf-8", errors="replace"), limit)
    except (OSError, UnicodeError):
        return ""


def parse_os_release(content: str) -> Dict[str, str]:
    result: Dict[str, str] = {}
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, raw_value = line.split("=", 1)
        try:
            parsed = shlex.split(raw_value, posix=True)
            value = parsed[0] if parsed else ""
        except ValueError:
            value = raw_value.strip().strip('"\'')
        result[key.strip()] = value
    return result


def parse_meminfo(content: str) -> Dict[str, int]:
    result: Dict[str, int] = {}
    for raw_line in content.splitlines():
        match = re.match(r"^([A-Za-z_()]+):\s+(\d+)\s*(kB)?", raw_line)
        if not match:
            continue
        value = int(match.group(2))
        result[match.group(1)] = value * 1024 if match.group(3) else value
    return result


def sanitize_filename(value: str) -> str:
    sanitized = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip())
    return sanitized.strip("-._") or "linux-host"


def parse_listener_port(endpoint: str) -> Optional[int]:
    endpoint = endpoint.strip()
    if not endpoint:
        return None
    match = re.search(r":(\d+)$", endpoint)
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def first_nonempty_line(value: str) -> Optional[str]:
    for line in value.splitlines():
        line = line.strip()
        if line:
            return line
    return None


def command_version(command: Sequence[str]) -> Dict[str, Any]:
    result = run_command(command)
    return {
        "installed": result.found,
        "version": first_nonempty_line(result.output) if result.found else None,
        "command_ok": result.succeeded,
    }


def collect_system() -> Dict[str, Any]:
    os_release = parse_os_release(read_text(pathlib.Path("/etc/os-release")))
    virtualization = run_command(["systemd-detect-virt"])
    uptime = run_command(["uptime", "-s"])
    return {
        "platform": platform.system(),
        "hostname": socket.gethostname(),
        "distribution_id": os_release.get("ID"),
        "distribution_name": os_release.get("PRETTY_NAME"),
        "distribution_version": os_release.get("VERSION_ID"),
        "kernel": platform.release(),
        "architecture": platform.machine(),
        "python": platform.python_version(),
        "virtualization": virtualization.output if virtualization.succeeded else None,
        "boot_time": uptime.output if uptime.succeeded else None,
        "systemd": pathlib.Path("/run/systemd/system").exists(),
    }


def collect_identity() -> Dict[str, Any]:
    uid = os.getuid() if hasattr(os, "getuid") else None
    gid = os.getgid() if hasattr(os, "getgid") else None
    groups = run_command(["id", "-Gn"])
    passwd_entry = run_command(["getent", "passwd", getpass.getuser()])
    login_shell = None
    if passwd_entry.succeeded:
        fields = passwd_entry.output.split(":")
        if len(fields) >= 7:
            login_shell = fields[6]
    return {
        "username": getpass.getuser(),
        "uid": uid,
        "gid": gid,
        "groups": groups.output.split() if groups.succeeded else [],
        "home": str(pathlib.Path.home()),
        "login_shell": login_shell,
        "running_as_root": uid == 0 if uid is not None else False,
        "sudo_installed": shutil.which("sudo") is not None,
    }


def collect_cpu() -> Dict[str, Any]:
    content = read_text(pathlib.Path("/proc/cpuinfo"), limit=256_000)
    records = [block for block in content.split("\n\n") if block.strip()]
    models: List[str] = []
    physical_cores: set[Tuple[str, str]] = set()
    for record in records:
        fields: Dict[str, str] = {}
        for line in record.splitlines():
            if ":" in line:
                key, value = line.split(":", 1)
                fields[key.strip()] = value.strip()
        model = fields.get("model name") or fields.get("Hardware") or fields.get("Processor")
        if model and model not in models:
            models.append(model)
        if "physical id" in fields and "core id" in fields:
            physical_cores.add((fields["physical id"], fields["core id"]))

    try:
        load_average = list(os.getloadavg())
    except (AttributeError, OSError):
        load_average = []

    return {
        "logical_cpus": os.cpu_count(),
        "physical_cores_detected": len(physical_cores) or None,
        "models": models,
        "load_average_1_5_15": load_average,
    }


def collect_memory() -> Dict[str, Any]:
    values = parse_meminfo(read_text(pathlib.Path("/proc/meminfo")))
    return {
        "total_bytes": values.get("MemTotal"),
        "available_bytes": values.get("MemAvailable"),
        "swap_total_bytes": values.get("SwapTotal"),
        "swap_free_bytes": values.get("SwapFree"),
    }


def _disk_usage(path: pathlib.Path) -> Optional[Dict[str, Any]]:
    try:
        usage = shutil.disk_usage(str(path))
    except OSError:
        return None
    return {
        "path": str(path),
        "total_bytes": usage.total,
        "used_bytes": usage.used,
        "free_bytes": usage.free,
    }


def collect_storage() -> Dict[str, Any]:
    usages: List[Dict[str, Any]] = []
    seen_devices: set[Tuple[int, int]] = set()
    for candidate in (pathlib.Path("/"), pathlib.Path.home(), pathlib.Path("/data")):
        if not candidate.exists():
            continue
        try:
            stat_result = candidate.stat()
            device_key = (stat_result.st_dev, stat_result.st_ino if candidate == pathlib.Path("/") else 0)
        except OSError:
            continue
        if device_key in seen_devices:
            continue
        seen_devices.add(device_key)
        usage = _disk_usage(candidate)
        if usage:
            usages.append(usage)

    lsblk = run_command(
        [
            "lsblk",
            "--json",
            "--bytes",
            "--output",
            "NAME,TYPE,SIZE,FSTYPE,MOUNTPOINTS,MODEL,ROTA",
        ]
    )
    block_devices: List[Dict[str, Any]] = []
    if lsblk.succeeded:
        try:
            parsed = json.loads(lsblk.stdout)
            if isinstance(parsed.get("blockdevices"), list):
                block_devices = parsed["blockdevices"]
        except (json.JSONDecodeError, TypeError, AttributeError):
            block_devices = []

    return {
        "filesystem_usage": usages,
        "block_devices": block_devices,
        "lsblk_available": lsblk.found,
    }


def collect_gpu() -> Dict[str, Any]:
    nvidia = run_command(
        [
            "nvidia-smi",
            "--query-gpu=index,name,driver_version,memory.total",
            "--format=csv,noheader,nounits",
        ],
        timeout=15,
    )
    nvidia_devices: List[Dict[str, Any]] = []
    if nvidia.succeeded:
        for line in nvidia.stdout.splitlines():
            fields = [part.strip() for part in line.split(",")]
            if len(fields) != 4:
                continue
            try:
                memory_mib: Optional[int] = int(fields[3])
            except ValueError:
                memory_mib = None
            nvidia_devices.append(
                {
                    "index": fields[0],
                    "name": fields[1],
                    "driver_version": fields[2],
                    "memory_total_mib": memory_mib,
                }
            )

    pci = run_command(["lspci", "-mm"])
    display_devices = []
    if pci.succeeded:
        display_devices = [
            line.strip()
            for line in pci.stdout.splitlines()
            if re.search(r'"(?:VGA compatible controller|3D controller|Display controller)"', line)
        ]

    return {
        "nvidia_smi_available": nvidia.found,
        "nvidia_devices": nvidia_devices,
        "display_devices": display_devices,
    }


def collect_network() -> Dict[str, Any]:
    interfaces: List[Dict[str, Any]] = []
    address_result = run_command(["ip", "-json", "address", "show"])
    if address_result.succeeded:
        try:
            for item in json.loads(address_result.stdout):
                addresses = []
                for address in item.get("addr_info", []):
                    if address.get("family") not in {"inet", "inet6"}:
                        continue
                    addresses.append(
                        {
                            "family": address.get("family"),
                            "address": address.get("local"),
                            "prefix_length": address.get("prefixlen"),
                            "scope": address.get("scope"),
                        }
                    )
                interfaces.append(
                    {
                        "name": item.get("ifname"),
                        "state": item.get("operstate"),
                        "mtu": item.get("mtu"),
                        "addresses": addresses,
                    }
                )
        except (json.JSONDecodeError, TypeError):
            interfaces = []

    routes: List[Dict[str, Any]] = []
    route_result = run_command(["ip", "-json", "route", "show", "default"])
    if route_result.succeeded:
        try:
            for route in json.loads(route_result.stdout):
                routes.append(
                    {
                        "gateway": route.get("gateway"),
                        "device": route.get("dev"),
                        "metric": route.get("metric"),
                        "protocol": route.get("protocol"),
                    }
                )
        except (json.JSONDecodeError, TypeError):
            routes = []

    return {
        "ip_command_available": address_result.found,
        "interfaces": interfaces,
        "default_routes": routes,
    }


def collect_tailscale() -> Dict[str, Any]:
    version = command_version(["tailscale", "version"])
    status_result = run_command(["tailscale", "status", "--json"])
    status: Dict[str, Any] = {
        "installed": version["installed"],
        "version": version["version"],
        "status_command_ok": status_result.succeeded,
    }
    if not status_result.succeeded:
        return status

    try:
        parsed = json.loads(status_result.stdout)
    except json.JSONDecodeError:
        status["status_parse_error"] = True
        return status

    self_node = parsed.get("Self") if isinstance(parsed.get("Self"), dict) else {}
    status.update(
        {
            "backend_state": parsed.get("BackendState"),
            "tailscale_ips": parsed.get("TailscaleIPs") or self_node.get("TailscaleIPs") or [],
            "hostname": self_node.get("HostName"),
            "dns_name": self_node.get("DNSName"),
            "online": self_node.get("Online"),
            "tags": self_node.get("Tags") or [],
        }
    )
    return status


def systemd_service_status(names: Iterable[str]) -> Dict[str, Any]:
    result: Dict[str, Any] = {"systemctl_available": shutil.which("systemctl") is not None}
    if not result["systemctl_available"]:
        return result
    for name in names:
        active = run_command(["systemctl", "is-active", name])
        enabled = run_command(["systemctl", "is-enabled", name])
        result[name] = {
            "active": active.output or None,
            "enabled": enabled.output or None,
        }
    return result


def _effective_sshd_config(command_prefix: Sequence[str]) -> Dict[str, Any]:
    result = run_command([*command_prefix, "sshd", "-T"])
    if not result.succeeded:
        return {"available": result.found, "readable": False}

    allowed_keys = {
        "port",
        "listenaddress",
        "permitrootlogin",
        "passwordauthentication",
        "pubkeyauthentication",
        "kbdinteractiveauthentication",
        "authenticationmethods",
        "allowusers",
        "allowgroups",
        "maxauthtries",
        "clientaliveinterval",
        "clientalivecountmax",
        "x11forwarding",
        "allowtcpforwarding",
        "gatewayports",
    }
    config: Dict[str, Any] = {"available": True, "readable": True}
    for line in result.stdout.splitlines():
        if " " not in line:
            continue
        key, value = line.split(None, 1)
        if key not in allowed_keys:
            continue
        if key in {"port", "listenaddress"}:
            config.setdefault(key, []).append(value)
        else:
            config[key] = value
    return config


def _sudo_without_prompt_available(allow_sudo: bool) -> bool:
    if not allow_sudo or shutil.which("sudo") is None:
        return False
    return run_command(["sudo", "-n", "true"]).succeeded


def collect_ssh(allow_sudo: bool) -> Dict[str, Any]:
    version = command_version(["sshd", "-V"])
    services = systemd_service_status(["ssh", "sshd"])
    sudo_available = _sudo_without_prompt_available(allow_sudo)
    effective = _effective_sshd_config([])
    if not effective.get("readable") and sudo_available:
        effective = _effective_sshd_config(["sudo", "-n"])

    listeners_result = run_command(["ss", "-H", "-lnt"])
    listening_ports: List[int] = []
    if listeners_result.succeeded:
        for line in listeners_result.stdout.splitlines():
            fields = line.split()
            if len(fields) < 4:
                continue
            port = parse_listener_port(fields[3])
            if port is not None:
                listening_ports.append(port)

    fingerprints: List[Dict[str, Any]] = []
    for public_key in sorted(glob.glob("/etc/ssh/ssh_host_*_key.pub")):
        fingerprint = run_command(["ssh-keygen", "-lf", public_key])
        if not fingerprint.succeeded:
            continue
        fields = fingerprint.output.split()
        if len(fields) < 4:
            continue
        fingerprints.append(
            {
                "file": public_key,
                "bits": fields[0],
                "fingerprint": fields[1],
                "type": fields[-1].strip("()"),
            }
        )

    return {
        "server_installed": version["installed"],
        "server_version": version["version"],
        "services": services,
        "effective_config": effective,
        "listening_tcp_ports": sorted(set(listening_ports)),
        "host_key_fingerprints": fingerprints,
        "sudo_read_only_checks_used": sudo_available,
    }


def collect_firewall(allow_sudo: bool) -> Dict[str, Any]:
    sudo_available = _sudo_without_prompt_available(allow_sudo)
    prefix = ["sudo", "-n"] if sudo_available else []

    ufw_installed = shutil.which("ufw") is not None
    ufw = (
        run_command([*prefix, "ufw", "status", "verbose"])
        if ufw_installed
        else CommandResult(found=False)
    )
    ufw_status = None
    ufw_ssh_rules: List[str] = []
    if ufw.succeeded:
        for line in ufw.stdout.splitlines():
            stripped = line.strip()
            if stripped.lower().startswith("status:"):
                ufw_status = stripped.split(":", 1)[1].strip().lower()
            if re.search(r"(?:\b22(?:/tcp)?\b|OpenSSH)", stripped, flags=re.IGNORECASE):
                ufw_ssh_rules.append(stripped)

    firewalld_installed = shutil.which("firewall-cmd") is not None
    firewalld_state = (
        run_command([*prefix, "firewall-cmd", "--state"])
        if firewalld_installed
        else CommandResult(found=False)
    )
    firewalld_services = (
        run_command([*prefix, "firewall-cmd", "--list-services"])
        if firewalld_installed
        else CommandResult(found=False)
    )
    firewalld_ports = (
        run_command([*prefix, "firewall-cmd", "--list-ports"])
        if firewalld_installed
        else CommandResult(found=False)
    )
    return {
        "sudo_read_only_checks_used": sudo_available,
        "ufw": {
            "installed": ufw_installed,
            "readable": ufw.succeeded,
            "status": ufw_status,
            "ssh_rules": ufw_ssh_rules,
        },
        "firewalld": {
            "installed": firewalld_installed,
            "state": firewalld_state.output if firewalld_state.succeeded else None,
            "services": firewalld_services.output.split() if firewalld_services.succeeded else [],
            "ports": firewalld_ports.output.split() if firewalld_ports.succeeded else [],
        },
        "nft_command_installed": shutil.which("nft") is not None,
    }


def collect_power() -> Dict[str, Any]:
    chassis = run_command(["hostnamectl", "chassis"])
    default_target = run_command(["systemctl", "get-default"])
    targets: Dict[str, Any] = {}
    for target in ("sleep.target", "suspend.target", "hibernate.target", "hybrid-sleep.target"):
        result = run_command(["systemctl", "is-enabled", target])
        targets[target] = result.output or None
    batteries = [pathlib.Path(path).name for path in glob.glob("/sys/class/power_supply/BAT*")]
    return {
        "chassis": chassis.output if chassis.succeeded else None,
        "default_systemd_target": default_target.output if default_target.succeeded else None,
        "sleep_targets": targets,
        "batteries": batteries,
    }


def collect_time_sync() -> Dict[str, Any]:
    timezone = run_command(["timedatectl", "show", "--property=Timezone", "--value"])
    synchronized = run_command(
        ["timedatectl", "show", "--property=NTPSynchronized", "--value"]
    )
    return {
        "timezone": timezone.output if timezone.succeeded else None,
        "ntp_synchronized": synchronized.output if synchronized.succeeded else None,
    }


def collect_security_modules() -> Dict[str, Any]:
    selinux = run_command(["getenforce"])
    apparmor = run_command(["aa-enabled"])
    if not apparmor.found:
        apparmor = run_command(["aa-status", "--enabled"])
    return {
        "selinux": selinux.output if selinux.succeeded else None,
        "apparmor_enabled": apparmor.succeeded if apparmor.found else None,
    }


def collect_tools() -> Dict[str, Any]:
    commands: Dict[str, Sequence[str]] = {
        "python3": ["python3", "--version"],
        "git": ["git", "--version"],
        "curl": ["curl", "--version"],
        "rsync": ["rsync", "--version"],
        "tmux": ["tmux", "-V"],
        "screen": ["screen", "--version"],
        "docker": ["docker", "--version"],
        "docker_compose": ["docker", "compose", "version"],
        "podman": ["podman", "--version"],
        "gcc": ["gcc", "--version"],
        "nvcc": ["nvcc", "--version"],
        "slurm_sbatch": ["sbatch", "--version"],
    }
    tools = {name: command_version(command) for name, command in commands.items()}
    docker_server = run_command(["docker", "version", "--format", "{{.Server.Version}}"])
    tools["docker"]["server_accessible"] = docker_server.succeeded
    tools["docker"]["server_version"] = docker_server.output if docker_server.succeeded else None
    return tools


def collect_network_checks() -> Dict[str, Any]:
    checks: Dict[str, Any] = {}
    for host in ("github.com", "login.tailscale.com", "pypi.org"):
        started = time.monotonic()
        dns_ok = False
        tcp_ok = False
        try:
            socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
            dns_ok = True
            with socket.create_connection((host, 443), timeout=5):
                tcp_ok = True
        except OSError:
            pass
        checks[host] = {
            "dns_ok": dns_ok,
            "tcp_443_ok": tcp_ok,
            "elapsed_ms": round((time.monotonic() - started) * 1000),
        }
    return checks


def build_readiness(report: Dict[str, Any]) -> Dict[str, Any]:
    ssh_services = report["ssh"].get("services", {})
    ssh_active = any(
        ssh_services.get(name, {}).get("active") == "active" for name in ("ssh", "sshd")
    )
    tailscale = report["tailscale"]
    tools = report["tools"]
    root_usage = next(
        (
            item
            for item in report["storage"].get("filesystem_usage", [])
            if item.get("path") == "/"
        ),
        None,
    )
    return {
        "linux": report["system"].get("platform") == "Linux",
        "openssh_server_installed": report["ssh"].get("server_installed", False),
        "openssh_service_active": ssh_active,
        "tailscale_installed": tailscale.get("installed", False),
        "tailscale_online": tailscale.get("online") is True
        or tailscale.get("backend_state") == "Running",
        "persistent_terminal_available": bool(
            tools.get("tmux", {}).get("installed") or tools.get("screen", {}).get("installed")
        ),
        "root_free_bytes": root_usage.get("free_bytes") if root_usage else None,
    }


def build_warnings(report: Dict[str, Any]) -> List[str]:
    warnings: List[str] = []
    readiness = report["readiness"]
    memory = report["memory"]
    power = report["power"]

    if not readiness["linux"]:
        warnings.append("The collector is intended to run on the target Linux workstation.")
    if not readiness["openssh_server_installed"]:
        warnings.append("OpenSSH server is not installed or sshd is not on PATH.")
    elif not readiness["openssh_service_active"]:
        warnings.append("OpenSSH server is installed but no active ssh/sshd systemd service was detected.")
    if not readiness["tailscale_installed"]:
        warnings.append("Tailscale is not installed; private remote access is not ready.")
    elif not readiness["tailscale_online"]:
        warnings.append("Tailscale is installed but does not appear online.")
    if not readiness["persistent_terminal_available"]:
        warnings.append("Neither tmux nor screen is installed; disconnected SSH sessions may stop jobs.")

    free_bytes = readiness.get("root_free_bytes")
    if isinstance(free_bytes, int) and free_bytes < 20 * GIB:
        warnings.append("The root filesystem has less than 20 GiB free.")
    if memory.get("swap_total_bytes") == 0:
        warnings.append("Swap is disabled; memory pressure can terminate long-running jobs.")

    if power.get("chassis") in {"desktop", "laptop", "convertible"}:
        unmasked = [
            name
            for name, state in power.get("sleep_targets", {}).items()
            if state and state != "masked"
        ]
        if unmasked:
            warnings.append("Sleep targets are not all masked; automatic suspend may interrupt jobs.")

    effective = report["ssh"].get("effective_config", {})
    if effective.get("passwordauthentication") == "yes":
        warnings.append("SSH password authentication is enabled; key-only access should be considered.")
    if effective.get("permitrootlogin") not in {None, "no", "prohibit-password"}:
        warnings.append("SSH root login is enabled by the effective configuration.")
    return warnings


def collect_report(
    *,
    allow_sudo: bool,
    network_check: bool,
    progress: Optional[Callable[[str], None]] = None,
) -> Dict[str, Any]:
    report: Dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "collector": {
            "name": "connetlinux workstation collector",
            "version": COLLECTOR_VERSION,
            "collected_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        },
        "collection_options": {
            "allow_sudo": allow_sudo,
            "network_check": network_check,
        },
        "privacy": {
            "contains_private_keys": False,
            "contains_environment_variables": False,
            "contains_tokens_or_passwords": False,
            "notice": "Local IP addresses, hostnames and account names are operational data. Share this report privately and never commit it.",
        },
    }

    collectors: List[Tuple[str, str, Callable[[], Dict[str, Any]]]] = [
        ("system", "system", collect_system),
        ("identity", "user identity", collect_identity),
        ("cpu", "CPU", collect_cpu),
        ("memory", "memory and swap", collect_memory),
        ("storage", "storage", collect_storage),
        ("gpu", "GPU", collect_gpu),
        ("network", "local network", collect_network),
        ("tailscale", "Tailscale", collect_tailscale),
        ("ssh", "OpenSSH", lambda: collect_ssh(allow_sudo)),
        ("firewall", "firewall", lambda: collect_firewall(allow_sudo)),
        ("power", "power and sleep settings", collect_power),
        ("time_sync", "time synchronization", collect_time_sync),
        ("security_modules", "security modules", collect_security_modules),
        ("tools", "compute tools", collect_tools),
    ]
    if network_check:
        collectors.append(
            ("outbound_network_checks", "optional outbound network", collect_network_checks)
        )

    total_steps = len(collectors) + 1
    for index, (key, label, collector) in enumerate(collectors, start=1):
        if progress:
            progress(f"[{index}/{total_steps}] Collecting {label}...")
        report[key] = collector()

    if progress:
        progress(f"[{total_steps}/{total_steps}] Building readiness summary...")
    report["readiness"] = build_readiness(report)
    report["warnings"] = build_warnings(report)
    return report


def format_bytes(value: Any) -> str:
    if not isinstance(value, int):
        return "unknown"
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    size = float(value)
    for unit in units:
        if abs(size) < 1024 or unit == units[-1]:
            return f"{size:.1f} {unit}"
        size /= 1024
    return str(value)


def print_summary(report: Dict[str, Any], output_path: pathlib.Path) -> None:
    system = report["system"]
    memory = report["memory"]
    gpu_names = [item.get("name") for item in report["gpu"].get("nvidia_devices", [])]
    tailscale = report["tailscale"]
    readiness = report["readiness"]
    print("Linux workstation diagnostic completed.")
    print(f"Report: {output_path}")
    print(f"Host: {system.get('hostname')} | OS: {system.get('distribution_name') or system.get('platform')}")
    print(
        f"CPU: {report['cpu'].get('logical_cpus')} logical | "
        f"RAM: {format_bytes(memory.get('total_bytes'))} | "
        f"Swap: {format_bytes(memory.get('swap_total_bytes'))}"
    )
    print(f"GPU: {', '.join(str(name) for name in gpu_names if name) or 'none detected'}")
    print(
        f"SSH active: {readiness.get('openssh_service_active')} | "
        f"Tailscale: {tailscale.get('backend_state') or 'not ready'} | "
        f"Tailscale IPs: {', '.join(tailscale.get('tailscale_ips') or []) or 'none'}"
    )
    if report["warnings"]:
        print("Review items:")
        for warning in report["warnings"]:
            print(f"- {warning}")
    else:
        print("Review items: none detected")
    print("Share the JSON report privately. Do not commit it to GitHub.")


def write_report(report: Dict[str, Any], output_path: pathlib.Path) -> None:
    output_path = output_path.expanduser().resolve()
    if not output_path.parent.exists():
        raise FileNotFoundError(f"Output directory does not exist: {output_path.parent}")
    if not output_path.parent.is_dir():
        raise NotADirectoryError(f"Output parent is not a directory: {output_path.parent}")
    temporary_path = output_path.with_name(output_path.name + ".tmp")
    payload = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    temporary_path.write_text(payload, encoding="utf-8")
    if os.name == "posix":
        temporary_path.chmod(0o600)
    os.replace(str(temporary_path), str(output_path))


def default_output_path() -> pathlib.Path:
    timestamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    hostname = sanitize_filename(socket.gethostname())
    return pathlib.Path.cwd() / f"workstation-report-{hostname}-{timestamp}.json"


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Collect read-only Linux workstation facts for SSH and multi-user compute setup. "
            "No system configuration is changed."
        )
    )
    parser.add_argument(
        "--output",
        type=pathlib.Path,
        default=None,
        help="JSON report path. Defaults to workstation-report-<host>-<time>.json.",
    )
    parser.add_argument(
        "--network-check",
        action="store_true",
        help="Opt in to outbound DNS and TCP/443 checks for GitHub, Tailscale and PyPI.",
    )
    parser.add_argument(
        "--allow-sudo",
        action="store_true",
        help="Opt in to non-interactive read-only checks via 'sudo -n'. Never prompts for a password.",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Write the JSON report without the terminal summary.",
    )
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    output_path = args.output or default_output_path()
    progress = None
    if not args.quiet:
        progress = lambda message: print(message, file=sys.stderr, flush=True)
    try:
        report = collect_report(
            allow_sudo=bool(args.allow_sudo),
            network_check=bool(args.network_check),
            progress=progress,
        )
        write_report(report, output_path)
    except (OSError, ValueError) as exc:
        print(f"Collector failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    if not args.quiet:
        print_summary(report, output_path.expanduser().resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
