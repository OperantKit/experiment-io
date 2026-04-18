"""Hardware abstraction layer for the OperantKit experiment runtime.

This package defines the protocol contracts (``Clock``, ``Operandum``,
``Reinforcer``, ``ResponseSample``) and concrete backends that let the same
Session/Runner code drive in-memory virtual subjects, serial-attached
hardware, and a ``contingency-rs`` HIL bridge subprocess.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("experiment-io")
except PackageNotFoundError:  # pragma: no cover - fallback when not installed
    __version__ = "0.0.0+unknown"

__all__ = ["__version__"]
