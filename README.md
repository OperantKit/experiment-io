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

## Tests

```bash
.venv/bin/python -m pytest -q
```

## Local dependency notes

`experiment-io` is logically a leaf consumer of `contingency-py` and is in
turn consumed by `experiment-core`. Both of those packages live in this
monorepo and are not yet published to PyPI, so they are intentionally **not**
listed in `[project.dependencies]` here. When wiring this package into a
larger application, install the sibling packages from their local paths:

```bash
.venv/bin/python -m pip install -e ../../core/contingency-py
```

## HIL bridge binary

`HilBridge` defaults to launching a Rust binary at a built-in path. For
deployments where the binary lives elsewhere, pass the path explicitly:

```python
from experiment_io.hw.backends.hil_bridge import HilBridge

bridge = HilBridge(binary="/path/to/operantkit-hil")
```

The default may also be overridden via the `OPERANTKIT_HIL_BINARY`
environment variable.

## Wire formats

Both the serial and HIL bridge backends speak newline-delimited JSON.
See the docstrings of `experiment_io.hw.backends.serial` and
`experiment_io.hw.backends.hil_bridge` for the exact framing.
