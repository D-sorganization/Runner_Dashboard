"""Dynamic-loader environment for tests that launch a scrubbed Python subprocess.

On self-hosted runners the ``actions/setup-python`` toolcache interpreter (and
every venv symlinked to it) is a *shared* build: ``python3.11`` needs
``libpython3.11.so.1.0``, which the loader only finds through ``LD_LIBRARY_PATH``
pointing at the toolcache ``lib/``. A test that hands a subprocess a minimal,
hand-built environment drops that variable, and the child dies with
``error while loading shared libraries: libpython3.11.so.1.0`` before it runs a
single line of the code under test.

Tests that scrub the child environment on purpose (to prove behaviour under a
systemd-like or secret-free env) must still keep the loader variables, because
they describe where the interpreter lives and are not the state being scrubbed.
Use :func:`loader_env` for that instead of copying ``PATH`` alone.
"""

from __future__ import annotations

import os
import sysconfig
from collections.abc import Mapping

# Variables the dynamic loader consults to locate the interpreter's own shared
# libraries. ``LD_PRELOAD`` is deliberately absent: it injects code and is not
# needed to start the interpreter.
LOADER_VARS: tuple[str, ...] = (
    "LD_LIBRARY_PATH",
    "LIBPATH",
    "DYLD_LIBRARY_PATH",
    "DYLD_FALLBACK_LIBRARY_PATH",
)


def loader_env(environ: Mapping[str, str] | None = None) -> dict[str, str]:
    """Return the loader variables a child needs to start ``sys.executable``.

    Values come from ``environ`` (default ``os.environ``). When the running
    interpreter is a shared build and its ``LIBDIR`` is not already covered by
    ``LD_LIBRARY_PATH``, that directory is appended, so the child can start even
    if the parent was launched without the variable exported.
    """
    source = os.environ if environ is None else environ
    kept = {name: source[name] for name in LOADER_VARS if source.get(name)}
    if sysconfig.get_config_var("Py_ENABLE_SHARED"):
        libdir = sysconfig.get_config_var("LIBDIR")
        if libdir:
            current = [part for part in kept.get("LD_LIBRARY_PATH", "").split(os.pathsep) if part]
            if libdir not in current:
                kept["LD_LIBRARY_PATH"] = os.pathsep.join([*current, libdir])
    return kept


def with_loader_env(env: Mapping[str, str], environ: Mapping[str, str] | None = None) -> dict[str, str]:
    """Return a copy of ``env`` that also carries the dynamic-loader variables."""
    return {**env, **loader_env(environ)}
