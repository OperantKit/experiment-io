"""Tests for ``experiment_io.drive`` (HAL session loop)."""

from __future__ import annotations

import pytest

from experiment_io import SessionDriverLike, drive
from experiment_io.hw.backends.virtual import (
    ManualClock,
    RecordingReinforcer,
    VirtualOperandum,
    VirtualSubject,
)


class _SilentOperandum:
    """Operandum that never produces samples (deterministic idle)."""

    def __init__(self, operandum_index: int) -> None:
        self.operandum_index = operandum_index

    def poll(self):  # type: ignore[no-untyped-def]
        return []

    def close(self) -> None:
        pass


class _StubDriver:
    """Minimal :class:`SessionDriverLike` test double.

    Mimics SessionRunner without depending on the session-runner
    package: counts responses, and finishes after ``stop_after``
    reinforcers.
    """

    def __init__(self, *, stop_after: int = 1, concurrent: bool = False) -> None:
        self.responses: list[tuple[int | None, float]] = []
        self.ticks: list[float] = []
        self.ends: list[float] = []
        self._is_reinforcing = False
        self._is_finished = False
        self._stop_after = stop_after
        self._reinforcement_count = 0
        self._concurrent = concurrent
        self._last_op: int | None = None

    def handle_response(self, timestamp: float) -> None:
        self.responses.append((None, timestamp))
        self._is_reinforcing = True
        self._last_op = 0

    def handle_response_on(self, operandum: int, timestamp: float) -> None:
        self.responses.append((operandum, timestamp))
        self._is_reinforcing = True
        self._last_op = operandum

    def handle_tick(self, timestamp: float) -> None:
        self.ticks.append(timestamp)

    def end_reinforcement(self, timestamp: float) -> None:
        self.ends.append(timestamp)
        self._is_reinforcing = False
        self._reinforcement_count += 1
        if self._reinforcement_count >= self._stop_after:
            self._is_finished = True

    @property
    def is_finished(self) -> bool:
        return self._is_finished

    @property
    def is_reinforcing(self) -> bool:
        return self._is_reinforcing


@pytest.mark.unit
def test_session_driver_like_is_runtime_checkable() -> None:
    stub = _StubDriver()
    assert isinstance(stub, SessionDriverLike)


@pytest.mark.unit
def test_drive_requires_at_least_one_operandum() -> None:
    with pytest.raises(ValueError, match="at least one operandum"):
        drive(_StubDriver(), [], [], lambda: 0.0)


@pytest.mark.unit
def test_drive_requires_matching_lengths() -> None:
    clock = ManualClock()
    op = VirtualOperandum(0, VirtualSubject(rate_hz=1.0, seed=0), clock)
    with pytest.raises(ValueError, match="same length"):
        drive(_StubDriver(), [op], [], clock)


@pytest.mark.unit
def test_drive_routes_response_and_delivers_reinforcer() -> None:
    clock = ManualClock()
    subject = VirtualSubject(rate_hz=10.0, seed=0)
    op = VirtualOperandum(0, subject, clock)
    rein = RecordingReinforcer(operandum_index=0)
    driver = _StubDriver(stop_after=1)

    drive(
        driver,
        operanda=[op],
        reinforcers=[rein],
        clock=clock,
        reinforcer_duration=0.05,
        is_concurrent=False,
        last_reinforced_operandum=lambda: 0,
    )

    assert driver.is_finished
    assert len(rein.calls) == 1
    assert len(driver.responses) >= 1


@pytest.mark.unit
def test_drive_raises_when_max_steps_exceeded() -> None:
    clock = ManualClock()
    # rate_hz=0 → never produces a response; loop just ticks
    op = _SilentOperandum(0)
    rein = RecordingReinforcer(operandum_index=0)
    driver = _StubDriver(stop_after=1)

    with pytest.raises(RuntimeError, match="did not finish"):
        drive(
            driver,
            operanda=[op],
            reinforcers=[rein],
            clock=clock,
            max_steps=5,
            is_concurrent=False,
            last_reinforced_operandum=lambda: 0,
        )


@pytest.mark.unit
def test_drive_respects_max_duration() -> None:
    clock = ManualClock()
    op = _SilentOperandum(0)
    rein = RecordingReinforcer(operandum_index=0)
    driver = _StubDriver(stop_after=999)

    drive(
        driver,
        operanda=[op],
        reinforcers=[rein],
        clock=clock,
        max_duration=0.05,
        poll_interval=0.01,
        is_concurrent=False,
        last_reinforced_operandum=lambda: 0,
    )

    # Loop should have terminated via max_duration without finishing
    assert not driver.is_finished


@pytest.mark.unit
def test_drive_concurrent_routes_via_handle_response_on() -> None:
    clock = ManualClock()
    subject0 = VirtualSubject(rate_hz=20.0, seed=0)
    op0 = VirtualOperandum(0, subject0, clock)
    op1 = _SilentOperandum(1)  # never fires
    rein0 = RecordingReinforcer(operandum_index=0)
    rein1 = RecordingReinforcer(operandum_index=1)
    driver = _StubDriver(stop_after=1, concurrent=True)

    drive(
        driver,
        operanda=[op0, op1],
        reinforcers=[rein0, rein1],
        clock=clock,
        reinforcer_duration=0.05,
        is_concurrent=True,
        last_reinforced_operandum=lambda: 0,
    )

    assert driver.is_finished
    routed_ops = [op for op, _ in driver.responses]
    assert all(op == 0 for op in routed_ops)
    assert len(rein0.calls) == 1
    assert len(rein1.calls) == 0


@pytest.mark.unit
def test_drive_auto_detects_concurrent_from_operandum_count() -> None:
    """When ``is_concurrent`` is None and driver has no session attr,
    the loop infers concurrent from len(operanda) > 1."""
    clock = ManualClock()
    op0 = VirtualOperandum(0, VirtualSubject(rate_hz=10.0, seed=0), clock)
    op1 = _SilentOperandum(1)
    rein0 = RecordingReinforcer(operandum_index=0)
    rein1 = RecordingReinforcer(operandum_index=1)
    driver = _StubDriver(stop_after=1, concurrent=True)

    drive(
        driver,
        operanda=[op0, op1],
        reinforcers=[rein0, rein1],
        clock=clock,
        reinforcer_duration=0.05,
    )

    assert driver.is_finished
    assert all(op is not None for op, _ in driver.responses)
