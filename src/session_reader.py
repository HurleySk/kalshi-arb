import glob
import sqlite3
from pathlib import Path


class SessionReader:
    def __init__(self, session_dir: str):
        self._dir = Path(session_dir)

    def find_sessions(self, start: float | None = None, end: float | None = None) -> list[Path]:
        pattern = str(self._dir / "session_*.db")
        paths = sorted(glob.glob(pattern))
        result = []
        for p in paths:
            name = Path(p).stem
            try:
                ts = float(name.replace("session_", ""))
            except ValueError:
                continue
            if start is not None and ts < start:
                continue
            if end is not None and ts > end:
                continue
            result.append(Path(p))
        return result

    def query_across(self, sql: str, params: tuple = (),
                     start: float | None = None, end: float | None = None) -> list[sqlite3.Row]:
        files = self.find_sessions(start=start, end=end)
        rows: list[sqlite3.Row] = []
        for f in files:
            try:
                conn = sqlite3.connect(str(f), check_same_thread=False)
                conn.row_factory = sqlite3.Row
                rows.extend(conn.execute(sql, params).fetchall())
                conn.close()
            except sqlite3.DatabaseError:
                continue
        return rows
