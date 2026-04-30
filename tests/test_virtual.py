"""Unit tests for the in-memory virtual backend."""

from __future__ import annotations

from experiment_io.hw import Operandum, Reinforcer
from experiment_io.hw.backends.virtual import (
    ManualClock,
    MultiVirtualOperandum,
    MultiVirtualSubject,
    RecordingReinforcer,
    VirtualOperandum,
    VirtualSubject,
)

# ---------------------------------------------------------------------------
# ManualClock
# ---------------------------------------------------------------------------


def test_manual_clock_starts_at_zero() -> None:
    clock = ManualClock()
    assert clock() == 0.0
    assert clock.now == 0.0


def test_manual_clock_advance_increments_now() -> None:
    clock = ManualClock()
    assert clock.advance(0.5) == 0.5
    assert clock.advance(0.25) == 0.75
    assert clock() == 0.75


# ---------------------------------------------------------------------------
# VirtualSubject
# ---------------------------------------------------------------------------


def test_virtual_subject_is_deterministic_under_seed() -> None:
    a = VirtualSubject(rate_hz=2.0, seed=42)
    b = VirtualSubject(rate_hz=2.0, seed=42)
    seq_a = [a.next_irt() for _ in range(10)]
    seq_b = [b.next_irt() for _ in range(10)]
    assert seq_a == seq_b


def test_virtual_subject_irts_are_positive() -> None:
    subject = VirtualSubject(rate_hz=1.0, seed=1)
    for _ in range(50):
        assert subject.next_irt() > 0


# ---------------------------------------------------------------------------
# VirtualOperandum
# ---------------------------------------------------------------------------


def test_virtual_operandum_satisfies_operandum_protocol() -> None:
    clock = ManualClock()
    op = VirtualOperandum(0, VirtualSubject(rate_hz=2.0, seed=0), clock)
    assert isinstance(op, Operandum)


def test_virtual_operandum_advances_clock_and_emits_sample() -> None:
    clock = ManualClock()
    op = VirtualOperandum(3, VirtualSubject(rate_hz=2.0, seed=0), clock)
    samples = op.poll()
    assert len(samples) == 1
    assert samples[0].operandum_index == 3
    assert samples[0].timestamp == clock.now
    assert clock.now > 0


# ---------------------------------------------------------------------------
# MultiVirtualSubject / MultiVirtualOperandum
# ---------------------------------------------------------------------------


def test_multi_virtual_subject_validates_lengths() -> None:
    try:
        MultiVirtualSubject(rates_hz=[1.0, 2.0], seeds=[0])
    except ValueError:
        return
    raise AssertionError("MultiVirtualSubject must reject mismatched lengths")


def test_multi_virtual_subject_emits_events_for_known_indices() -> None:
    mvs = MultiVirtualSubject(rates_hz=[1.0, 2.0], seeds=[0, 1])
    seen_indices: set[int] = set()
    for _ in range(20):
        idx, delta = mvs.next_event()
        assert 0 <= idx < 2
        assert delta >= 0.0
        seen_indices.add(idx)
    assert seen_indices == {0, 1}


def test_multi_virtual_subject_freeze_for_reinforcer_shifts_schedule() -> None:
    mvs = MultiVirtualSubject(rates_hz=[1.0, 1.0], seeds=[0, 1])
    before = list(mvs._next_times)
    mvs.freeze_for_reinforcer(0.5)
    after = list(mvs._next_times)
    assert all(b + 0.5 == a for b, a in zip(before, after, strict=True))


def test_multi_virtual_operandum_split_yields_per_index_polls() -> None:
    clock = ManualClock()
    mvs = MultiVirtualSubject(rates_hz=[1.0, 2.0], seeds=[0, 1])
    parent = MultiVirtualOperandum(mvs, clock)
    children = parent.split()
    assert len(children) == 2

    # Round-robin polling across both children should eventually drain one
    # sample from each operandum index without raising.
    seen: set[int] = set()
    for _ in range(50):
        for child in children:
            for sample in child.poll():
                seen.add(sample.operandum_index)
        if seen == {0, 1}:
            break
    assert seen == {0, 1}


# ---------------------------------------------------------------------------
# RecordingReinforcer
# ---------------------------------------------------------------------------


def test_recording_reinforcer_satisfies_reinforcer_protocol() -> None:
    rein = RecordingReinforcer(operandum_index=1)
    assert isinstance(rein, Reinforcer)


def test_recording_reinforcer_logs_each_delivery() -> None:
    rein = RecordingReinforcer(operandum_index=1)
    rein.deliver(0.5)
    rein.deliver(1.25)
    assert rein.calls == [(1, 0.5), (1, 1.25)]
