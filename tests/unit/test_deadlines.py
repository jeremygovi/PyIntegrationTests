import signal
import time

import pytest

from pyintegrationtests.deadlines import time_limit
from pyintegrationtests.errors import DeadlineExceeded


def test_blocking_call_is_interrupted_and_signal_restored():
    previous = signal.getsignal(signal.SIGALRM)
    started = time.monotonic()
    with pytest.raises(DeadlineExceeded), time_limit(0.02):
        time.sleep(1)
    assert time.monotonic() - started < 0.5
    assert signal.getsignal(signal.SIGALRM) == previous
    assert signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0)


def test_nested_budget_does_not_cancel_outer_deadline():
    with time_limit(1):
        with time_limit(0.1):
            assert signal.getitimer(signal.ITIMER_REAL)[0] <= 0.1
        assert 0 < signal.getitimer(signal.ITIMER_REAL)[0] <= 1
    with pytest.raises(DeadlineExceeded), time_limit(0):
        pass
