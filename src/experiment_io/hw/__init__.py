"""Hardware abstraction layer for OperantKit.

The :mod:`experiment_io.hw` package defines the protocols and backends that
let the same Session/Runner code drive

* in-memory virtual subjects (test harness),
* serial-attached Arduino-style hardware, and
* a ``contingency-rs`` HIL bridge subprocess.

Imports here are intentionally light: pulling in
``experiment_io.hw.backends.serial`` does NOT require pyserial, and
``experiment_io.hw.backends.hil_bridge`` does NOT require the Rust binary
to exist.
"""

from .protocols import (
    Clock,
    HardwareClock,
    Operandum,
    Reinforcer,
    ResponseSample,
)

__all__ = [
    "Clock",
    "HardwareClock",
    "Operandum",
    "Reinforcer",
    "ResponseSample",
]
