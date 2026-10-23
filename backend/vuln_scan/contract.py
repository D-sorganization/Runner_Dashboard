"""Per-repository security-scan contract: ``.oss-scanner/`` in the upstream format.

Source of truth: Repository_Management ``shared_scripts/oss_scanner_contract.py``.
Runner_Dashboard vendors this file byte-identically (``backend/vuln_scan/contract.py``).
Edit it in Repository_Management first, then re-vendor; never edit only one copy.

Every fleet repository that opts in to security scanning carries the same files
anthropics/oss-scanner reads, at the same paths, so it could enrol upstream unchanged:

``.oss-scanner/project.yaml``     required  repo, primary_contact, dockerfile, ...
``.oss-scanner/Dockerfile``       required  builds the project (network at build only)
``.oss-scanner/threat_model.md``  advised   inputs, what matters, severity, exclusions

The rules mirror upstream ``tools/validate.py``: field names, allowed keys,
operator-only keys, the 64 KiB file cap, and the path and URL shapes. One difference:
an in-repository contract has no "file next to project.yaml" option, so
``dockerfile`` names a path in the repository and defaults to
``.oss-scanner/Dockerfile``. Fleet rules on top: ``repo`` must be this repository's
own GitHub URL, and operator settings (cadence, budget, priority) are rejected here
because they belong to the Runner Dashboard's ``config/security_scan.json``.

Contract: :func:`check_contract` is pure and never raises for bad input. Every
problem comes back as one human-readable line; no problems means the contract is
valid. Requires PyYAML.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

import yaml

CONTRACT_DIR = ".oss-scanner"
PROJECT_PATH = f"{CONTRACT_DIR}/project.yaml"
DEFAULT_DOCKERFILE = f"{CONTRACT_DIR}/Dockerfile"
DEFAULT_THREAT_MODEL = f"{CONTRACT_DIR}/threat_model.md"

REQUIRED_KEYS = ("repo", "primary_contact")
OPTIONAL_KEYS = (
    "auto_ccs",
    "homepage",
    "pgp",
    "disabled",
    "dockerfile",
    "threat_model",
)
ALLOWED_KEYS = REQUIRED_KEYS + OPTIONAL_KEYS
# Upstream rejects these in project.yaml; the fleet keeps them in operator config.
OPERATOR_KEYS = (
    "workflow",
    "workflows",
    "budget",
    "budget_usd",
    "cadence",
    "cadence_hours",
    "schedule",
    "priority",
)
MAX_FILE_BYTES = 64 * 1024
_KIB = MAX_FILE_BYTES // 1024

EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
URL = re.compile(r"https://[^\s#]+(#[A-Za-z0-9._/-]{1,100})?")
REPO_PATH = re.compile(r"(?!/)(?!.*\.\.)[A-Za-z0-9._/-]+")
PGP_BEGIN = "-----BEGIN PGP PUBLIC KEY BLOCK-----"
PGP_END = "-----END PGP PUBLIC KEY BLOCK-----"


@dataclass(frozen=True)
class ContractReport:
    """The outcome of checking one repository's contract.

    ``present`` is False when ``project.yaml`` is absent (the repository has not
    opted in). ``problems`` is empty exactly when a present contract is valid.
    """

    repo: str
    present: bool
    problems: tuple[str, ...] = ()
    dockerfile: str | None = None
    threat_model: str | None = None
    threat_model_present: bool = False
    disabled: bool = False
    config: Mapping[str, Any] = field(default_factory=dict)

    @property
    def valid(self) -> bool:
        return self.present and not self.problems

    def to_dict(self) -> dict[str, Any]:
        return {
            "repo": self.repo,
            "present": self.present,
            "valid": self.valid,
            "problems": list(self.problems),
            "dockerfile": self.dockerfile,
            "threat_model": self.threat_model,
            "threat_model_present": self.threat_model_present,
            "disabled": self.disabled,
        }


def is_https_url(value: object) -> bool:
    """True if ``value`` is an https:// URL with a host and at most one #fragment."""
    if not (isinstance(value, str) and URL.fullmatch(value)):
        return False
    try:
        return bool(urlparse(value.split("#", 1)[0]).hostname)
    except ValueError:
        return False


def is_public_key(text: str) -> bool:
    """True if ``text`` is exactly one armored PGP public key block (format only)."""
    text = text.strip()
    return (
        text.startswith(PGP_BEGIN)
        and text.endswith(PGP_END)
        and text.count(PGP_BEGIN) == 1
        and text.count(PGP_END) == 1
        and "PRIVATE KEY" not in text
        and len(text) <= MAX_FILE_BYTES
    )


def expected_repo_url(org: str, repo: str) -> str:
    return f"https://github.com/{org}/{repo}"


def _check_keys(config: Mapping[str, Any]) -> Iterator[str]:
    for key in config:
        if key in OPERATOR_KEYS:
            yield (
                f"{key!r} is an operator setting: set it in the Runner Dashboard's "
                "config/security_scan.json, not in project.yaml"
            )
        elif key not in ALLOWED_KEYS:
            yield f"unknown field {key!r} (allowed: {', '.join(ALLOWED_KEYS)})"
    for key in REQUIRED_KEYS:
        if not config.get(key):
            yield f"missing {key}"


def _check_types(config: Mapping[str, Any]) -> Iterator[str]:
    for key in ("primary_contact", "homepage", "pgp"):
        if config.get(key) is not None and not isinstance(config[key], str):
            yield f"{key} must be a string"
    disabled = config.get("disabled")
    if disabled is not None and not isinstance(disabled, bool):
        yield "disabled must be true or false"


def _check_urls(config: Mapping[str, Any], org: str, repo: str) -> Iterator[str]:
    value = config.get("repo")
    expected = expected_repo_url(org, repo)
    if value is not None and not is_https_url(value):
        yield (
            "repo must be ONE https:// URL of a git repository "
            "(a string, not a list; add #branch-or-tag to pin one)"
        )
    elif isinstance(value, str):
        base = value.split("#", 1)[0].removesuffix(".git").rstrip("/")
        if base.lower() != expected.lower():
            yield f"repo must be this repository ({expected}), not {base}"
    homepage = config.get("homepage")
    if isinstance(homepage, str) and not is_https_url(homepage.split("#", 1)[0]):
        yield "homepage must be an https:// URL"


def _check_addresses(config: Mapping[str, Any]) -> Iterator[str]:
    addresses: list[str] = []
    if isinstance(config.get("primary_contact"), str):
        addresses.append(config["primary_contact"])
    ccs = config.get("auto_ccs")
    if isinstance(ccs, list) and all(isinstance(a, str) for a in ccs):
        addresses += ccs
    elif ccs is not None:
        yield "auto_ccs must be a list of addresses"
    for address in addresses:
        if not EMAIL.fullmatch(address):
            yield f"bad email {address!r}"


def _check_pgp(config: Mapping[str, Any]) -> Iterator[str]:
    key = config.get("pgp")
    if key and config.get("auto_ccs"):
        yield (
            "pgp and auto_ccs together are not supported: "
            "an encrypted report goes to primary_contact only"
        )
    if isinstance(key, str) and not is_public_key(key):
        yield f"pgp must be one armored public key: from {PGP_BEGIN} to {PGP_END}"


def _repo_path(
    config: Mapping[str, Any], key: str, default: str
) -> tuple[str | None, str | None]:
    """Return (path, problem) for the ``dockerfile`` or ``threat_model`` key."""
    value = config.get(key)
    if value is None:
        return default, None
    if isinstance(value, str) and REPO_PATH.fullmatch(value):
        return value, None
    return None, f"{key} must be a relative path inside your repository"


def _check_files(
    files: Mapping[str, int | None], dockerfile: str | None, threat_model: str | None
) -> Iterator[str]:
    if dockerfile is not None:
        size = files.get(dockerfile)
        if size is None:
            yield f"Dockerfile not found at {dockerfile}: it is the whole build"
        elif size == 0:
            yield f"{dockerfile} is empty: it is the whole build"
        elif size > MAX_FILE_BYTES:
            yield f"{dockerfile} is larger than {_KIB} KiB"
    tm_size = files.get(threat_model) if threat_model else None
    if tm_size is not None and tm_size > MAX_FILE_BYTES:
        yield f"{threat_model} is larger than {_KIB} KiB"


def _invalid(repo: str, problem: str) -> ContractReport:
    return ContractReport(repo=repo, present=True, problems=(problem,))


def check_contract(
    *,
    org: str,
    repo: str,
    project_yaml: str | None,
    files: Mapping[str, int | None],
) -> ContractReport:
    """Check one repository's ``.oss-scanner/`` contract.

    ``project_yaml`` is the text of ``.oss-scanner/project.yaml``, or None when the
    file is absent. ``files`` maps each repository path the caller looked up to its
    size in bytes, or to None when it does not exist. The paths that matter are the
    configured Dockerfile and threat model.
    """
    if project_yaml is None:
        return ContractReport(repo=repo, present=False)
    if len(project_yaml.encode("utf-8")) > MAX_FILE_BYTES:
        return _invalid(repo, f"project.yaml is larger than {_KIB} KiB")
    try:
        config = yaml.safe_load(project_yaml)
    except yaml.YAMLError as exc:
        return _invalid(repo, f"project.yaml is not valid YAML: {exc}")
    if not isinstance(config, dict):
        kind = type(config).__name__
        return _invalid(repo, f"not a mapping (top level is a {kind})")

    problems = [
        *_check_keys(config),
        *_check_types(config),
        *_check_urls(config, org, repo),
        *_check_addresses(config),
        *_check_pgp(config),
    ]
    dockerfile, problem = _repo_path(config, "dockerfile", DEFAULT_DOCKERFILE)
    if problem:
        problems.append(problem)
    threat_model, problem = _repo_path(config, "threat_model", DEFAULT_THREAT_MODEL)
    if problem:
        problems.append(problem)
    problems.extend(_check_files(files, dockerfile, threat_model))

    return ContractReport(
        repo=repo,
        present=True,
        problems=tuple(problems),
        dockerfile=dockerfile,
        threat_model=threat_model,
        threat_model_present=bool(threat_model and files.get(threat_model)),
        disabled=config.get("disabled") is True,
        config=config,
    )
