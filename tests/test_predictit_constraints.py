from src.exchanges.predictit.constraints import PredictItConstraints


def test_max_position_size():
    c = PredictItConstraints()
    assert c.max_position_size("ANY-TICKER") == 3500


def test_max_position_size_custom():
    c = PredictItConstraints(max_contracts=1000)
    assert c.max_position_size("ANY-TICKER") == 1000


def test_max_total_exposure_is_none():
    c = PredictItConstraints()
    assert c.max_total_exposure() is None
