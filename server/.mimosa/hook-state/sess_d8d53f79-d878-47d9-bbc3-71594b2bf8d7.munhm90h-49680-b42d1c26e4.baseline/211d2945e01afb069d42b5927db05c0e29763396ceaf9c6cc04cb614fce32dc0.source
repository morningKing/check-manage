from utils.rate_limit import RateLimiter


def test_per_minute_limit():
    rl = RateLimiter()
    t = 1000.0
    assert all(rl.allow('v1', 3, 0, now=t + i * 0.1) for i in range(3))
    assert rl.allow('v1', 3, 0, now=t + 0.4) is False  # 第 4 条同一分钟内被拒


def test_window_resets_next_minute():
    rl = RateLimiter()
    assert rl.allow('v1', 1, 0, now=1000.0) is True
    assert rl.allow('v1', 1, 0, now=1000.5) is False
    assert rl.allow('v1', 1, 0, now=1061.0) is True  # 跨过 60s 窗口


def test_zero_means_unlimited():
    rl = RateLimiter()
    assert all(rl.allow('v1', 0, 0, now=1000.0 + i) for i in range(50))


def test_keys_isolated():
    rl = RateLimiter()
    assert rl.allow('a', 1, 0, now=1000.0) is True
    assert rl.allow('b', 1, 0, now=1000.0) is True
