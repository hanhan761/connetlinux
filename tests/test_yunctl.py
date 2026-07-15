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


def target(*, protected: bool = False, roles: list[str] | None = None) -> dict:
    selected_roles = roles or ["server"]
    return {
        "description": "test target",
        "ssh_alias": "test-alias",
        "expected_hostname": "host.example.invalid",
        "expected_user": "yun-admin",
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
                "--ssh-alias",
                "test-alias",
                "--hostname",
                "host.example.invalid",
                "--user",
                "yun-admin",
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

    def test_register_refuses_unconfirmed_replacement(self) -> None:
        payload = {"schema_version": 1, "targets": {"worker-one": target()}}
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "targets.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            args = argparse.Namespace(
                name="worker-one",
                ssh_alias="test-alias",
                hostname="host.example.invalid",
                user="yun-admin",
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

    def test_registry_rejects_ssh_alias_and_description_injection(self) -> None:
        malformed_alias = target()
        malformed_alias["ssh_alias"] = "user@host"
        malformed_description = target()
        malformed_description["description"] = "line one\nline two"
        for malformed in (malformed_alias, malformed_description):
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

    def test_verify_target_requires_exact_effective_identity_and_fingerprint(self) -> None:
        config = {
            "hostname": "host.example.invalid",
            "user": "yun-admin",
            "stricthostkeychecking": "true",
            "identitiesonly": "true",
        }
        with mock.patch.object(MODULE, "effective_ssh_config", return_value=config), mock.patch.object(
            MODULE, "pinned_host_fingerprints", return_value={FINGERPRINT}
        ):
            self.assertEqual(MODULE.verify_target_payload("worker-one", target()), target())

        with mock.patch.object(MODULE, "effective_ssh_config", return_value=config), mock.patch.object(
            MODULE, "pinned_host_fingerprints", return_value=set()
        ):
            with self.assertRaisesRegex(MODULE.YunError, "fingerprint mismatch"):
                MODULE.verify_target_payload("worker-one", target())

    def test_verify_target_rejects_non_strict_or_ambient_identity(self) -> None:
        for field in ("stricthostkeychecking", "identitiesonly"):
            config = {
                "hostname": "host.example.invalid",
                "user": "yun-admin",
                "stricthostkeychecking": "true",
                "identitiesonly": "true",
            }
            config[field] = "false"
            with self.subTest(field=field), mock.patch.object(
                MODULE, "effective_ssh_config", return_value=config
            ):
                with self.assertRaises(MODULE.YunError):
                    MODULE.verify_target_payload("worker-one", target())


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

    def test_automation_key_requires_explicit_unencrypted_confirmation(self) -> None:
        args = argparse.Namespace(
            directory=tempfile.gettempdir(),
            name="worker-one",
            format="ed25519",
            automation_key=True,
            confirm_unencrypted=False,
            dry_run=True,
        )
        with self.assertRaisesRegex(MODULE.YunError, "--confirm-unencrypted"):
            MODULE.cmd_keygen(args)

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
