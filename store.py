"""
Tracks which message IDs we've already classified so re-runs don't
reclassify or double-label the same email. Also holds the persistent LLM
cache, the known-contacts cache, and senders you've rescued from archive.
"""
import json
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
        if "sender_address" not in cols:
            c.execute("ALTER TABLE processed ADD COLUMN sender_address TEXT")
        c.execute("""
            CREATE TABLE IF NOT EXISTS undo_log (
                message_id TEXT PRIMARY KEY,
                category TEXT,
                undone_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS llm_cache (
                key TEXT PRIMARY KEY,
                result TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS contacts (
                address TEXT PRIMARY KEY,
                known INTEGER,
                checked_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # Senders whose mail you moved back to the inbox: never archive again.
        c.execute("""
            CREATE TABLE IF NOT EXISTS keep_senders (
                address TEXT PRIMARY KEY,
                reason TEXT,
                added_at TEXT DEFAULT CURRENT_TIMESTAMP
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


def mark_processed(message_id: str, category: str, priority: str, source: str, subject: str = "",
                   sender_domain: str = "", sender_address: str = ""):
    with _conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO processed (message_id, category, priority, source, subject, sender_domain, sender_address, processed_at) VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)",
            (message_id, category, priority, source, subject[:200], sender_domain, sender_address),
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


def get_digest_items(hours: int = 24):
    """High priority, plus medium items other than the generic 'other' bucket."""
    with _conn() as c:
        return c.execute(
            """SELECT message_id, category, priority, subject FROM processed
               WHERE (priority = 'high' OR (priority = 'medium' AND category != 'other'))
               AND processed_at >= datetime('now', ?)
               AND message_id NOT IN (SELECT message_id FROM undo_log)
               ORDER BY priority = 'high' DESC, processed_at DESC""",
            (f"-{hours} hours",),
        ).fetchall()


def get_top_archived_senders(days: int = 7, limit: int = 10):
    with _conn() as c:
        return c.execute(
            """SELECT sender_domain, COUNT(*) n FROM processed
               WHERE priority = 'low' AND sender_domain IS NOT NULL AND sender_domain != ''
               AND processed_at >= datetime('now', ?)
               GROUP BY sender_domain ORDER BY n DESC LIMIT ?""",
            (f"-{days} days", limit),
        ).fetchall()


def get_undo_candidates(hours: int = 24, sender: str = ""):
    """Only archived (low) mail — medium/high were never removed from the inbox."""
    query = """SELECT message_id, category FROM processed
               WHERE priority = 'low'
               AND processed_at >= datetime('now', ?)
               AND message_id NOT IN (SELECT message_id FROM undo_log)"""
    params = [f"-{hours} hours"]
    if sender:
        query += " AND (sender_domain = ? OR sender_address = ?)"
        params += [sender.lower(), sender.lower()]
    with _conn() as c:
        return c.execute(query, params).fetchall()


def mark_undone(message_id: str, category: str = ""):
    with _conn() as c:
        c.execute("INSERT OR REPLACE INTO undo_log VALUES (?, ?, CURRENT_TIMESTAMP)", (message_id, category))


def get_archived_since(days: int = 14):
    """Archived mail we could check for 'user moved it back to inbox'."""
    with _conn() as c:
        return c.execute(
            """SELECT message_id, category, sender_address FROM processed
               WHERE priority = 'low' AND processed_at >= datetime('now', ?)
               AND message_id NOT IN (SELECT message_id FROM undo_log)""",
            (f"-{days} days",),
        ).fetchall()


# --- LLM cache -------------------------------------------------------------

def cache_get(key: str):
    with _conn() as c:
        row = c.execute("SELECT result FROM llm_cache WHERE key = ?", (key,)).fetchone()
        return json.loads(row[0]) if row else None


def cache_put(key: str, result: dict):
    with _conn() as c:
        c.execute("INSERT OR REPLACE INTO llm_cache (key, result) VALUES (?, ?)", (key, json.dumps(result)))


# --- Known contacts --------------------------------------------------------

def contact_get(address: str, max_age_days: int = 30):
    """Returns True/False if checked recently, None if unknown/stale."""
    with _conn() as c:
        row = c.execute(
            "SELECT known FROM contacts WHERE address = ? AND checked_at >= datetime('now', ?)",
            (address, f"-{max_age_days} days"),
        ).fetchone()
        return bool(row[0]) if row else None


def contact_put(address: str, known: bool):
    with _conn() as c:
        c.execute("INSERT OR REPLACE INTO contacts (address, known, checked_at) VALUES (?, ?, CURRENT_TIMESTAMP)",
                  (address, int(known)))


# --- Senders rescued by the user -------------------------------------------

def is_keep_sender(address: str) -> bool:
    with _conn() as c:
        return c.execute("SELECT 1 FROM keep_senders WHERE address = ?", (address,)).fetchone() is not None


def add_keep_sender(address: str, reason: str):
    with _conn() as c:
        c.execute("INSERT OR IGNORE INTO keep_senders (address, reason) VALUES (?, ?)", (address, reason))
