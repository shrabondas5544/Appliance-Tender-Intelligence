"""
Every portal — e-GP today, corporate/subscription portals later — implements
this interface. main.py never imports a specific scraper by special-casing it;
it just iterates ACTIVE_SCRAPERS (see scrapers/__init__.py).

To add a new portal later:
  1. Create scrapers/<portal_name>_scraper.py
  2. Subclass BaseScraper, implement fetch()
  3. Register it in scrapers/__init__.py's ACTIVE_SCRAPERS list
That's the whole integration surface — storage, dedup, categorization,
and the email digest all work unchanged.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from typing import Dict, List

from core.models import TenderRecord
from core.settings import load_categories


class BaseScraper(ABC):
    source_portal: str = "unknown"

    def __init__(self, today_date: date | None = None) -> None:
        import datetime
        self.categories: Dict = load_categories()
        self.thresholds: Dict = self.categories.get("thresholds", {})
        self.today_date = today_date or datetime.date.today()

    @abstractmethod
    def fetch(self) -> List[TenderRecord]:
        """Return TenderRecord objects for every appliance-relevant tender found."""
        raise NotImplementedError

    # --- Shared helpers available to every scraper subclass ---

    def match_category(self, text: str) -> str | None:
        """Return the first matching category name for this text, or None."""
        text_lower = text.lower()
        skip_keys = {"thresholds", "intent_keywords", "portal_targets", "epaper_targets"}
        for category, keywords in self.categories.items():
            if category in skip_keys or not isinstance(keywords, list):
                continue
            for kw in keywords:
                if not isinstance(kw, str):
                    continue
                kw_check = kw.strip().lower()
                if kw_check and kw_check in text_lower:
                    return category
        return None

    def extract_quantity(self, text: str) -> int | None:
        # Matches patterns like "45 units", "Qty: 45", "quantity 45 pcs"
        m = re.search(r"(?:qty|quantity)?\s*[:\-]?\s*(\d{1,6})\s*(?:units?|pcs?|nos?)\b", text, re.IGNORECASE)
        return int(m.group(1)) if m else None

    def extract_value_bdt(self, text: str) -> float | None:
        # Matches "BDT 25,00,000", "Tk. 2,500,000", "৳ 25,00,000"
        m = re.search(r"(?:BDT|Tk\.?|৳)\s*([\d,]+(?:\.\d+)?)", text, re.IGNORECASE)
        if not m:
            return None
        try:
            return float(m.group(1).replace(",", ""))
        except ValueError:
            return None

    def apply_flags(self, record: TenderRecord) -> TenderRecord:
        high_value_threshold = self.thresholds.get("high_value_bdt", 1_000_000)
        high_qty_threshold = self.thresholds.get("high_quantity_units", 20)
        critical_days = self.thresholds.get("critical_days_to_close", 3)

        if (record.estimated_value_bdt or 0) >= high_value_threshold:
            record.is_high_value = True
        if (record.quantity or 0) >= high_qty_threshold:
            record.is_high_value = True

        if record.closing_date:
            days_left = (record.closing_date - self.today_date).days
            if 0 <= days_left <= critical_days:
                record.is_critical = True

        return record

