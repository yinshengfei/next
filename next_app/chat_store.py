"""Small, local conversation log and explicit Todoist draft state."""

import json
import os
import sqlite3
from pathlib import Path
from uuid import uuid4

from .config import CONFIG_DIR


CHAT_PATH = CONFIG_DIR / "chat.sqlite3"


class ChatStore:
    def __init__(self, path: Path = CHAT_PATH):
        self.path = Path(path)
        parent_existed = self.path.parent.exists()
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not parent_existed or self.path.parent == CONFIG_DIR:
            os.chmod(self.path.parent, 0o700)
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(fd)
        os.chmod(self.path, 0o600)
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY,
                    role TEXT NOT NULL CHECK(role IN ('user', 'assistant')),
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS drafts (
                    id TEXT PRIMARY KEY,
                    message_id INTEGER NOT NULL REFERENCES messages(id),
                    payload TEXT NOT NULL,
                    state TEXT NOT NULL DEFAULT 'pending',
                    task_id TEXT,
                    error TEXT
                );
            """)
            # A crash after a Todoist write may leave a draft in progress.
            # Require a manual Todoist check rather than silently retrying.
            db.execute("UPDATE drafts SET state='uncertain', error=? WHERE state='creating'", (
                "Next closed during creation. Check Todoist before trying again.",
            ))

    def connect(self):
        db = sqlite3.connect(str(self.path), timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        return db

    def add_message(self, role: str, content: str) -> int:
        with self.connect() as db:
            return db.execute("INSERT INTO messages(role, content) VALUES (?, ?)", (role, content)).lastrowid

    def add_reply(self, content: str, drafts: list) -> list:
        with self.connect() as db:
            message_id = db.execute(
                "INSERT INTO messages(role, content) VALUES ('assistant', ?)", (content,)
            ).lastrowid
            saved = []
            for draft in drafts:
                draft_id = uuid4().hex
                payload = draft.model_dump()
                db.execute(
                    "INSERT INTO drafts(id, message_id, payload) VALUES (?, ?, ?)",
                    (draft_id, message_id, json.dumps(payload, ensure_ascii=False)),
                )
                saved.append({"id": draft_id, **payload, "state": "pending"})
            return saved

    def recent(self, limit: int = 12) -> list:
        with self.connect() as db:
            rows = db.execute(
                "SELECT role, content FROM messages ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(row) for row in reversed(rows)]

    def history(self) -> dict:
        with self.connect() as db:
            messages = [dict(row) for row in db.execute(
                "SELECT id, role, content, created_at FROM messages ORDER BY id"
            )]
            drafts = [
                {"id": row["id"], "message_id": row["message_id"],
                 **json.loads(row["payload"]), "state": row["state"],
                 "task_id": row["task_id"], "error": row["error"]}
                for row in db.execute("SELECT * FROM drafts ORDER BY rowid")
            ]
        return {"messages": messages, "drafts": drafts}

    def claim(self, draft_id: str, payload: dict) -> bool:
        with self.connect() as db:
            changed = db.execute(
                "UPDATE drafts SET state='creating', payload=?, error=NULL "
                "WHERE id=? AND state='pending'",
                (json.dumps(payload, ensure_ascii=False), draft_id),
            ).rowcount
        return changed == 1

    def finish(self, draft_id: str, state: str, task_id: str = None, error: str = None):
        with self.connect() as db:
            db.execute(
                "UPDATE drafts SET state=?, task_id=?, error=? WHERE id=? AND state='creating'",
                (state, task_id, error, draft_id),
            )

    def clear(self) -> bool:
        with self.connect() as db:
            if db.execute("SELECT 1 FROM drafts WHERE state='creating' LIMIT 1").fetchone():
                return False
            db.execute("DELETE FROM drafts")
            db.execute("DELETE FROM messages")
        return True
