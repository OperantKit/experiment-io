"""Hardware-driven session loop.

:func:`drive` polls a list of HAL :class:`~experiment_io.hw.Operandum`
inputs, routes responses into a structurally-typed
:class:`SessionDriverLike` (typically a
``session_runner.SessionRunner``), and pulses
:class:`~experiment_io.hw.Reinforcer` outputs whenever the driver
enters its reinforcing state.

The loop is HAL-backend-agnostic: virtual, serial, and HIL backends
all satisfy the :class:`~experiment_io.hw.Operandum` /
:class:`~experiment_io.hw.Reinforcer` Protocols and therefore plug
in interchangeably.

Why this lives in ``experiment-io`` and not ``session-runner``: the
loop's substantive content is "how to use HAL devices to drive
something". The driver itself is structurally typed
(:class:`SessionDriverLike`) so this module can stay free of any
import dependency on ``session-runner``.
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable, Sequence
from typing import Protocol, runtime_checkable

from .hw import Operandum, Reinforcer, ResponseSample


@runtime_checkable
class SessionDriverLike(Protocol):
    """Structural type satisfied by ``session_runner.SessionRunner``.

    Any object exposing this minimal surface can be driven by
    :func:`drive`. Defining it here avoids a circular import with
    ``session-runner``.
    """

    def handle_response_on(self, operandum: int, timestamp: float) -> None: ...
    def handle_response(self, timestamp: float) -> None: ...
    def handle_tick(self, timestamp: float) -> None: ...
    def end_reinforcement(self, timestamp: float) -> None: ...

    @property
    def is_finished(self) -> bool: ...
    @property
    def is_reinforcing(self) -> bool: ...


def drive(
    driver: SessionDriverLike,
    operanda: Sequence[Operandum],
    reinforcers: Sequence[Reinforcer],
    clock: Callable[[], float],
    *,
    is_concurrent: bool | None = None,
    last_reinforced_operandum: Callable[[], int | None] | None = None,
    reinforcer_duration: float = 0.5,
    max_duration: float | None = None,
    max_steps: int = 5_000,
    poll_interval: float = 0.001,
    on_response: Callable[[ResponseSample, float], None] | None = None,
) -> None:
    """Drive ``driver`` from HAL devices until the session finishes.

    Each iteration:

    1. If ``driver.is_reinforcing``, deliver the reinforcer matching the
       triggering operandum, advance the clock by ``reinforcer_duration``
       (deterministically for virtual clocks; via :func:`time.sleep` for
       real wall-clocks), call any virtual-subject ``freeze_for_reinforcer``
       hooks, then call :meth:`driver.end_reinforcement`.
    2. Otherwise route any samples left over from the previous poll, or
       poll the operanda in order. Each
       :class:`~experiment_io.hw.ResponseSample` is routed through
       :meth:`driver.handle_response_on` (concurrent) or
       :meth:`driver.handle_response` (single-component). Concurrent
       routing is selected by ``is_concurrent`` (auto-detected when
       ``None`` from ``driver.session._is_concurrent`` if available,
       else inferred from operandum count > 1). When a sample starts a
       reinforcer or finishes the session, the samples after it are kept
       and routed on a later iteration instead of being dropped.
    3. If no sample arrived and the session is still running, advance
       the clock by ``poll_interval`` (or sleep) and call
       :meth:`driver.handle_tick`.

    Session time comes from ``clock`` alone. A response is handed to the
    driver with ``clock()`` read at that moment, not with
    ``sample.timestamp``: a device stamps responses with its own clock,
    and its lines arrive after the tick that ``drive`` already stamped
    with the host clock, so the device time would move session time
    backwards. The device time stays in ``sample.timestamp`` as a record;
    pass ``on_response`` to receive each sample together with the session
    time it was given. Virtual operanda advance ``clock`` themselves, so
    for them the two times are equal.

    Parameters
    ----------
    driver:
        Anything satisfying :class:`SessionDriverLike` (typically
        ``session_runner.SessionRunner``).
    operanda:
        Input devices. Length must match ``reinforcers``.
    reinforcers:
        Output devices; ``reinforcers[i]`` pairs with ``operanda[i]``.
    clock:
        Monotonic time source. Virtual backends with
        ``advance(seconds)`` are detected and used for deterministic
        time stepping; real wall-clocks fall back to :func:`time.sleep`.
    is_concurrent:
        Force the routing decision; ``None`` (default) auto-detects.
    last_reinforced_operandum:
        Optional callable returning the operandum index that triggered
        the current reinforcement (used to select the matching
        reinforcer). Default introspects ``driver.session._last_reinforced_operandum``
        if present, else returns ``0``.
    reinforcer_duration, max_duration, max_steps, poll_interval:
        Loop parameters; see method body for details.
    on_response:
        Optional callable receiving each routed sample and the session
        time it was handed over with (for recording the device's time).
    """
    if not operanda:
        raise ValueError("drive requires at least one operandum")
    if len(reinforcers) != len(operanda):
        raise ValueError(
            f"operanda and reinforcers must have the same length "
            f"(got {len(operanda)} vs {len(reinforcers)})"
        )

    is_concurrent_resolved = _resolve_concurrent(driver, operanda, is_concurrent)
    get_last_op = _resolve_last_op_getter(driver, last_reinforced_operandum)

    try:
        session_start = clock()
        pending: deque[ResponseSample] = deque()

        steps = 0
        while not driver.is_finished and steps < max_steps:
            steps += 1
            if max_duration is not None and (clock() - session_start) >= max_duration:
                break

            if driver.is_reinforcing:
                op_idx = get_last_op() or 0
                reinforcers[op_idx].deliver(reinforcer_duration)
                _advance(clock, reinforcer_duration)
                _freeze_virtual_subjects(operanda, reinforcer_duration)
                driver.end_reinforcement(clock())
                continue

            routed_any = False
            if pending:
                routed_any = True
                _route(driver, pending, clock, is_concurrent_resolved, on_response)
            else:
                for op in operanda:
                    pending.extend(op.poll())
                    if pending:
                        routed_any = True
                        _route(driver, pending, clock, is_concurrent_resolved, on_response)
                    if driver.is_reinforcing or driver.is_finished:
                        break

            if not routed_any and not driver.is_reinforcing:
                _advance(clock, poll_interval)
                driver.handle_tick(clock())

        if steps >= max_steps and not driver.is_finished:
            raise RuntimeError(
                f"drive: did not finish within {max_steps} steps"
            )
    finally:
        for op in operanda:
            close = getattr(op, "close", None)
            if callable(close):
                close()
        for rein in reinforcers:
            close = getattr(rein, "close", None)
            if callable(close):
                close()


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def _resolve_concurrent(
    driver: object, operanda: Sequence[Operandum], explicit: bool | None
) -> bool:
    if explicit is not None:
        return explicit
    session = getattr(driver, "session", None)
    is_concurrent_attr = getattr(session, "_is_concurrent", None)
    if isinstance(is_concurrent_attr, bool):
        return is_concurrent_attr
    return len(operanda) > 1


def _resolve_last_op_getter(
    driver: object, explicit: Callable[[], int | None] | None
) -> Callable[[], int | None]:
    if explicit is not None:
        return explicit

    def _default() -> int | None:
        session = getattr(driver, "session", None)
        return getattr(session, "_last_reinforced_operandum", None)

    return _default


def _route(
    driver: SessionDriverLike,
    pending: deque[ResponseSample],
    clock: Callable[[], float],
    is_concurrent: bool,
    on_response: Callable[[ResponseSample, float], None] | None,
) -> None:
    """Hand queued samples to ``driver`` at host time until it reinforces or finishes."""
    while pending and not (driver.is_reinforcing or driver.is_finished):
        sample = pending.popleft()
        now = clock()
        if is_concurrent:
            driver.handle_response_on(sample.operandum_index, now)
        else:
            driver.handle_response(now)
        if on_response is not None:
            on_response(sample, now)


def _advance(clock: Callable[[], float], duration: float) -> None:
    advance = getattr(clock, "advance", None)
    if callable(advance):
        advance(duration)
    else:
        time.sleep(duration)


def _freeze_virtual_subjects(operanda: Sequence[Operandum], duration: float) -> None:
    """Advance the pending-event timeline of virtual backends.

    Real hardware operanda report wall-clock samples and need no
    adjustment. Virtual backends expose ``freeze_for_reinforcer`` so
    their internal timelines stay ahead of ``now`` after a reinforcer
    delivery.
    """
    seen: set[int] = set()
    for op in operanda:
        targets = []
        if hasattr(op, "freeze_for_reinforcer"):
            targets.append(op)
        parent = getattr(op, "_parent", None)
        if parent is not None and hasattr(parent, "freeze_for_reinforcer"):
            targets.append(parent)
        for tgt in targets:
            if id(tgt) in seen:
                continue
            seen.add(id(tgt))
            tgt.freeze_for_reinforcer(duration)
