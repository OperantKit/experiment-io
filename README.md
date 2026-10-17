# experiment-io

:jp: [日本語版 README](README.ja.md)

Hardware abstraction layer (HAL) for the OperantKit experiment runtime.

`experiment-io` defines the protocol contracts and concrete backends that let
the same Session/Runner code drive:

- **virtual** — in-memory Poisson responders for hermetic unit tests
- **serial** — Arduino-style JSONL over a duplex byte stream (pyserial optional)
- **hil_bridge** — stdio bridge to a `contingency-rs` hardware-in-the-loop subprocess

Imports are intentionally light: pulling in `experiment_io.hw.backends.serial`
does not require `pyserial`, and `experiment_io.hw.backends.hil_bridge` does
not require the Rust binary to exist.

## Public API

```python
from experiment_io import drive, SessionDriverLike
from experiment_io.hw import Clock, Operandum, Reinforcer, ResponseSample
from experiment_io.hw.backends.virtual import (
    ManualClock,
    VirtualSubject,
    ScallopSubject,
    SpacedRespondingSubject,
    MatchingSubject,
    MultiVirtualSubject,
    VirtualOperandum,
    MultiVirtualOperandum,
    RecordingReinforcer,
)
from experiment_io.hw.backends.serial import (
    SerialOperandum,
    SerialReinforcer,
    open_serial,
)
from experiment_io.hw.backends.hil_bridge import HilBridge
```

### Driving a session from hardware

`experiment_io.drive(driver, operanda, reinforcers, clock, ...)`
loops over HAL devices and feeds responses into anything satisfying
the `SessionDriverLike` Protocol (typically a
`session_runner.SessionRunner`). The driver type is structurally
typed so this package does not import `session-runner`.

```python
from contingency import ScheduleBuilder
from experiment_core import ReinforcementCountExit
from experiment_io import drive
from experiment_io.hw.backends.virtual import (
    ManualClock, RecordingReinforcer, VirtualOperandum, VirtualSubject,
)
from session_runner import SessionConfig, SessionRunner

clock = ManualClock()
op = VirtualOperandum(0, VirtualSubject(rate_hz=5.0, seed=0), clock)
rein = RecordingReinforcer(operandum_index=0)

runner = SessionRunner(
    SessionConfig(
        name="demo",
        schedule=ScheduleBuilder.fr(1),
        exit_condition=ReinforcementCountExit(count=3),
    ),
    clock=clock,
)
runner.start(start_time=clock())
drive(runner, [op], [rein], clock, reinforcer_duration=0.2)
```

## Installation

```bash
pip install experiment-io
pip install 'experiment-io[hw-serial]'   # optional serial backend
```

For development:

```bash
mise exec -- python -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"
```

For the optional serial backend on real hardware:

```bash
.venv/bin/python -m pip install -e ".[hw-serial]"
```

## 30-second example

Drive a concurrent schedule from two virtual operanda, with a feeder per operandum, until ten reinforcers have been delivered:

```python
from contingency_dsl import parse
from contingency import from_dsl
from experiment_core import ReinforcementCountExit
from experiment_io import drive
from experiment_io.hw.backends.virtual import (
    ManualClock, MultiVirtualOperandum, MultiVirtualSubject, RecordingReinforcer,
)
from session_runner import SessionConfig, SessionRunner

clock = ManualClock()
subject = MultiVirtualSubject(rates_hz=[2.0, 0.5], seeds=[1, 2])  # seeded Poisson responders
operanda = MultiVirtualOperandum(subject, clock).split()
feeders = [RecordingReinforcer(operandum_index=i) for i in range(2)]

runner = SessionRunner(
    SessionConfig(
        name="conc-fr",
        schedule=from_dsl(parse("Conc(FR 5, FR 5)")),
        exit_condition=ReinforcementCountExit(count=10),
    ),
    clock=clock,
)
runner.start(start_time=clock())
drive(runner, operanda, feeders, clock, reinforcer_duration=3.0)

print(runner.state_name, runner.component_responses, [len(f.calls) for f in feeders], round(clock(), 1))
# FINISHED [45, 6] [9, 1] 49.8
```

The faster responder (2 responses/s) produced 9 of the 10 reinforcers, each delivered by the feeder paired with the operandum that completed the ratio; the clock includes 3 s of reinforcer access per delivery. Replacing `MultiVirtualOperandum` and `RecordingReinforcer` with `SerialOperandum` and `SerialReinforcer` runs the same session on a microcontroller. The example uses [session-runner](https://github.com/OperantKit/session-runner) (`pip install session-runner`, which installs experiment-core, contingency-py, and contingency-dsl-py).

## Why not a 50-line hand-written hardware loop?

A polling loop written for one device mixes timing, routing, and device code. This package separates them:

- **One loop for every backend.** `drive` works with any object satisfying the `Operandum` / `Reinforcer` protocols: virtual subjects, a serial device, or the contingency-rs HIL bridge.
- **Reinforcer routed to the right operandum.** On a concurrent schedule each response goes to `handle_response_on(index, ...)`, and the reinforcer is delivered by the device paired with the operandum that produced it.
- **Deterministic simulation.** With `ManualClock` the loop advances virtual time instead of sleeping, and virtual subjects are seeded, so a whole session runs in milliseconds and repeats exactly. Multi-operandum virtual subjects do not respond during reinforcer access.
- **Time-based schedules keep running.** When no response arrives the loop advances the clock and calls `handle_tick`, so FI, VI, FT, VT, DRO and time limits are evaluated; `max_duration` and `max_steps` bound the loop.
- **Documented wire formats.** Serial devices and the HIL bridge speak newline-delimited JSON (`{"op": 0, "ts": 1.25, "kind": "response"}` in, `{"cmd": "reinforcer", "op": 0, "duration": 3.0}` out); `pyserial` is optional and imported lazily.

## Tests

```bash
.venv/bin/python -m pytest -q
```

## Dependencies

`experiment-io` has no runtime dependency on other OperantKit packages.
`drive` accepts any object with the `SessionDriverLike` surface, so the
session itself comes from `session-runner` (`pip install session-runner`),
which is what the examples above use.

## HIL bridge binary

`HilBridge` defaults to launching a Rust binary at a built-in path. For
deployments where the binary lives elsewhere, pass the path explicitly:

```python
# requires the operantkit-hil binary
from experiment_io.hw.backends.hil_bridge import HilBridge

bridge = HilBridge(binary="/path/to/operantkit-hil")
```

The default may also be overridden via the `OPERANTKIT_HIL_BINARY`
environment variable.

## Wire formats

Both the serial and HIL bridge backends speak newline-delimited JSON.
See the docstrings of `experiment_io.hw.backends.serial` and
`experiment_io.hw.backends.hil_bridge` for the exact framing.
