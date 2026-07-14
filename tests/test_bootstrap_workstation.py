from __future__ import annotations

import base64
import importlib.util
import pathlib
import struct
import subprocess
import sys
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "bootstrap_workstation",
    ROOT / "bootstrap_workstation.py",
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def valid_ed25519_key() -> str:
    algorithm = b"ssh-ed25519"
    public_bytes = bytes(range(32))
    blob = (
        struct.pack(">I", len(algorithm))
        + algorithm
        + struct.pack(">I", len(public_bytes))
        + public_bytes
    )
    encoded = base64.b64encode(blob).decode("ascii")
    return f"ssh-ed25519 {encoded} test-key"


class BootstrapValidationTests(unittest.TestCase):
    def test_validate_public_key_accepts_ed25519(self) -> None:
        key = valid_ed25519_key()

        self.assertEqual(MODULE.validate_public_key(key + "\n"), key)
        self.assertTrue(MODULE.public_key_fingerprint(key).startswith("SHA256:"))

    def test_validate_public_key_rejects_private_key(self) -> None:
        with self.assertRaises(ValueError):
            MODULE.validate_public_key(
                "-----BEGIN OPENSSH PRIVATE KEY-----\nsecret\n"
            )

    def test_validate_public_key_rejects_non_ed25519(self) -> None:
        with self.assertRaises(ValueError):
            MODULE.validate_public_key("ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQ test")

    def test_render_sshd_config_is_key_only_and_tailscale_only(self) -> None:
        rendered = MODULE.render_sshd_config("codex-admin")

        self.assertIn("PermitRootLogin no", rendered)
        self.assertIn("PasswordAuthentication no", rendered)
        self.assertIn("KbdInteractiveAuthentication no", rendered)
        self.assertIn("AuthenticationMethods publickey", rendered)
        self.assertIn("codex-admin@100.64.0.0/10", rendered)
        self.assertIn("codex-admin@fd7a:115c:a1e0::/48", rendered)
        self.assertNotIn("PasswordAuthentication yes", rendered)

    def test_supported_os_accepts_ubuntu_and_rejects_unrelated_distros(self) -> None:
        MODULE.require_supported_os({"ID": "ubuntu", "ID_LIKE": "debian"})

        with self.assertRaises(RuntimeError):
            MODULE.require_supported_os({"ID": "arch", "ID_LIKE": ""})

    def test_dry_run_never_invokes_executor(self) -> None:
        def forbidden_executor(*args: object, **kwargs: object) -> object:
            raise AssertionError("dry-run invoked the command executor")

        runner = MODULE.CommandRunner(apply=False, executor=forbidden_executor)
        result = runner.run(["apt-get", "install", "openssh-server"])

        self.assertIsInstance(result, subprocess.CompletedProcess)
        self.assertEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
