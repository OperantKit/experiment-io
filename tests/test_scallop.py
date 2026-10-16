"""Unit tests for ``ScallopSubject`` (time-since-reinforcement responder)."""

from __future__ import annotations

import pytest

from experiment_io.hw import Operandum
from experiment_io.hw.backends.virtual import (
    ManualClock,
    ScallopSubject,
    VirtualOperandum,
    VirtualSubject,
)


def _interval_halves(subject: ScallopSubject, interval: float) -> tuple[int, int]:
    """Count responses in the first and second half of one interval."""
    subject.freeze_for_reinforcer(0.0)
    t = 0.0
    first = second = 0
    while True:
        t += subject.next_irt()
        if t >= interval:
            return first, second
        if t < interval / 2:
            first += 1
        else:
            second += 1


def test_scallop_subject_is_deterministic_under_seed() -> None:
    a = ScallopSubject(seed=7)
    b = ScallopSubject(seed=7)
    assert [a.next_irt() for _ in range(20)] == [b.next_irt() for _ in range(20)]


def test_scallop_subject_irts_are_positive() -> None:
    subject = ScallopSubject(seed=1)
    for _ in range(100):
        assert subject.next_irt() > 0


@pytest.mark.parametrize(
    "kwargs",
    [{"max_rate_hz": 0.0}, {"max_rate_hz": -1.0}, {"ramp_s": 0.0}, {"ramp_s": -5.0}],
)
def test_scallop_subject_rejects_non_positive_parameters(kwargs: dict[str, float]) -> None:
    with pytest.raises(ValueError):
        ScallopSubject(**kwargs)


def test_scallop_subject_reaches_terminal_rate_after_ramp() -> None:
    subject = ScallopSubject(max_rate_hz=2.0, ramp_s=60.0, seed=3)
    while subject.elapsed < 60.0:
        subject.next_irt()
    irts = [subject.next_irt() for _ in range(2000)]
    mean = sum(irts) / len(irts)
    assert 0.45 < mean < 0.55  # Exponential(2 Hz) once the ramp is complete


def test_freeze_for_reinforcer_resets_time_since_reinforcement() -> None:
    subject = ScallopSubject(seed=0)
    for _ in range(5):
        subject.next_irt()
    assert subject.elapsed > 0
    subject.freeze_for_reinforcer(0.5)
    assert subject.elapsed == 0.0


def test_responding_accelerates_within_each_interval() -> None:
    subject = ScallopSubject(max_rate_hz=2.0, ramp_s=60.0, seed=11)
    halves = [_interval_halves(subject, 60.0) for _ in range(30)]
    accelerating = sum(1 for first, second in halves if second > first)
    assert accelerating >= 25


def test_poisson_subject_does_not_accelerate() -> None:
    subject = VirtualSubject(rate_hz=2.0, seed=11)
    first = second = 0
    t = 0.0
    while t < 30 * 60.0:
        t += subject.next_irt()
        if t % 60.0 < 30.0:
            first += 1
        else:
            second += 1
    assert abs(second - first) / (first + second) < 0.05


def test_virtual_operandum_forwards_reinforcer_to_scallop_subject() -> None:
    clock = ManualClock()
    subject = ScallopSubject(seed=0)
    op = VirtualOperandum(0, subject, clock)
    assert isinstance(op, Operandum)
    op.poll()
    assert subject.elapsed > 0
    op.freeze_for_reinforcer(0.5)
    assert subject.elapsed == 0.0


def test_virtual_operandum_freeze_is_noop_for_poisson_subject() -> None:
    clock = ManualClock()
    subject = VirtualSubject(rate_hz=2.0, seed=0)
    reference = VirtualSubject(rate_hz=2.0, seed=0)
    op = VirtualOperandum(0, subject, clock)
    op.freeze_for_reinforcer(0.5)
    assert subject.next_irt() == reference.next_irt()
