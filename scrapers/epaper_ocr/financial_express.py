"""
Engine 2: The Financial Express e-Paper OCR Scraper.

Dynamically scans all pages of the daily edition, performs Tesseract OCR,
and filters for appliance categories and procurement intents.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
from datetime import date
from typing import List, Optional
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from core.models import TenderRecord
from core.settings import settings
from scrapers.base import BaseScraper
from scrapers.epaper_ocr.ocr_engine import OCREngine

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
}


class FinancialExpressScraper(BaseScraper):
    source_portal = "The Financial Express"
    BASE_URL = "https://epaper.thefinancialexpress.com.bd"

    def __init__(self, today_date: Optional[date] = None):
        super().__init__(today_date=today_date)
        self.ocr = OCREngine(lang="eng")
        self.cache_dir = settings.PROJECT_ROOT / "data" / "epaper_cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        
        # Load intent keywords from categories.yaml
        self.intent_keywords = self.categories.get("intent_keywords", [
            "tender", "rfq", "rfp", "ift", "quotation", "procurement", "notice"
        ])

    def fetch(self) -> List[TenderRecord]:
        """Orchestrator interface: fetches tenders for self.today_date."""
        return self.scrape_date(self.today_date)

    def get_page_image_urls(self, target_date: date) -> List[dict]:
        """Dynamically discovers all page image URLs for target_date from the e-paper edition."""
        date_str = target_date.strftime("%Y-%m-%d")
        url = f"{self.BASE_URL}/index.php?opt=view&page=1&date={date_str}"
        
        pages = []
        try:
            resp = requests.get(url, headers=HEADERS, timeout=20)
            if resp.status_code != 200:
                logger.warning("[%s] E-Paper page index returned status %d", self.source_portal, resp.status_code)
                return pages

            soup = BeautifulSoup(resp.text, "html.parser")
            
            # Find all thumbnail image links to discover total pages dynamically
            # Example: 2026/08/16//thumb/page01.jpg
            thumbs = soup.find_all("img", src=re.compile(r'/thumb/page\d+\.jpg'))
            
            # Fallback if double slashes or absolute path formats are different
            if not thumbs:
                thumbs = soup.find_all("img", src=re.compile(r'page\d+\.jpg'))

            seen_pages = set()
            date_slash = target_date.strftime("%Y/%m/%d")

            for img in thumbs:
                src = img.get("src", "")
                m = re.search(r'page(\d+)\.jpg', src)
                if m:
                    page_num_str = m.group(1)
                    page_num = int(page_num_str)
                    if page_num in seen_pages:
                        continue
                    seen_pages.add(page_num)

                    # Build the full-resolution page image URL
                    # Format: https://epaper.thefinancialexpress.com.bd/YYYY/MM/DD/pages/pageXX.jpg
                    full_image_url = f"{self.BASE_URL}/{date_slash}/pages/page{page_num_str}.jpg"
                    pages.append({
                        "page_num": page_num,
                        "url": full_image_url
                    })

            # Sort pages sequentially by page number
            pages.sort(key=lambda x: x["page_num"])
            logger.info("[%s] Dynamically discovered %d pages for date %s", self.source_portal, len(pages), date_str)

        except Exception as e:
            logger.error("[%s] Failed to discover e-paper pages dynamically: %s", self.source_portal, e)
            
        return pages

    def scrape_date(self, target_date: date) -> List[TenderRecord]:
        records = []
        pages = self.get_page_image_urls(target_date)
        
        for idx, page in enumerate(pages, start=1):
            page_num = page["page_num"]
            image_url = page["url"]
            filename = f"fe_{target_date.strftime('%Y-%m-%d')}_p{page_num:02d}.jpg"
            image_path = self.cache_dir / filename

            logger.info("[%s] Page %d/%d (%s): Scanning OCR text for appliance categories (air_conditioner, television, refrigerator, washing_machine, fan)...", self.source_portal, idx, len(pages), filename)

            # 1. Download full page image if not already cached
            if not image_path.exists():
                try:
                    resp = requests.get(image_url, headers=HEADERS, timeout=30)
                    if resp.status_code == 200 and len(resp.content) > 5000:
                        image_path.write_bytes(resp.content)
                    else:
                        logger.debug("[%s] Page %d image not found/invalid (Status: %d)", self.source_portal, page_num, resp.status_code)
                        continue
                except Exception as exc:
                    logger.debug("[%s] Failed to download Page %d image: %s", self.source_portal, page_num, exc)
                    continue

            # 2. Extract text via OCR Engine
            text = self.ocr.extract_text(str(image_path))
            if not text.strip():
                continue

            # 3. Two-Tier Verification
            # Tier 1: Check for general procurement intent keywords
            text_lower = text.lower()
            if not any(intent.lower() in text_lower for intent in self.intent_keywords):
                continue

            # Tier 2: Check for specific appliance category keywords
            category = self.match_category(text)
            if not category:
                continue

            # Extract matching lines as a context snippet
            lines = text.split("\n")
            matching_lines = []
            for line in lines:
                if self.match_category(line) or any(intent.lower() in line.lower() for intent in self.intent_keywords):
                    if len(line.strip()) > 10:
                        matching_lines.append(line.strip())

            snippet = " | ".join(matching_lines[:5])
            if not snippet:
                snippet = text[:300].replace("\n", " ").strip()

            tender_id = f"FE-{target_date.strftime('%Y%m%d')}-P{page_num:02d}-{category}"
            title = f"Appliance Procurement Notice on Page {page_num}"

            record = TenderRecord(
                tender_id=tender_id,
                source_portal=f"{self.source_portal} (Print)",
                source_type="EPAPER_OCR",
                source_language="EN",
                title=title,
                category_matched=category,
                procuring_entity="Corporate / Classified Notice",
                publish_date=target_date,
                closing_date=self._extract_closing_date(text),
                detail_url=image_url,
                is_manual_tender=True,
                raw_snippet=snippet[:500]
            )
            records.append(self.apply_flags(record))
            logger.info("[%s] Match found on Page %d! Category: %s", self.source_portal, page_num, category.upper())

        return records

    def _extract_closing_date(self, text: str) -> Optional[date]:
        # Look for deadline patterns in text
        m = re.search(
            r"(?:last date|closing date|deadline|submission date)[:\s]*([0-9]{1,2}[\-/][0-9]{1,2}[\-/][0-9]{2,4})",
            text,
            re.IGNORECASE
        )
        if not m:
            m = re.search(r"\b([0-9]{1,2}[\-/][0-9]{1,2}[\-/][0-9]{2,4})\b", text)

        if m:
            from dateutil import parser as dateparser
            try:
                return dateparser.parse(m.group(1), dayfirst=True).date()
            except Exception:
                pass
        return None
