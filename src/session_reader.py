import glob
from pathlib import Path

import duckdb


class SessionReader:
    def __init__(self, session_dir: str):
        self._dir = Path(session_dir)

    def find_sessions(self, start: float | None = None, end: float | None = None) -> list[Path]:
        pattern = str(self._dir / "session_*.duckdb")
        paths = sorted(glob.glob(pattern))
        result = []
        for p in paths:
            name = Path(p).stem
            try:
                ts = float(name.replace("session_", ""))
            except ValueError:
                continue
            if end is not None and ts > end:
                continue
            result.append(Path(p))
        return result

    def query_across(self, sql: str, params: tuple = (),
                     start: float | None = None, end: float | None = None) -> list[tuple]:
        files = self.find_sessions(start=start, end=end)
        rows: list[tuple] = []
        for f in files:
            try:
                conn = duckdb.connect(str(f), read_only=True)
                rows.extend(conn.execute(sql, list(params)).fetchall())
                conn.close()
            except duckdb.Error:
                continue
        return rows
