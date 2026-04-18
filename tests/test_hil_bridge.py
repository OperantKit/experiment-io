"""Unit tests for the HIL bridge backend.

A fake ``Popen``-like object is injected via ``HilBridge(process=...)``
so no real subprocess (and no Rust binary on disk) is needed.
"""

from __future__ import annotations

import io
import json

import pytest

from experiment_io.hw.backends.hil_bridge import (
    DEFAULT_BINARY,
    HilBridge,
    _resolve_binary,
)


class _FakeProcess:
    """Popen-like stub with in-memory stdin / stdout streams."""

    def __init__(self, stdout_lines: list[str] | None = None) -> None:
        self.stdin = io.StringIO()
        self.stdout = io.StringIO("".join((line + "\n") for line in (stdout_lines or [])))
        self.terminated = False

    def terminate(self) -> None:
        self.terminated = True


def test_resolve_binary_prefers_explicit_argument(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPERANTKIT_HIL_BINARY", "/from/env")
    assert _resolve_binary("/explicit") == "/explicit"


def test_resolve_binary_falls_back_to_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPERANTKIT_HIL_BINARY", "/from/env")
    assert _resolve_binary(None) == "/from/env"


def test_resolve_binary_falls_back_to_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPERANTKIT_HIL_BINARY", raising=False)
    assert _resolve_binary(None) == DEFAULT_BINARY


def test_start_writes_start_command_to_stdin() -> None:
    proc = _FakeProcess()
    bridge = HilBridge(process=proc, session_id="sess-1")
    bridge.start()
    line = proc.stdin.getvalue().strip()
    assert json.loads(line) == {"cmd": "start", "session_id": "sess-1"}


def test_deliver_writes_reinforcer_command() -> None:
    proc = _FakeProcess()
    bridge = HilBridge(process=proc)
    bridge.deliver(operandum=2, duration=0.5)
    line = proc.stdin.getvalue().strip()
    assert json.loads(line) == {"cmd": "reinforcer", "op": 2, "duration": 0.5}


def test_stop_sends_stop_then_terminates_process() -> None:
    proc = _FakeProcess()
    bridge = HilBridge(process=proc)
    bridge.stop()
    line = proc.stdin.getvalue().strip()
    assert json.loads(line) == {"cmd": "stop"}
    assert proc.terminated is True


def test_poll_yields_response_samples() -> None:
    proc = _FakeProcess(
        stdout_lines=[
            json.dumps({"event": "ready", "ts": 0.0}),
            json.dumps({"event": "response", "op": 1, "ts": 0.5}),
            json.dumps({"event": "reinforcer_done", "op": 1, "ts": 1.0}),
            json.dumps({"event": "response", "op": 0, "ts": 1.5}),
        ]
    )
    bridge = HilBridge(process=proc)
    samples = bridge.poll()
    assert [(s.operandum_index, s.timestamp) for s in samples] == [
        (1, 0.5),
        (0, 1.5),
    ]


def test_poll_tolerates_malformed_lines() -> None:
    proc = _FakeProcess(
        stdout_lines=[
            "not json",
            json.dumps({"event": "response", "op": "nan", "ts": "x"}),
            json.dumps({"event": "unknown_kind"}),
            json.dumps({"event": "error", "msg": "device failed"}),
            json.dumps({"event": "response", "op": 0, "ts": 2.0}),
        ]
    )
    bridge = HilBridge(process=proc)
    samples = bridge.poll()
    assert len(samples) == 1
    assert samples[0].operandum_index == 0
    assert samples[0].timestamp == 2.0
