"""
Orchestrator — Dual-Engine Ingestion Pipeline runner (e-GP + Direct Portals + E-Paper OCR).

Flow: Run ACTIVE_SCRAPERS concurrently -> Save new records (dedup handled in db.py)
      -> Pull unnotified/target date records -> Send HTML digest -> Mark as notified.

Usage:
    python main.py
    python main.py --now 16.08.2026
"""

from __future__ import annotations

import logging
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path
from typing import List

from core.db import get_unnotified_records, init_db, mark_notified, save_records
from core.models import TenderRecord
from core.settings import settings
from notifiers.email_digest import send_digest
from scrapers import ACTIVE_SCRAPERS

settings.LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(settings.LOG_DIR / "run.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("main")


def _run_single_scraper(scraper_cls, target_date: date | None) -> List[TenderRecord]:
    try:
        scraper = scraper_cls(today_date=target_date)
    except TypeError:
        scraper = scraper_cls()

    try:
        records = scraper.fetch()
        logger.info("%s completed: %d records fetched", scraper_cls.__name__, len(records))
        return records
    except Exception:
        logger.exception("Scraper %s failed — skipping this run for it", scraper_cls.__name__)
        return []


def run(target_date: date | None = None) -> None:
    init_db()
    if target_date:
        logger.info("=== Dual-Engine Tender Scan Started (Simulated date: %s) ===", target_date)
    else:
        logger.info("=== Dual-Engine Tender Scan Started ===")

    all_fetched_records: List[TenderRecord] = []

    # Sequential Execution across active scrapers for clean, un-jumbled log output
    for scraper_cls in ACTIVE_SCRAPERS:
        logger.info("--- Running %s ---", scraper_cls.__name__)
        records = _run_single_scraper(scraper_cls, target_date)
        all_fetched_records.extend(records)

    total_new = save_records(all_fetched_records)
    logger.info("Total fetched across engines: %d | Total new records saved: %d", len(all_fetched_records), total_new)

    if target_date:
        import sqlite3
        from core.db import DB_PATH
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        iso_date = target_date.isoformat()
        cur.execute(
            "SELECT * FROM tenders WHERE publish_date = ? ORDER BY publish_date DESC",
            (iso_date,)
        )
        tenders_to_send = cur.fetchall()
        conn.close()
        logger.info("Retrieved %d tender(s) for date %s from database to email", len(tenders_to_send), iso_date)
    else:
        tenders_to_send = get_unnotified_records()

    if tenders_to_send:
        sent = send_digest(tenders_to_send, today_date=target_date)
        if sent and not target_date:
            mark_notified([r["dedup_key"] for r in tenders_to_send])
    else:
        logger.info("Nothing to notify.")

    logger.info("=== Dual-Engine Tender Scan Finished ===")


if __name__ == "__main__":
    import argparse
    from dateutil import parser as dateparser

    parser = argparse.ArgumentParser(description="Appliance Tender Intelligence System (Dual-Engine Ingestion)")
    parser.add_argument(
        "--now", "--date",
        help="Run multi-engine scraper and send email digest simulated for a specific date (format: DD-MM-YYYY, DD/MM/YYYY, or YYYY-MM-DD)."
    )
    args = parser.parse_args()

    if args.now:
        try:
            target_date = dateparser.parse(args.now, dayfirst=True).date()
        except Exception:
            print(f"Error: Invalid date format '{args.now}'. Please use DD/MM/YYYY, DD-MM-YYYY, or YYYY-MM-DD.")
            sys.exit(1)
        run(target_date)
    else:
        run()
