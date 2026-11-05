import time

from rh.engines._proc import run


def test_timeout_does_not_raise():
    t0 = time.monotonic()
    rc, *_ = run(["sleep", "5"], None, 0.2)
    assert rc == -1 and time.monotonic() - t0 < 1


def test_missing_binary():
    assert run(["definitely-not-a-binary"], None, 1)[0] == 127
