"""Unit tests for ``SpacedRespondingSubject`` (IRT-shaping responder for DRL)."""

from __future__ import annotations

import pytest

from experiment_io.hw import Operandum
from experiment_io.hw.backends.virtual import (
    ManualClock,
    SpacedRespondingSubject,
    VirtualOperandum,
)


def _run_drl(subject: SpacedRespondingSubject, threshold: float, n: int) -> list[float]:
    """Respond ``n`` times under a DRL contingency; return the IRTs."""
    irts = []
    for _ in range(n):
        irt = subject.next_irt()
        irts.append(irt)
        if irt >= threshold:
            subject.freeze_for_reinforcer(0.5)
    return irts


def test_spaced_responding_subject_is_deterministic_under_seed() -> None:
    a = SpacedRespondingSubject(seed=7)
    b = SpacedRespondingSubject(seed=7)
    assert _run_drl(a, 10.0, 200) == _run_drl(b, 10.0, 200)


def test_spaced_responding_subject_irts_are_positive() -> None:
    subject = SpacedRespondingSubject(seed=1)
    assert all(irt > 0 for irt in _run_drl(subject, 10.0, 500))


def test_initial_mean_irt_is_one_second() -> None:
    subject = SpacedRespondingSubject(seed=0)
    assert subject.mean_irt == 1.0


@pytest.mark.parametrize(
    "kwargs",
    [
        {"initial_mean_s": 0.0},
        {"cv": 0.0},
        {"cv": -0.1},
        {"overshoot": 0.0},
        {"learning_rate": 0.0},
        {"learning_rate": 1.5},
        {"shrink": -0.1},
        {"shrink": 1.0},
    ],
)
def test_spaced_responding_subject_rejects_invalid_parameters(kwargs: dict[str, float]) -> None:
    with pytest.raises(ValueError):
        SpacedRespondingSubject(**kwargs)


def test_unreinforced_irt_moves_mean_toward_overshoot_times_it() -> None:
    subject = SpacedRespondingSubject(overshoot=1.2, learning_rate=0.5, seed=3)
    first = subject.next_irt()  # outcome pending
    subject.next_irt()  # previous response was not reinforced
    assert subject.mean_irt == pytest.approx(1.0 + 0.5 * (1.2 * first - 1.0))


def test_reinforcer_shrinks_mean() -> None:
    subject = SpacedRespondingSubject(shrink=0.1, seed=3)
    subject.next_irt()
    subject.freeze_for_reinforcer(0.5)
    assert subject.mean_irt == pytest.approx(0.9)
    # The next IRT does not apply an extinction update for a reinforced response.
    subject.next_irt()
    assert subject.mean_irt == pytest.approx(0.9)


def test_drl_shapes_irts_past_the_threshold() -> None:
    subject = SpacedRespondingSubject(seed=11)
    irts = _run_drl(subject, 10.0, 600)
    early = irts[:20]  # still near the initial 1-s mean
    late = irts[-200:]
    assert sum(irt >= 10.0 for irt in early) / len(early) < 0.2
    assert sum(irt >= 10.0 for irt in late) / len(late) >= 0.5


def test_virtual_operandum_forwards_reinforcer_to_spaced_subject() -> None:
    clock = ManualClock()
    subject = SpacedRespondingSubject(shrink=0.2, seed=0)
    op = VirtualOperandum(0, subject, clock)
    assert isinstance(op, Operandum)
    op.poll()
    op.freeze_for_reinforcer(0.5)
    assert subject.mean_irt == pytest.approx(0.8)
