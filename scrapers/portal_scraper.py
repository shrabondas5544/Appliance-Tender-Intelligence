"""
Engine 1: Direct Company & Commercial Bank Portals Scraper.

Scrapes notice boards and procurement pages for banks and corporate entities
(e.g., Prime Bank, BRAC Bank, Jamuna Bank, DBBL, Bangladesh Bank).

Supports:
  1. html_table: Standard HTML table parsing via BeautifulSoup
  2. pdf_links: Notice boards with attached PDF documents (parsed via pypdf/pdfplumber)
  3. dynamic_playwright: JS-rendered dynamic tables (with requests fallback)
"""

from __future__ import annotations

import hashlib
import io
import logging
import re
from datetime import date, datetime
from typing import List, Optional
from urllib.parse import urljoin

import requests
import urllib3
from bs4 import BeautifulSoup

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

from core.models import TenderRecord
from scrapers.base import BaseScraper

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
}


class PortalScraper(BaseScraper):
    source_portal = "Direct Portals"

    def fetch(self) -> List[TenderRecord]:
        records: List[TenderRecord] = []
        portal_targets = self.categories.get("portal_targets", [])

        for target in portal_targets:
            if not target.get("active", True):
                continue

            name = target.get("name", "Unknown Portal")
            url = target.get("url")
            parser_type = target.get("parser_type", "html_table")

            if not url:
                continue

            logger.info("Scraping direct portal [%s] (%s) via parser [%s]...", name, url, parser_type)

            try:
                if parser_type == "html_table":
                    parsed = self._scrape_html_table(name, url)
                elif parser_type == "pdf_links":
                    parsed = self._scrape_pdf_links(name, url)
                elif parser_type == "dynamic_playwright":
                    parsed = self._scrape_dynamic_playwright(name, url)
                elif parser_type == "kv_cards":
                    parsed = self._scrape_kv_cards(name, url)
                else:
                    parsed = self._scrape_html_table(name, url)

                records.extend(parsed)
            except Exception as exc:
                logger.error("Error scraping portal [%s]: %s", name, exc)

        return records

    # --- Parser Strategies ---

    def _scrape_html_table(self, portal_name: str, url: str) -> List[TenderRecord]:
        records: List[TenderRecord] = []
        try:
            try:
                resp = requests.get(url, headers=HEADERS, timeout=20)
                resp.raise_for_status()
            except requests.exceptions.SSLError:
                resp = requests.get(url, headers=HEADERS, timeout=20, verify=False)
                resp.raise_for_status()
        except Exception as exc:
            logger.warning("Could not fetch HTML portal [%s]: %s", portal_name, exc)
            return []

        soup = BeautifulSoup(resp.text, "html.parser")
        rows = soup.find_all("tr")

        # Parse header map dynamically accounting for multi-row headers and rowspan/colspan
        header_matrix = {}
        for r_idx, r in enumerate(rows[:5]):
            ths = r.find_all(["th", "td"])
            if not ths or not any(c.name == "th" for c in ths):
                continue
            col_cursor = 0
            for cell in ths:
                while (r_idx, col_cursor) in header_matrix:
                    col_cursor += 1
                rowspan = int(cell.get("rowspan", 1))
                colspan = int(cell.get("colspan", 1))
                txt = cell.get_text(" ", strip=True).lower()
                for rs in range(rowspan):
                    for cs in range(colspan):
                        pos = (r_idx + rs, col_cursor + cs)
                        prev = header_matrix.get(pos, "")
                        header_matrix[pos] = f"{prev} {txt}".strip()
                col_cursor += colspan

        max_col = max((c for r, c in header_matrix.keys()), default=-1)
        max_row = max((r for r, c in header_matrix.keys()), default=-1)

        pub_idx = -1
        close_idx = -1
        title_idx = -1
        entity_idx = -1

        if max_col >= 0:
            for c in range(max_col + 1):
                h_text = " ".join([header_matrix.get((r, c), "") for r in range(max_row + 1)]).strip()
                if not h_text:
                    continue
                if any(x in h_text for x in ["publish", "posted", "start", "issue", "from"]) and pub_idx == -1:
                    pub_idx = c
                if (any(x in h_text for x in ["last date", "closing", "deadline", "submission", "close"]) or "to" in h_text.split()) and close_idx == -1:
                    close_idx = c
                if any(x in h_text for x in ["title", "description", "subject", "notice", "details", "tender name", "name"]) and title_idx == -1:
                    title_idx = c
                if any(x in h_text for x in ["offered", "entity", "department", "dept", "procuring", "organization", "branch", "section", "wing"]) and entity_idx == -1:
                    entity_idx = c

        for r in rows:
            cells = r.find_all(["td", "th"])
            # Skip header rows, search/filter rows, or rows with too few cells
            if len(cells) < 2 or any(c.name == "th" for c in cells):
                continue

            row_text = " ".join([c.get_text(" ", strip=True) for c in cells])
            category = self.match_category(row_text)
            if not category:
                continue

            # Determine column mapping indices for this row
            p_idx, c_idx, t_idx, e_idx = pub_idx, close_idx, title_idx, entity_idx
            
            if p_idx == -1 or t_idx == -1:
                # Fallback to positional heuristics if not resolved by headers
                if len(cells) >= 4:
                    p_idx = 1
                    c_idx = 2
                    t_idx = 3
                elif len(cells) == 3:
                    date_1 = self._parse_single_date(cells[1].get_text(" ", strip=True))
                    date_2 = self._parse_single_date(cells[2].get_text(" ", strip=True))
                    if date_1 and not date_2:
                        p_idx = 1
                        t_idx = 2
                        c_idx = -1
                    elif date_2:
                        t_idx = 1
                        c_idx = 2
                        p_idx = -1
                    else:
                        t_idx = 1
                        c_idx = -1
                        p_idx = -1
                else:
                    t_idx = 0
                    p_idx = -1
                    c_idx = -1

            # Extract cell values using mapped indices
            publish_date = self.today_date
            closing_date = None

            if p_idx != -1 and p_idx < len(cells):
                parsed_pub = self._parse_single_date(cells[p_idx].get_text(" ", strip=True))
                if parsed_pub:
                    publish_date = parsed_pub

            if c_idx != -1 and c_idx < len(cells):
                parsed_close = self._parse_single_date(cells[c_idx].get_text(" ", strip=True))
                if parsed_close:
                    closing_date = parsed_close

            # Extract tender title and link if available
            link_tag = r.find("a", href=True)
            detail_url = urljoin(url, link_tag["href"]) if link_tag else url
            
            if t_idx != -1 and t_idx < len(cells):
                title = cells[t_idx].get_text(" ", strip=True)
            else:
                title = link_tag.get_text(" ", strip=True) if link_tag else cells[0].get_text(" ", strip=True)

            if len(title) < 10:
                title = row_text[:200]

            # Strip leading serial numbers & dates from title if present
            title = re.sub(r"^\d+\s+(?:\d{4}-\d{2}-\d{2}|\d{1,2}\s+[A-Za-z]+\s+\d{4})\s*", "", title).strip()
            title = re.sub(r"^(?:\d{4}-\d{2}-\d{2}|\d{1,2}\s+[A-Za-z]+\s+\d{4})\s*", "", title).strip()

            if not closing_date:
                closing_date = self._extract_date(row_text)

            # Skip expired tenders
            if closing_date and closing_date < self.today_date:
                continue

            # Skip if no closing date is available and the publish date is older than 30 days
            if not closing_date and publish_date:
                age_days = (self.today_date - publish_date).days
                if age_days > 30:
                    continue

            procuring_entity = portal_name
            if e_idx != -1 and e_idx < len(cells):
                dept_text = cells[e_idx].get_text(" ", strip=True)
                if dept_text and len(dept_text) > 2:
                    procuring_entity = f"{portal_name} ({dept_text})"

            record = TenderRecord(
                tender_id=self._generate_id(portal_name, title, detail_url),
                source_portal=portal_name,
                source_type="PORTAL",
                source_language="EN" if any(c.isascii() for c in title) else "BN",
                title=title,
                category_matched=category,
                procuring_entity=procuring_entity,
                publish_date=publish_date,
                closing_date=closing_date,
                estimated_value_bdt=self.extract_value_bdt(row_text),
                quantity=self.extract_quantity(row_text),
                detail_url=detail_url,
                is_manual_tender="hardcopy" in row_text.lower() or "manual" in row_text.lower(),
                raw_snippet=row_text[:500],
            )
            records.append(self.apply_flags(record))

        return records

    def _scrape_pdf_links(self, portal_name: str, url: str) -> List[TenderRecord]:
        records: List[TenderRecord] = []
        try:
            resp = requests.get(url, headers=HEADERS, timeout=20)
            resp.raise_for_status()
        except Exception as exc:
            logger.warning("Could not fetch PDF portal [%s]: %s", portal_name, exc)
            return []

        soup = BeautifulSoup(resp.text, "html.parser")
        pdf_links = soup.find_all("a", href=re.compile(r"\.pdf", re.IGNORECASE))

        for a in pdf_links[:10]:  # Inspect top 10 recent PDF links
            href = a["href"]
            pdf_url = urljoin(url, href)
            link_text = a.get_text(" ", strip=True) or href.split("/")[-1]

            # Quick category match on link text first
            category = self.match_category(link_text)
            pdf_text = ""

            if not category:
                # Download PDF and extract text in memory
                pdf_text = self._extract_pdf_text(pdf_url)
                category = self.match_category(pdf_text)

            if not category:
                continue

            combined_text = f"{link_text} {pdf_text}"
            tender_id = self._generate_id(portal_name, link_text, pdf_url)
            closing_date = self._extract_date(combined_text)

            if closing_date and closing_date < self.today_date:
                continue

            record = TenderRecord(
                tender_id=tender_id,
                source_portal=portal_name,
                source_type="PORTAL",
                source_language="EN",
                title=link_text if len(link_text) > 10 else f"{portal_name} Appliance Tender Notice",
                category_matched=category,
                procuring_entity=portal_name,
                publish_date=self.today_date,
                closing_date=closing_date,
                estimated_value_bdt=self.extract_value_bdt(combined_text),
                quantity=self.extract_quantity(combined_text),
                detail_url=pdf_url,
                is_manual_tender=True,
                raw_snippet=combined_text[:500],
            )
            records.append(self.apply_flags(record))

        return records

    def _scrape_dynamic_playwright(self, portal_name: str, url: str) -> List[TenderRecord]:
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page()
                page.goto(url, timeout=30000, wait_until="networkidle")
                content = page.content()
                browser.close()

                soup = BeautifulSoup(content, "html.parser")
                rows = soup.find_all("tr")
                records = []
                for r in rows:
                    row_text = r.get_text(" ", strip=True)
                    category = self.match_category(row_text)
                    if not category:
                        continue
                    link_tag = r.find("a", href=True)
                    detail_url = urljoin(url, link_tag["href"]) if link_tag else url
                    title = link_tag.get_text(" ", strip=True) if link_tag else row_text[:150]
                    closing_date = self._extract_date(row_text)

                    record = TenderRecord(
                        tender_id=self._generate_id(portal_name, title, detail_url),
                        source_portal=portal_name,
                        source_type="PORTAL",
                        title=title,
                        category_matched=category,
                        procuring_entity=portal_name,
                        publish_date=self.today_date,
                        closing_date=closing_date,
                        detail_url=detail_url,
                        raw_snippet=row_text[:500],
                    )
                    records.append(self.apply_flags(record))
                return records
        except Exception as exc:
            logger.info("Playwright not active for [%s], falling back to HTML table parser: %s", portal_name, exc)
            return self._scrape_html_table(portal_name, url)

    def _scrape_kv_cards(self, portal_name: str, url: str) -> List[TenderRecord]:
        records: List[TenderRecord] = []
        try:
            try:
                resp = requests.get(url, headers=HEADERS, timeout=20)
                resp.raise_for_status()
            except requests.exceptions.SSLError:
                resp = requests.get(url, headers=HEADERS, timeout=20, verify=False)
                resp.raise_for_status()
        except Exception as exc:
            logger.warning("Could not fetch KV card portal [%s]: %s", portal_name, exc)
            return []

        soup = BeautifulSoup(resp.text, "html.parser")
        cards = [
            c for c in soup.find_all("div", class_=lambda x: x and "card" in x)
            if "publishing date" in c.get_text().lower() or "closing date" in c.get_text().lower()
        ]

        seen_ids = set()

        for card in cards:
            card_text = card.get_text(" ", strip=True)
            category = self.match_category(card_text)
            if not category:
                continue

            # Title extraction: look for top heading tag
            heading = card.find(["h1", "h2", "h3", "h4", "h5", "u", "a"])
            title = heading.get_text(" ", strip=True) if heading else card_text[:200]
            if len(title) < 10:
                title = card_text[:200]

            title = re.sub(r"^\d+\s+(?:\d{4}-\d{2}-\d{2}|\d{1,2}\s+[A-Za-z]+\s+\d{4})\s*", "", title).strip()

            # Detail URL extraction
            link_tag = card.find("a", href=True)
            detail_url = urljoin(url, link_tag["href"]) if link_tag else url

            tender_id = self._generate_id(portal_name, title, detail_url)
            if tender_id in seen_ids:
                continue
            seen_ids.add(tender_id)

            publish_date = self.today_date
            closing_date = None
            procuring_entity = portal_name

            # Key-value table parsing inside card
            rows = card.find_all("tr")
            for r in rows:
                cells = r.find_all(["td", "th"])
                if len(cells) < 2:
                    continue
                k = cells[0].get_text(" ", strip=True).lower()
                v = cells[1].get_text(" ", strip=True)

                if "publishing" in k or "posted" in k or "start" in k:
                    parsed_pub = self._parse_single_date(v)
                    if parsed_pub:
                        publish_date = parsed_pub
                elif "closing" in k or "last date" in k or "deadline" in k:
                    parsed_close = self._parse_single_date(v)
                    if parsed_close:
                        closing_date = parsed_close
                elif "location" in k or "procuring" in k or "entity" in k:
                    if v and len(v) > 2:
                        procuring_entity = f"{portal_name} ({v[:80]})"

            if not closing_date:
                closing_date = self._extract_date(card_text)

            # Skip expired tenders
            if closing_date and closing_date < self.today_date:
                continue

            if not closing_date and publish_date:
                age_days = (self.today_date - publish_date).days
                if age_days > 30:
                    continue

            record = TenderRecord(
                tender_id=tender_id,
                source_portal=portal_name,
                source_type="PORTAL",
                source_language="EN" if any(c.isascii() for c in title) else "BN",
                title=title,
                category_matched=category,
                procuring_entity=procuring_entity,
                publish_date=publish_date,
                closing_date=closing_date,
                estimated_value_bdt=self.extract_value_bdt(card_text),
                quantity=self.extract_quantity(card_text),
                detail_url=detail_url,
                is_manual_tender="hardcopy" in card_text.lower() or "manual" in card_text.lower(),
                raw_snippet=card_text[:500],
            )
            records.append(self.apply_flags(record))

        return records

    # --- Helpers ---

    def _extract_pdf_text(self, pdf_url: str) -> str:
        try:
            resp = requests.get(pdf_url, headers=HEADERS, timeout=15)
            resp.raise_for_status()

            # Try pypdf first
            try:
                import pypdf
                reader = pypdf.PdfReader(io.BytesIO(resp.content))
                text = "\n".join([page.extract_text() or "" for page in reader.pages[:3]])
                if text.strip():
                    return text
            except Exception:
                pass

            # Try pdfplumber fallback
            try:
                import pdfplumber
                with pdfplumber.open(io.BytesIO(resp.content)) as pdf:
                    text = "\n".join([page.extract_text() or "" for page in pdf.pages[:3]])
                    if text.strip():
                        return text
            except Exception:
                pass

        except Exception as exc:
            logger.debug("Failed to extract PDF text from %s: %s", pdf_url, exc)
        return ""

    def _generate_id(self, portal: str, title: str, link: str) -> str:
        # Check for numeric pattern in title/link
        m = re.search(r"\b(TN-?\d{4,10}|\d{5,10})\b", title + " " + link)
        if m:
            return m.group(1)
        return hashlib.sha1(f"{portal}{title}{link}".encode("utf-8")).hexdigest()[:12]

    def _parse_single_date(self, text: str) -> Optional[date]:
        if not text:
            return None
        from dateutil import parser as dateparser
        try:
            return dateparser.parse(text.strip(), dayfirst=True).date()
        except Exception:
            return self._extract_date(text)

    def _extract_date(self, text: str) -> Optional[date]:
        m = re.search(r"(?:last date|closing date|deadline|submission date)[:\s]*([0-9]{1,2}[\-/][0-9]{1,2}[\-/][0-9]{2,4})", text, re.IGNORECASE)
        if not m:
            m = re.search(r"\b([0-9]{1,2}[\-/][0-9]{1,2}[\-/][0-9]{2,4})\b", text)
        if m:
            from dateutil import parser as dateparser
            try:
                return dateparser.parse(m.group(1), dayfirst=True).date()
            except Exception:
                pass
        return None
