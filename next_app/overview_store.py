"""Owner-only local snapshot between Todoist and AI overview layout."""

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .config import CONFIG_DIR


class OverviewStore:
    def __init__(self, path: Path = None):
        self.path = Path(path or CONFIG_DIR / "overview.sqlite3")
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.path.parent == CONFIG_DIR:
            os.chmod(self.path.parent, 0o700)
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(fd)
        os.chmod(self.path, 0o600)
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS active (id TEXT PRIMARY KEY, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS completed (id TEXT PRIMARY KEY, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS layout (id TEXT PRIMARY KEY, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            """)

    def connect(self):
        db = sqlite3.connect(str(self.path), timeout=10)
        db.row_factory = sqlite3.Row
        return db

    def meta(self, key, default=None):
        with self.connect() as db:
            row = db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    def set_meta(self, key, value):
        with self.connect() as db:
            db.execute("INSERT INTO meta(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                       (key, str(value)))

    def snapshot(self):
        with self.connect() as db:
            active = [json.loads(row["payload"]) for row in db.execute("SELECT payload FROM active")]
            completed = [json.loads(row["payload"]) for row in db.execute("SELECT payload FROM completed")]
            layout = {row["id"]: json.loads(row["payload"]) for row in db.execute("SELECT id,payload FROM layout")}
        return active, completed, layout

    def sync(self, active, completed, backfill_until=None):
        """Replace the complete active set; upsert historical completions atomically."""
        with self.connect() as db:
            db.execute("DELETE FROM active")
            db.executemany("INSERT INTO active(id,payload) VALUES (?,?)",
                           [(item["id"], json.dumps(item, ensure_ascii=False)) for item in active])
            db.executemany("INSERT INTO completed(id,payload) VALUES (?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload",
                           [(item["id"], json.dumps(item, ensure_ascii=False)) for item in completed])
            db.execute("INSERT INTO meta(key,value) VALUES ('last_sync',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                       (datetime.now(timezone.utc).isoformat(),))
            db.execute("INSERT INTO meta(key,value) VALUES ('dirty','0') ON CONFLICT(key) DO UPDATE SET value='0'")
            if backfill_until:
                db.execute("INSERT INTO meta(key,value) VALUES ('backfill_until',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                           (backfill_until,))

    def save_layout(self, placements, signature=None):
        with self.connect() as db:
            previous = {row["id"]: row["payload"] for row in db.execute("SELECT id,payload FROM layout")}
            current = {item["task_id"]: json.dumps(item, ensure_ascii=False) for item in placements}
            db.executemany("DELETE FROM layout WHERE id=?", [(key,) for key in previous.keys() - current.keys()])
            db.executemany("INSERT INTO layout(id,payload) VALUES (?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload",
                           [(key, value) for key, value in current.items() if previous.get(key) != value])
            if signature is not None:
                db.execute("INSERT INTO meta(key,value) VALUES ('layout_signature',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (signature,))

    def invalidate(self):
        self.set_meta("dirty", "1")
