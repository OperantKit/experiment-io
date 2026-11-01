"""Drive a device that decides the contingency itself.

Where the contingency is decided is chosen by the researcher: on the host
(the session runner steps the schedule; :func:`experiment_io.drive`) or on
the device (this module). Nothing switches between them on its own. A
schedule the device cannot decide is refused before it starts — before the
caller's ``on_start`` runs and before ``start`` is sent — with a message
that says how to run it on the host instead.

The device speaks JSON lines. Commands: ``load`` (``schedule: {type,
params}`` in the conformance fixture form, ``reinforcer: {op, duration}``,
``stop_after: {reinforcers}``), ``start``, ``stop``, ``report`` and
``probe``. Events: ``loaded {firmware}``, ``error {code}``, ``started {ts,
seq}``, ``response``, ``reinforcer_start``, ``reinforcer_end``,
``stopped``, ``report`` and ``outcome``. Session time is the device clock:
``session_start`` is the ``ts`` of ``started`` and every event carries the
device's ``ts``. A log of such a run says ``clock_type = "device"`` and
``decision = "device"``; :func:`check_decision_clock` refuses a pair that
does not belong together.

This module knows the wire format only. Turning a schedule expression into
a ``{type, params}`` spec, and writing the events to a log, are the
caller's (``on_start`` / ``on_event``).
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "DECISIONS",
    "DEVICE_CLOCK_TYPE",
    "SEEDED_ON_DEVICE",
    "SUPPORTED_ON_DEVICE",
    "DeviceEvent",
    "DeviceRun",
    "DeviceUnsupportedError",
    "ProbeResult",
    "check_decision_clock",
    "device_schedule_from_spec",
    "drive_device",
    "probe_fixture",
]

DECISIONS = ("host", "device")
DEVICE_CLOCK_TYPE = "device"
SUPPORTED_ON_DEVICE = ("CRF", "EXT", "FR", "VR", "RR", "FI", "VI", "RI", "FT", "VT", "RT")
SEEDED_ON_DEVICE = ("VR", "RR", "VI", "RI", "VT", "RT")
EVENT_KINDS = ("response", "reinforcer_start", "reinforcer_end")
_REPLY_TIMEOUT_S = 2.0
_POLL_S = 0.001
_SETTLE_S = 0.2

Port = Any
Clock = Callable[[], float]


class DeviceUnsupportedError(ValueError):
    """The schedule cannot be decided on the device; nothing was started."""


def _host_hint(label: str, reason: str) -> str:
    supported = ", ".join(SUPPORTED_ON_DEVICE)
    return (
        f'schedule "{label}" cannot run on the device ({reason}; supported: {supported}); '
        "run it on the host with --decision host"
    )


def check_decision_clock(decision: str, clock_type: str) -> None:
    """Refuse a header whose decision and clock type do not belong together."""
    if decision not in DECISIONS:
        raise ValueError(f"decision must be one of {DECISIONS}, not {decision!r}")
    if decision == "device" and clock_type != DEVICE_CLOCK_TYPE:
        raise ValueError('decision = "device" needs clock_type = "device"')
    if decision == "host" and clock_type == DEVICE_CLOCK_TYPE:
        raise ValueError('decision = "host" runs on the host clock, not clock_type = "device"')


def device_schedule_from_spec(spec: Mapping[str, Any], label: str) -> dict[str, Any]:
    """Return the ``load`` schedule for a conformance spec, or refuse it.

    ``label`` names the schedule in the message (the expression the
    researcher wrote). Every atomic schedule in :data:`SUPPORTED_ON_DEVICE`
    is kept; FR's ``n`` becomes an integer. A random one
    (:data:`SEEDED_ON_DEVICE`) needs ``params.seed``, because the device has
    no entropy source: without it the schedule is refused here, before
    anything starts, rather than by the device.
    """
    kind = spec.get("type")
    if kind not in SUPPORTED_ON_DEVICE:
        raise DeviceUnsupportedError(
            _host_hint(label, f"{kind} is not an atomic schedule this firmware decides")
        )
    params = dict(spec.get("params") or {})
    if kind == "FR":
        n = float(params.get("n", 0))
        if not (n >= 1 and n.is_integer()):
            raise DeviceUnsupportedError(_host_hint(label, "FR needs an integer n >= 1"))
        params["n"] = int(n)
    if kind in SEEDED_ON_DEVICE and params.get("seed") is None:
        raise DeviceUnsupportedError(
            f'schedule "{label}" draws random numbers and the device needs a seed for it; '
            'add @algorithm("fleshler-hoffman", seed=N) to the schedule, '
            "or run it on the host with --decision host"
        )
    return {"type": kind, "params": params}


def _send(port: Port, command: dict[str, Any]) -> None:
    port.write((json.dumps(command) + "\n").encode())
    port.flush()


def _wait(clock: Clock, seconds: float) -> None:
    advance = getattr(clock, "advance", None)
    if callable(advance):
        advance(seconds)
    else:
        time.sleep(seconds)


def _read_json(port: Port) -> dict[str, Any] | None:
    raw = port.readline()
    text = (raw.decode(errors="replace") if isinstance(raw, bytes) else str(raw)).strip()
    if not text:
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _await(port: Port, clock: Clock, kinds: set[str], timeout_s: float) -> dict[str, Any] | None:
    deadline = clock() + timeout_s
    while clock() < deadline:
        data = _read_json(port)
        if data is None:
            _wait(clock, _POLL_S)
            continue
        if data.get("kind") in kinds:
            return data
    return None


def _quiet(port: Port, clock: Clock) -> None:
    _send(port, {"cmd": "stop"})
    _wait(clock, _SETTLE_S)
    reset = getattr(port, "reset_input_buffer", None)
    if callable(reset):
        reset()


def _load(port: Port, clock: Clock, label: str, command: dict[str, Any]) -> dict[str, Any]:
    _send(port, command)
    reply = _await(port, clock, {"loaded", "error"}, _REPLY_TIMEOUT_S)
    if reply is None:
        raise OSError("the device did not answer load")
    if reply["kind"] == "error":
        raise DeviceUnsupportedError(_host_hint(label, f"device error {reply.get('code')}"))
    return reply


@dataclass(frozen=True)
class DeviceEvent:
    """One ``response`` / ``reinforcer_start`` / ``reinforcer_end`` from the device.

    ``ts`` is on the device clock. ``index`` counts from 1: the n-th
    response, or the n-th reinforcer for both its start and its end.
    """

    kind: str
    ts: float
    index: int
    op: int = 0
    seq: int | None = None


@dataclass(frozen=True)
class DeviceRun:
    """Outcome of one run decided on the device."""

    label: str
    target_reinforcers: int
    reinforcer_duration_s: float
    finished: bool
    n_reinforcers: int
    n_responses: int
    elapsed_s: float
    seq_gaps: int
    decision: str = "device"
    firmware: str | None = None
    session_start: float | None = None
    host_minus_device_ms: list[float] = field(default_factory=list)
    device: dict[str, Any] | None = None


def drive_device(
    open_port: Callable[[], Port],
    schedule: Mapping[str, Any],
    *,
    reinforcers: int,
    reinforcer_duration: float = 0.05,
    clock: Clock | None = None,
    on_start: Callable[[float, str | None], None] | None = None,
    on_event: Callable[[DeviceEvent], None] | None = None,
    max_duration: float = 600.0,
    report_timeout: float = 5.0,
    label: str | None = None,
) -> DeviceRun:
    """Load ``schedule`` on the device, run until ``reinforcers``, and fetch its report.

    ``schedule`` is a conformance spec ``{type, params}``; it is checked with
    :func:`device_schedule_from_spec` before the port is opened.
    ``on_start(session_start, firmware)`` runs once the device has started,
    before any event; ``on_event`` receives each response and reinforcer
    edge in order. Raises :class:`DeviceUnsupportedError` before anything
    starts when the device cannot decide the schedule.
    """
    if reinforcers < 1:
        raise ValueError("reinforcers must be >= 1")
    name = label if label is not None else str(schedule.get("type"))
    load_schedule = device_schedule_from_spec(schedule, name)  # before the port is touched
    check_decision_clock("device", DEVICE_CLOCK_TYPE)
    clock = clock or time.monotonic

    port = open_port()
    try:
        _quiet(port, clock)
        loaded = _load(
            port,
            clock,
            name,
            {
                "cmd": "load",
                "schedule": load_schedule,
                "reinforcer": {"op": 0, "duration": reinforcer_duration},
                "stop_after": {"reinforcers": reinforcers},
            },
        )
        firmware = loaded.get("firmware")
        firmware = None if firmware is None else str(firmware)
        _send(port, {"cmd": "start"})
        host_start = clock()
        started = _await(port, clock, {"started"}, _REPLY_TIMEOUT_S)
        if started is None:
            raise OSError("the device did not answer start")
        session_start = float(started["ts"])
        last_seq = int(started.get("seq", 0))
        if on_start is not None:
            on_start(session_start, firmware)

        n_resp = n_sr = gaps = 0
        lags: list[float] = []
        stopped = False
        deadline = host_start + max_duration
        while clock() < deadline:
            data = _read_json(port)
            if data is None:
                _wait(clock, _POLL_S)
                continue
            kind = data.get("kind")
            seq = data.get("seq")
            if isinstance(seq, int):
                gaps += max(0, seq - last_seq - 1)
                last_seq = seq
            if kind == "stopped":
                stopped = True
                break
            if kind not in EVENT_KINDS:
                continue
            ts = float(data["ts"])
            if kind == "response":
                n_resp += 1
                index = n_resp
                lags.append(round((clock() - host_start - (ts - session_start)) * 1e3, 3))
            elif kind == "reinforcer_start":
                n_sr += 1
                index = n_sr
            else:
                index = n_sr
            if on_event is not None:
                event_seq = seq if isinstance(seq, int) else None
                op = int(data.get("op", 0))
                on_event(DeviceEvent(kind=kind, ts=ts, index=index, op=op, seq=event_seq))
            if kind == "reinforcer_end" and n_sr >= reinforcers:
                _send(port, {"cmd": "stop"})  # the device stops itself too
        elapsed = clock() - host_start
        if not stopped:
            _send(port, {"cmd": "stop"})
        _send(port, {"cmd": "report"})
        report = _await(port, clock, {"report"}, report_timeout)
    finally:
        port.close()

    return DeviceRun(
        label=name,
        target_reinforcers=reinforcers,
        reinforcer_duration_s=reinforcer_duration,
        finished=n_sr >= reinforcers,
        n_reinforcers=n_sr,
        n_responses=n_resp,
        elapsed_s=round(elapsed, 3),
        seq_gaps=gaps,
        firmware=firmware,
        session_start=session_start,
        host_minus_device_ms=lags,
        device=report,
    )


@dataclass(frozen=True)
class ProbeResult:
    """Outcome of one conformance fixture sent step by step with ``probe``."""

    name: str
    n_steps: int
    mismatches: list[dict[str, Any]]


def probe_fixture(
    port: Port, fixture: Mapping[str, Any], *, clock: Clock | None = None
) -> ProbeResult:
    """Load the fixture's schedule and compare each ``probe`` outcome with ``expect``."""
    clock = clock or time.monotonic
    name = str(fixture.get("name", "?"))
    _quiet(port, clock)
    _load(port, clock, name, {"cmd": "load", "schedule": fixture["schedule"]})
    mismatches: list[dict[str, Any]] = []
    for i, step in enumerate(fixture["steps"]):
        _send(port, {"cmd": "probe", "now": step["now"], "event": step["event"]})
        reply = _await(port, clock, {"outcome", "error"}, _REPLY_TIMEOUT_S)
        got = None if reply is None else {k: v for k, v in reply.items() if k != "kind"}
        if got != step["expect"]:
            mismatches.append({"step": i, "expect": step["expect"], "got": got})
    return ProbeResult(name=name, n_steps=len(fixture["steps"]), mismatches=mismatches)
