"""Interrupt blocking calls on the sequential POSIX runner, including SDK retry overhead."""

from __future__ import annotations

import signal
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from types import FrameType

from .errors import DeadlineExceeded, ValidationError


@contextmanager
def time_limit(seconds: float) -> Iterator[None]:
    if seconds <= 0:
        raise DeadlineExceeded("Action deadline exceeded")
    if (
        not hasattr(signal, "setitimer")
        or threading.current_thread() is not threading.main_thread()
    ):
        raise ValidationError(
            "Infrastructure execution requires the main thread of a POSIX process; use Docker"
        )

    def expired(signum: int, frame: FrameType | None) -> None:
        raise DeadlineExceeded("Action deadline exceeded")

    started = time.monotonic()
    old_handler = signal.getsignal(signal.SIGALRM)
    old_delay, old_interval = signal.getitimer(signal.ITIMER_REAL)
    if not old_delay or seconds < old_delay:
        signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, min(seconds, old_delay) if old_delay else seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old_handler)
        if old_delay:
            signal.setitimer(
                signal.ITIMER_REAL,
                max(0.000001, old_delay - (time.monotonic() - started)),
                old_interval,
            )
