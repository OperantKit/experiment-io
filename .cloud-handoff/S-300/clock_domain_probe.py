"""S-300 probe: device clock vs host tick (Non-monotonic time) in ``drive``.

Runs the real ``SessionRunner`` + contingency-py schedules through the
unmodified ``experiment_io.drive`` with a fake serial-like device whose
timestamps come from its own clock and reach the host after a
transmission delay (median 1.25 ms, max 7 ms, as measured on the real
device). Options (a), (b), (c) from the S-300 note are prototyped as
wrappers around the operanda / driver so that ``driver.py`` is untouched.

Usage: .venv/bin/python .cloud-handoff/S-300/clock_domain_probe.py
"""

from __future__ import annotations

import math
import random
import statistics
import sys
from dataclasses import dataclass, field

from contingency.errors import ScheduleStateError
from contingency.schedules.interval import FI
from contingency.schedules.ratio import CRF, FR
from experiment_core import ReinforcementCountExit
from experiment_core.events import ResponseEvent
from experiment_io import drive
from experiment_io.hw.protocols import ResponseSample
from experiment_io.hw.backends.virtual import ManualClock, RecordingReinforcer
from session_runner import SessionConfig, SessionRunner

MS = 1e-3


# ----------------------------------------------------------------------
# Fake device: own clock, transmission delay, optional drift
# ----------------------------------------------------------------------


def measured_delay(rng: random.Random) -> float:
    """Transmission delay model: median 1.25 ms, clipped at 7 ms."""
    return min(7.0 * MS, 0.8 * MS + rng.expovariate(1.0 / (0.65 * MS)))


@dataclass
class LaggedDevice:
    """Serial-like operandum: Poisson responder stamped by the device clock.

    ``t_resp`` is the true response time in the host domain. The device
    stamps it as ``epoch + t_resp * (1 + drift_ppm * 1e-6)`` and the line
    reaches the host at ``t_resp + delay``. ``poll`` returns the lines
    that have arrived by ``clock()``; ``drive`` only polls between ticks,
    so a line is seen at the first poll after it arrives.
    """

    clock: ManualClock
    rate_hz: float = 2.0
    seed: int = 0
    drift_ppm: float = 0.0
    epoch: float = 0.0
    delay: object = measured_delay  # callable(rng) -> seconds; 0.0 allowed
    responds_during_reinforcer: bool = False
    max_per_poll: int | None = 1  # None: hand over every line that has arrived
    operandum_index: int = 0
    true_times: list[float] = field(default_factory=list)  # host domain, emitted order
    device_times: list[float] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._rng = random.Random(self.seed)
        self._next_t = self._draw_irt()

    def _draw_irt(self) -> float:
        return -math.log(1.0 - self._rng.random()) / self.rate_hz

    def _delay(self) -> float:
        return self.delay(self._rng) if callable(self.delay) else float(self.delay)

    def _device_stamp(self, t_host: float) -> float:
        return self.epoch + t_host * (1.0 + self.drift_ppm * 1e-6)

    def poll(self) -> list[ResponseSample]:
        now = self.clock()
        out: list[ResponseSample] = []
        while self._next_t + self._pending_delay() <= now:
            if self.max_per_poll is not None and len(out) >= self.max_per_poll:
                break
            t = self._next_t
            ts = self._device_stamp(t)
            self.true_times.append(t)
            self.device_times.append(ts)
            out.append(ResponseSample(self.operandum_index, ts))
            self._next_t = t + self._draw_irt()
            self._pending = None
        return out

    _pending: float | None = None

    def _pending_delay(self) -> float:
        if self._pending is None:
            self._pending = self._delay()
        return self._pending

    def freeze_for_reinforcer(self, duration: float) -> None:
        # Called by drive after the clock advanced through reinforcer access.
        if not self.responds_during_reinforcer:
            now = self.clock()
            while self._next_t < now:
                self._next_t = now + self._draw_irt()
            self._pending = None

    def close(self) -> None:
        pass


# ----------------------------------------------------------------------
# Option prototypes (wrappers; driver.py unchanged)
# ----------------------------------------------------------------------


class TrackingDriver:
    """Proxy recording the last timestamp handed to the session."""

    def __init__(self, inner: SessionRunner) -> None:
        self.inner = inner
        self.last_now: float | None = None

    def _note(self, t: float) -> float:
        self.last_now = t if self.last_now is None else max(self.last_now, t)
        return t

    def handle_response(self, timestamp: float) -> None:
        self.inner.handle_response(self._note(timestamp))

    def handle_response_on(self, operandum: int, timestamp: float) -> None:
        self.inner.handle_response_on(operandum, self._note(timestamp))

    def handle_tick(self, timestamp: float) -> None:
        self.inner.handle_tick(self._note(timestamp))

    def end_reinforcement(self, timestamp: float) -> None:
        self.inner.end_reinforcement(self._note(timestamp))

    @property
    def is_finished(self) -> bool:
        return self.inner.is_finished

    @property
    def is_reinforcing(self) -> bool:
        return self.inner.is_reinforcing

    def __getattr__(self, name: str):  # session, state_name, ...
        return getattr(self.inner, name)


class RestampOperandum:
    """Option (a): stamp each sample with the host clock at receipt; keep
    the device time in ``metadata``."""

    def __init__(self, inner: LaggedDevice, clock: ManualClock) -> None:
        self.inner, self.clock = inner, clock
        self.operandum_index = inner.operandum_index

    def poll(self) -> list[ResponseSample]:
        now = self.clock()
        return [
            ResponseSample(s.operandum_index, now, {"device_ts": s.timestamp})
            for s in self.inner.poll()
        ]

    def freeze_for_reinforcer(self, duration: float) -> None:
        self.inner.freeze_for_reinforcer(duration)

    def close(self) -> None:
        pass


class ClampOperandum:
    """Option (c): ``max(device ts, last time handed to the session)``."""

    def __init__(self, inner: LaggedDevice, tracker: TrackingDriver) -> None:
        self.inner, self.tracker = inner, tracker
        self.operandum_index = inner.operandum_index

    def poll(self) -> list[ResponseSample]:
        out = []
        for s in self.inner.poll():
            floor = self.tracker.last_now if self.tracker.last_now is not None else s.timestamp
            out.append(ResponseSample(s.operandum_index, max(s.timestamp, floor)))
        return out

    def freeze_for_reinforcer(self, duration: float) -> None:
        self.inner.freeze_for_reinforcer(duration)

    def close(self) -> None:
        pass


class DeviceDomainDriver(TrackingDriver):
    """Option (b): the device clock is the session clock. Ticks are
    converted with the estimated offset and held back by ``guard`` (a
    watermark: no line older than this can still be in flight), and never
    go behind the last response. Responses pass through unchanged."""

    def __init__(self, inner: SessionRunner, offset_est: float, guard: float) -> None:
        super().__init__(inner)
        self.offset_est, self.guard = offset_est, guard

    def _to_device(self, t_host: float) -> float:
        t = t_host + self.offset_est - self.guard
        if self.last_now is not None:
            t = max(t, self.last_now)
        return t

    def handle_response(self, timestamp: float) -> None:
        self.inner.handle_response(self._note(timestamp))

    def handle_tick(self, timestamp: float) -> None:
        self.inner.handle_tick(self._note(self._to_device(timestamp)))

    def end_reinforcement(self, timestamp: float) -> None:
        self.inner.end_reinforcement(self._note(self._to_device(timestamp)))


# ----------------------------------------------------------------------
# Scenario runner
# ----------------------------------------------------------------------


def run(
    label: str,
    *,
    option: str = "raw",
    schedule=None,
    reinforcers: int = 5,
    rate_hz: float = 2.0,
    drift_ppm: float = 0.0,
    epoch: float = 0.0,
    delay=measured_delay,
    responds_during_reinforcer: bool = False,
    max_per_poll: int | None = 1,
    reinforcer_duration: float = 0.5,
    offset_est: float = 0.0,
    guard: float = 8.0 * MS,
    seed: int = 1,
) -> None:
    clock = ManualClock()
    device = LaggedDevice(
        clock, rate_hz=rate_hz, seed=seed, drift_ppm=drift_ppm, epoch=epoch,
        delay=delay, responds_during_reinforcer=responds_during_reinforcer,
        max_per_poll=max_per_poll,
    )
    runner = SessionRunner(
        SessionConfig(
            name=label,
            schedule=schedule if schedule is not None else CRF(),
            exit_condition=ReinforcementCountExit(count=reinforcers),
        ),
        clock=clock,
    )
    tracker: TrackingDriver
    if option == "raw":
        tracker = TrackingDriver(runner)
        op = device
        runner.start(start_time=clock())
    elif option == "a":
        tracker = TrackingDriver(runner)
        op = RestampOperandum(device, clock)
        runner.start(start_time=clock())
    elif option == "c":
        tracker = TrackingDriver(runner)
        op = ClampOperandum(device, tracker)
        runner.start(start_time=clock())
    elif option == "b":
        tracker = DeviceDomainDriver(runner, offset_est=offset_est, guard=guard)
        op = device
        runner.start(start_time=clock() + offset_est - guard)
    else:
        raise ValueError(option)

    print(f"--- {label} [option {option}]")
    try:
        drive(
            tracker, [op], [RecordingReinforcer(0)], clock,
            is_concurrent=False, last_reinforced_operandum=lambda: 0,
            reinforcer_duration=reinforcer_duration, max_steps=2_000_000,
        )
    except ScheduleStateError as exc:
        n = len(device.true_times)
        print(f"    ScheduleStateError after {n} device response(s), host t={clock():.4f}s")
        print(f"    {exc}")
        return
    responses = [e for e in runner.session.events if isinstance(e, ResponseEvent)]
    n = len(responses)
    # drive drops samples polled in the same batch after the session finished,
    # so the device may have emitted one more than the session recorded.
    assert n <= len(device.true_times), (n, len(device.true_times))
    emitted = len(device.true_times)
    if emitted - n > 1:
        print(
            f"    finished={runner.is_finished} device emitted={emitted} session recorded={n} "
            f"-> {emitted - n} response(s) dropped by drive (batch after a reinforced response)"
        )
        return
    # Session (recorded) time minus the true time of each response, in host domain.
    # Under option (b) the record is in the device domain; compare to device_times.
    truth = (device.device_times if option == "b" else device.true_times)[:n]
    err_ms = [(e.timestamp - t) / MS for e, t in zip(responses, truth, strict=True)]
    irt_rec = [b.timestamp - a.timestamp for a, b in zip(responses, responses[1:])]
    irt_true = [b - a for a, b in zip(truth, truth[1:])]
    irt_err_ms = [(r - t) / MS for r, t in zip(irt_rec, irt_true, strict=True)]
    print(
        f"    finished={runner.is_finished} responses={n} host t={clock():.3f}s "
        f"reinforcers={runner.session.reinforcement_count}"
    )
    print(
        f"    response-time error (recorded - true) ms: "
        f"median={statistics.median(err_ms):.3f} max={max(err_ms):.3f} min={min(err_ms):.3f}"
    )
    if irt_err_ms:
        print(
            f"    IRT error ms: median|.|={statistics.median(abs(x) for x in irt_err_ms):.3f} "
            f"max|.|={max(abs(x) for x in irt_err_ms):.3f}"
        )


def main() -> None:
    print("== 1. Reproduction with the unmodified drive ==")
    run("CRF, measured delay (median 1.25 ms, max 7 ms), clocks aligned", option="raw")
    run("CRF, zero transmission delay, clocks aligned", option="raw", delay=0.0)
    run("CRF, device clock 30 s ahead (epoch not aligned)", option="raw", epoch=30.0)
    run("FI 5 s, measured delay", option="raw", schedule=FI(5.0))

    print("\n== 2. Option (a): host clock at receipt ==")
    run("CRF, measured delay", option="a")
    run("FI 5 s, measured delay", option="a", schedule=FI(5.0))
    run("FI 5 s x 20, device fast by 50 ppm", option="a", schedule=FI(5.0), drift_ppm=50,
        reinforcers=20)
    run("CRF, epoch not aligned (+30 s)", option="a", epoch=30.0)

    print("\n== 3. Option (c): max(device ts, last tick) ==")
    run("CRF, measured delay", option="c")
    run("FI 5 s, measured delay", option="c", schedule=FI(5.0))
    run("FI 5 s x 20, device slow by 50 ppm", option="c", schedule=FI(5.0), drift_ppm=-50,
        reinforcers=20)
    run("FI 5 s x 20, device fast by 20 ppm", option="c", schedule=FI(5.0), drift_ppm=20,
        reinforcers=20)
    run("FI 5 s x 20, device fast by 50 ppm", option="c", schedule=FI(5.0), drift_ppm=50,
        reinforcers=20)
    run("CRF, epoch not aligned (+30 s)", option="c", epoch=30.0)

    print("\n== 4. Option (b): device clock is the session clock, ticks = host + offset - guard ==")
    run("CRF, offset estimate off by +0.6 ms, guard 8 ms", option="b", offset_est=0.6 * MS)
    run("FI 5 s, offset estimate off by +0.6 ms, guard 8 ms", option="b", schedule=FI(5.0),
        offset_est=0.6 * MS)
    run("FI 5 s x 20, device fast by 50 ppm, no resync", option="b", schedule=FI(5.0),
        drift_ppm=50, offset_est=0.6 * MS, reinforcers=20)
    run("FI 5 s x 20, device slow by 50 ppm, no resync", option="b", schedule=FI(5.0),
        drift_ppm=-50, offset_est=0.6 * MS, reinforcers=20)
    run("CRF, offset estimate off by +10 ms (> guard)", option="b", offset_est=10 * MS)
    run("CRF, guard 0 (no watermark)", option="b", guard=0.0)

    print("\n== 5. Responses during reinforcer access (buffered by the device) ==")
    for opt in ("raw", "a", "c", "b"):
        run("FR 3, 0.5 s access, subject keeps responding", option=opt, schedule=FR(3),
            responds_during_reinforcer=True, rate_hz=4.0,
            offset_est=0.6 * MS)

    print("\n== 6. Side finding: lines buffered during access arrive in one poll (option a) ==")
    run("FR 3, 0.5 s access, subject keeps responding, all arrived lines per poll", option="a",
        schedule=FR(3), responds_during_reinforcer=True, rate_hz=4.0, max_per_poll=None)
    run("FR 3, 0.5 s access, subject keeps responding, one line per poll", option="a",
        schedule=FR(3), responds_during_reinforcer=True, rate_hz=4.0, max_per_poll=1)


if __name__ == "__main__":
    sys.exit(main())
