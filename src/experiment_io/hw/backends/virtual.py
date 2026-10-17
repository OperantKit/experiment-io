"""Virtual (in-memory) HAL backend.

Wraps a virtual subject (the Poisson ``VirtualSubject`` or the
reinforcement-sensitive ``ScallopSubject``, ``SpacedRespondingSubject``
and ``MatchingSubject``) and a
deterministic ``ManualClock`` so that the same hardware-driven runner
code path can be exercised with no real I/O. This is the canonical home
for the ``ManualClock`` / ``VirtualSubject`` / ``ScallopSubject`` /
``SpacedRespondingSubject`` / ``IrtCopyistSubject`` / ``MatchingSubject`` /
``MultiVirtualSubject`` primitives used by
experiment harnesses.
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


class SpacedRespondingSubject:
    """Responder whose inter-response times are shaped by which IRTs pay off.

    The subject holds a mean IRT ``mu`` (``initial_mean_s`` at the start,
    1 s by default, i.e. roughly a 1 Hz responder). Each IRT is drawn from
    a normal distribution with mean ``mu`` and coefficient of variation
    ``cv``, redrawn until positive. When a response is not followed by a
    reinforcer, ``mu`` moves toward ``overshoot`` times that response's
    IRT: ``mu += learning_rate * (overshoot * irt - mu)``. When it is
    reinforced, ``mu`` shrinks: ``mu *= 1 - shrink``.

    The subject knows nothing about the schedule. Under differential
    reinforcement of low rate (DRL), short IRTs go unreinforced and push
    ``mu`` up, reinforced long IRTs pull it back down, and the IRT
    distribution settles around the DRL interval. Because only IRTs
    shorter than the interval go unreinforced, ``mu`` levels off near the
    point where ``overshoot`` times the mean unreinforced IRT equals
    ``mu``. With ``overshoot = 1.2`` that point lies below a 10-s interval
    and fewer than a third of the IRTs reach it, so the default is 2.0.

    ``freeze_for_reinforcer`` is the reinforcer notification: it marks the
    last response as reinforced.
    """

    def __init__(
        self,
        *,
        initial_mean_s: float = 1.0,
        cv: float = 0.3,
        overshoot: float = 2.0,
        learning_rate: float = 0.1,
        shrink: float = 0.02,
        seed: int = 0,
    ) -> None:
        if initial_mean_s <= 0:
            raise ValueError(f"initial_mean_s must be > 0, got {initial_mean_s}")
        if cv <= 0:
            raise ValueError(f"cv must be > 0, got {cv}")
        if overshoot <= 0:
            raise ValueError(f"overshoot must be > 0, got {overshoot}")
        if not 0 < learning_rate <= 1:
            raise ValueError(f"learning_rate must be in (0, 1], got {learning_rate}")
        if not 0 <= shrink < 1:
            raise ValueError(f"shrink must be in [0, 1), got {shrink}")
        self.cv = cv
        self.overshoot = overshoot
        self.learning_rate = learning_rate
        self.shrink = shrink
        self._mean = initial_mean_s
        self._rng = random.Random(seed)
        self._last_irt: float | None = None

    @property
    def mean_irt(self) -> float:
        """Current mean of the IRT distribution, in seconds."""
        return self._mean

    def next_irt(self) -> float:
        if self._last_irt is not None:
            self._mean += self.learning_rate * (self.overshoot * self._last_irt - self._mean)
        while True:
            irt = self._rng.gauss(self._mean, self.cv * self._mean)
            if irt > 0:
                break
        self._last_irt = irt
        return irt

    def freeze_for_reinforcer(self, duration: float) -> None:
        if self._last_irt is not None:
            self._mean *= 1.0 - self.shrink
        self._last_irt = None


class IrtCopyistSubject:
    """Responder that copies the inter-response times that were reinforced.

    The subject holds a pool of ``pool_size`` IRTs. Each IRT is a pool
    member picked at random, multiplied by log-normal noise with mean 1
    and coefficient of variation ``cv``. When a response is reinforced,
    its IRT joins the end of the pool and the oldest member leaves. The
    initial pool is ``initial_irts`` if given, otherwise drawn from an
    exponential distribution at ``initial_rate_hz``. The model follows the
    copyist account of response emission (Tanno & Silberberg, 2012).

    The subject knows nothing about the schedule. A ratio schedule
    reinforces a response regardless of the IRT before it, so the pool
    keeps the IRTs the subject already emits; an interval schedule sets
    up a reinforcer with the passage of time, so the response that
    collects it tends to end a long IRT and the pool drifts toward longer
    IRTs. Any rate difference between the two is left to the contingency.

    ``initial_irts`` lets two subjects start from the same pool while
    ``seed`` keeps their response streams independent (as in a yoked
    pair). ``freeze_for_reinforcer`` is the reinforcer notification: it
    marks the last response as reinforced.

    Tanno, T., & Silberberg, A. (2012). The copyist model of response
    emission. *Psychonomic Bulletin & Review*, *19*(5), 759–778.
    https://doi.org/10.3758/s13423-012-0267-1
    """

    def __init__(
        self,
        *,
        pool_size: int = 10,
        cv: float = 0.3,
        initial_rate_hz: float = 1.0,
        initial_irts: tuple[float, ...] | None = None,
        seed: int = 0,
    ) -> None:
        if pool_size < 1:
            raise ValueError(f"pool_size must be >= 1, got {pool_size}")
        if cv < 0:
            raise ValueError(f"cv must be >= 0, got {cv}")
        if initial_rate_hz <= 0:
            raise ValueError(f"initial_rate_hz must be > 0, got {initial_rate_hz}")
        self._rng = random.Random(seed)
        if initial_irts is None:
            pool = [
                -math.log(1.0 - self._rng.random()) / initial_rate_hz
                for _ in range(pool_size)
            ]
        else:
            if len(initial_irts) != pool_size:
                raise ValueError(
                    f"initial_irts must have {pool_size} values, got {len(initial_irts)}"
                )
            if any(irt <= 0 for irt in initial_irts):
                raise ValueError(f"initial_irts must be > 0, got {initial_irts}")
            pool = [float(irt) for irt in initial_irts]
        self.cv = cv
        self._sigma = math.sqrt(math.log(1.0 + cv * cv))
        self._pool = pool
        self._last_irt: float | None = None

    @property
    def pool(self) -> tuple[float, ...]:
        """The IRTs the next one is copied from, oldest first."""
        return tuple(self._pool)

    def next_irt(self) -> float:
        base = self._pool[self._rng.randrange(len(self._pool))]
        noise = math.exp(self._rng.gauss(-self._sigma * self._sigma / 2.0, self._sigma))
        irt = base * noise
        self._last_irt = irt
        return irt

    def freeze_for_reinforcer(self, duration: float) -> None:
        if self._last_irt is not None:
            self._pool.pop(0)
            self._pool.append(self._last_irt)
        self._last_irt = None


class SingleSubject(Protocol):
    """A single-operandum responder that emits inter-response times."""

    def next_irt(self) -> float: ...


class MultiSubject(Protocol):
    """A responder spread over ``n`` operanda.

    ``next_event`` returns the operandum of the next response and the
    time since the previous one; ``freeze_for_reinforcer`` is called
    after each reinforcer delivery.
    """

    @property
    def n(self) -> int: ...

    def next_event(self) -> tuple[int, float]: ...

    def freeze_for_reinforcer(self, duration: float) -> None: ...


class MatchingSubject:
    """Responder that shifts responses toward the key with the higher local reinforcement rate.

    Each operandum carries a local reinforcement rate: an exponential
    moving average, updated by ``learning_rate`` after every response on
    that operandum, of whether the response was reinforced (1) or not (0).
    A key that is not being visited keeps its last estimate. After each
    update the response allocation moves by ``step_size`` toward the keys
    whose local rate is above the mean (melioration; Herrnstein & Vaughan,
    1980), and no key's allocation falls below ``floor``. The allocation
    starts from ``initial_allocation`` (equal by default).

    Responses arrive as a Poisson process at ``rate_hz``. Before each
    response the subject stays on its current operandum with probability
    ``stay``; otherwise it picks an operandum in proportion to the
    allocation (possibly the current one). The stay bias lowers the
    switching rate but leaves the long-run response share equal to the
    allocation.

    The allocation is never set from the programmed schedule. It stops
    moving when the local rates are equal, i.e. when reinforcers per
    response are the same on every key, which is where relative responses
    equal relative obtained reinforcers. Whether a schedule produces
    matching is therefore left to the contingency.

    ``freeze_for_reinforcer`` is the reinforcer notification: it marks the
    last response as reinforced.
    """

    def __init__(
        self,
        n: int = 2,
        *,
        rate_hz: float = 1.0,
        learning_rate: float = 0.003,
        step_size: float = 0.01,
        stay: float = 0.0,
        floor: float = 0.02,
        initial_allocation: tuple[float, ...] | None = None,
        seed: int = 0,
    ) -> None:
        if n < 2:
            raise ValueError(f"n must be >= 2, got {n}")
        if rate_hz <= 0:
            raise ValueError(f"rate_hz must be > 0, got {rate_hz}")
        if not 0 < learning_rate <= 1:
            raise ValueError(f"learning_rate must be in (0, 1], got {learning_rate}")
        if not 0 < step_size <= 1:
            raise ValueError(f"step_size must be in (0, 1], got {step_size}")
        if not 0 <= stay < 1:
            raise ValueError(f"stay must be in [0, 1), got {stay}")
        if not 0 < floor < 1 / n:
            raise ValueError(f"floor must be in (0, 1/n), got {floor}")
        allocation = initial_allocation if initial_allocation is not None else (1.0,) * n
        if len(allocation) != n:
            raise ValueError(f"initial_allocation must have {n} values, got {len(allocation)}")
        if any(a <= 0 for a in allocation):
            raise ValueError(f"initial_allocation must be > 0, got {allocation}")
        total = sum(allocation)
        self._n = n
        self.rate_hz = rate_hz
        self.learning_rate = learning_rate
        self.step_size = step_size
        self.stay = stay
        self.floor = floor
        self._allocation = [a / total for a in allocation]
        self._local_rates = [0.0] * n
        self._rng = random.Random(seed)
        self._current: int | None = None
        self._awaiting_outcome = False

    @property
    def n(self) -> int:
        return self._n

    @property
    def allocation(self) -> tuple[float, ...]:
        """Probability of choosing each key when not staying (sums to 1)."""
        return tuple(self._allocation)

    @property
    def local_rates(self) -> tuple[float, ...]:
        """Estimated reinforcers per response on each key."""
        return tuple(self._local_rates)

    def _pick(self) -> int:
        u = self._rng.random()
        acc = 0.0
        for k, a in enumerate(self._allocation):
            acc += a
            if u < acc:
                return k
        return self._n - 1

    def _learn(self, key: int, reinforced: float) -> None:
        rates = self._local_rates
        rates[key] += self.learning_rate * (reinforced - rates[key])
        total = sum(rates)
        if total <= 0:
            return
        mean = total / self._n
        moved = [a + self.step_size * (r - mean) / total for a, r in zip(self._allocation, rates)]
        # Lift any allocation below the floor and take the difference from
        # the largest, so the allocation still sums to 1.
        deficit = sum(self.floor - a for a in moved if a < self.floor)
        moved = [max(self.floor, a) for a in moved]
        largest = max(range(self._n), key=lambda k: moved[k])
        moved[largest] -= deficit
        self._allocation = moved

    def next_event(self) -> tuple[int, float]:
        if self._awaiting_outcome and self._current is not None:
            self._learn(self._current, 0.0)
        delta = -math.log(1.0 - self._rng.random()) / self.rate_hz
        if self._current is None or self._rng.random() >= self.stay:
            self._current = self._pick()
        self._awaiting_outcome = True
        return self._current, delta

    def freeze_for_reinforcer(self, duration: float) -> None:
        if self._awaiting_outcome and self._current is not None:
            self._learn(self._current, 1.0)
        self._awaiting_outcome = False


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
        subject: MultiSubject,
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
    "SpacedRespondingSubject",
    "IrtCopyistSubject",
    "SingleSubject",
    "MultiSubject",
    "MatchingSubject",
    "MultiVirtualSubject",
    "VirtualOperandum",
    "MultiVirtualOperandum",
    "RecordingReinforcer",
]
