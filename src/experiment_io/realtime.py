"""Opt-in time-constraint scheduling for the host loop on macOS.

``time_constraint(period)`` asks the macOS scheduler to treat the calling
thread as a periodic real-time thread (``THREAD_TIME_CONSTRAINT_POLICY``)
for the duration of a ``with`` block, and puts it back to the standard
policy when the block ends. Use it around :func:`experiment_io.drive` with
``period = poll_interval``::

    from experiment_io import drive
    from experiment_io.realtime import time_constraint

    with time_constraint(0.001) as rt:
        print(rt.applied, rt.reason)
        drive(runner, operanda, reinforcers, clock, poll_interval=0.001)

The policy is set with ``period = period``, ``computation = period / 5``,
``constraint = period`` and ``preemptible = 1``, converted to mach absolute
time units with ``mach_timebase_info``. No administrator rights are needed.
The kernel takes the policy away again from a thread that keeps running
past its computation time.

On other platforms, or when the call fails, nothing is changed and the
returned :class:`RealtimeResult` says why (``applied`` is ``False``). The
caller decides whether that matters.
"""

from __future__ import annotations

import ctypes
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

__all__ = [
    "THREAD_STANDARD_POLICY",
    "THREAD_TIME_CONSTRAINT_POLICY",
    "RealtimeResult",
    "time_constraint",
]

THREAD_STANDARD_POLICY = 1
THREAD_TIME_CONSTRAINT_POLICY = 2
_TIME_CONSTRAINT_COUNT = 4
_STANDARD_COUNT = 0
_LIBSYSTEM = "/usr/lib/libSystem.B.dylib"
_UINT32_MAX = 2**32 - 1


@dataclass(frozen=True)
class RealtimeResult:
    """Whether the time-constraint policy is in effect for the block."""

    applied: bool
    kern_return: int | None
    reason: str


class _Timebase(ctypes.Structure):
    _fields_ = [("numer", ctypes.c_uint32), ("denom", ctypes.c_uint32)]


class _TimeConstraint(ctypes.Structure):
    _fields_ = [
        ("period", ctypes.c_uint32),
        ("computation", ctypes.c_uint32),
        ("constraint", ctypes.c_uint32),
        ("preemptible", ctypes.c_int32),
    ]


def _load_libc() -> Any:
    libc = ctypes.CDLL(_LIBSYSTEM, use_errno=True)
    libc.mach_thread_self.restype = ctypes.c_uint32
    return libc


def _policy(period_s: float, timebase: _Timebase) -> _TimeConstraint:
    def to_abs(ns: float) -> int:
        value = int(ns * timebase.denom / timebase.numer)
        if value > _UINT32_MAX:
            raise ValueError(f"period {period_s} s does not fit in mach absolute time units")
        return value

    period_ns = period_s * 1e9
    return _TimeConstraint(to_abs(period_ns), to_abs(period_ns / 5), to_abs(period_ns), 1)


@contextmanager
def time_constraint(
    period_s: float,
    *,
    libc: Any | None = None,
    platform: str | None = None,
) -> Iterator[RealtimeResult]:
    """Run the ``with`` block on a time-constraint thread when possible.

    ``period_s`` is the loop period in seconds (for ``drive``, its
    ``poll_interval``). ``libc`` and ``platform`` replace the system library
    and ``sys.platform``; they exist for tests.
    """
    if period_s <= 0:
        raise ValueError("period_s must be > 0")
    if (platform or sys.platform) != "darwin":
        yield RealtimeResult(False, None, "time-constraint threads are only set on macOS")
        return
    try:
        lib = libc if libc is not None else _load_libc()
    except OSError as exc:
        yield RealtimeResult(False, None, f"could not load {_LIBSYSTEM}: {exc}")
        return

    timebase = _Timebase()
    lib.mach_timebase_info(ctypes.byref(timebase))
    if timebase.numer == 0 or timebase.denom == 0:
        yield RealtimeResult(False, None, "mach_timebase_info returned zero")
        return
    policy = _policy(period_s, timebase)
    thread = lib.mach_thread_self()
    kr = int(
        lib.thread_policy_set(
            thread, THREAD_TIME_CONSTRAINT_POLICY, ctypes.byref(policy), _TIME_CONSTRAINT_COUNT
        )
    )
    if kr != 0:
        yield RealtimeResult(False, kr, f"thread_policy_set returned {kr}")
        return
    try:
        yield RealtimeResult(True, kr, "")
    finally:
        lib.thread_policy_set(
            thread, THREAD_STANDARD_POLICY, ctypes.byref(ctypes.c_int32(0)), _STANDARD_COUNT
        )
