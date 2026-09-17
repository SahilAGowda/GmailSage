"""
Tracks which message IDs we've already classified so re-runs don't
reclassify or double-label the same email.
"""
import os
import sqlite3
from contextlib import contextmanager

DB_PATH = os.environ.get("TRIAGE_DB_PATH", "triage.db")


def init_db():
    with _conn() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS processed (
                message_id TEXT PRIMARY KEY,
                category TEXT,
                priority TEXT,
                source TEXT,       -- 'rules' or 'llm'
                subject TEXT,
                sender_domain TEXT,
                processed_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # Migrate old DBs that lack new columns
        cols = [r[1] for r in c.execute("PRAGMA table_info(processed)").fetchall()]
        if "subject" not in cols:
            c.execute("ALTER TABLE processed ADD COLUMN subject TEXT")
        if "sender_domain" not in cols:
            c.execute("ALTER TABLE processed ADD COLUMN sender_domain TEXT")
        c.execute("""
            CREATE TABLE IF NOT EXISTS undo_log (
                message_id TEXT PRIMARY KEY,
                category TEXT,
                undone_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)


@contextmanager
def _conn():
    conn = sqlite3.connect(DB_PATH)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def already_processed(message_id: str) -> bool:
    with _conn() as c:
        row = c.execute(
            "SELECT 1 FROM processed WHERE message_id = ?", (message_id,)
        ).fetchone()
        return row is not None


def mark_processed(message_id: str, category: str, priority: str, source: str, subject: str = "", sender_domain: str = ""):
    with _conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO processed (message_id, category, priority, source, subject, sender_domain, processed_at) VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)",
            (message_id, category, priority, source, subject[:200], sender_domain),
        )


def get_recent_processed(hours: int = 24):
    with _conn() as c:
        return c.execute(
            """SELECT message_id, category, priority, source, subject, sender_domain, processed_at FROM processed
               WHERE processed_at >= datetime('now', ?)
               ORDER BY processed_at DESC""",
            (f"-{hours} hours",),
        ).fetchall()


def get_stats(hours: int = 24):
    with _conn() as c:
        rows = c.execute(
            """SELECT category, priority, source, COUNT(*) FROM processed
               WHERE processed_at >= datetime('now', ?)
               GROUP BY category, priority, source""",
            (f"-{hours} hours",),
        ).fetchall()
        total = c.execute(
            "SELECT COUNT(*) FROM processed WHERE processed_at >= datetime('now', ?)",
            (f"-{hours} hours",),
        ).fetchone()[0]
        return rows, total


def get_high_priority_since(hours: int = 24):
    with _conn() as c:
        return c.execute(
            """SELECT message_id, category, priority FROM processed
               WHERE priority = 'high'
               AND processed_at >= datetime('now', ?)""",
            (f"-{hours} hours",),
        ).fetchall()


def get_undo_candidates(hours: int = 24):
    with _conn() as c:
        return c.execute(
            """SELECT message_id, category FROM processed
               WHERE processed_at >= datetime('now', ?)
               AND message_id NOT IN (SELECT message_id FROM undo_log)""",
            (f"-{hours} hours",),
        ).fetchall()


def mark_undone(message_id: str):
    with _conn() as c:
        c.execute("INSERT OR REPLACE INTO undo_log VALUES (?, '', CURRENT_TIMESTAMP)", (message_id,))
