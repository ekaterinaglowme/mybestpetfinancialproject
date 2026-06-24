from ratelimit import TokenBucket


def test_bucket_allows_up_to_capacity():
    clock = [0.0]
    b = TokenBucket(rps=100, capacity=3, now=lambda: clock[0])
    assert b.allow() is True
    assert b.allow() is True
    assert b.allow() is True
    assert b.allow() is False  # ёмкость исчерпана, время не шло


def test_bucket_refills_over_time():
    clock = [0.0]
    b = TokenBucket(rps=10, capacity=1, now=lambda: clock[0])
    assert b.allow() is True
    assert b.allow() is False
    clock[0] = 0.1  # 0.1с * 10 rps = 1 токен
    assert b.allow() is True


def test_seconds_until_token():
    clock = [0.0]
    b = TokenBucket(rps=2, capacity=1, now=lambda: clock[0])
    assert b.allow() is True
    assert b.seconds_until_token() == 0.5  # 1 токен / 2 rps


def test_seconds_until_token_zero_when_available():
    b = TokenBucket(rps=100, capacity=5, now=lambda: 0.0)
    assert b.seconds_until_token() == 0.0
