"""Reproduction: device-stamped response times against host-stamped ticks.

A serial device stamps each response with its own clock, and the line
reaches the host a little later. ``drive`` hands that device time to the
session as is, but stamps every tick with the host clock after the poll
interval has elapsed, so a response made during the poll interval is
older than the tick that precedes its arrival. contingency-py schedules
reject time that moves backwards (``ScheduleStateError``), and a
continuous-reinforcement session stops at its first response. The
virtual backends do not show this because the operandum advances the
same clock the ticks read.

The test is marked ``xfail(strict=True)``: it passes once ``drive``
keeps the session on one clock, at which point the marker is removed.
"""

from __future__ import annotations

import pytest
from contingency.errors import ScheduleStateError
from contingency.schedules.ratio import CRF
from experiment_core import ReinforcementCountExit
from session_runner import SessionConfig, SessionRunner

from experiment_io import drive
from experiment_io.hw.backends.virtual import ManualClock, RecordingReinforcer
from experiment_io.hw.protocols import ResponseSample


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


@pytest.mark.integration
@pytest.mark.parametrize(
    "delay",
    [
        pytest.param(0.00125, id="median-transmission-delay"),
        pytest.param(0.0, id="no-transmission-delay"),
    ],
)
@pytest.mark.xfail(
    strict=True,
    raises=ScheduleStateError,
    reason=(
        "drive hands the device's timestamp to the schedule while ticks carry host "
        "time, so a response made during the poll interval precedes the tick before it"
    ),
)
def test_crf_runs_when_the_device_stamps_the_response(delay: float) -> None:
    clock = ManualClock()
    device = _LaggedDevice(clock, response_at=0.0721, delay=delay)
    runner = SessionRunner(
        SessionConfig(
            name="crf",
            schedule=CRF(),
            exit_condition=ReinforcementCountExit(count=1),
        ),
        clock=clock,
    )
    runner.start(start_time=clock())

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
