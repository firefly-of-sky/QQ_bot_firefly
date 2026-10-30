import sqlite3
import time
from pathlib import Path


class Store:
    def __init__(self, path: str):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.executescript('''
            PRAGMA journal_mode=WAL;
            PRAGMA secure_delete=ON;
            CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, model TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS turns (id INTEGER PRIMARY KEY, session TEXT NOT NULL, user TEXT NOT NULL, assistant TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS recalled (session TEXT, message_id TEXT, created REAL, PRIMARY KEY(session,message_id));
            CREATE TABLE IF NOT EXISTS turn_sources (turn_id INTEGER, session TEXT, message_id TEXT);
            CREATE INDEX IF NOT EXISTS source_lookup ON turn_sources(session,message_id);
            CREATE TABLE IF NOT EXISTS seen (id TEXT PRIMARY KEY, created REAL NOT NULL);
        ''')

    def claim(self, event_id: str) -> bool:
        with self.db:
            self.db.execute("DELETE FROM seen WHERE created < ?", (time.time() - 7 * 86400,))
            result = self.db.execute("INSERT OR IGNORE INTO seen VALUES (?, ?)", (event_id, time.time()))
        return result.rowcount == 1

    def is_recalled(self, session: str, message_id) -> bool:
        return self.db.execute("SELECT 1 FROM recalled WHERE session=? AND message_id=?",
                               (session, str(message_id))).fetchone() is not None

    def recall(self, session: str, message_id):
        with self.db:
            self.db.execute("DELETE FROM recalled WHERE created < ?", (time.time() - 7 * 86400,))
            self.db.execute("INSERT OR IGNORE INTO recalled VALUES (?,?,?)", (session, str(message_id), time.time()))
            self.db.execute("DELETE FROM turns WHERE id IN (SELECT turn_id FROM turn_sources WHERE session=? AND message_id=?)",
                            (session, str(message_id)))
            self.db.execute("DELETE FROM turn_sources WHERE turn_id NOT IN (SELECT id FROM turns)")

    def source_ids(self, session):
        return {row[0] for row in self.db.execute("SELECT message_id FROM turn_sources WHERE session=?", (session,))}

    def model(self, session: str, default: str) -> str:
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO sessions VALUES (?, ?)", (session, default))
        return self.db.execute("SELECT model FROM sessions WHERE id=?", (session,)).fetchone()[0]

    def reset(self, session: str, model: str | None = None):
        with self.db:
            self.db.execute("DELETE FROM turns WHERE session=?", (session,))
            self.db.execute("DELETE FROM turn_sources WHERE session=?", (session,))
            if model is not None:
                self.db.execute("INSERT OR REPLACE INTO sessions VALUES (?, ?)", (session, model))

    def history(self, session: str, limit: int) -> list[tuple[str, str]]:
        return list(reversed(self.db.execute("SELECT user, assistant FROM turns WHERE session=? ORDER BY id DESC LIMIT ?", (session, limit)).fetchall()))

    def history_with_sources(self, session: str, limit: int) -> list[dict]:
        rows = self.db.execute('SELECT id,user,assistant FROM turns WHERE session=? ORDER BY id DESC LIMIT ?',
                               (session, limit)).fetchall()
        return [dict(user=user, assistant=assistant, sources=[r[0] for r in self.db.execute(
            'SELECT message_id FROM turn_sources WHERE turn_id=? AND session=?', (tid, session))])
            for tid, user, assistant in reversed(rows)]

    def append(self, session: str, user: str, assistant: str, keep: int, sources=()):
        with self.db:
            result = self.db.execute("INSERT INTO turns(session,user,assistant) VALUES (?,?,?)", (session, user, assistant))
            self.db.executemany("INSERT INTO turn_sources VALUES (?,?,?)",
                                [(result.lastrowid, session, str(source)) for source in sources])
            self.db.execute("DELETE FROM turns WHERE session=? AND id NOT IN (SELECT id FROM turns WHERE session=? ORDER BY id DESC LIMIT ?)", (session, session, keep))

            self.db.execute("DELETE FROM turn_sources WHERE turn_id NOT IN (SELECT id FROM turns)")

    def close(self):
        self.db.close()
