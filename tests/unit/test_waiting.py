import pytest

from pyintegrationtests.contracts import ActionResult
from pyintegrationtests.errors import AssertionFailure, DeadlineExceeded, OperationError
from pyintegrationtests.waiting import Clock, wait_for


class FakeClock:
    time = 0.0

    def now(self):
        return self.time

    def sleep(self, duration):
        self.time += duration


def poll(observe, verify, *, mode="eventually", timeout=5):
    clock = FakeClock()
    return wait_for(
        observe,
        verify,
        mode=mode,
        timeout=timeout,
        interval=1,
        deadline=20,
        observation_timeout=2,
        clock=Clock(clock.now, clock.sleep),
    )


def test_eventually_rereads_and_assertions_share_a_poll():
    calls = []

    def observe(timeout):
        calls.append(timeout)
        return ActionResult({"generation": len(calls), "replicas": len(calls)})

    def verify(result):
        assert result.data["generation"] == result.data["replicas"]
        if result.data["generation"] < 3:
            raise AssertionFailure("stale")

    assert poll(observe, verify).data["generation"] == 3
    assert len(calls) == 3


@pytest.mark.parametrize("mode", ["immediate", "consistently"])
def test_non_eventual_failure_is_immediate(mode):
    with pytest.raises(AssertionFailure):
        poll(
            lambda t: ActionResult(0),
            lambda r: (_ for _ in ()).throw(AssertionFailure("bad")),
            mode=mode,
        )


def test_consistently_samples_for_the_whole_duration():
    observations = []
    poll(
        lambda t: observations.append(t) or ActionResult(True), lambda r: None, mode="consistently"
    )
    assert len(observations) >= 4


def test_permissions_fail_fast_and_retry_after_is_bounded():
    calls = []

    def forbidden(timeout):
        calls.append(timeout)
        raise OperationError("aws", "AccessDenied", 403)

    with pytest.raises(OperationError):
        poll(forbidden, lambda r: None)
    assert len(calls) == 1

    def throttled(timeout):
        raise OperationError("cloudflare", "429", 429, retryable=True, retry_after=100)

    with pytest.raises(DeadlineExceeded, match="after 1 polls"):
        poll(throttled, lambda r: None)


def test_timeout_keeps_last_failure():
    with pytest.raises(DeadlineExceeded, match="stale generation"):
        poll(
            lambda t: ActionResult(None),
            lambda r: (_ for _ in ()).throw(AssertionFailure("stale generation")),
        )


def test_late_response_cannot_pass():
    clock = FakeClock()

    def slow(timeout):
        clock.sleep(10)
        return ActionResult(True)

    with pytest.raises(DeadlineExceeded):
        wait_for(
            slow,
            lambda r: None,
            mode="immediate",
            timeout=1,
            interval=1,
            deadline=2,
            observation_timeout=1,
            clock=Clock(clock.now, clock.sleep),
        )
