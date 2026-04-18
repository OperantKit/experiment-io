# experiment-io

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

## Installation

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
