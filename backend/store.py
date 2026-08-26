# -*- coding: utf-8 -*-
"""
store.py - PostgreSQL-backed persistence (Neon).

Replaces the local SQLite store so notification-dedup state and manual
IPO overrides survive redeploys/restarts on platforms with ephemeral
disks (e.g. Render's free web service tier, which wipes local files on
every deploy).

Requires a DATABASE_URL env var pointing at your Neon connection string.
"""

import os
import json
import logging
import threading
from contextlib import contextmanager
from datetime import datetime
from typing import Dict, Any, List

import psycopg2
from psycopg2.pool import ThreadedConnectionPool
from dotenv import load_dotenv

logger = logging.getLogger(__name__)
load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError(
        "DATABASE_URL environment variable is not set. "
        "Copy your Neon connection string into .env as DATABASE_URL=..."
    )

# Neon requires SSL. Make sure it's requested even if the copied URL
# didn't include it.
if "sslmode" not in DATABASE_URL:
    sep = "&" if "?" in DATABASE_URL else "?"
    DATABASE_URL = f"{DATABASE_URL}{sep}sslmode=require"

# A small connection pool — cheap to keep a handful of connections open,
# and avoids paying a new-TLS-handshake cost on every request the way a
# single connect()/close() per call would.
_pool = ThreadedConnectionPool(minconn=1, maxconn=5, dsn=DATABASE_URL)
_lock = threading.Lock()


@contextmanager
def _connect():
    conn = _pool.getconn()
    try:
        yield conn
    finally:
        _pool.putconn(conn)


def _init_db() -> None:
    with _lock, _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS notified_ipos (
                    ipo_name    TEXT NOT NULL,
                    apply_date  TEXT NOT NULL,
                    notified_at TIMESTAMPTZ NOT NULL,
                    PRIMARY KEY (ipo_name, apply_date)
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS manual_ipos (
                    ipo_name   TEXT NOT NULL,
                    apply_date TEXT NOT NULL,
                    data       JSONB NOT NULL,
                    PRIMARY KEY (ipo_name, apply_date)
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS subscribers (
                    email         TEXT PRIMARY KEY,
                    subscribed_at TIMESTAMPTZ NOT NULL
                )
            """)
        conn.commit()
    logger.info("Initialized Postgres store (Neon)")


# ── Notification dedup ─────────────────────────────────────────────────────────

def is_notified(ipo_name: str, apply_date: str) -> bool:
    """Return True if we've already sent a notification for this IPO+date."""
    with _lock, _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM notified_ipos WHERE ipo_name = %s AND apply_date = %s",
                (ipo_name, apply_date),
            )
            return cur.fetchone() is not None


def mark_notified(ipo_name: str, apply_date: str) -> None:
    """Record that a notification was sent for this IPO+date."""
    with _lock, _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO notified_ipos (ipo_name, apply_date, notified_at) "
                "VALUES (%s, %s, %s) "
                "ON CONFLICT (ipo_name, apply_date) DO NOTHING",
                (ipo_name, apply_date, datetime.now()),
            )
        conn.commit()


# ── Manual IPO overrides (replacement for /api/add_ipo → SheetDB) ─────────────

def add_manual_ipo(ipo: Dict[str, Any]) -> None:
    """Add or replace a manually-entered IPO, keyed on (IPO, Apply Date)."""
    ipo_name = ipo.get("IPO", "")
    apply_date = ipo.get("Apply Date", "")
    if not ipo_name or not apply_date:
        raise ValueError("Manual IPO entry requires both 'IPO' and 'Apply Date'")
    with _lock, _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO manual_ipos (ipo_name, apply_date, data) "
                "VALUES (%s, %s, %s) "
                "ON CONFLICT (ipo_name, apply_date) DO UPDATE SET data = EXCLUDED.data",
                (ipo_name, apply_date, json.dumps(ipo)),
            )
        conn.commit()


def get_manual_ipos() -> List[Dict[str, Any]]:
    """Return all manually-added IPO overrides."""
    with _lock, _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT data FROM manual_ipos")
            # psycopg2 decodes JSONB columns straight into dict/list.
            return [row[0] for row in cur.fetchall()]


def remove_manual_ipo(ipo_name: str, apply_date: str) -> bool:
    """Delete a manual override. Returns True if a row was removed."""
    with _lock, _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM manual_ipos WHERE ipo_name = %s AND apply_date = %s",
                (ipo_name, apply_date),
            )
            removed = cur.rowcount > 0
        conn.commit()
        return removed


# ── Subscribed users (replacement for the SUBSCRIBED_USERS env var) ───────────

def add_subscriber(email: str) -> None:
    """Add an email to the notification subscriber list (idempotent)."""
    email = email.strip().lower()
    if not email or "@" not in email:
        raise ValueError("A valid email address is required")
    with _lock, _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO subscribers (email, subscribed_at) VALUES (%s, %s) "
                "ON CONFLICT (email) DO NOTHING",
                (email, datetime.now()),
            )
        conn.commit()


def remove_subscriber(email: str) -> bool:
    """Remove an email from the subscriber list. Returns True if it existed."""
    email = email.strip().lower()
    with _lock, _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM subscribers WHERE email = %s", (email,))
            removed = cur.rowcount > 0
        conn.commit()
        return removed


def get_subscribers() -> List[str]:
    """Return all subscribed email addresses."""
    with _lock, _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT email FROM subscribers ORDER BY subscribed_at")
            return [row[0] for row in cur.fetchall()]


_init_db()