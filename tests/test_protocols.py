"""Tests for the HAL protocol contracts."""

from __future__ import annotations

import time

from experiment_io.hw import Clock, Operandum, Reinforcer, ResponseSample


def test_response_sample_is_frozen() -> None:
    sample = ResponseSample(operandum_index=2, timestamp=1.5)
    assert sample.operandum_index == 2
    assert sample.timestamp == 1.5
    assert sample.metadata is None
    try:
        sample.timestamp = 9.0  # type: ignore[misc]
    except Exception:
        return
    raise AssertionError("ResponseSample must be frozen")


def test_response_sample_metadata() -> None:
    sample = ResponseSample(operandum_index=0, timestamp=0.0, metadata={"pin": 7})
    assert sample.metadata == {"pin": 7}


def test_real_clock_satisfies_clock_protocol() -> None:
    # time.monotonic is a callable returning float and should be Clock-compatible.
    clock: Clock = time.monotonic
    assert isinstance(clock(), float)


def test_operandum_runtime_checkable() -> None:
    class _Stub:
        operandum_index = 0

        def poll(self) -> list[ResponseSample]:
            return []

        def close(self) -> None:
            pass

    assert isinstance(_Stub(), Operandum)


def test_reinforcer_runtime_checkable() -> None:
    class _Stub:
        operandum_index = 0

        def deliver(self, duration: float) -> None:
            pass

        def close(self) -> None:
            pass

    assert isinstance(_Stub(), Reinforcer)
