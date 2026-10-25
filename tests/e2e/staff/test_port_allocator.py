"""Tests for collision-free ports used by the hermetic Staff E2E job."""

import socket
import tempfile
import unittest
from pathlib import Path

from port_allocator import publish_staff_e2e_ports


class StaffE2EPortAllocatorTests(unittest.TestCase):
    def test_publishes_distinct_ports_while_reserved_then_releases_them(self) -> None:
        with socket.socket() as occupied:
            occupied.bind(("127.0.0.1", 0))
            occupied.listen()
            occupied_port = occupied.getsockname()[1]

            with tempfile.TemporaryDirectory() as directory:
                github_env = Path(directory) / "github-env"
                ports = publish_staff_e2e_ports(github_env)

                self.assertEqual(set(ports), {"STAFF_E2E_BACKEND_PORT", "STAFF_E2E_UI_PORT"})
                backend_port = ports["STAFF_E2E_BACKEND_PORT"]
                ui_port = ports["STAFF_E2E_UI_PORT"]
                self.assertNotEqual(backend_port, ui_port)
                self.assertNotIn(occupied_port, ports.values())

                lines = github_env.read_text(encoding="utf-8").splitlines()
                self.assertEqual(
                    lines,
                    [
                        f"STAFF_E2E_BACKEND_PORT={backend_port}",
                        f"STAFF_E2E_UI_PORT={ui_port}",
                    ],
                )

        for port in ports.values():
            with socket.socket() as probe:
                probe.bind(("127.0.0.1", port))


if __name__ == "__main__":
    unittest.main()
