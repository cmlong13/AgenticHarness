import time


def test_sleeps_past_timeout():
    time.sleep(30)
    assert True
