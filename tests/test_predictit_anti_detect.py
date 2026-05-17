from src.exchanges.predictit.anti_detect import (
    USER_AGENTS,
    get_headers,
    random_delay,
    random_viewport,
)


def test_user_agents_has_at_least_five():
    assert len(USER_AGENTS) >= 5


def test_user_agents_all_contain_mozilla():
    for ua in USER_AGENTS:
        assert "Mozilla" in ua


def test_get_headers_has_required_keys():
    headers = get_headers()
    assert "User-Agent" in headers
    assert "Accept" in headers
    assert "Accept-Language" in headers
    assert "Accept-Encoding" in headers
    assert "DNT" in headers
    assert "Connection" in headers


def test_get_headers_user_agent_from_pool():
    headers = get_headers()
    assert headers["User-Agent"] in USER_AGENTS


def test_random_delay_within_bounds():
    for _ in range(20):
        d = random_delay(min_secs=1.0, max_secs=3.0)
        assert 1.0 <= d <= 3.0


def test_random_delay_defaults():
    d = random_delay()
    assert 2.0 <= d <= 5.0


def test_random_viewport_reasonable_size():
    vp = random_viewport()
    assert 1200 <= vp["width"] <= 1920
    assert 700 <= vp["height"] <= 1080
