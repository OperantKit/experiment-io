"""Driving a device that decides the contingency itself (``experiment_io.device``).

The fake port speaks the device-mode line protocol: it answers ``load`` with
``loaded`` (or ``error`` for a type it does not know), and after ``start``
presses every 300 ms on its own clock, deciding CRF / FR itself and
reporting ``response`` / ``reinforcer_start`` / ``reinforcer_end`` /
``stopped`` with a ``seq`` that goes up by one.
"""

from __future__ import annotations

import json

import pytest

from experiment_io.device import (
    DECISIONS,
    DEVICE_CLOCK_TYPE,
    SEEDED_ON_DEVICE,
    SUPPORTED_ON_DEVICE,
    DeviceEvent,
    DeviceUnsupportedError,
    check_decision_clock,
    device_schedule_from_spec,
    drive_device,
)
from experiment_io.hw.backends.virtual import ManualClock

_IRT = 0.3
_EPOCH = 500.0


class _FakeDevicePort:
    def __init__(self, clock: ManualClock, *, skip_seq_at: int | None = None) -> None:
        self.clock = clock
        self.commands: list[dict[str, object]] = []
        self._out: list[tuple[float, bytes]] = []
        self._buffer = b""
        self._seq = 0
        self._skip_seq_at = skip_seq_at
        self._n = 1
        self._count = 0
        self._running = False
        self._duration = 0.05
        self._stop_after = -1
        self._next_press: float | None = None
        self._feeder_off: float | None = None
        self.n_responses = 0
        self.n_reinforcers = 0
        self.closed = 0

    def _ts(self, t: float) -> float:
        return round(t + _EPOCH, 6)

    def _say(self, t: float, obj: dict[str, object], with_seq: bool = True) -> None:
        if with_seq:
            if self._skip_seq_at is not None and self._seq == self._skip_seq_at:
                self._seq += 1
            obj = {**obj, "seq": self._seq}
            self._seq += 1
        self._out.append((t + 0.002, (json.dumps(obj) + "\n").encode()))

    def _advance(self) -> None:
        now = self.clock()
        while self._running:
            due = [t for t in (self._next_press, self._feeder_off) if t is not None]
            if not due or min(due) > now:
                break
            t = min(due)
            if self._feeder_off is not None and t == self._feeder_off:
                self._feeder_off = None
                self._say(t, {"kind": "reinforcer_end", "op": 0, "ts": self._ts(t)})
                if 0 < self._stop_after <= self.n_reinforcers:
                    self._stop(t)
                continue
            self.n_responses += 1
            self._count += 1
            reinforced = self._count >= self._n
            self._say(t, {"kind": "response", "op": 0, "ts": self._ts(t), "reinforced": reinforced})
            if reinforced:
                self._count = 0
                self.n_reinforcers += 1
                self._say(t, {"kind": "reinforcer_start", "op": 0, "ts": self._ts(t)})
                self._feeder_off = t + self._duration
                self._next_press = t + self._duration + _IRT
            else:
                self._next_press = t + _IRT

    def _stop(self, t: float) -> None:
        self._running = False
        self._next_press = None
        self._say(t, {"kind": "stopped", "ts": self._ts(t)})

    def readline(self) -> bytes:
        self._advance()
        now = self.clock()
        ready = sorted((x for x in self._out if x[0] <= now), key=lambda x: x[0])
        self._out = [x for x in self._out if x[0] > now]
        for _, raw in ready:
            self._buffer += raw
        if b"\n" not in self._buffer:
            return b""
        line, self._buffer = self._buffer.split(b"\n", 1)
        return line + b"\n"

    def write(self, data: bytes) -> int:
        now = self.clock()
        cmd = json.loads(data.decode())
        self.commands.append(cmd)
        kind = cmd.get("cmd")
        if kind == "load":
            sched = cmd["schedule"]
            if sched["type"] not in ("CRF", "FR"):
                self._say(now, {"kind": "error", "code": "unsupported_schedule"}, False)
                return len(data)
            self._n = int(sched["params"].get("n", 1))
            self._duration = float(cmd["reinforcer"]["duration"])
            self._stop_after = int(cmd["stop_after"]["reinforcers"])
            self._say(now, {"kind": "loaded", "firmware": "fake-device"}, False)
        elif kind == "start":
            self._seq = 0
            self._running = True
            self._say(now, {"kind": "started", "ts": self._ts(now)})
            self._next_press = now + _IRT
        elif kind == "stop":
            if self._running:
                self._stop(now)
        elif kind == "report":
            self._say(now, {"kind": "report", "n": self.n_reinforcers}, False)
        return len(data)

    def flush(self) -> None:
        pass

    def reset_input_buffer(self) -> None:
        self._advance()
        self._out = []
        self._buffer = b""

    def close(self) -> None:
        self.closed += 1


def test_constants_name_the_two_decisions_and_the_device_clock():
    assert DECISIONS == ("host", "device")
    assert DEVICE_CLOCK_TYPE == "device"
    assert set(SEEDED_ON_DEVICE) <= set(SUPPORTED_ON_DEVICE)


def test_decision_and_clock_must_agree():
    check_decision_clock("device", "device")
    check_decision_clock("host", "monotonic")
    with pytest.raises(ValueError):
        check_decision_clock("device", "monotonic")
    with pytest.raises(ValueError):
        check_decision_clock("host", "device")
    with pytest.raises(ValueError):
        check_decision_clock("auto", "device")


@pytest.mark.parametrize(
    ("spec", "expected"),
    [
        ({"type": "CRF", "params": {}}, {"type": "CRF", "params": {}}),
        ({"type": "FR", "params": {"n": 5.0}}, {"type": "FR", "params": {"n": 5}}),
        (
            {"type": "VI", "params": {"mean_interval": 30.0, "seed": 7}},
            {"type": "VI", "params": {"mean_interval": 30.0, "seed": 7}},
        ),
    ],
)
def test_device_schedule_from_spec_keeps_what_the_device_decides(spec, expected):
    assert device_schedule_from_spec(spec, "x") == expected


@pytest.mark.parametrize(
    "spec",
    [
        {"type": "Conc", "params": {}},
        {"type": "FR", "params": {"n": 2.5}},
        {"type": "FR", "params": {"n": 0}},
    ],
)
def test_device_schedule_from_spec_refuses_the_rest_with_the_host_fix(spec):
    with pytest.raises(DeviceUnsupportedError, match="--decision host") as err:
        device_schedule_from_spec(spec, "LABEL")
    assert "LABEL" in str(err.value)


@pytest.mark.parametrize("kind", SEEDED_ON_DEVICE)
def test_random_schedule_without_seed_is_refused(kind):
    with pytest.raises(DeviceUnsupportedError) as err:
        device_schedule_from_spec({"type": kind, "params": {"mean_interval": 5.0}}, "R")
    msg = str(err.value)
    assert "seed" in msg
    assert "--decision host" in msg


def test_drive_device_reports_events_on_the_device_clock():
    clock = ManualClock()
    port = _FakeDevicePort(clock)
    starts: list[tuple[float, str | None]] = []
    events: list[DeviceEvent] = []

    run = drive_device(
        lambda: port,
        {"type": "FR", "params": {"n": 3}},
        reinforcers=2,
        reinforcer_duration=0.05,
        clock=clock,
        on_start=lambda start, firmware: starts.append((start, firmware)),
        on_event=events.append,
        label="FR 3",
    )

    assert run.finished
    assert run.n_reinforcers == 2
    assert run.n_responses == 6
    assert run.seq_gaps == 0
    assert run.firmware == "fake-device"
    assert run.decision == "device"
    assert run.label == "FR 3"
    assert run.device == {"kind": "report", "n": 2}
    assert len(starts) == 1
    session_start, firmware = starts[0]
    assert firmware == "fake-device"
    assert session_start == run.session_start
    assert session_start >= _EPOCH
    kinds = [e.kind for e in events]
    assert kinds.count("response") == 6
    assert kinds.count("reinforcer_start") == 2
    assert kinds.count("reinforcer_end") == 2
    assert [e.index for e in events if e.kind == "response"] == [1, 2, 3, 4, 5, 6]
    assert [e.index for e in events if e.kind == "reinforcer_end"] == [1, 2]
    times = [e.ts for e in events]
    assert times == sorted(times)
    assert min(times) >= session_start
    load = next(c for c in port.commands if c.get("cmd") == "load")
    assert load == {
        "cmd": "load",
        "schedule": {"type": "FR", "params": {"n": 3}},
        "reinforcer": {"op": 0, "duration": 0.05},
        "stop_after": {"reinforcers": 2},
    }
    sent = [c["cmd"] for c in port.commands]
    assert sent.index("load") < sent.index("start") < sent.index("report")
    assert port.closed == 1


def test_drive_device_counts_missing_seq():
    clock = ManualClock()
    port = _FakeDevicePort(clock, skip_seq_at=3)

    run = drive_device(lambda: port, {"type": "CRF", "params": {}}, reinforcers=2, clock=clock)

    assert run.finished
    assert run.seq_gaps == 1


def test_a_load_error_stops_before_start_and_before_on_start():
    clock = ManualClock()
    port = _FakeDevicePort(clock)
    starts: list[object] = []

    with pytest.raises(DeviceUnsupportedError, match="--decision host"):
        drive_device(
            lambda: port,
            {"type": "VI", "params": {"mean_interval": 5.0, "seed": 1}},
            reinforcers=1,
            clock=clock,
            on_start=lambda *a: starts.append(a),
        )
    assert starts == []
    assert not any(c.get("cmd") == "start" for c in port.commands)
    assert port.closed == 1


def test_an_unsupported_schedule_never_opens_the_port():
    opened: list[int] = []

    def open_port() -> _FakeDevicePort:
        opened.append(1)
        return _FakeDevicePort(ManualClock())

    with pytest.raises(DeviceUnsupportedError):
        drive_device(open_port, {"type": "Conc", "params": {}}, reinforcers=1)
    with pytest.raises(ValueError):
        drive_device(open_port, {"type": "CRF", "params": {}}, reinforcers=0)
    assert opened == []
