"""Docker commands for phase A and for cleanup (#2572, hardened by #2603).

Every scan object carries the ``fleet.security.scan=1`` label, so leftovers a
dead dashboard process left behind can be found and swept. Only argv lists and
output parsing live here; the executor runs them through its injected runner.
"""

from __future__ import annotations

import re
from pathlib import Path

from vuln_scan.operator_config import ScanHost
from vuln_scan.watch import ScanAborted

BUILDER_KEEP_STORAGE = "20gb"
SCAN_LABEL = "fleet.security.scan=1"
_DOCKER_ID = re.compile(r"^(?:sha256:)?[0-9a-f]{12,64}$")
_INSPECT_FORMAT = "{{.Id}} {{json .Config.Volumes}}"

# (list leftovers, remove them): containers with their volumes, then images.
SWEEPS = (
    (
        ["docker", "ps", "-aq", "--filter", f"label={SCAN_LABEL}"],
        ["docker", "rm", "-f", "-v"],
    ),
    (
        ["docker", "image", "ls", "-q", "--filter", f"label={SCAN_LABEL}"],
        ["docker", "rmi", "-f"],
    ),
)


def builder_name(build_network: str) -> str:
    """The ``docker-container`` buildx builder attached to the filtered network.

    BuildKit ignores ``docker build --network <custom>``, so phase A runs in a
    builder whose buildkitd container sits on the filtered network
    (``deploy/security-scan-host/setup-build-network.sh`` creates both).
    """
    return f"{build_network}-builder"


def build_argv(host: ScanHost, context: Path, dockerfile: str, image: str) -> list[str]:
    return [
        "docker",
        "buildx",
        "build",
        "--builder",
        builder_name(str(host.build_network)),
        "--load",
        "--pull",
        "--file",
        str(context / dockerfile),
        "--tag",
        image,
        "--label",
        SCAN_LABEL,
        str(context),
    ]


def inspect_argv(image: str) -> list[str]:
    return ["docker", "image", "inspect", "--format", _INSPECT_FORMAT, image]


def image_digest(inspect_out: str) -> str:
    """The digest from :func:`inspect_argv` output; refuse an image with a VOLUME.

    A volume would be writable host storage behind phase B's read-only root.
    """
    digest, _, volumes = inspect_out.partition(" ")
    if not (digest.startswith("sha256:") and len(digest) == 71):
        raise RuntimeError(f"image inspect returned no digest: {inspect_out[:200]!r}")
    if volumes.strip() not in ("null", "{}"):
        raise ScanAborted(
            "the scan image declares a VOLUME, which phase B refuses: "
            + volumes.strip()[:200]
        )
    return digest


def docker_ids(listing: str, limit: int = 100) -> list[str]:
    """Container or image ids from a ``-q`` listing; anything else is ignored."""
    return [i for i in listing.split() if _DOCKER_ID.match(i)][:limit]


def cleanup_steps(
    containers: tuple[str, ...], image: str, host: ScanHost
) -> list[list[str]]:
    """Containers (with volumes), the image, then the builder cache down to a cap."""
    steps = [["docker", "rm", "-f", "-v", c] for c in containers]
    steps.append(["docker", "rmi", "-f", image])
    if host.build_network:
        builder = builder_name(host.build_network)
        prune = ["docker", "buildx", "prune", "--builder", builder, "--force"]
        steps.append([*prune, "--keep-storage", BUILDER_KEEP_STORAGE])
    return steps
