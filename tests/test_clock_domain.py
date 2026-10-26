"""Session time comes from the host clock; the device's time is kept as a record.

A serial device stamps each response with its own clock, and the line
reaches the host a little later. Ticks are stamped with the host clock
after the poll interval has elapsed, so a response made during the poll
interval is older than the tick that precedes its arrival. If ``drive``
handed the device time to the session, contingency-py schedules would
reject the time that moves backwards (``ScheduleStateError``) and a
continuous-reinforcement session would stop at its first response.

``drive`` therefore stamps each response with ``clock()`` when it hands
the response to the session, and leaves the device's time in
``ResponseSample.timestamp`` for the ``on_response`` callback to record.
"""

from __future__ import annotations

import json

import pytest
from contingency.schedules.interval import FI
from contingency.schedules.ratio import CRF
from experiment_core import ReinforcementCountExit
from session_runner import SessionConfig, SessionRunner

from experiment_io import drive
from experiment_io.hw.backends.serial import SerialOperandum, SerialReinforcer
from experiment_io.hw.backends.virtual import (
    ManualClock,
    RecordingReinforcer,
    VirtualOperandum,
    VirtualSubject,
)
from experiment_io.hw.protocols import ResponseSample

# Receive delay minus device time measured on a Teensy 4.1 over USB serial:
# median 1.25 ms, maximum 7.0 ms.
_DELAYS = (0.001, 0.00125, 0.003, 0.007)


class _LaggedDevice:
    """Serial-like operandum: one response, stamped by the device, seen late.

    The response occurs at host time ``response_at`` (the device clock is
    aligned with the host's) and its line arrives ``delay`` seconds later.
    ``poll`` returns it at the first poll after it has arrived.
    """

    operandum_index = 0

    def __init__(self, clock: ManualClock, response_at: float, delay: float) -> None:
        self.clock = clock
        self.response_at = response_at
        self.delay = delay
        self._sent = False

    def poll(self) -> list[ResponseSample]:
        if not self._sent and self.clock() >= self.response_at + self.delay:
            self._sent = True
            return [ResponseSample(operandum_index=0, timestamp=self.response_at)]
        return []

    def close(self) -> None:
        pass


class _LaggedSerialStream:
    """Byte stream that behaves like a pyserial port with ``timeout=0``.

    Each response line carries the device's time ``ts`` and becomes
    readable once the host clock reaches ``ts + delay``. ``readline``
    returns ``b""`` when no line has arrived yet. Writes are recorded.
    """

    def __init__(
        self, clock: ManualClock, device_times: list[float], delays: tuple[float, ...]
    ) -> None:
        self.clock = clock
        self._lines = [
            (ts + delays[i % len(delays)], ts) for i, ts in enumerate(device_times)
        ]
        self.written: list[bytes] = []

    def readline(self) -> bytes:
        if self._lines and self._lines[0][0] <= self.clock():
            _, ts = self._lines.pop(0)
            return (json.dumps({"op": 0, "ts": ts, "kind": "response"}) + "\n").encode()
        return b""

    def write(self, data: bytes) -> int:
        if not isinstance(data, bytes):
            raise TypeError("unicode strings are not supported, please encode to bytes")
        self.written.append(data)
        return len(data)

    def flush(self) -> None:
        pass


class _BurstOperandum:
    """Returns every queued response in one poll, as a backed-up port does."""

    operandum_index = 0

    def __init__(self, clock: ManualClock, at: float, device_times: list[float]) -> None:
        self.clock = clock
        self.at = at
        self.device_times = device_times
        self._sent = False

    def poll(self) -> list[ResponseSample]:
        if not self._sent and self.clock() >= self.at:
            self._sent = True
            return [ResponseSample(operandum_index=0, timestamp=t) for t in self.device_times]
        return []

    def close(self) -> None:
        pass


def _runner(clock: ManualClock, schedule: object, count: int) -> SessionRunner:
    runner = SessionRunner(
        SessionConfig(
            name="clock-domain",
            schedule=schedule,
            exit_condition=ReinforcementCountExit(count=count),
        ),
        clock=clock,
    )
    runner.start(start_time=clock())
    return runner


@pytest.mark.integration
@pytest.mark.parametrize(
    "delay",
    [
        pytest.param(0.00125, id="median-transmission-delay"),
        pytest.param(0.0, id="no-transmission-delay"),
    ],
)
def test_crf_runs_when_the_device_stamps_the_response(delay: float) -> None:
    clock = ManualClock()
    device = _LaggedDevice(clock, response_at=0.0721, delay=delay)
    runner = _runner(clock, CRF(), 1)

    drive(
        runner,
        operanda=[device],
        reinforcers=[RecordingReinforcer(operandum_index=0)],
        clock=clock,
        is_concurrent=False,
        last_reinforced_operandum=lambda: 0,
        reinforcer_duration=0.5,
    )

    assert runner.is_finished
    assert runner.total_responses == 1


@pytest.mark.integration
@pytest.mark.parametrize(
    ("schedule", "device_times", "count"),
    [
        pytest.param(CRF(), [0.02 * (i + 1) for i in range(20)], 20, id="CRF"),
        pytest.param(FI(0.05), [0.0125 * (i + 1) for i in range(40)], 5, id="FI"),
    ],
)
def test_lagged_serial_stream_runs_to_the_exit_condition(
    schedule: object, device_times: list[float], count: int
) -> None:
    clock = ManualClock()
    stream = _LaggedSerialStream(clock, device_times, _DELAYS)
    runner = _runner(clock, schedule, count)
    received: list[tuple[float, float]] = []

    drive(
        runner,
        operanda=[SerialOperandum(stream, operandum_index=0)],
        reinforcers=[SerialReinforcer(stream, operandum_index=0)],
        clock=clock,
        is_concurrent=False,
        last_reinforced_operandum=lambda: 0,
        reinforcer_duration=0.005,
        max_steps=50_000,
        on_response=lambda sample, t: received.append((sample.timestamp, t)),
    )

    assert runner.is_finished
    assert runner.session.reinforcement_count == count
    assert len(stream.written) == count
    # The session saw host time at hand-over; the callback saw the device time.
    response_times = [
        e.timestamp for e in runner.session.events if type(e).__name__ == "ResponseEvent"
    ]
    assert response_times == [t for _, t in received]
    assert [d for d, _ in received] == device_times[: len(received)]
    lags = [t - d for d, t in received]
    assert all(lag >= 0 for lag in lags)
    assert max(lags) <= max(_DELAYS) + 0.001 + 0.005 + 1e-9


@pytest.mark.integration
def test_responses_after_the_reinforcing_one_in_the_same_poll_are_kept() -> None:
    clock = ManualClock()
    burst = _BurstOperandum(clock, at=0.1, device_times=[0.095, 0.097, 0.099])
    runner = _runner(clock, CRF(), 3)

    drive(
        runner,
        operanda=[burst],
        reinforcers=[RecordingReinforcer(operandum_index=0)],
        clock=clock,
        is_concurrent=False,
        last_reinforced_operandum=lambda: 0,
        reinforcer_duration=0.05,
    )

    assert runner.is_finished
    assert runner.total_responses == 3
    assert runner.session.reinforcement_count == 3


@pytest.mark.unit
def test_virtual_response_times_equal_the_operandum_timestamps() -> None:
    clock = ManualClock()
    op = VirtualOperandum(0, VirtualSubject(rate_hz=5.0, seed=3), clock)
    runner = _runner(clock, CRF(), 4)
    received: list[tuple[float, float]] = []

    drive(
        runner,
        operanda=[op],
        reinforcers=[RecordingReinforcer(operandum_index=0)],
        clock=clock,
        is_concurrent=False,
        last_reinforced_operandum=lambda: 0,
        reinforcer_duration=0.5,
        on_response=lambda sample, t: received.append((sample.timestamp, t)),
    )

    assert len(received) == 4
    assert all(d == t for d, t in received)
