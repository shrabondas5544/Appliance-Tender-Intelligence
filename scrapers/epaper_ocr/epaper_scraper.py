"""
Engine 2: Daily Newspaper E-Paper OCR Scraper.

Coordinates downloading, OpenCV preprocessing, Tesseract OCR, and 3-Phase NLP Extraction.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import date
from typing import List, Optional

from core.models import TenderRecord
from scrapers.base import BaseScraper
from scrapers.epaper_ocr.epaper_downloader import EPaperDownloader
from scrapers.epaper_ocr.nlp_matcher import NLPMatcher
from scrapers.epaper_ocr.ocr_processor import OCRProcessor

logger = logging.getLogger(__name__)


class EPaperScraper(BaseScraper):
    source_portal = "E-Paper Print Ads"

    def __init__(self, today_date: Optional[date] = None):
        super().__init__(today_date=today_date)
        self.downloader = EPaperDownloader()
        self.ocr_processor = OCRProcessor(lang="ben+eng")
        self.nlp_matcher = NLPMatcher()

    def fetch(self) -> List[TenderRecord]:
        records: List[TenderRecord] = []
        epaper_targets = self.categories.get("epaper_targets", [])

        for epaper_cfg in epaper_targets:
            if not epaper_cfg.get("active", True):
                continue

            name = epaper_cfg.get("name", "Unknown Newspaper")
            lang = epaper_cfg.get("language", "EN")
            logger.info("Running Engine 2 OCR pipeline for E-Paper [%s] (%s)...", name, self.today_date)

            page_images = self.downloader.fetch_page_images(epaper_cfg, self.today_date)
            for img_path in page_images:
                text, confidence = self.ocr_processor.extract_text_and_confidence(img_path)
                if not text.strip():
                    continue

                # Split page OCR text into candidate snippet blocks
                blocks = [b.strip() for b in text.split("\n\n") if len(b.strip()) > 30]
                for idx, block in enumerate(blocks):
                    category, org_name, closing_date = self.nlp_matcher.extract_metadata(block, name)
                    if not category:
                        continue

                    if closing_date and closing_date < self.today_date:
                        continue

                    content_hash = hashlib.sha1(f"{name}{block[:100]}".encode("utf-8")).hexdigest()[:12]
                    tender_id = f"EPAPER-{content_hash}"
                    title = block.split("\n")[0][:150]

                    record = TenderRecord(
                        tender_id=tender_id,
                        source_portal=f"{name} (Print)",
                        source_type="EPAPER_OCR",
                        source_language=lang,
                        title=title if len(title) > 10 else f"{name} Appliance Tender Notice",
                        category_matched=category,
                        procuring_entity=org_name,
                        publish_date=self.today_date,
                        closing_date=closing_date,
                        estimated_value_bdt=self.extract_value_bdt(block),
                        quantity=self.extract_quantity(block),
                        ocr_confidence=confidence,
                        clipped_image_url=str(img_path),
                        detail_url=f"file://{img_path}",
                        is_manual_tender=True,
                        raw_snippet=block[:500],
                    )
                    records.append(self.apply_flags(record))

        logger.info("Engine 2 E-Paper OCR found %d appliance tender records", len(records))
        return records
