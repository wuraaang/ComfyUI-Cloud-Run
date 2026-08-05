"""Bounded, secret-safe diagnostics for the internal ComfyUI process."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import re
from typing import Optional, Tuple

if "." in (__package__ or ""):
    from ..cloud_run.run_errors import (
        MAX_SAFE_LOG_LINES,
        MAX_SAFE_TEXT_BYTES,
        sanitize_text,
    )
else:
    from cloud_run.run_errors import (
        MAX_SAFE_LOG_LINES,
        MAX_SAFE_TEXT_BYTES,
        sanitize_text,
    )


_STREAMS = frozenset({"stdout", "stderr"})
_PHASE = re.compile(r"[a-z][a-z0-9_-]{0,63}")
_MAX_LINE_BYTES = 8 * 1024


def _bounded(value, limit):
    encoded = value.encode("utf-8", errors="replace")
    if len(encoded) <= limit:
        return value
    suffix = b"...[truncated]"
    prefix = encoded[: max(0, limit - len(suffix))]
    return prefix.decode("utf-8", errors="ignore") + suffix.decode("ascii")


@dataclass(frozen=True)
class ProcessDiagnostic:
    phase: str
    exit_code: Optional[int]
    restart_count: int
    last_probe: Optional[str]
    stdout_tail: Tuple[str, ...]
    stderr_tail: Tuple[str, ...]


class BoundedDiagnostics:
    def __init__(self, *, local_roots=()):
        self._local_roots = tuple(str(root) for root in local_roots if str(root))
        self._lines = deque(maxlen=MAX_SAFE_LOG_LINES)
        self._pending = {"stdout": "", "stderr": ""}
        self._total_bytes = 0

    def _safe_line(self, value):
        return _bounded(
            sanitize_text(value, local_roots=self._local_roots),
            _MAX_LINE_BYTES,
        )

    def _append(self, stream, value):
        line = self._safe_line(value)
        size = len(line.encode("utf-8")) + 1
        if len(self._lines) == self._lines.maxlen:
            _old_stream, old_line = self._lines[0]
            self._total_bytes -= len(old_line.encode("utf-8")) + 1
        self._lines.append((stream, line))
        self._total_bytes += size
        while self._lines and self._total_bytes > MAX_SAFE_TEXT_BYTES:
            _old_stream, old_line = self._lines.popleft()
            self._total_bytes -= len(old_line.encode("utf-8")) + 1

    def feed(self, stream, chunk):
        if stream not in _STREAMS:
            raise ValueError("Unsupported diagnostic stream.")
        if isinstance(chunk, bytes):
            text = chunk.decode("utf-8", errors="replace")
        elif isinstance(chunk, str):
            text = chunk
        else:
            raise ValueError("Invalid diagnostic chunk.")
        combined = self._pending[stream] + text
        self._pending[stream] = ""
        for part in combined.splitlines(keepends=True):
            if part.endswith(("\n", "\r")):
                self._append(stream, part.rstrip("\r\n"))
            else:
                self._pending[stream] = part
        pending = self._pending[stream]
        if len(pending.encode("utf-8", errors="replace")) > _MAX_LINE_BYTES:
            self._append(stream, pending)
            self._pending[stream] = ""

    def snapshot(
        self,
        *,
        phase,
        exit_code=None,
        restart_count=0,
        last_probe=None,
    ):
        if not isinstance(phase, str) or not _PHASE.fullmatch(phase):
            raise ValueError("Invalid diagnostic phase.")
        if (
            exit_code is not None
            and (isinstance(exit_code, bool) or not isinstance(exit_code, int))
        ):
            raise ValueError("Invalid diagnostic exit code.")
        if (
            isinstance(restart_count, bool)
            or not isinstance(restart_count, int)
            or restart_count < 0
        ):
            raise ValueError("Invalid diagnostic restart count.")
        if last_probe is not None:
            if not isinstance(last_probe, str):
                raise ValueError("Invalid diagnostic probe.")
            last_probe = self._safe_line(last_probe)

        records = list(self._lines)
        for stream in ("stdout", "stderr"):
            if self._pending[stream]:
                records.append((stream, self._safe_line(self._pending[stream])))
        while len(records) > MAX_SAFE_LOG_LINES:
            records.pop(0)
        total = sum(len(line.encode("utf-8")) + 1 for _stream, line in records)
        while records and total > MAX_SAFE_TEXT_BYTES:
            _stream, line = records.pop(0)
            total -= len(line.encode("utf-8")) + 1
        return ProcessDiagnostic(
            phase=phase,
            exit_code=exit_code,
            restart_count=restart_count,
            last_probe=last_probe,
            stdout_tail=tuple(
                line for stream, line in records if stream == "stdout"
            ),
            stderr_tail=tuple(
                line for stream, line in records if stream == "stderr"
            ),
        )
