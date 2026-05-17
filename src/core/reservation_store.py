import json
import os
import tempfile
from dataclasses import dataclass, asdict
from datetime import datetime, timezone


@dataclass
class Reservation:
    ticker: str
    side: str
    quantity: int
    exchange: str
    created_at: str
    note: str = ""


class ReservationStore:
    def __init__(self, path: str = "data/reservations.json"):
        self._path = path
        self._reservations: dict[str, Reservation] = {}
        self._load()

    def _load(self):
        if not os.path.exists(self._path):
            return
        try:
            with open(self._path) as f:
                data = json.load(f)
            for entry in data:
                r = Reservation(**entry)
                self._reservations[r.ticker] = r
        except (json.JSONDecodeError, TypeError, KeyError):
            pass

    def _save(self):
        os.makedirs(os.path.dirname(self._path) or ".", exist_ok=True)
        data = [asdict(r) for r in self._reservations.values()]
        dir_name = os.path.dirname(self._path) or "."
        fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(data, f, indent=2)
            os.replace(tmp_path, self._path)
        except Exception:
            os.unlink(tmp_path)
            raise

    def reserve(self, ticker: str, side: str, quantity: int, exchange: str, note: str = "") -> None:
        self._reservations[ticker] = Reservation(
            ticker=ticker,
            side=side,
            quantity=quantity,
            exchange=exchange,
            created_at=datetime.now(timezone.utc).isoformat(),
            note=note,
        )
        self._save()

    def release(self, ticker: str) -> None:
        self._reservations.pop(ticker, None)
        self._save()

    def is_reserved(self, ticker: str) -> bool:
        return ticker in self._reservations

    def get_reserved_quantity(self, ticker: str, side: str) -> int:
        r = self._reservations.get(ticker)
        if r and r.side == side:
            return r.quantity
        return 0

    def list_all(self) -> list[Reservation]:
        return list(self._reservations.values())
