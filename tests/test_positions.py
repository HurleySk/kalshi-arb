from src.positions import PositionTracker


def test_record_fill_creates_position():
    tracker = PositionTracker()
    tracker.record_fill(ticker="M1", side="yes", price=0.55, quantity=10, action="sell")
    pos = tracker.get_position("M1")
    assert pos is not None
    assert pos.quantity == 10
    assert pos.avg_price == 0.55


def test_record_multiple_fills_same_ticker():
    tracker = PositionTracker()
    tracker.record_fill(ticker="M1", side="yes", price=0.50, quantity=10, action="sell")
    tracker.record_fill(ticker="M1", side="yes", price=0.60, quantity=10, action="sell")
    pos = tracker.get_position("M1")
    assert pos.quantity == 20
    assert pos.avg_price == 0.55


def test_pnl_all_outcomes_filled():
    tracker = PositionTracker()
    tracker.record_fill(ticker="M1", side="yes", price=0.40, quantity=10, action="sell")
    tracker.record_fill(ticker="M2", side="yes", price=0.35, quantity=10, action="sell")
    tracker.record_fill(ticker="M3", side="yes", price=0.35, quantity=10, action="sell")
    # Premiums collected: (0.40 + 0.35 + 0.35) * 10 = $11.00
    # Payout on winning leg: $1.00 * 10 = $10.00
    # Gross profit: $1.00
    pnl = tracker.calculate_event_pnl(["M1", "M2", "M3"])
    assert pnl["total_premium"] == 11.0
    assert pnl["max_payout"] == 10.0
    assert pnl["gross_profit"] == 1.0


def test_open_positions_list():
    tracker = PositionTracker()
    tracker.record_fill(ticker="M1", side="yes", price=0.40, quantity=10, action="sell")
    tracker.record_fill(ticker="M2", side="yes", price=0.35, quantity=10, action="sell")
    assert len(tracker.open_positions()) == 2
