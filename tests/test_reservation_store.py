import json
import os
import tempfile

from src.core.reservation_store import ReservationStore, Reservation


def test_reserve_creates_entry():
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        store = ReservationStore(path=path)
        store.reserve("KXBTC-25MAY16-T55000", "yes", 5, "kalshi", note="my BTC bet")
        assert store.is_reserved("KXBTC-25MAY16-T55000")
        assert store.get_reserved_quantity("KXBTC-25MAY16-T55000", "yes") == 5
    finally:
        os.unlink(path)


def test_release_removes_entry():
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        store = ReservationStore(path=path)
        store.reserve("KXBTC-25MAY16-T55000", "yes", 5, "kalshi")
        store.release("KXBTC-25MAY16-T55000")
        assert not store.is_reserved("KXBTC-25MAY16-T55000")
        assert store.get_reserved_quantity("KXBTC-25MAY16-T55000", "yes") == 0
    finally:
        os.unlink(path)


def test_persistence_survives_reload():
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        store = ReservationStore(path=path)
        store.reserve("KXBTC-25MAY16-T55000", "yes", 3, "kalshi")

        store2 = ReservationStore(path=path)
        assert store2.is_reserved("KXBTC-25MAY16-T55000")
        assert store2.get_reserved_quantity("KXBTC-25MAY16-T55000", "yes") == 3
    finally:
        os.unlink(path)


def test_list_all_returns_reservations():
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        store = ReservationStore(path=path)
        store.reserve("M1", "yes", 2, "kalshi")
        store.reserve("M2", "no", 4, "predictit", note="hedge")
        all_res = store.list_all()
        assert len(all_res) == 2
        tickers = {r.ticker for r in all_res}
        assert tickers == {"M1", "M2"}
    finally:
        os.unlink(path)


def test_reserve_updates_existing():
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        store = ReservationStore(path=path)
        store.reserve("M1", "yes", 2, "kalshi")
        store.reserve("M1", "yes", 5, "kalshi")
        assert store.get_reserved_quantity("M1", "yes") == 5
        assert len(store.list_all()) == 1
    finally:
        os.unlink(path)


def test_get_reserved_quantity_wrong_side_returns_zero():
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        store = ReservationStore(path=path)
        store.reserve("M1", "yes", 5, "kalshi")
        assert store.get_reserved_quantity("M1", "no") == 0
    finally:
        os.unlink(path)


def test_empty_file_loads_gracefully():
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        store = ReservationStore(path=path)
        assert store.list_all() == []
        assert not store.is_reserved("anything")
    finally:
        os.unlink(path)
