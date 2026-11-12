"""``drive`` keeps reading a device while a reinforcer is being delivered.

Responses the device sends during reinforcer access are handed to the
session at the host time they arrive, so they land between the
``ReinforcerStartEvent`` and the ``ReinforcerEndEvent`` in the record.
The session records them without counting them toward the schedule.
Virtual subjects do not respond during access, as before.
"""

from __future__ import annotations

import pytest
from contingency.schedules.ratio import CRF
from experiment_core import ReinforcerEndEvent, ReinforcerStartEvent, ResponseEvent

from experiment_io import drive
from experiment_io.hw.backends.serial import SerialOperandum, SerialReinforcer
from experiment_io.hw.backends.virtual import (
    ManualClock,
    RecordingReinforcer,
    VirtualOperandum,
    VirtualSubject,
)

from .test_clock_domain import _LaggedSerialStream, _runner

_DELAY = (0.00125,)


def _drive_serial(
    clock: ManualClock, device_times: list[float], count: int = 5
) -> tuple[object, list[tuple[float, float]]]:
    stream = _LaggedSerialStream(clock, device_times, _DELAY)
    runner = _runner(clock, CRF(), count)
    received: list[tuple[float, float]] = []
    drive(
        runner,
        operanda=[SerialOperandum(stream, operandum_index=0)],
        reinforcers=[SerialReinforcer(stream, operandum_index=0)],
        clock=clock,
        is_concurrent=False,
        last_reinforced_operandum=lambda: 0,
        reinforcer_duration=0.5,
        max_duration=2.0,
        max_steps=50_000,
        on_response=lambda sample, t: received.append((sample.timestamp, t)),
    )
    return runner, received


def _times(runner: object, kind: type) -> list[float]:
    return [e.timestamp for e in runner.session.events if isinstance(e, kind)]  # type: ignore[attr-defined]


@pytest.mark.integration
def test_responses_during_access_get_session_times_inside_access() -> None:
    clock = ManualClock()
    runner, _ = _drive_serial(clock, [0.1, 0.2, 0.3, 0.4])

    (start,) = _times(runner, ReinforcerStartEvent)
    (end,) = _times(runner, ReinforcerEndEvent)
    responses = _times(runner, ResponseEvent)
    assert len(responses) == 4
    assert all(start < t < end for t in responses[1:])


@pytest.mark.integration
def test_responses_during_access_do_not_add_reinforcers() -> None:
    clock = ManualClock()
    runner, _ = _drive_serial(clock, [0.1, 0.2, 0.3, 0.4])

    assert runner.session.reinforcement_count == 1  # type: ignore[attr-defined]


@pytest.mark.integration
def test_response_arriving_in_the_last_slice_is_read_before_access_ends() -> None:
    clock = ManualClock()
    # Reinforcer starts at about 0.1013 and ends about 0.6013; this line
    # arrives about 0.6006.
    runner, _ = _drive_serial(clock, [0.1, 0.5993])

    events = [
        e
        for e in runner.session.events  # type: ignore[attr-defined]
        if isinstance(e, (ResponseEvent, ReinforcerEndEvent))
    ]
    assert [type(e).__name__ for e in events[:3]] == [
        "ResponseEvent",
        "ResponseEvent",
        "ReinforcerEndEvent",
    ]
    assert runner.session.reinforcement_count == 1  # type: ignore[attr-defined]


@pytest.mark.integration
def test_device_times_reach_on_response_for_responses_during_access() -> None:
    clock = ManualClock()
    runner, received = _drive_serial(clock, [0.1, 0.2, 0.3, 0.4])

    assert [d for d, _ in received] == [0.1, 0.2, 0.3, 0.4]
    assert [t for _, t in received] == _times(runner, ResponseEvent)


@pytest.mark.unit
def test_virtual_subjects_still_do_not_respond_during_access() -> None:
    clock = ManualClock()
    op = VirtualOperandum(0, VirtualSubject(rate_hz=20.0, seed=3), clock)
    runner = _runner(clock, CRF(), 5)

    drive(
        runner,
        operanda=[op],
        reinforcers=[RecordingReinforcer(operandum_index=0)],
        clock=clock,
        is_concurrent=False,
        last_reinforced_operandum=lambda: 0,
        reinforcer_duration=0.5,
    )

    starts = _times(runner, ReinforcerStartEvent)
    ends = _times(runner, ReinforcerEndEvent)
    for t in _times(runner, ResponseEvent):
        assert not any(s < t < e for s, e in zip(starts, ends, strict=True))
