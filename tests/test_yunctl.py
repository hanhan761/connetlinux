from __future__ import annotations

import argparse
import base64
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "yunctl.py"
SPEC = importlib.util.spec_from_file_location("yunctl_under_test", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


FINGERPRINT = "SHA256:" + "A" * 43
IDENTITY_FILE = str((Path(tempfile.gettempdir()) / "yun_worker-one.pem").resolve())
KNOWN_HOSTS_FILE = str((Path(tempfile.gettempdir()) / "yun_worker-one.known_hosts").resolve())


def target(*, protected: bool = False, roles: list[str] | None = None) -> dict:
    selected_roles = roles or ["server"]
    return {
        "description": "test target",
        "hostname": "host.example.invalid",
        "port": 22,
        "user": "yun-admin",
        "identity_file": IDENTITY_FILE,
        "known_hosts_file": KNOWN_HOSTS_FILE,
        "expected_host_key_sha256": FINGERPRINT,
        "roles": selected_roles,
        "protected": protected,
        "compute_backend": "tmux" if "compute" in selected_roles else None,
        "job_root": MODULE.JOB_ROOT if "compute" in selected_roles else None,
    }


class RegistryTests(unittest.TestCase):
    def test_registry_refuses_skill_internal_runtime_state(self) -> None:
        internal = MODULE.SKILL_ROOT / "targets.json"
        with mock.patch.dict(os.environ, {MODULE.REGISTRY_ENV: str(internal)}):
            with self.assertRaisesRegex(MODULE.YunError, "outside the installed skill"):
                MODULE.registry_path()

    def test_init_is_idempotent_and_registry_is_external(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "config" / "targets.json"
            with mock.patch.dict(os.environ, {MODULE.REGISTRY_ENV: str(path)}), mock.patch.object(
                MODULE, "restrict_local_file"
            ):
                self.assertEqual(MODULE.main(["init"]), 0)
                self.assertEqual(MODULE.main(["init"]), 0)
                self.assertEqual(MODULE.load_registry(), {})
            self.assertEqual(
                json.loads(path.read_text(encoding="utf-8")),
                {"schema_version": 1, "targets": {}},
            )

    def test_register_validates_then_writes_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "targets.json"
            path.write_text(
                json.dumps({"schema_version": 1, "targets": {}}), encoding="utf-8"
            )
            args = [
                "register",
                "worker-one",
                "--host",
                "host.example.invalid",
                "--user",
                "yun-admin",
                "--pem",
                IDENTITY_FILE,
                "--known-hosts",
                KNOWN_HOSTS_FILE,
                "--host-fingerprint",
                FINGERPRINT,
                "--role",
                "server",
                "--role",
                "compute",
            ]
            with mock.patch.dict(os.environ, {MODULE.REGISTRY_ENV: str(path)}), mock.patch.object(
                MODULE, "verify_target_payload", side_effect=lambda name, value: value
            ), mock.patch.object(MODULE, "restrict_local_file"):
                self.assertEqual(MODULE.main(args), 0)
                saved = MODULE.load_registry()["worker-one"]
            self.assertEqual(saved["roles"], ["server", "compute"])
            self.assertEqual(saved["compute_backend"], "tmux")
            self.assertEqual(saved["job_root"], ".yun/jobs")
            self.assertEqual(saved["identity_file"], IDENTITY_FILE)
            self.assertNotIn("ssh_alias", saved)

    def test_register_refuses_unconfirmed_replacement(self) -> None:
        payload = {"schema_version": 1, "targets": {"worker-one": target()}}
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "targets.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            args = argparse.Namespace(
                name="worker-one",
                host="host.example.invalid",
                port=22,
                user="yun-admin",
                pem=IDENTITY_FILE,
                known_hosts=KNOWN_HOSTS_FILE,
                host_fingerprint=FINGERPRINT,
                role=["server"],
                description=None,
                protected=False,
                confirm_replace=None,
                dry_run=False,
            )
            with mock.patch.dict(os.environ, {MODULE.REGISTRY_ENV: str(path)}):
                with self.assertRaisesRegex(MODULE.YunError, "--confirm-replace worker-one"):
                    MODULE.cmd_register(args)

    def test_registry_rejects_unknown_target_fields(self) -> None:
        malformed = target()
        malformed["private_key"] = "must-not-be-stored"
        with self.assertRaisesRegex(MODULE.YunError, "unknown fields"):
            MODULE.validate_registry(
                {"schema_version": 1, "targets": {"worker-one": malformed}}
            )

    def test_registry_refuses_one_pem_shared_by_two_targets(self) -> None:
        with self.assertRaisesRegex(MODULE.YunError, "cannot share one PEM"):
            MODULE.validate_registry(
                {
                    "schema_version": 1,
                    "targets": {
                        "worker-one": target(),
                        "worker-two": target(),
                    },
                }
            )

    def test_registry_rejects_connection_and_description_injection(self) -> None:
        malformed_hosts = []
        for value in ("-oProxyCommand=bad", "admin@host.example", "[::1]", "bad host"):
            malformed = target()
            malformed["hostname"] = value
            malformed_hosts.append(malformed)
        malformed_user = target()
        malformed_user["user"] = "admin@host"
        malformed_identity = target()
        malformed_identity["identity_file"] = str(Path(tempfile.gettempdir()) / "not-pem.key")
        malformed_description = target()
        malformed_description["description"] = "line one\nline two"
        for malformed in (
            *malformed_hosts,
            malformed_user,
            malformed_identity,
            malformed_description,
        ):
            with self.subTest(malformed=malformed), self.assertRaises(MODULE.YunError):
                MODULE.validate_target("worker-one", malformed)


class IdentityTests(unittest.TestCase):
    def test_known_hosts_line_is_fingerprinted_without_emitting_key_data(self) -> None:
        key_bytes = b"synthetic-public-host-key"
        encoded = base64.b64encode(key_bytes).decode("ascii")
        expected = "SHA256:" + base64.b64encode(
            hashlib.sha256(key_bytes).digest()
        ).decode("ascii").rstrip("=")
        output = f"# Host found\nexample.invalid ssh-ed25519 {encoded}\n"
        self.assertEqual(MODULE.fingerprints_from_known_hosts(output), {expected})

    def test_verify_target_requires_exact_registered_identity_and_fingerprint(self) -> None:
        files = (Path(IDENTITY_FILE), Path(KNOWN_HOSTS_FILE))
        with mock.patch.object(MODULE, "resolved_connection_files", return_value=files), mock.patch.object(
            MODULE, "pinned_host_fingerprints", return_value={FINGERPRINT}
        ):
            self.assertEqual(MODULE.verify_target_payload("worker-one", target()), target())

        with mock.patch.object(MODULE, "resolved_connection_files", return_value=files), mock.patch.object(
            MODULE, "pinned_host_fingerprints", return_value=set()
        ):
            with self.assertRaisesRegex(MODULE.YunError, "fingerprint mismatch"):
                MODULE.verify_target_payload("worker-one", target())

    def test_connection_arguments_disable_ssh_config_and_pin_one_pem(self) -> None:
        files = (Path(IDENTITY_FILE), Path(KNOWN_HOSTS_FILE))
        with mock.patch.object(MODULE, "resolved_connection_files", return_value=files):
            options = MODULE.connection_options(target())
            ssh_target = MODULE.destination(target())
        self.assertEqual(options[:2], ["-F", "none"])
        self.assertIn("IdentitiesOnly=yes", options)
        self.assertIn("IdentityAgent=none", options)
        self.assertIn("StrictHostKeyChecking=yes", options)
        self.assertIn(f"UserKnownHostsFile={KNOWN_HOSTS_FILE}", options)
        self.assertEqual(options[options.index("-i") + 1], IDENTITY_FILE)
        self.assertEqual(ssh_target, "yun-admin@host.example.invalid")


class SafetyTests(unittest.TestCase):
    def test_protected_target_requires_exact_confirmation(self) -> None:
        protected = target(protected=True)
        with self.assertRaisesRegex(MODULE.YunError, "--confirm-target worker-one"):
            MODULE.require_protected_confirmation("worker-one", protected, None)
        MODULE.require_protected_confirmation("worker-one", protected, "worker-one")

    def test_job_ids_and_paths_cannot_escape_root(self) -> None:
        compute = target(roles=["compute"])
        self.assertEqual(
            MODULE.job_paths(compute, "job-123"),
            ("$HOME/.yun/jobs/job-123", "~/.yun/jobs/job-123"),
        )
        for invalid in ("../escape", "/absolute", "job name", ""):
            with self.subTest(invalid=invalid), self.assertRaises(MODULE.YunError):
                MODULE.validate_job_id(invalid)

    def test_redaction_covers_common_secret_shapes(self) -> None:
        text = (
            "Authorization: Bearer synthetic-bearer-value\n"
            "token=synthetic-token-value\n"
            "password: synthetic-password-value\n"
        )
        redacted = MODULE.redact(text)
        self.assertNotIn("synthetic-bearer-value", redacted)
        self.assertNotIn("synthetic-token-value", redacted)
        self.assertNotIn("synthetic-password-value", redacted)
        self.assertGreaterEqual(redacted.count("[REDACTED]"), 3)

    def test_keygen_dry_run_produces_one_rsa_pem_identity(self) -> None:
        args = argparse.Namespace(
            directory=tempfile.gettempdir(),
            name="worker-one",
            dry_run=True,
        )
        with mock.patch.object(MODULE, "run_external") as run:
            run.return_value.returncode = 0
            self.assertEqual(MODULE.cmd_keygen(args), 0)
        command = run.call_args.args[0]
        self.assertIn("rsa", command)
        self.assertIn("4096", command)
        self.assertIn("PEM", command)
        self.assertEqual(command[command.index("-N") + 1], "")
        self.assertIn(str(Path(tempfile.gettempdir()).resolve() / "yun_worker-one.pem"), command)

    def test_remote_paths_reject_option_and_control_injection(self) -> None:
        self.assertEqual(MODULE.valid_remote_path("/tmp/result.json"), "/tmp/result.json")
        for invalid in ("-rf", "line\nbreak", "nul\x00byte", ""):
            with self.subTest(invalid=invalid), self.assertRaises(MODULE.YunError):
                MODULE.valid_remote_path(invalid)


class PackageTests(unittest.TestCase):
    def test_skill_frontmatter_and_ui_metadata_match_yun(self) -> None:
        root = MODULE_PATH.parents[1]
        skill = (root / "SKILL.md").read_text(encoding="utf-8")
        metadata = (root / "agents" / "openai.yaml").read_text(encoding="utf-8")
        self.assertTrue(skill.startswith("---\nname: yun\n"))
        self.assertIn("/yun", skill)
        self.assertIn("$yun", skill)
        self.assertIn('display_name: "云"', metadata)
        self.assertIn("allow_implicit_invocation: true", metadata)


if __name__ == "__main__":
    unittest.main()
