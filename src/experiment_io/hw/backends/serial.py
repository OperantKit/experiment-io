"""Serial-port HAL backend (Arduino-style).

The backend does **not** depend on pyserial at import time. Instead, it
accepts any duplex file-like stream (anything implementing
``readline()`` / ``write()`` / ``flush()``) so that tests can pass an
``io.StringIO`` and so that production callers can pass either a
pyserial ``Serial`` instance or a unix pty / TCP socket wrapper.

Both byte streams (pyserial) and text streams work. Reads accept
``bytes`` or ``str`` lines; writes send UTF-8 ``bytes`` unless the
stream is a text stream (``io.TextIOBase``) or rejects bytes with
``TypeError``, in which case ``str`` is written. A non-blocking read
(pyserial with ``timeout=0``) can return a line cut before its newline;
the operandum keeps the partial line and completes it on a later poll,
so a line is parsed only once its newline has arrived.

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

import io
import json
import logging
from typing import Any, Protocol

from ..protocols import ResponseSample

logger = logging.getLogger(__name__)


class _LineStream(Protocol):
    def readline(self) -> bytes | str: ...
    def write(self, data: Any) -> Any: ...
    def flush(self) -> None: ...


class SerialOperandum:
    """Operandum that reads JSONL response events from a stream."""

    def __init__(self, stream: _LineStream, operandum_index: int) -> None:
        self.stream = stream
        self.operandum_index = operandum_index
        self._partial: bytes | str = ""

    def _next_line(self) -> str | None:
        """Return the next complete line, or ``None`` when none is ready."""
        while True:
            chunk = self.stream.readline()
            if not chunk:
                return None
            if self._partial:
                if isinstance(chunk, bytes) and isinstance(self._partial, str):
                    chunk = self._partial.encode("utf-8") + chunk
                elif isinstance(chunk, str) and isinstance(self._partial, bytes):
                    chunk = self._partial.decode("utf-8", errors="replace") + chunk
                else:
                    chunk = self._partial + chunk  # type: ignore[operator]
                self._partial = ""
            newline = b"\n" if isinstance(chunk, bytes) else "\n"
            if not chunk.endswith(newline):  # type: ignore[arg-type]
                self._partial = chunk
                return None
            if isinstance(chunk, bytes):
                return chunk.decode("utf-8", errors="replace")
            return chunk

    def poll(self) -> list[ResponseSample]:
        samples: list[ResponseSample] = []
        while True:
            line = self._next_line()
            if line is None:
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
        self._text = isinstance(stream, io.TextIOBase)

    def _write_line(self, line: str) -> None:
        if self._text:
            self.stream.write(line)
            return
        try:
            self.stream.write(line.encode("utf-8"))
        except TypeError:
            self._text = True
            self.stream.write(line)

    def deliver(self, duration: float) -> None:
        cmd = {
            "cmd": "reinforcer",
            "op": self.operandum_index,
            "duration": float(duration),
        }
        self._write_line(json.dumps(cmd) + "\n")
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
