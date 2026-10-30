"""``drive`` hands the device's own time to drivers that accept ``device_time``.

Session time stays on the host clock (see ``test_clock_domain.py``); the
device's stamp in ``ResponseSample.timestamp`` is passed alongside as the
keyword ``device_time`` only when the driver's response methods take it, so
drivers written against the two-argument :class:`SessionDriverLike` keep
working unchanged.
"""

from __future__ import annotations

import inspect

import pytest
from contingency.schedules.ratio import CRF
from experiment_core import ReinforcementCountExit, ResponseEvent
from session_runner import SessionConfig, SessionRunner

from experiment_io import SessionDriverLike, drive
from experiment_io.hw.backends.virtual import ManualClock
from experiment_io.hw.protocols import ResponseSample


class _Device:
    """Returns one response per poll, stamped with a device clock far from the host's."""

    def __init__(self, operandum_index: int, device_times: list[float]) -> None:
        self.operandum_index = operandum_index
        self._pending = list(device_times)

    def poll(self) -> list[ResponseSample]:
        if not self._pending:
            return []
        ts = self._pending.pop(0)
        return [ResponseSample(operandum_index=self.operandum_index, timestamp=ts)]

    def close(self) -> None:
        pass


class _Reinforcer:
    def deliver(self, duration: float) -> None:
        pass


class _Driver:
    """Finishes after ``stop_after`` reinforcers; every response is reinforced."""

    def __init__(self, stop_after: int) -> None:
        self.calls: list[tuple[int | None, float, float | None]] = []
        self._reinforcing = False
        self._done = 0
        self._stop_after = stop_after

    def _record(self, op: int | None, t: float, device_time: float | None) -> None:
        self.calls.append((op, t, device_time))
        self._reinforcing = True

    def handle_tick(self, timestamp: float) -> None:
        pass

    def end_reinforcement(self, timestamp: float) -> None:
        self._reinforcing = False
        self._done += 1

    @property
    def is_finished(self) -> bool:
        return self._done >= self._stop_after

    @property
    def is_reinforcing(self) -> bool:
        return self._reinforcing


class _DeviceTimeDriver(_Driver):
    def handle_response(self, timestamp: float, *, device_time: float | None = None) -> None:
        self._record(None, timestamp, device_time)

    def handle_response_on(
        self, operandum: int, timestamp: float, *, device_time: float | None = None
    ) -> None:
        self._record(operandum, timestamp, device_time)


class _TwoArgDriver(_Driver):
    """The original surface: no ``device_time`` keyword at all."""

    def handle_response(self, timestamp: float) -> None:
        self._record(None, timestamp, None)

    def handle_response_on(self, operandum: int, timestamp: float) -> None:
        self._record(operandum, timestamp, None)


_DEVICE_TIMES = [324419.687, 324420.001, 324420.5]


def _drive(driver: SessionDriverLike, operanda: list[_Device], *, concurrent: bool) -> ManualClock:
    clock = ManualClock()
    drive(
        driver,
        operanda,
        [_Reinforcer() for _ in operanda],
        clock,
        is_concurrent=concurrent,
        last_reinforced_operandum=lambda: 0,
        reinforcer_duration=0.05,
    )
    return clock


@pytest.mark.unit
def test_single_component_driver_receives_device_time() -> None:
    driver = _DeviceTimeDriver(stop_after=3)
    _drive(driver, [_Device(0, _DEVICE_TIMES)], concurrent=False)
    assert [c[2] for c in driver.calls] == _DEVICE_TIMES
    # Session time is still the host clock, not the device's.
    assert all(t < 1.0 for _, t, _ in driver.calls)


@pytest.mark.unit
def test_concurrent_driver_receives_device_time_with_the_operandum() -> None:
    driver = _DeviceTimeDriver(stop_after=2)
    _drive(driver, [_Device(0, [10.0]), _Device(1, [20.0])], concurrent=True)
    assert [(op, dt) for op, _, dt in driver.calls] == [(0, 10.0), (1, 20.0)]


@pytest.mark.unit
def test_two_argument_driver_still_runs() -> None:
    driver = _TwoArgDriver(stop_after=3)
    assert isinstance(driver, SessionDriverLike)
    _drive(driver, [_Device(0, _DEVICE_TIMES)], concurrent=False)
    assert len(driver.calls) == 3


_RUNNER_TAKES_DEVICE_TIME = (
    "device_time" in inspect.signature(SessionRunner.handle_response).parameters
)


@pytest.mark.integration
@pytest.mark.skipif(
    not _RUNNER_TAKES_DEVICE_TIME, reason="installed session-runner has no device_time"
)
def test_session_runner_events_carry_device_time() -> None:
    clock = ManualClock()
    runner = SessionRunner(
        SessionConfig(
            name="device-time",
            schedule=CRF(),
            exit_condition=ReinforcementCountExit(count=3),
        ),
        clock=clock,
    )
    runner.start(start_time=clock())
    drive(runner, [_Device(0, _DEVICE_TIMES)], [_Reinforcer()], clock, reinforcer_duration=0.05)
    responses = [e for e in runner.sink.events if isinstance(e, ResponseEvent)]
    assert [e.device_time for e in responses] == _DEVICE_TIMES
    assert all(e.timestamp < 1.0 for e in responses)
