from __future__ import annotations

import importlib.util
import pathlib
import sys
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "collect_linux_info",
    ROOT / "collect_linux_info.py",
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class CollectorParsingTests(unittest.TestCase):
    def test_parse_os_release_handles_quotes_and_comments(self) -> None:
        parsed = MODULE.parse_os_release(
            '# comment\nID=ubuntu\nPRETTY_NAME="Ubuntu 24.04.2 LTS"\nVERSION_ID="24.04"\n'
        )

        self.assertEqual(parsed["ID"], "ubuntu")
        self.assertEqual(parsed["PRETTY_NAME"], "Ubuntu 24.04.2 LTS")
        self.assertEqual(parsed["VERSION_ID"], "24.04")

    def test_parse_meminfo_converts_kib_to_bytes(self) -> None:
        parsed = MODULE.parse_meminfo("MemTotal:       16384 kB\nSwapTotal: 2048 kB\n")

        self.assertEqual(parsed["MemTotal"], 16384 * 1024)
        self.assertEqual(parsed["SwapTotal"], 2048 * 1024)

    def test_sanitize_filename_removes_path_characters(self) -> None:
        self.assertEqual(MODULE.sanitize_filename("lab node/01\\gpu"), "lab-node-01-gpu")
        self.assertEqual(MODULE.sanitize_filename("***"), "linux-host")

    def test_parse_listener_port_supports_ipv4_and_ipv6(self) -> None:
        self.assertEqual(MODULE.parse_listener_port("0.0.0.0:22"), 22)
        self.assertEqual(MODULE.parse_listener_port("[::]:2222"), 2222)
        self.assertIsNone(MODULE.parse_listener_port("invalid"))

    def test_build_warnings_flags_missing_remote_access(self) -> None:
        report = {
            "readiness": {
                "linux": True,
                "openssh_server_installed": False,
                "openssh_service_active": False,
                "tailscale_installed": False,
                "tailscale_online": False,
                "persistent_terminal_available": False,
                "root_free_bytes": 10 * MODULE.GIB,
            },
            "memory": {"swap_total_bytes": 0},
            "power": {"chassis": "server", "sleep_targets": {}},
            "ssh": {"effective_config": {}},
        }

        warnings = MODULE.build_warnings(report)

        self.assertTrue(any("OpenSSH" in warning for warning in warnings))
        self.assertTrue(any("Tailscale" in warning for warning in warnings))
        self.assertTrue(any("20 GiB" in warning for warning in warnings))

    def test_write_report_does_not_create_output_directories(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            missing_directory = pathlib.Path(temporary_directory) / "not-created"
            output_path = missing_directory / "report.json"

            with self.assertRaises(FileNotFoundError):
                MODULE.write_report({"schema_version": "test"}, output_path)

            self.assertFalse(missing_directory.exists())


if __name__ == "__main__":
    unittest.main()
