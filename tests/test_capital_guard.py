from src.core.capital_guard import CapitalGuard


def test_can_execute_within_budget():
    guard = CapitalGuard(budgets={"kalshi": 25.0})
    assert guard.can_execute("kalshi", 10.0)


def test_can_execute_exceeds_budget():
    guard = CapitalGuard(budgets={"kalshi": 25.0})
    guard.commit("kalshi", "order1", 20.0)
    assert not guard.can_execute("kalshi", 10.0)


def test_can_execute_exact_budget():
    guard = CapitalGuard(budgets={"kalshi": 25.0})
    guard.commit("kalshi", "order1", 15.0)
    assert guard.can_execute("kalshi", 10.0)


def test_release_frees_headroom():
    guard = CapitalGuard(budgets={"kalshi": 25.0})
    guard.commit("kalshi", "order1", 20.0)
    assert not guard.can_execute("kalshi", 10.0)
    guard.release("kalshi", "order1")
    assert guard.can_execute("kalshi", 10.0)


def test_headroom_returns_remaining():
    guard = CapitalGuard(budgets={"kalshi": 25.0})
    assert guard.headroom("kalshi") == 25.0
    guard.commit("kalshi", "order1", 10.0)
    assert guard.headroom("kalshi") == 15.0


def test_deployed_returns_total():
    guard = CapitalGuard(budgets={"kalshi": 25.0})
    guard.commit("kalshi", "order1", 10.0)
    guard.commit("kalshi", "order2", 5.0)
    assert guard.deployed("kalshi") == 15.0


def test_no_budget_configured_unlimited():
    guard = CapitalGuard(budgets={})
    assert guard.can_execute("kalshi", 1000.0)
    assert guard.headroom("kalshi") == float("inf")


def test_release_nonexistent_order_is_noop():
    guard = CapitalGuard(budgets={"kalshi": 25.0})
    guard.release("kalshi", "nonexistent")
    assert guard.headroom("kalshi") == 25.0


def test_commit_same_order_id_replaces():
    guard = CapitalGuard(budgets={"kalshi": 25.0})
    guard.commit("kalshi", "order1", 10.0)
    guard.commit("kalshi", "order1", 15.0)
    assert guard.deployed("kalshi") == 15.0


def test_multiple_exchanges_independent():
    guard = CapitalGuard(budgets={"kalshi": 25.0, "predictit": 50.0})
    guard.commit("kalshi", "k1", 20.0)
    guard.commit("predictit", "p1", 30.0)
    assert guard.headroom("kalshi") == 5.0
    assert guard.headroom("predictit") == 20.0
    assert guard.can_execute("kalshi", 5.0)
    assert not guard.can_execute("kalshi", 6.0)
    assert guard.can_execute("predictit", 20.0)
