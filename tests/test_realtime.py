"""Tests for ``experiment_io.realtime`` with the mach calls replaced."""

from __future__ import annotations

import ctypes
from typing import Any

import pytest

from experiment_io import realtime
from experiment_io.realtime import RealtimeResult, time_constraint


class FakeLibc:
    """Stands in for libSystem: records ``thread_policy_set`` calls."""

    def __init__(self, numer: int = 125, denom: int = 3, kern_return: int = 0) -> None:
        self.numer = numer
        self.denom = denom
        self.kern_return = kern_return
        self.calls: list[dict[str, Any]] = []
        self.mach_thread_self = self._mach_thread_self
        self.mach_timebase_info = self._mach_timebase_info
        self.thread_policy_set = self._thread_policy_set

    def _mach_thread_self(self) -> int:
        return 4242

    def _mach_timebase_info(self, ref: Any) -> int:
        tb = ref._obj
        tb.numer = self.numer
        tb.denom = self.denom
        return 0

    def _thread_policy_set(self, thread: int, flavor: int, ref: Any, count: int) -> int:
        policy = ref._obj
        if isinstance(policy, ctypes.Structure):
            values = {name: getattr(policy, name) for name, _ in policy._fields_}
        else:
            values = {}
        self.calls.append({"thread": thread, "flavor": flavor, "count": count, **values})
        return self.kern_return if flavor == realtime.THREAD_TIME_CONSTRAINT_POLICY else 0


def test_sets_time_constraint_policy_in_mach_units() -> None:
    libc = FakeLibc(numer=125, denom=3)
    with time_constraint(0.001, libc=libc, platform="darwin") as result:
        assert result == RealtimeResult(applied=True, kern_return=0, reason="")
    first = libc.calls[0]
    assert first["thread"] == 4242
    assert first["flavor"] == 2
    assert first["count"] == 4
    # 1 ms = 1_000_000 ns; ns * denom / numer = 24_000 mach units.
    assert first["period"] == 24_000
    assert first["computation"] == 4_800
    assert first["constraint"] == 24_000
    assert first["preemptible"] == 1


def test_identity_timebase_keeps_nanoseconds() -> None:
    libc = FakeLibc(numer=1, denom=1)
    with time_constraint(0.002, libc=libc, platform="darwin"):
        pass
    first = libc.calls[0]
    assert first["period"] == 2_000_000
    assert first["computation"] == 400_000
    assert first["constraint"] == 2_000_000


def test_restores_standard_policy_on_exit() -> None:
    libc = FakeLibc()
    with time_constraint(0.001, libc=libc, platform="darwin"):
        assert len(libc.calls) == 1
    assert len(libc.calls) == 2
    assert libc.calls[1]["flavor"] == realtime.THREAD_STANDARD_POLICY
    assert libc.calls[1]["count"] == 0
    assert libc.calls[1]["thread"] == 4242


def test_restores_even_when_body_raises() -> None:
    libc = FakeLibc()
    with pytest.raises(RuntimeError), time_constraint(0.001, libc=libc, platform="darwin"):
        raise RuntimeError("boom")
    assert [c["flavor"] for c in libc.calls] == [2, realtime.THREAD_STANDARD_POLICY]


def test_failed_call_reports_kern_return_and_does_not_restore() -> None:
    libc = FakeLibc(kern_return=4)
    with time_constraint(0.001, libc=libc, platform="darwin") as result:
        assert result.applied is False
        assert result.kern_return == 4
        assert "4" in result.reason
    assert len(libc.calls) == 1


def test_non_macos_is_a_no_op() -> None:
    libc = FakeLibc()
    with time_constraint(0.001, libc=libc, platform="linux") as result:
        assert result.applied is False
        assert result.kern_return is None
        assert "macOS" in result.reason
    assert libc.calls == []


def test_unloadable_libc_is_a_no_op(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*_args: Any, **_kwargs: Any) -> Any:
        raise OSError("no libSystem")

    monkeypatch.setattr(realtime.ctypes, "CDLL", fail)
    with time_constraint(0.001, platform="darwin") as result:
        assert result.applied is False
        assert "no libSystem" in result.reason


@pytest.mark.parametrize("period", [0.0, -0.001])
def test_rejects_non_positive_period(period: float) -> None:
    with pytest.raises(ValueError), time_constraint(period, libc=FakeLibc(), platform="darwin"):
        pass


def test_rejects_period_that_overflows_mach_units() -> None:
    libc = FakeLibc(numer=1, denom=1)
    with pytest.raises(ValueError), time_constraint(1_000.0, libc=libc, platform="darwin"):
        pass
