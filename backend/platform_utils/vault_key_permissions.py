"""Platform-aware vault master-key file permission checks."""

from __future__ import annotations

import stat
import sys
from pathlib import Path


def enforces_posix_mode_bits() -> bool:
    """True when Unix mode bits from ``stat`` reflect intended key confidentiality."""
    return sys.platform != "win32"


def assert_master_key_file_permissions(path: Path) -> None:
    """Reject lax POSIX mode bits; on Windows skip mode-bit checks (NTFS dev lanes).

    Precondition: ``path`` exists and points at the vault master-key file.
    Postcondition: raises ``PermissionError`` on POSIX when group/other bits are set.
    """
    if not path.is_file():
        raise FileNotFoundError(f"Vault master key file missing: {path}")
    if not enforces_posix_mode_bits():
        # NTFS ignores Unix mode bits from os.open(..., 0o600); production vault hosts
        # are POSIX. Windows dev lanes rely on default user-private ACLs under %USERPROFILE%.
        return
    st = path.stat()
    if st.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise PermissionError(
            f"Vault master key {path} has unsafe permissions {oct(st.st_mode & 0o777)}; expected 0o600"
        )
