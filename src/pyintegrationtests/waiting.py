"""Fresh coherent polls under a monotonic deadline; no retries of mutations."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from tenacity import RetryCallState, Retrying
from tenacity.wait import wait_random_exponential

from .contracts import ActionResult
from .errors import AssertionFailure, DeadlineExceeded, OperationError


@dataclass
class Clock:
    now: Callable[[], float] = time.monotonic
    sleep: Callable[[float], None] = time.sleep


def wait_for(
    observe: Callable[[float], ActionResult],
    verify: Callable[[ActionResult], None],
    *,
    mode: str,
    timeout: float,
    interval: float,
    deadline: float,
    observation_timeout: float,
    clock: Clock,
) -> ActionResult:
    end = min(deadline, clock.now() + timeout)
    attempts = 0
    last: Exception | None = None
    last_result: ActionResult | None = None
    while clock.now() < end:
        attempts += 1
        retry_after = 0.0
        try:
            last_result = observe(min(observation_timeout, end - clock.now()))
            if clock.now() > end:
                raise DeadlineExceeded("Observation exceeded its deadline")
            verify(last_result)
            if mode in {"immediate", "eventually"}:
                return last_result
        except AssertionFailure as exc:
            if mode != "eventually":
                raise
            last = exc
        except OperationError as exc:
            if mode != "eventually" or not exc.retryable:
                raise
            retry_after = exc.retry_after or 0
            last = exc
        remaining = end - clock.now()
        if remaining <= 0:
            break
        # tenacity computes the jitter; the engine owns the overall retry budget.
        state = RetryCallState(Retrying(), None, (), {})
        state.attempt_number = attempts
        jitter = wait_random_exponential(multiplier=interval / 4, max=interval)(state)
        clock.sleep(min(remaining, max(interval + jitter, retry_after)))
    if mode == "consistently" and last_result is not None:
        return last_result
    detail = f"; last failure: {last}" if last else ""
    raise DeadlineExceeded(f"Observation deadline exceeded after {attempts} polls{detail}")
