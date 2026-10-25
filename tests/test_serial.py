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


class _PySerialLike:
    """Mimics the pyserial ``Serial`` surface the backend touches.

    ``readline()`` returns bytes and, like pyserial with ``timeout=0``,
    may return a line cut before its newline when the rest has not
    arrived yet. ``write()`` rejects ``str`` with the same ``TypeError``
    pyserial raises.
    """

    def __init__(self, chunks: list[bytes] | None = None) -> None:
        self._chunks = list(chunks or [])
        self.written = b""

    def feed(self, *chunks: bytes) -> None:
        self._chunks.extend(chunks)

    def readline(self) -> bytes:
        return self._chunks.pop(0) if self._chunks else b""

    def write(self, data: bytes) -> int:
        if isinstance(data, str):
            raise TypeError("unicode strings are not supported, please encode to bytes")
        self.written += bytes(data)
        return len(data)

    def flush(self) -> None:
        pass


def _response_line(ts: float) -> bytes:
    return (json.dumps({"op": 0, "ts": ts, "kind": "response"}) + "\n").encode()


def test_serial_reinforcer_writes_bytes_to_pyserial_like_stream() -> None:
    port = _PySerialLike()
    SerialReinforcer(port, operandum_index=0).deliver(0.05)
    assert json.loads(port.written.decode()) == {
        "cmd": "reinforcer",
        "op": 0,
        "duration": 0.05,
    }


def test_serial_operandum_reads_bytes_lines() -> None:
    port = _PySerialLike([_response_line(0.5), _response_line(1.0)])
    samples = SerialOperandum(port, operandum_index=0).poll()
    assert [s.timestamp for s in samples] == [0.5, 1.0]


def test_serial_operandum_keeps_a_line_split_across_polls() -> None:
    line = _response_line(0.5)
    port = _PySerialLike([line[:10]])
    op = SerialOperandum(port, operandum_index=0)
    assert op.poll() == []
    port.feed(line[10:])
    samples = op.poll()
    assert [s.timestamp for s in samples] == [0.5]


def test_serial_operandum_keeps_a_split_str_line() -> None:
    line = _response_line(0.5).decode()
    port = io.StringIO()
    op = SerialOperandum(port, operandum_index=0)
    port.write(line[:7])
    port.seek(0)
    assert op.poll() == []
    pos = port.tell()
    port.write(line[7:])
    port.seek(pos)
    assert [s.timestamp for s in op.poll()] == [0.5]
