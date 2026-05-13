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


def test_buy_decrements_position():
    tracker = PositionTracker()
    tracker.record_fill(ticker="M1", side="yes", price=0.55, quantity=10, action="sell")
    tracker.record_fill(ticker="M1", side="yes", price=0.60, quantity=5, action="buy")
    pos = tracker.get_position("M1")
    assert pos is not None
    assert pos.quantity == 5


def test_buy_fully_closes_position():
    tracker = PositionTracker()
    tracker.record_fill(ticker="M1", side="yes", price=0.55, quantity=10, action="sell")
    tracker.record_fill(ticker="M1", side="yes", price=0.60, quantity=10, action="buy")
    pos = tracker.get_position("M1")
    assert pos is None


def test_open_positions_excludes_closed():
    tracker = PositionTracker()
    tracker.record_fill(ticker="M1", side="yes", price=0.55, quantity=10, action="sell")
    tracker.record_fill(ticker="M2", side="yes", price=0.40, quantity=5, action="sell")
    tracker.record_fill(ticker="M1", side="yes", price=0.60, quantity=10, action="buy")
    positions = tracker.open_positions()
    assert len(positions) == 1
    assert positions[0].ticker == "M2"


def test_buy_tracks_realized_pnl():
    tracker = PositionTracker()
    tracker.record_fill(ticker="M1", side="yes", price=0.55, quantity=10, action="sell")
    tracker.record_fill(ticker="M1", side="yes", price=0.60, quantity=10, action="buy")
    assert abs(tracker.realized_pnl - (-0.5)) < 1e-9


def test_load_position_creates_long():
    tracker = PositionTracker()
    tracker.load_position("M1", "yes", 3)
    pos = tracker.get_position("M1")
    assert pos is not None
    assert pos.quantity == 3
    assert pos.avg_price == 0.0
    assert pos.opened_by == "buy"


def test_load_position_close_via_sell():
    # inherited long is closed by a sell fill; P&L is overstated (avg_price=0) but position clears
    tracker = PositionTracker()
    tracker.load_position("M1", "yes", 2)
    tracker.record_fill(ticker="M1", side="yes", price=0.75, quantity=2, action="sell")
    assert tracker.get_position("M1") is None
    assert abs(tracker.realized_pnl - 1.50) < 1e-9  # overstated: (0.75 - 0.00) * 2


def test_buy_without_prior_sell_opens_long():
    # buy-side arb: initial buy opens a long position
    tracker = PositionTracker()
    tracker.record_fill(ticker="M1", side="yes", price=0.05, quantity=2, action="buy")
    pos = tracker.get_position("M1")
    assert pos is not None
    assert pos.quantity == 2
    assert pos.avg_price == 0.05
    assert tracker.realized_pnl == 0.0


def test_sell_closes_long_position():
    # buy-side arb unwind: sell closes an existing long
    tracker = PositionTracker()
    tracker.record_fill(ticker="M1", side="yes", price=0.05, quantity=5, action="buy")
    tracker.record_fill(ticker="M1", side="yes", price=0.80, quantity=5, action="sell")
    pos = tracker.get_position("M1")
    assert pos is None
    assert abs(tracker.realized_pnl - 3.75) < 1e-9  # (0.80 - 0.05) * 5


def test_multiple_buys_average_long():
    tracker = PositionTracker()
    tracker.record_fill(ticker="M1", side="yes", price=0.04, quantity=3, action="buy")
    tracker.record_fill(ticker="M1", side="yes", price=0.06, quantity=3, action="buy")
    pos = tracker.get_position("M1")
    assert pos.quantity == 6
    assert abs(pos.avg_price - 0.05) < 1e-9
