"""
e-GP (eprocure.gov.bd) scraper.

Primary source: the portal's own public RSS feeds — no login required:
  - Daily:  https://www.eprocure.gov.bd/RSS/DailyTender.xml
  - Weekly: https://www.eprocure.gov.bd/RSS/WeeklyTender.xml

Why RSS instead of the search page: the search UI (StdTenderSearch.jsp /
AllTenders.jsp) renders its result table via JS/postback — a plain HTTP GET
returns an empty table, so it needs a headless browser (Playwright) to scrape
reliably. RSS is plain XML, no JS needed, and is exactly what "new tenders
today" is for. We fall back to the search page (Playwright) in Phase 2b if the
feed turns out to be too shallow (e.g. missing categories, capped at N items).

IMPORTANT — first-run verification needed:
This module was built without live access to eprocure.gov.bd from this sandbox
(its network isn't in this environment's allowlist). The RSS field-parsing
below follows the standard RSS 2.0 shape (title/link/description/pubDate/guid),
which is what e-GP's own page advertises, but the *exact wording* of title and
description strings (where tender ID, entity name, and closing date live) can
only be confirmed by running this on your laptop against the live feed. See
the `debug_dump_feed()` helper below — run it first and send me a sample of
5-10 raw entries so I can tighten the regex parsing to match reality exactly.
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime
from typing import List, Optional

import feedparser
import requests

from core.models import TenderRecord
from core.settings import settings
from scrapers.base import BaseScraper

logger = logging.getLogger(__name__)

DAILY_FEED_URL = "https://www.eprocure.gov.bd/RSS/DailyTender.xml"
WEEKLY_FEED_URL = "https://www.eprocure.gov.bd/RSS/WeeklyTender.xml"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
}


from bs4 import BeautifulSoup

class EGPScraper(BaseScraper):
    source_portal = "eprocure.gov.bd"

    def __init__(self, today_date: Optional[date] = None):
        super().__init__(today_date=today_date)

    def fetch(self) -> List[TenderRecord]:
        records: List[TenderRecord] = []
        
        # Bangladesh e-GP search servlet URL
        url = "https://www.eprocure.gov.bd/TenderDetailsServlet"
        
        # We search using primary appliance terms that match the categories
        search_keywords = [
            "air conditioner", "washing machine", "television", "refrigerator",
            "freezer", "fan", "air cooler", "air purifier", "soundbar"
        ]
        
        for kw in search_keywords:
            try:
                logger.info("Querying e-GP search servlet for keyword: '%s'...", kw)
                data = {
                    "funName": "AllTenders",
                    "keyword": kw,
                    "pageNo": "1",
                    "size": "100",  # Retrieve up to 100 rows per term to get all recent tenders
                    "homeWSearch": "homeWSearch",
                    "approve": "false",
                    "h": "t"
                }
                
                resp = requests.post(url, headers=HEADERS, data=data, timeout=30)
                resp.raise_for_status()
                
                soup = BeautifulSoup(resp.text, 'html.parser')
                rows = soup.find_all('tr')
                
                for r in rows:
                    cells = r.find_all('td')
                    if not cells or len(cells) < 6:
                        continue
                    
                    # Cell 2: ID, Ref No, Status
                    id_ref_status = cells[1].get_text("|", strip=True)
                    id_ref_parts = [p.strip() for p in id_ref_status.split("|") if p.strip()]
                    tender_id = id_ref_parts[0].replace(",", "").strip() if id_ref_parts else ""
                    ref_no = id_ref_parts[1].replace(",", "").strip() if len(id_ref_parts) > 1 else ""
                    status = id_ref_parts[2].strip() if len(id_ref_parts) > 2 else "Live"
                    
                    # Cell 3: Nature, Title
                    title_text = cells[2].get_text(" ", strip=True)
                    title_text = re.sub(r'\s+', ' ', title_text).strip()
                    
                    # Cell 4: Organization PE details
                    org_text = cells[3].get_text(", ", strip=True)
                    org_text = re.sub(r'\s+', ' ', org_text).strip()
                    
                    # Cell 5: Method
                    method_text = cells[4].get_text(" ", strip=True)
                    
                    # Cell 6: Publishing & Closing dates
                    dates_text = cells[5].get_text("|", strip=True)
                    date_parts = [d.strip() for d in dates_text.split("|") if d.strip()]
                    publish_raw = date_parts[0] if date_parts else ""
                    closing_raw = date_parts[1] if len(date_parts) > 1 else ""
                    
                    # Match against configured appliance categories
                    category = self.match_category(title_text)
                    if not category:
                        continue
                    
                    publish_date = self._parse_date(publish_raw)
                    closing_date = self._parse_date(closing_raw)
                    
                    # Ignore expired tenders (closing date is in the past)
                    if closing_date and closing_date < self.today_date:
                        continue

                    
                    detail_url = f"https://www.eprocure.gov.bd/resources/common/ViewTender.jsp?id={tender_id}&h=t"
                    is_manual = "manual tender" in title_text.lower()
                    
                    record = TenderRecord(
                        tender_id=tender_id,
                        source_portal=self.source_portal,
                        title=title_text,
                        category_matched=category,
                        procuring_entity=org_text,
                        publish_date=publish_date,
                        closing_date=closing_date,
                        estimated_value_bdt=self.extract_value_bdt(title_text),
                        quantity=self.extract_quantity(title_text),
                        detail_url=detail_url,
                        is_manual_tender=is_manual,
                        raw_snippet=title_text[:500],
                    )
                    
                    records.append(self.apply_flags(record))
                    
            except Exception as exc:
                logger.error("Error fetching/scraping e-GP search results for keyword '%s': %s", kw, exc)
                
        # De-duplicate entries within this run
        seen = set()
        unique = []
        for r in records:
            if r.dedup_key() not in seen:
                seen.add(r.dedup_key())
                unique.append(r)
                
        logger.info("e-GP: %d appliance-matching tenders found from search results", len(unique))
        return unique

    # --- internals ---

    def _fetch_feed_entries(self, url: str):
        try:
            resp = requests.get(url, headers=HEADERS, timeout=30)
            resp.raise_for_status()
        except requests.RequestException as exc:
            logger.error("Failed to fetch %s: %s", url, exc)
            return []

        parsed = feedparser.parse(resp.content)
        if parsed.bozo:
            logger.warning("Feed %s parsed with warnings: %s", url, parsed.bozo_exception)
        return parsed.entries

    def _parse_entry(self, entry) -> Optional[TenderRecord]:
        title = getattr(entry, "title", "") or ""
        description = getattr(entry, "description", "") or getattr(entry, "summary", "") or ""
        link = getattr(entry, "link", "") or ""
        combined_text = f"{title} {description}"

        category = self.match_category(combined_text)
        if not category:
            return None  # not an appliance tender, skip

        tender_id = self._extract_tender_id(title, description, link)
        if not tender_id:
            # Can't dedup without an ID — fall back to a hash of title+link
            import hashlib
            tender_id = hashlib.sha1(f"{title}{link}".encode("utf-8")).hexdigest()[:16]
            logger.debug("No explicit tender ID found, using content hash for: %s", title[:80])

        publish_date = self._parse_date(getattr(entry, "published", None))
        closing_date = self._extract_closing_date(description)
        entity = self._extract_entity(description)
        is_manual = "manual tender" in combined_text.lower()

        record = TenderRecord(
            tender_id=tender_id,
            source_portal=self.source_portal,
            title=title.strip(),
            category_matched=category,
            procuring_entity=entity,
            publish_date=publish_date,
            closing_date=closing_date,
            estimated_value_bdt=self.extract_value_bdt(combined_text),
            quantity=self.extract_quantity(combined_text),
            detail_url=link,
            is_manual_tender=is_manual,
            raw_snippet=combined_text[:500],
        )
        return self.apply_flags(record)

    def _extract_tender_id(self, title: str, description: str, link: str) -> Optional[str]:
        # e-GP tender IDs are typically numeric, e.g. "12345" in title or link query params.
        m = re.search(r"Tender\s*ID[:\s]*([A-Za-z0-9\-/]+)", title + " " + description, re.IGNORECASE)
        if m:
            return m.group(1)
        m = re.search(r"[?&](?:id|tenderId|tenderid)=([A-Za-z0-9\-]+)", link)
        if m:
            return m.group(1)
        return None

    def _extract_entity(self, description: str) -> Optional[str]:
        m = re.search(r"(?:Organi[sz]ation|Procuring Entity|PE)\s*[:\-]\s*([^\|;\n]+)", description, re.IGNORECASE)
        return m.group(1).strip() if m else None

    def _extract_closing_date(self, description: str) -> Optional[date]:
        m = re.search(
            r"Closing\s*Date[:\s]*([0-9]{1,2}[\-/][0-9]{1,2}[\-/][0-9]{2,4})",
            description,
            re.IGNORECASE,
        )
        if not m:
            return None
        return self._parse_date(m.group(1))

    def _parse_date(self, value) -> Optional[date]:
        if not value:
            return None
        from dateutil import parser as dateparser
        try:
            return dateparser.parse(str(value), dayfirst=True).date()
        except (ValueError, TypeError):
            return None


def debug_dump_feed(limit: int = 10) -> None:
    """
    Run this once against the live feed (on your laptop, where eprocure.gov.bd
    is reachable) to see the RAW field content e-GP actually sends. Paste the
    output back to me and I'll tighten the regexes in this file to match it
    exactly instead of guessing at the format.

    Usage:  python -c "from scrapers.egp_scraper import debug_dump_feed; debug_dump_feed()"
    """
    resp = requests.get(DAILY_FEED_URL, headers=HEADERS, timeout=30)
    parsed = feedparser.parse(resp.content)
    print(f"Feed status: {resp.status_code} | Entries found: {len(parsed.entries)}")
    for i, entry in enumerate(parsed.entries[:limit]):
        print(f"\n--- Entry {i+1} ---")
        print("TITLE:      ", getattr(entry, "title", None))
        print("LINK:       ", getattr(entry, "link", None))
        print("PUBLISHED:  ", getattr(entry, "published", None))
        print("DESCRIPTION:", getattr(entry, "description", getattr(entry, "summary", None)))


if __name__ == "__main__":
    debug_dump_feed()
