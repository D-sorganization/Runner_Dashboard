"""Built-in host aliases for lane checkout (#4607).

This is the single sanctioned place where a live fleet hostname is named in
tracked code. It is allowlisted in ``tests/test_no_private_topology_in_repo.py``
so lane checkout recognises the CT host without a local ``host_aliases.json``.
Operators may extend or override these via ``host_aliases.json`` or
``RUNNER_DASHBOARD_HOST_ALIASES``; user config always wins over these defaults.
"""

from __future__ import annotations

from types import MappingProxyType

DEFAULT_HOST_ALIASES = MappingProxyType({"controltower": "ct"})
