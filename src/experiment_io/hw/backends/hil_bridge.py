"""contingency-rs HIL bridge backend.

contingency-rs HIL stdio protocol
---------------------------------
Host -> device (one JSON object per line on stdin)::

    {"cmd": "start", "session_id": "..."}
    {"cmd": "reinforcer", "op": <int>, "duration": <float>}
    {"cmd": "stop"}

Device -> host (one JSON object per line on stdout)::

    {"event": "ready", "ts": <float>}
    {"event": "response", "op": <int>, "ts": <float>}
    {"event": "reinforcer_done", "op": <int>, "ts": <float>}
    {"event": "error", "msg": "..."}

Timestamps are monotonic seconds reported by the device's hardware
clock. They are kept in ``ResponseSample.timestamp`` as a record;
:func:`experiment_io.drive` gives the session the host's ``clock()``
at the moment it hands the response over, not this device time.

This module never imports the Rust binary at module-load time. Tests
inject a fake ``Popen``-like object via ``HilBridge(process=...)``.

The default HIL binary path is resolved in this order:

1. The ``binary`` constructor argument, if provided.
2. The ``OPERANTKIT_HIL_BINARY`` environment variable, if set.
3. The compile-time default ``DEFAULT_BINARY``.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
from collections.abc import Sequence
from typing import Any

from ..protocols import ResponseSample

logger = logging.getLogger(__name__)


DEFAULT_BINARY = "apps/contingency-rs/target/debug/operantkit-hil"
"""Fallback path to the contingency-rs HIL binary.

Override at runtime via the ``OPERANTKIT_HIL_BINARY`` environment
variable or by passing ``binary=...`` to :class:`HilBridge`.
"""


def _resolve_binary(binary: str | None) -> str:
    if binary is not None:
        return binary
    env = os.environ.get("OPERANTKIT_HIL_BINARY")
    if env:
        return env
    return DEFAULT_BINARY


class HilBridge:
    """Stdio bridge to a contingency-rs HIL process.

    Construct either with ``binary=<path>`` (the bridge launches it via
    ``subprocess.Popen``) or with ``process=<popen-like>`` (used by
    tests to inject a fake stdio pair).
    """

    def __init__(
        self,
        binary: str | None = None,
        args: Sequence[str] | None = None,
        process: Any = None,
        session_id: str = "default",
    ) -> None:
        self.session_id = session_id
        if process is not None:
            self._proc = process
        else:
            cmd: list[str] = [_resolve_binary(binary), *(args or [])]
            self._proc = subprocess.Popen(  # noqa: S603 - command is constructed from trusted config
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,  # line-buffered
            )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        self._send({"cmd": "start", "session_id": self.session_id})

    def stop(self) -> None:
        try:
            self._send({"cmd": "stop"})
        except Exception:  # pragma: no cover - tolerate broken pipes
            logger.warning("HilBridge: failed to send stop command", exc_info=True)
        terminate = getattr(self._proc, "terminate", None)
        if callable(terminate):
            terminate()

    # ------------------------------------------------------------------
    # I/O
    # ------------------------------------------------------------------

    def poll(self) -> list[ResponseSample]:
        samples: list[ResponseSample] = []
        stdout = self._proc.stdout
        if stdout is None:
            return samples
        while True:
            line = stdout.readline()
            if not line:
                break
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                logger.warning("HilBridge: skipping malformed line: %r", line)
                continue
            kind = payload.get("event")
            if kind == "response":
                try:
                    op = int(payload["op"])
                    ts = float(payload["ts"])
                except (KeyError, TypeError, ValueError):
                    logger.warning(
                        "HilBridge: malformed response payload: %r", payload
                    )
                    continue
                samples.append(ResponseSample(operandum_index=op, timestamp=ts))
            elif kind in ("ready", "reinforcer_done"):
                logger.debug("HilBridge: device event %s: %r", kind, payload)
            elif kind == "error":
                logger.error("HilBridge: device error: %s", payload.get("msg"))
            else:
                logger.warning("HilBridge: unknown event: %r", payload)
        return samples

    def deliver(self, operandum: int, duration: float) -> None:
        self._send(
            {
                "cmd": "reinforcer",
                "op": int(operandum),
                "duration": float(duration),
            }
        )

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _send(self, obj: dict[str, Any]) -> None:
        stdin = self._proc.stdin
        if stdin is None:
            raise RuntimeError("HilBridge: subprocess has no stdin")
        stdin.write(json.dumps(obj) + "\n")
        flush = getattr(stdin, "flush", None)
        if callable(flush):
            flush()


__all__ = ["HilBridge", "DEFAULT_BINARY"]
