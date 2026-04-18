"""Serial-port HAL backend (Arduino-style).

The backend does **not** depend on pyserial at import time. Instead, it
accepts any duplex file-like stream (anything implementing
``readline()`` / ``write()`` / ``flush()``) so that tests can pass an
``io.StringIO`` and so that production callers can pass either a
pyserial ``Serial`` instance or a unix pty / TCP socket wrapper.

Wire format
-----------

Device -> host (one JSON object per line)::

    {"op": <int>, "ts": <float>, "kind": "response"}

Host -> device (one JSON object per line)::

    {"cmd": "reinforcer", "op": <int>, "duration": <float>}

Use :func:`open_serial` for the convenience case of attaching to a real
``/dev/tty*`` port via pyserial (lazy import — pyserial is optional).
"""

from __future__ import annotations

import json
import logging
from typing import Any, Protocol

from ..protocols import ResponseSample

logger = logging.getLogger(__name__)


class _LineStream(Protocol):
    def readline(self) -> str: ...
    def write(self, data: str) -> Any: ...
    def flush(self) -> None: ...


class SerialOperandum:
    """Operandum that reads JSONL response events from a stream."""

    def __init__(self, stream: _LineStream, operandum_index: int) -> None:
        self.stream = stream
        self.operandum_index = operandum_index

    def poll(self) -> list[ResponseSample]:
        samples: list[ResponseSample] = []
        while True:
            line = self.stream.readline()
            if not line:
                break
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                logger.warning("SerialOperandum: skipping malformed line: %r", line)
                continue
            if not isinstance(payload, dict):
                logger.warning(
                    "SerialOperandum: skipping non-object payload: %r", payload
                )
                continue
            if payload.get("kind") != "response":
                continue
            try:
                op = int(payload["op"])
                ts = float(payload["ts"])
            except (KeyError, TypeError, ValueError):
                logger.warning(
                    "SerialOperandum: skipping payload with bad fields: %r",
                    payload,
                )
                continue
            if op != self.operandum_index:
                continue
            samples.append(
                ResponseSample(operandum_index=op, timestamp=ts)
            )
        return samples

    def close(self) -> None:
        close = getattr(self.stream, "close", None)
        if callable(close):
            close()


class SerialReinforcer:
    """Reinforcer that emits a JSONL command line per delivery."""

    def __init__(self, stream: _LineStream, operandum_index: int) -> None:
        self.stream = stream
        self.operandum_index = operandum_index

    def deliver(self, duration: float) -> None:
        cmd = {
            "cmd": "reinforcer",
            "op": self.operandum_index,
            "duration": float(duration),
        }
        self.stream.write(json.dumps(cmd) + "\n")
        flush = getattr(self.stream, "flush", None)
        if callable(flush):
            flush()

    def close(self) -> None:
        close = getattr(self.stream, "close", None)
        if callable(close):
            close()


def open_serial(port: str, baud: int = 115200, timeout: float = 0.0) -> Any:
    """Open a real serial port via pyserial (lazy import).

    Raises a clear ``ImportError`` if pyserial is not installed.
    Returned object is a pyserial ``Serial`` instance and can be passed
    directly to ``SerialOperandum`` / ``SerialReinforcer``.
    """
    try:
        import serial as _pyserial  # type: ignore
    except ImportError as exc:  # pragma: no cover - exercised manually
        raise ImportError(
            "pyserial is required for open_serial(); install with "
            "`pip install pyserial`."
        ) from exc
    return _pyserial.Serial(port=port, baudrate=baud, timeout=timeout)


__all__ = [
    "SerialOperandum",
    "SerialReinforcer",
    "open_serial",
]
