"""
NLP & Entity Matcher for E-Paper OCR Snippets.

3-Phase Pipeline:
  Phase 1: Procurement Intent Verification (RFQ, IFT, RFP, দরপত্র, কোটেশন)
  Phase 2: Appliance Category Classification
  Phase 3: Metadata & Date Parsing (Bengali & English Numerals)
"""

from __future__ import annotations

import logging
import re
from datetime import date
from typing import Dict, Optional, Tuple

from core.settings import load_categories

logger = logging.getLogger(__name__)

# Map Bengali numerals to English numerals
BANGLA_DIGIT_MAP = str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789")


class NLPMatcher:
    def __init__(self):
        self.categories: Dict = load_categories()
        self.intent_keywords = self.categories.get("intent_keywords", [
            "tender", "rfq", "rfp", "ift", "quotation", "procurement",
            "দরপত্র", "দরপত্র বিজ্ঞপ্তি", "কোটেশন", "বিজ্ঞপ্তি", "সংগ্রহ", "ক্রয়"
        ])

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

    def has_procurement_intent(self, text: str) -> bool:
        """Phase 1: Verify if the OCR text block is a procurement notice."""
        text_lower = text.lower()
        return any(kw.lower() in text_lower for kw in self.intent_keywords)

    def extract_metadata(self, text: str, paper_name: str) -> Tuple[Optional[str], Optional[str], Optional[date]]:
        """
        Phase 2 & 3: Extract (category, organization_name, closing_date) from OCR text snippet.
        """
        # Phase 1 verification
        if not self.has_procurement_intent(text):
            return None, None, None

        # Phase 2: Category matching
        category = self.match_category(text)
        if not category:
            return None, None, None

        # Phase 3: Organization & Date Extraction
        org_name = self._extract_organization(text, paper_name)
        closing_date = self._extract_closing_date(text)

        return category, org_name, closing_date

    def _extract_organization(self, text: str, default_name: str) -> str:
        # Pattern matching for common company / bank indicators
        m = re.search(r"([A-Za-z0-9\s,\.]{3,40}\s*(?:Bank|Company|Limited|Ltd|Corporation|Authority|University|Hospital))", text, re.IGNORECASE)
        if m:
            return m.group(1).strip()

        # Bangla organization pattern
        m_bn = re.search(r"([০-৯a-zA-Z\s]{3,40}\s*(?:ব্যাংক|লিমিটেড|কর্পোরেশন|মন্ত্রণালয়|বিভাগ|বিশ্ববিদ্যালয়))", text)
        if m_bn:
            return m_bn.group(1).strip()

        return f"{default_name} Classified Notice"

    def _extract_closing_date(self, text: str) -> Optional[date]:
        # Convert Bangla numerals to English numerals
        normalized_text = text.translate(BANGLA_DIGIT_MAP)

        # Match English/Converted dates (DD/MM/YYYY, DD-MM-YYYY, YYYY-MM-DD)
        m = re.search(r"(?:শেষ তারিখ|আবেদনের শেষ তারিখ|deadline|closing date|last date)[:\s]*([0-9]{1,2}[\-/][0-9]{1,2}[\-/][0-9]{2,4})", normalized_text, re.IGNORECASE)
        if not m:
            m = re.search(r"\b([0-9]{1,2}[\-/][0-9]{1,2}[\-/][0-9]{2,4})\b", normalized_text)

        if m:
            from dateutil import parser as dateparser
            try:
                return dateparser.parse(m.group(1), dayfirst=True).date()
            except Exception:
                pass
        return None
