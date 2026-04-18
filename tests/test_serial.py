"""Unit tests for the serial JSONL backend.

These tests use ``io.StringIO`` so no real serial port or pyserial
install is required.
"""

from __future__ import annotations

import io
import json

import pytest

from experiment_io.hw.backends.serial import (
    SerialOperandum,
    SerialReinforcer,
    open_serial,
)


def _stream(*lines: str) -> io.StringIO:
    return io.StringIO("".join(line + "\n" for line in lines))


def test_serial_operandum_emits_response_for_matching_index() -> None:
    stream = _stream(
        json.dumps({"op": 0, "ts": 0.5, "kind": "response"}),
        json.dumps({"op": 0, "ts": 1.0, "kind": "response"}),
    )
    op = SerialOperandum(stream, operandum_index=0)
    samples = op.poll()
    assert len(samples) == 2
    assert [s.timestamp for s in samples] == [0.5, 1.0]


def test_serial_operandum_filters_other_indices() -> None:
    stream = _stream(
        json.dumps({"op": 1, "ts": 0.5, "kind": "response"}),
        json.dumps({"op": 0, "ts": 1.0, "kind": "response"}),
    )
    op = SerialOperandum(stream, operandum_index=0)
    samples = op.poll()
    assert len(samples) == 1
    assert samples[0].operandum_index == 0
    assert samples[0].timestamp == 1.0


def test_serial_operandum_skips_non_response_kinds() -> None:
    stream = _stream(
        json.dumps({"op": 0, "ts": 0.0, "kind": "ready"}),
        json.dumps({"op": 0, "ts": 0.5, "kind": "response"}),
    )
    op = SerialOperandum(stream, operandum_index=0)
    samples = op.poll()
    assert len(samples) == 1
    assert samples[0].timestamp == 0.5


def test_serial_operandum_tolerates_malformed_lines() -> None:
    stream = _stream(
        "not json at all",
        json.dumps([1, 2, 3]),  # not an object
        json.dumps({"op": "nan", "ts": 0.5, "kind": "response"}),
        json.dumps({"op": 0, "ts": 1.0, "kind": "response"}),
    )
    op = SerialOperandum(stream, operandum_index=0)
    samples = op.poll()
    assert len(samples) == 1
    assert samples[0].timestamp == 1.0


def test_serial_reinforcer_writes_json_command_line() -> None:
    stream = io.StringIO()
    rein = SerialReinforcer(stream, operandum_index=2)
    rein.deliver(0.75)
    rein.deliver(1.0)

    written = stream.getvalue().strip().splitlines()
    assert len(written) == 2
    cmd0 = json.loads(written[0])
    cmd1 = json.loads(written[1])
    assert cmd0 == {"cmd": "reinforcer", "op": 2, "duration": 0.75}
    assert cmd1 == {"cmd": "reinforcer", "op": 2, "duration": 1.0}


def test_open_serial_raises_helpful_import_error_when_pyserial_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import builtins

    real_import = builtins.__import__

    def fake_import(name: str, *args: object, **kwargs: object) -> object:
        if name == "serial":
            raise ImportError("simulated missing pyserial")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(ImportError, match="pyserial"):
        open_serial("/dev/null")
