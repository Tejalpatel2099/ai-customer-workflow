"""Data layer: SQLite storage for tickets and an activity log.

Deliberately boring on purpose: the standard-library ``sqlite3`` module,
one short-lived connection per operation, and plain SQL. For a demo-scale
app this is easier to read and debug than an ORM, and every query in the
project lives in this one file.

The database path can be overridden with the ``WORKFLOW_DB_PATH``
environment variable, which is how the tests get an isolated database.
"""

from __future__ import annotations

import os
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

STATUSES = ("New", "Pending approval", "Resolved", "Needs attention")
CATEGORIES = ("Billing", "Technical", "Account", "Shipping", "General")
URGENCIES = ("low", "medium", "high")


def db_path() -> Path:
    """Where the SQLite file lives. Evaluated per call so tests can
    point the app at a temporary database via the environment."""
    return Path(os.environ.get("WORKFLOW_DB_PATH", PROJECT_ROOT / "workflow.db"))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(db_path())
    conn.row_factory = sqlite3.Row
    return conn


SCHEMA = """
CREATE TABLE IF NOT EXISTS tickets (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_name  TEXT NOT NULL,
    customer_email TEXT NOT NULL,
    subject        TEXT NOT NULL,
    body           TEXT NOT NULL,
    category       TEXT NOT NULL DEFAULT 'General',
    status         TEXT NOT NULL DEFAULT 'New',
    ai_category    TEXT,
    urgency        TEXT,
    sentiment      TEXT,
    summary        TEXT,
    draft_reply    TEXT,
    next_action    TEXT,
    mode           TEXT,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL,
    processed_at   TEXT,
    decided_at     TEXT
);
CREATE TABLE IF NOT EXISTS events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id  INTEGER,
    action     TEXT NOT NULL,
    detail     TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
"""


def init_db() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)


@dataclass
class Ticket:
    id: int | None
    customer_name: str
    customer_email: str
    subject: str
    body: str
    category: str = "General"
    status: str = "New"
    ai_category: str | None = None
    urgency: str | None = None
    sentiment: str | None = None
    summary: str | None = None
    draft_reply: str | None = None
    next_action: str | None = None
    mode: str | None = None
    created_at: str = ""
    updated_at: str = ""
    processed_at: str | None = None
    decided_at: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def _row_to_ticket(row: sqlite3.Row) -> Ticket:
    return Ticket(**dict(row))


def create_ticket(customer_name: str, customer_email: str, subject: str,
                  body: str, category: str = "General") -> Ticket:
    now = _now()
    with connect() as conn:
        cur = conn.execute(
            """INSERT INTO tickets (customer_name, customer_email, subject, body,
                                    category, status, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, 'New', ?, ?)""",
            (customer_name, customer_email, subject, body, category, now, now),
        )
        ticket_id = cur.lastrowid
    ticket = get_ticket(ticket_id)
    assert ticket is not None
    log_event(ticket_id, "created", f"Ticket created: {subject}")
    return ticket


def get_ticket(ticket_id: int) -> Ticket | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
    return _row_to_ticket(row) if row else None


def list_tickets(status: str | None = None, category: str | None = None) -> list[Ticket]:
    query = "SELECT * FROM tickets"
    clauses, params = [], []
    if status:
        clauses.append("status = ?")
        params.append(status)
    if category:
        clauses.append("category = ?")
        params.append(category)
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY id DESC"
    with connect() as conn:
        rows = conn.execute(query, params).fetchall()
    return [_row_to_ticket(r) for r in rows]


def count_tickets() -> int:
    with connect() as conn:
        return conn.execute("SELECT COUNT(*) FROM tickets").fetchone()[0]


def update_ticket(ticket_id: int, **fields) -> Ticket | None:
    """Update whitelisted columns and stamp updated_at."""
    allowed = {
        "status", "ai_category", "urgency", "sentiment", "summary",
        "draft_reply", "next_action", "mode", "processed_at", "decided_at",
        "category",
    }
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return get_ticket(ticket_id)
    updates["updated_at"] = _now()
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    with connect() as conn:
        conn.execute(f"UPDATE tickets SET {set_clause} WHERE id = ?",
                     (*updates.values(), ticket_id))
    return get_ticket(ticket_id)


def log_event(ticket_id: int | None, action: str, detail: str = "") -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO events (ticket_id, action, detail, created_at) VALUES (?, ?, ?, ?)",
            (ticket_id, action, detail, _now()),
        )


def list_events(limit: int = 10) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT ticket_id, action, detail, created_at FROM events ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def stats() -> dict:
    """Aggregate counts for the dashboard.

    Approval rate = approved / (approved + rejected) over tickets that
    reached a decision, so it measures the AI drafts' acceptance, not
    overall resolution.
    """
    with connect() as conn:
        by_status = {r[0]: r[1] for r in conn.execute(
            "SELECT status, COUNT(*) FROM tickets GROUP BY status")}
        by_category = {r[0]: r[1] for r in conn.execute(
            "SELECT COALESCE(ai_category, category), COUNT(*) FROM tickets GROUP BY 1")}
        by_urgency = {r[0]: r[1] for r in conn.execute(
            "SELECT urgency, COUNT(*) FROM tickets WHERE urgency IS NOT NULL GROUP BY urgency")}
        decided = conn.execute(
            "SELECT action, COUNT(*) FROM events WHERE action IN ('approved', 'rejected') GROUP BY action"
        ).fetchall()
    decisions = {r[0]: r[1] for r in decided}
    approved, rejected = decisions.get("approved", 0), decisions.get("rejected", 0)
    total_decisions = approved + rejected
    approval_rate = round(approved / total_decisions, 3) if total_decisions else None
    return {
        "total": sum(by_status.values()),
        "by_status": {s: by_status.get(s, 0) for s in STATUSES},
        "by_category": {c: by_category.get(c, 0) for c in CATEGORIES},
        "by_urgency": {u: by_urgency.get(u, 0) for u in URGENCIES},
        "decisions": {"approved": approved, "rejected": rejected},
        "approval_rate": approval_rate,
        "recent_activity": list_events(10),
    }
