"""Virtual (in-memory) HAL backend.

Wraps a virtual subject (the Poisson ``VirtualSubject`` or the
reinforcement-sensitive ``ScallopSubject``) and a deterministic
``ManualClock`` so that the same hardware-driven runner code path can be
exercised with no real I/O. This is the canonical home for the
``ManualClock`` / ``VirtualSubject`` / ``ScallopSubject`` /
``MultiVirtualSubject`` primitives used by experiment harnesses.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Protocol

from ..protocols import ResponseSample

# ---------------------------------------------------------------------------
# Clock
# ---------------------------------------------------------------------------


@dataclass
class ManualClock:
    """A monotonic clock advanced explicitly by callers/tests.

    The current time is exposed as the ``now`` attribute. The clock is
    also callable (``clock()``) which is what runners expect from their
    ``clock`` injection point.
    """

    now: float = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> float:
        self.now += seconds
        return self.now


# ---------------------------------------------------------------------------
# Virtual subjects (Poisson responders)
# ---------------------------------------------------------------------------


@dataclass
class VirtualSubject:
    """Poisson responder.

    Emits inter-response times drawn from ``Exponential(rate)`` using a
    seeded RNG.
    """

    rate_hz: float = 2.0
    seed: int = 0
    _rng: random.Random = field(init=False)

    def __post_init__(self) -> None:
        self._rng = random.Random(self.seed)

    def next_irt(self) -> float:
        u = self._rng.random()
        return -math.log(1.0 - u) / self.rate_hz


@dataclass
class ScallopSubject:
    """Responder whose rate grows with time since the last reinforcer.

    The hazard of responding at time ``t`` since the last reinforcer is
    ``max_rate_hz * min(1, (t / ramp_s) ** 2)``: zero right after a
    reinforcer, rising quadratically, and constant at ``max_rate_hz``
    once ``t >= ramp_s``. Under a fixed-interval schedule this yields the
    post-reinforcement pause followed by accelerated responding (the FI
    scallop). IRTs are drawn by thinning a ``max_rate_hz`` Poisson
    process with a seeded RNG.

    ``freeze_for_reinforcer`` is the reinforcer notification: it resets
    the time since reinforcement to zero.
    """

    max_rate_hz: float = 2.0
    ramp_s: float = 60.0
    seed: int = 0
    _rng: random.Random = field(init=False)
    _elapsed: float = field(init=False, default=0.0)

    def __post_init__(self) -> None:
        if self.max_rate_hz <= 0:
            raise ValueError(f"max_rate_hz must be > 0, got {self.max_rate_hz}")
        if self.ramp_s <= 0:
            raise ValueError(f"ramp_s must be > 0, got {self.ramp_s}")
        self._rng = random.Random(self.seed)

    @property
    def elapsed(self) -> float:
        """Time since the last reinforcer (or since start)."""
        return self._elapsed

    def hazard(self, t: float) -> float:
        return self.max_rate_hz * min(1.0, (t / self.ramp_s) ** 2)

    def next_irt(self) -> float:
        start = self._elapsed
        t = start
        while True:
            t += -math.log(1.0 - self._rng.random()) / self.max_rate_hz
            if self._rng.random() * self.max_rate_hz < self.hazard(t):
                break
        self._elapsed = t
        return t - start

    def freeze_for_reinforcer(self, duration: float) -> None:
        self._elapsed = 0.0


class SingleSubject(Protocol):
    """A single-operandum responder that emits inter-response times."""

    def next_irt(self) -> float: ...


class MultiVirtualSubject:
    """N independent Poisson responders, one per operandum."""

    def __init__(self, rates_hz: list[float], seeds: list[int]) -> None:
        if len(rates_hz) != len(seeds):
            raise ValueError("rates_hz and seeds must be the same length")
        self.subjects: list[VirtualSubject] = [
            VirtualSubject(rate_hz=r, seed=s) for r, s in zip(rates_hz, seeds, strict=True)
        ]
        self._next_times: list[float] = [s.next_irt() for s in self.subjects]
        self._elapsed: float = 0.0

    @property
    def n(self) -> int:
        return len(self.subjects)

    def next_event(self) -> tuple[int, float]:
        idx = min(range(self.n), key=lambda k: self._next_times[k])
        next_abs = self._next_times[idx]
        delta = next_abs - self._elapsed
        if delta < 0:
            delta = 0.0
        self._elapsed = next_abs
        self._next_times[idx] = next_abs + self.subjects[idx].next_irt()
        return idx, delta

    def freeze_for_reinforcer(self, duration: float) -> None:
        self._elapsed += duration
        self._next_times = [t + duration for t in self._next_times]


# ---------------------------------------------------------------------------
# Virtual HAL devices
# ---------------------------------------------------------------------------


class VirtualOperandum:
    """Single-operandum virtual operandum.

    On each ``poll()`` call, advances the supplied ``ManualClock`` by
    one IRT drawn from the underlying subject and returns a single
    ``ResponseSample`` at the new clock time. Reinforcer deliveries are
    forwarded to subjects that define ``freeze_for_reinforcer``.
    """

    def __init__(
        self,
        operandum_index: int,
        subject: SingleSubject,
        clock: ManualClock,
    ) -> None:
        self.operandum_index = operandum_index
        self.subject = subject
        self.clock = clock

    def poll(self) -> list[ResponseSample]:
        irt = self.subject.next_irt()
        ts = self.clock.advance(irt)
        return [
            ResponseSample(operandum_index=self.operandum_index, timestamp=ts)
        ]

    def freeze_for_reinforcer(self, duration: float) -> None:
        freeze = getattr(self.subject, "freeze_for_reinforcer", None)
        if callable(freeze):
            freeze(duration)

    def close(self) -> None:  # pragma: no cover - nothing to release
        pass


class _MultiVirtualChild:
    """One child operandum sharing a MultiVirtualSubject across siblings."""

    def __init__(
        self,
        operandum_index: int,
        parent: MultiVirtualOperandum,
    ) -> None:
        self.operandum_index = operandum_index
        self._parent = parent

    def poll(self) -> list[ResponseSample]:
        return self._parent._poll_for(self.operandum_index)

    def close(self) -> None:  # pragma: no cover
        pass


class MultiVirtualOperandum:
    """Adapter that exposes a MultiVirtualSubject as N HAL Operanda.

    Because the underlying ``MultiVirtualSubject.next_event()`` advances
    a global virtual clock and *returns whichever operandum fires next*,
    we cannot answer ``poll()`` per-operandum independently. Instead,
    when ANY child is polled, the next event is drawn and queued; the
    matching child returns the sample, while non-matching children
    return ``[]``.
    """

    def __init__(
        self,
        subject: MultiVirtualSubject,
        clock: ManualClock,
    ) -> None:
        self.subject = subject
        self.clock = clock
        self._pending: list[ResponseSample] = []

    def split(self) -> list[_MultiVirtualChild]:
        return [_MultiVirtualChild(i, self) for i in range(self.subject.n)]

    def freeze_for_reinforcer(self, duration: float) -> None:
        self.subject.freeze_for_reinforcer(duration)

    def _poll_for(self, idx: int) -> list[ResponseSample]:
        if not self._pending:
            op, delta = self.subject.next_event()
            ts = self.clock.advance(delta)
            self._pending.append(
                ResponseSample(operandum_index=op, timestamp=ts)
            )
        if self._pending and self._pending[0].operandum_index == idx:
            return [self._pending.pop(0)]
        return []


# ---------------------------------------------------------------------------
# Recording reinforcer
# ---------------------------------------------------------------------------


class RecordingReinforcer:
    """In-memory reinforcer that records each ``deliver()`` call."""

    def __init__(self, operandum_index: int) -> None:
        self.operandum_index = operandum_index
        self.calls: list[tuple[int, float]] = []

    def deliver(self, duration: float) -> None:
        self.calls.append((self.operandum_index, duration))

    def close(self) -> None:  # pragma: no cover
        pass


__all__ = [
    "ManualClock",
    "VirtualSubject",
    "ScallopSubject",
    "SingleSubject",
    "MultiVirtualSubject",
    "VirtualOperandum",
    "MultiVirtualOperandum",
    "RecordingReinforcer",
]
