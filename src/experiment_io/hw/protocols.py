"""Protocol definitions for the OperantKit hardware abstraction layer."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class ResponseSample:
    """A single response event sampled from an operandum.

    Attributes
    ----------
    operandum_index:
        Zero-based index of the operandum the response was made on.
    timestamp:
        Monotonic seconds reported by the device's hardware (or
        ManualClock under test). A record of when the device saw the
        response: :func:`experiment_io.drive` gives the session the
        host's ``clock()`` at hand-over instead, because the device's
        clock and the host's are different clocks.
    metadata:
        Optional dict of arbitrary backend-specific extras (e.g.
        Arduino pin number, raw bytes, etc.).
    """

    operandum_index: int
    timestamp: float
    metadata: dict[str, Any] | None = None


@runtime_checkable
class Operandum(Protocol):
    """Input device producing response events for one operandum index."""

    operandum_index: int

    def poll(self) -> list[ResponseSample]:
        """Return any response samples available since the previous poll."""
        ...

    def close(self) -> None:
        """Release any underlying resources."""
        ...


@runtime_checkable
class Reinforcer(Protocol):
    """Output device that delivers a reinforcer for one operandum index."""

    operandum_index: int

    def deliver(self, duration: float) -> None:
        """Deliver a reinforcer for ``duration`` seconds."""
        ...

    def close(self) -> None:
        """Release any underlying resources."""
        ...


@runtime_checkable
class Clock(Protocol):
    """Monotonic clock for behavioral experiments.

    A Clock is any callable returning the current time in monotonic
    seconds.  This is the single timing interface across the OperantKit
    ecosystem — simulations, hardware sessions, and tests all use the
    same protocol.

    Real clocks: ``time.monotonic``, ``time.perf_counter``, or hardware
    device clocks satisfy this by being callable.

    Virtual clocks: additionally expose ``advance(seconds) -> float``
    for deterministic time stepping.  Runners detect this via
    ``getattr(clock, "advance", None)`` — it is intentionally NOT part
    of the protocol so that real clocks satisfy ``Clock`` trivially.
    """

    def __call__(self) -> float:
        ...


# Backward-compatible alias (deprecated — prefer Clock).
HardwareClock = Clock
