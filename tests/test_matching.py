"""Unit tests for ``MatchingSubject`` (melioration responder over two keys)."""

from __future__ import annotations

import pytest

from experiment_io.hw.backends.virtual import (
    ManualClock,
    MatchingSubject,
    MultiVirtualOperandum,
)


def _allocation(subject: MatchingSubject, events: int) -> list[float]:
    counts = [0] * subject.n
    for _ in range(events):
        idx, _delta = subject.next_event()
        counts[idx] += 1
    return [c / events for c in counts]


def test_matching_subject_is_deterministic_under_seed() -> None:
    a = MatchingSubject(seed=11)
    b = MatchingSubject(seed=11)
    assert [a.next_event() for _ in range(50)] == [b.next_event() for _ in range(50)]


def test_matching_subject_events_are_valid() -> None:
    subject = MatchingSubject(seed=2)
    for _ in range(500):
        idx, delta = subject.next_event()
        assert idx in (0, 1)
        assert delta > 0


@pytest.mark.parametrize(
    "kwargs",
    [
        {"rate_hz": 0.0},
        {"learning_rate": 0.0},
        {"learning_rate": 1.5},
        {"step_size": 0.0},
        {"stay": -0.1},
        {"stay": 1.0},
        {"floor": 0.0},
        {"floor": 0.5},
        {"initial_allocation": (1.0,)},
        {"initial_allocation": (1.0, -1.0)},
    ],
)
def test_matching_subject_rejects_invalid_parameters(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        MatchingSubject(**kwargs)  # type: ignore[arg-type]


def test_matching_subject_allocates_responses_by_allocation() -> None:
    # With no reinforcers the allocation stays fixed at 3:1, so the long-run
    # share of responses on key 0 is 0.75 whatever the stay bias.
    for stay in (0.0, 0.6):
        subject = MatchingSubject(initial_allocation=(3.0, 1.0), stay=stay, seed=5)
        share = _allocation(subject, 40_000)[0]
        assert share == pytest.approx(0.75, abs=0.02)


def test_matching_subject_stay_bias_reduces_switching() -> None:
    def switches(stay: float) -> int:
        subject = MatchingSubject(stay=stay, seed=9)
        keys = [subject.next_event()[0] for _ in range(5000)]
        return sum(1 for a, b in zip(keys, keys[1:]) if a != b)

    assert switches(0.8) < switches(0.0) / 2


def _respond_on(subject: MatchingSubject, key: int) -> None:
    while subject.next_event()[0] != key:
        pass


def test_reinforcer_raises_local_rate_and_allocation_of_that_key() -> None:
    subject = MatchingSubject(seed=3)
    _respond_on(subject, 1)
    before = subject.allocation
    subject.freeze_for_reinforcer(0.5)
    assert subject.local_rates[1] > subject.local_rates[0]
    assert subject.allocation[1] > before[1]
    assert sum(subject.allocation) == pytest.approx(1.0)


def test_unreinforced_responses_lower_local_rate() -> None:
    subject = MatchingSubject(learning_rate=0.2, seed=6)
    _respond_on(subject, 0)
    subject.freeze_for_reinforcer(0.5)
    reinforced = subject.local_rates[0]
    for _ in range(20):
        _respond_on(subject, 0)
    assert subject.local_rates[0] < reinforced


def test_allocation_moves_toward_richer_local_rate() -> None:
    # Key 1 pays every 5th response, key 0 every 20th: the allocation
    # shifts toward key 1 although nothing tells the subject the schedules.
    subject = MatchingSubject(learning_rate=0.05, step_size=0.05, seed=8)
    counts = [0, 0]
    for _ in range(20_000):
        key, _delta = subject.next_event()
        counts[key] += 1
        if counts[key] % (20 if key == 0 else 5) == 0:
            subject.freeze_for_reinforcer(0.5)
    assert subject.allocation[1] > 0.7


def test_allocation_never_falls_below_floor() -> None:
    subject = MatchingSubject(learning_rate=0.5, step_size=0.5, floor=0.05, seed=4)
    for _ in range(200):
        _respond_on(subject, 0)
        subject.freeze_for_reinforcer(0.5)
    assert min(subject.allocation) >= 0.05 - 1e-12


def test_matching_subject_drives_multi_virtual_operandum() -> None:
    clock = ManualClock()
    children = MultiVirtualOperandum(MatchingSubject(seed=1), clock).split()
    seen: set[int] = set()
    for _ in range(200):
        for child in children:
            for sample in child.poll():
                seen.add(sample.operandum_index)
    assert seen == {0, 1}
    assert clock.now > 0
