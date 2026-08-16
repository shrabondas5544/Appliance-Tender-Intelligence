"""
Storage layer.

This is the ONLY file in the project that knows about SQLite. Everything else
calls save_records() / get_unnotified_records() / etc.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, List, Optional

from core.models import TenderRecord
from core.settings import settings

DB_PATH = Path(settings.DATABASE_URL.replace("sqlite:///", ""))
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS tenders (
    dedup_key           TEXT PRIMARY KEY,
    tender_id           TEXT NOT NULL,
    source_portal       TEXT NOT NULL,
    source_type         TEXT NOT NULL DEFAULT 'EGP',
    source_language     TEXT NOT NULL DEFAULT 'EN',
    title               TEXT NOT NULL,
    category_matched    TEXT NOT NULL,
    procuring_entity    TEXT,
    publish_date        TEXT,
    closing_date        TEXT,
    estimated_value_bdt REAL,
    quantity            INTEGER,
    ocr_confidence      REAL,
    clipped_image_url   TEXT,
    detail_url          TEXT,
    is_manual_tender    INTEGER NOT NULL DEFAULT 0,
    is_high_value       INTEGER NOT NULL DEFAULT 0,
    is_critical         INTEGER NOT NULL DEFAULT 0,
    scraped_at          TEXT NOT NULL,
    raw_snippet         TEXT,
    notified_at         TEXT
);
"""

CREATE_INDEXES_SQL = """
CREATE INDEX IF NOT EXISTS idx_closing_date ON tenders(closing_date);
CREATE INDEX IF NOT EXISTS idx_notified ON tenders(notified_at);
CREATE INDEX IF NOT EXISTS idx_source_type ON tenders(source_type);
"""


@contextmanager
def get_conn() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _migrate_db(conn: sqlite3.Connection) -> None:
    """Safely migrate existing database tables without breaking historical records."""
    cursor = conn.cursor()
    cursor.execute("PRAGMA table_info(tenders)")
    existing_cols = {row["name"] for row in cursor.fetchall()}

    migrations = [
        ("source_type", "TEXT NOT NULL DEFAULT 'EGP'"),
        ("source_language", "TEXT NOT NULL DEFAULT 'EN'"),
        ("ocr_confidence", "REAL"),
        ("clipped_image_url", "TEXT"),
    ]

    for col_name, col_type in migrations:
        if col_name not in existing_cols:
            cursor.execute(f"ALTER TABLE tenders ADD COLUMN {col_name} {col_type}")


def init_db() -> None:
    with get_conn() as conn:
        conn.execute(CREATE_TABLE_SQL)
        _migrate_db(conn)
        conn.executescript(CREATE_INDEXES_SQL)


def save_records(records: List[TenderRecord]) -> int:
    """Insert new records, silently skip duplicates. Returns count of NEW records inserted."""
    inserted = 0
    with get_conn() as conn:
        for r in records:
            try:
                conn.execute(
                    """
                    INSERT INTO tenders (
                        dedup_key, tender_id, source_portal, source_type, source_language,
                        title, category_matched, procuring_entity, publish_date, closing_date,
                        estimated_value_bdt, quantity, ocr_confidence, clipped_image_url,
                        detail_url, is_manual_tender, is_high_value, is_critical,
                        scraped_at, raw_snippet
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        r.dedup_key(),
                        r.tender_id,
                        r.source_portal,
                        r.source_type,
                        r.source_language,
                        r.title,
                        r.category_matched,
                        r.procuring_entity,
                        r.publish_date.isoformat() if r.publish_date else None,
                        r.closing_date.isoformat() if r.closing_date else None,
                        r.estimated_value_bdt,
                        r.quantity,
                        r.ocr_confidence,
                        r.clipped_image_url,
                        r.detail_url,
                        int(r.is_manual_tender),
                        int(r.is_high_value),
                        int(r.is_critical),
                        r.scraped_at.isoformat(),
                        r.raw_snippet,
                    ),
                )
                inserted += 1
            except sqlite3.IntegrityError:
                # Already seen this tender_id from this portal — expected, not an error.
                continue
    return inserted


def get_unnotified_records() -> List[sqlite3.Row]:
    """Records not yet included in an email digest."""
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT * FROM tenders WHERE notified_at IS NULL ORDER BY is_critical DESC, is_high_value DESC, closing_date ASC"
        )
        return cur.fetchall()


def mark_notified(dedup_keys: List[str]) -> None:
    from datetime import datetime

    now = datetime.utcnow().isoformat()
    with get_conn() as conn:
        conn.executemany(
            "UPDATE tenders SET notified_at = ? WHERE dedup_key = ?",
            [(now, k) for k in dedup_keys],
        )
