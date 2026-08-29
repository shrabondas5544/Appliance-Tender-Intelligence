"""
Orchestrator — Portal Ingestion Pipeline runner (e-GP + Direct Bank/Corporate Portals).

Flow: Run ACTIVE_SCRAPERS -> Save new records (dedup handled in db.py)
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

from core.db import clear_tenders_db, get_unnotified_records, init_db, mark_notified, save_records
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


def run(target_date: date | None = None, start_date: date | None = None, end_date: date | None = None, egp_only: bool = False) -> None:
    init_db()
    if start_date and end_date:
        logger.info("=== Portal Tender Scan Started (Date Range: %s to %s) ===", start_date, end_date)
    elif target_date:
        logger.info("=== Portal Tender Scan Started (Simulated date: %s) ===", target_date)
    else:
        logger.info("=== Portal Tender Scan Started ===")

    all_fetched_records: List[TenderRecord] = []

    scrapers_to_run = [s for s in ACTIVE_SCRAPERS if "EGP" in s.__name__] if egp_only else ACTIVE_SCRAPERS

    # Sequential Execution across active scrapers for clean, un-jumbled log output
    for scraper_cls in scrapers_to_run:
        logger.info("--- Running %s ---", scraper_cls.__name__)
        records = _run_single_scraper(scraper_cls, target_date or end_date)
        all_fetched_records.extend(records)

    total_new = save_records(all_fetched_records)
    logger.info("Total fetched across scrapers: %d | Total new records saved: %d", len(all_fetched_records), total_new)

    if start_date and end_date:
        import sqlite3
        from core.db import DB_PATH
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        iso_start = start_date.isoformat()
        iso_end = end_date.isoformat()
        if egp_only:
            cur.execute(
                "SELECT * FROM tenders WHERE publish_date BETWEEN ? AND ? AND source_portal = 'eprocure.gov.bd' ORDER BY publish_date DESC",
                (iso_start, iso_end)
            )
        else:
            cur.execute(
                "SELECT * FROM tenders WHERE publish_date BETWEEN ? AND ? ORDER BY publish_date DESC",
                (iso_start, iso_end)
            )
        tenders_to_send = cur.fetchall()
        conn.close()
        logger.info("Retrieved %d tender(s) for range %s to %s from database to email", len(tenders_to_send), iso_start, iso_end)
    elif target_date:
        import sqlite3
        from core.db import DB_PATH
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        iso_date = target_date.isoformat()
        if egp_only:
            cur.execute(
                "SELECT * FROM tenders WHERE publish_date = ? AND source_portal = 'eprocure.gov.bd' ORDER BY publish_date DESC",
                (iso_date,)
            )
        else:
            cur.execute(
                "SELECT * FROM tenders WHERE publish_date = ? ORDER BY publish_date DESC",
                (iso_date,)
            )
        tenders_to_send = cur.fetchall()
        conn.close()
        logger.info("Retrieved %d tender(s) for date %s from database to email", len(tenders_to_send), iso_date)
    else:
        today_iso = date.today().isoformat()
        unnotified = get_unnotified_records()
        if egp_only:
            tenders_to_send = [
                r for r in unnotified
                if r["source_portal"] == "eprocure.gov.bd" and r["publish_date"] == today_iso
            ]
        else:
            tenders_to_send = [
                r for r in unnotified
                if r["publish_date"] == today_iso
            ]

    if tenders_to_send:
        sent = send_digest(tenders_to_send, today_date=end_date or target_date)
        if sent and not target_date and not (start_date and end_date):
            mark_notified([r["dedup_key"] for r in tenders_to_send])
    else:
        logger.info("Nothing to notify.")

    logger.info("=== Portal Tender Scan Finished ===")


if __name__ == "__main__":
    import argparse
    from dateutil import parser as dateparser

    parser = argparse.ArgumentParser(description="Appliance Tender Intelligence System (Dual-Engine Ingestion)")
    parser.add_argument(
        "--now", "--date",
        help="Run multi-engine scraper and send email digest simulated for a specific date (format: DD-MM-YYYY, DD/MM/YYYY, or YYYY-MM-DD)."
    )
    parser.add_argument(
        "--range",
        help="Date range for tender query (format: DD/MM/YYYY-DD/MM/YYYY or DD/MM/YYYY..DD/MM/YYYY, e.g. 20/08/2026-24/08/2026)."
    )
    parser.add_argument(
        "--from-date", "--from",
        help="Start date for date range query (format: DD/MM/YYYY, DD-MM-YYYY, or YYYY-MM-DD)."
    )
    parser.add_argument(
        "--to-date", "--to",
        help="End date for date range query (format: DD/MM/YYYY, DD-MM-YYYY, or YYYY-MM-DD)."
    )
    parser.add_argument(
        "--egp-only", "--egp",
        action="store_true",
        help="Run only Engine 1 (e-GP National Portal Scraper), skipping e-paper OCR engines."
    )
    parser.add_argument(
        "--reset-db", "--reset",
        action="store_true",
        help="Clear all recorded tenders from the local database before running."
    )
    args = parser.parse_args()

    if args.reset_db:
        clear_tenders_db()
        logger.info("Cleared all records from database tenders table.")

    target_date = None
    start_date = None
    end_date = None

    if args.range:
        parts = args.range.replace("..", "-").split("-")
        if len(parts) == 2:
            try:
                start_date = dateparser.parse(parts[0].strip(), dayfirst=True).date()
                end_date = dateparser.parse(parts[1].strip(), dayfirst=True).date()
            except Exception:
                print(f"Error: Invalid date range format '{args.range}'. Please use DD/MM/YYYY-DD/MM/YYYY.")
                sys.exit(1)
        else:
            print(f"Error: Invalid date range '{args.range}'. Format should be start_date-end_date.")
            sys.exit(1)
    elif getattr(args, "from_date", None) or getattr(args, "to_date", None):
        try:
            if getattr(args, "from_date", None):
                start_date = dateparser.parse(getattr(args, "from_date"), dayfirst=True).date()
            if getattr(args, "to_date", None):
                end_date = dateparser.parse(getattr(args, "to_date"), dayfirst=True).date()
            if start_date and not end_date:
                end_date = date.today()
            elif end_date and not start_date:
                start_date = end_date
        except Exception:
            print("Error: Invalid date format in --from / --to. Please use DD/MM/YYYY, DD-MM-YYYY, or YYYY-MM-DD.")
            sys.exit(1)
    elif args.now:
        try:
            target_date = dateparser.parse(args.now, dayfirst=True).date()
        except Exception:
            print(f"Error: Invalid date format '{args.now}'. Please use DD/MM/YYYY, DD-MM-YYYY, or YYYY-MM-DD.")
            sys.exit(1)

    run(target_date=target_date, start_date=start_date, end_date=end_date, egp_only=args.egp_only)
