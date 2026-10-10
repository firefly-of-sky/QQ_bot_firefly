import sqlite3
import time
from pathlib import Path


class Store:
    def __init__(self, path: str):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.executescript('''
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, model TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS turns (id INTEGER PRIMARY KEY, session TEXT NOT NULL, user TEXT NOT NULL, assistant TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS seen (id TEXT PRIMARY KEY, created REAL NOT NULL);
        ''')

    def claim(self, event_id: str) -> bool:
        with self.db:
            self.db.execute("DELETE FROM seen WHERE created < ?", (time.time() - 7 * 86400,))
            result = self.db.execute("INSERT OR IGNORE INTO seen VALUES (?, ?)", (event_id, time.time()))
        return result.rowcount == 1

    def model(self, session: str, default: str) -> str:
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO sessions VALUES (?, ?)", (session, default))
        return self.db.execute("SELECT model FROM sessions WHERE id=?", (session,)).fetchone()[0]

    def reset(self, session: str, model: str | None = None):
        with self.db:
            self.db.execute("DELETE FROM turns WHERE session=?", (session,))
            if model is not None:
                self.db.execute("INSERT OR REPLACE INTO sessions VALUES (?, ?)", (session, model))

    def history(self, session: str, limit: int) -> list[tuple[str, str]]:
        return list(reversed(self.db.execute("SELECT user, assistant FROM turns WHERE session=? ORDER BY id DESC LIMIT ?", (session, limit)).fetchall()))

    def append(self, session: str, user: str, assistant: str, keep: int):
        with self.db:
            self.db.execute("INSERT INTO turns(session,user,assistant) VALUES (?,?,?)", (session, user, assistant))
            self.db.execute("DELETE FROM turns WHERE session=? AND id NOT IN (SELECT id FROM turns WHERE session=? ORDER BY id DESC LIMIT ?)", (session, session, keep))

    def close(self):
        self.db.close()
