"""Choose two loopback ports for the fake Staff Console E2E job."""

import os
import socket
from contextlib import ExitStack
from pathlib import Path


def publish_staff_e2e_ports(github_env: Path) -> dict[str, int]:
    """Publish distinct ephemeral ports while both reservations are held."""
    names = ("STAFF_E2E_BACKEND_PORT", "STAFF_E2E_UI_PORT")
    with ExitStack() as reservations:
        ports: dict[str, int] = {}
        for name in names:
            reservation = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            reservations.enter_context(reservation)
            reservation.bind(("127.0.0.1", 0))
            reservation.listen()
            ports[name] = reservation.getsockname()[1]

        if len(set(ports.values())) != len(names):
            raise RuntimeError("OS returned duplicate Staff E2E ports")

        with github_env.open("a", encoding="utf-8") as env_file:
            for name in names:
                env_file.write(f"{name}={ports[name]}\n")
    return ports


if __name__ == "__main__":
    env_path = os.environ.get("GITHUB_ENV")
    if not env_path:
        raise SystemExit("GITHUB_ENV must point to the workflow environment file")
    publish_staff_e2e_ports(Path(env_path))
