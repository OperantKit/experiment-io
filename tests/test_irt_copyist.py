"""Unit tests for ``IrtCopyistSubject`` (copies the IRTs that were reinforced)."""

from __future__ import annotations

import pytest

from experiment_io.hw import Operandum
from experiment_io.hw.backends.virtual import (
    IrtCopyistSubject,
    ManualClock,
    VirtualOperandum,
)


def _run(subject: IrtCopyistSubject, n: int, every: int) -> list[float]:
    """Emit ``n`` IRTs, reinforcing every ``every``-th response."""
    irts = []
    for i in range(1, n + 1):
        irts.append(subject.next_irt())
        if i % every == 0:
            subject.freeze_for_reinforcer(0.5)
    return irts


def test_irt_copyist_subject_is_deterministic_under_seed() -> None:
    a = IrtCopyistSubject(seed=7)
    b = IrtCopyistSubject(seed=7)
    assert _run(a, 300, 5) == _run(b, 300, 5)
    assert a.pool == b.pool


def test_irt_copyist_subject_irts_are_positive() -> None:
    subject = IrtCopyistSubject(seed=1)
    assert all(irt > 0 for irt in _run(subject, 500, 3))


def test_initial_pool_has_pool_size_irts() -> None:
    subject = IrtCopyistSubject(pool_size=10, seed=0)
    assert len(subject.pool) == 10
    assert all(irt > 0 for irt in subject.pool)


def test_initial_irts_override_the_drawn_pool() -> None:
    subject = IrtCopyistSubject(pool_size=3, initial_irts=(1.0, 2.0, 3.0), seed=0)
    assert subject.pool == (1.0, 2.0, 3.0)


def test_same_initial_irts_with_different_seeds_give_different_streams() -> None:
    pool = IrtCopyistSubject(pool_size=5, seed=0).pool
    a = IrtCopyistSubject(pool_size=5, initial_irts=pool, seed=1)
    b = IrtCopyistSubject(pool_size=5, initial_irts=pool, seed=2)
    assert a.pool == b.pool
    assert _run(a, 20, 4) != _run(b, 20, 4)


def test_reinforced_irt_enters_the_pool_and_the_oldest_leaves() -> None:
    subject = IrtCopyistSubject(pool_size=3, initial_irts=(1.0, 2.0, 3.0), seed=0)
    irt = subject.next_irt()
    subject.freeze_for_reinforcer(0.5)
    assert subject.pool == (2.0, 3.0, irt)


def test_unreinforced_irt_does_not_enter_the_pool() -> None:
    subject = IrtCopyistSubject(pool_size=3, initial_irts=(1.0, 2.0, 3.0), seed=0)
    subject.next_irt()
    subject.next_irt()
    assert subject.pool == (1.0, 2.0, 3.0)


def test_reinforcer_without_a_response_leaves_the_pool() -> None:
    subject = IrtCopyistSubject(pool_size=3, initial_irts=(1.0, 2.0, 3.0), seed=0)
    subject.freeze_for_reinforcer(0.5)
    subject.next_irt()
    subject.freeze_for_reinforcer(0.5)
    subject.freeze_for_reinforcer(0.5)  # no response since the last reinforcer
    assert len(subject.pool) == 3
    assert subject.pool[:2] == (2.0, 3.0)


def test_irts_are_drawn_from_the_pool_with_noise() -> None:
    subject = IrtCopyistSubject(pool_size=1, initial_irts=(2.0,), cv=0.3, seed=4)
    irts = [subject.next_irt() for _ in range(2000)]
    mean = sum(irts) / len(irts)
    sd = (sum((x - mean) ** 2 for x in irts) / len(irts)) ** 0.5
    assert mean == pytest.approx(2.0, rel=0.05)
    assert sd / mean == pytest.approx(0.3, rel=0.15)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"pool_size": 0},
        {"cv": -0.1},
        {"initial_rate_hz": 0.0},
        {"pool_size": 2, "initial_irts": (1.0,)},
        {"pool_size": 2, "initial_irts": (1.0, 0.0)},
    ],
)
def test_irt_copyist_subject_rejects_invalid_parameters(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        IrtCopyistSubject(**kwargs)  # type: ignore[arg-type]


def test_virtual_operandum_forwards_reinforcer_to_copyist() -> None:
    clock = ManualClock()
    subject = IrtCopyistSubject(pool_size=2, initial_irts=(1.0, 2.0), seed=0)
    op = VirtualOperandum(0, subject, clock)
    assert isinstance(op, Operandum)
    [sample] = op.poll()
    op.freeze_for_reinforcer(0.5)
    assert subject.pool == (2.0, pytest.approx(sample.timestamp))
