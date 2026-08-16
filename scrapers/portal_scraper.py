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
from bs4 import BeautifulSoup

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
            resp = requests.get(url, headers=HEADERS, timeout=20)
            resp.raise_for_status()
        except Exception as exc:
            logger.warning("Could not fetch HTML portal [%s]: %s", portal_name, exc)
            return []

        soup = BeautifulSoup(resp.text, "html.parser")
        rows = soup.find_all("tr")

        for r in rows:
            cells = r.find_all(["td", "th"])
            if len(cells) < 2:
                continue

            row_text = " ".join([c.get_text(" ", strip=True) for c in cells])
            category = self.match_category(row_text)
            if not category:
                continue

            # Extract tender title and link if available
            link_tag = r.find("a", href=True)
            detail_url = urljoin(url, link_tag["href"]) if link_tag else url
            title = link_tag.get_text(" ", strip=True) if link_tag else cells[0].get_text(" ", strip=True)

            if len(title) < 10:
                title = row_text[:200]

            tender_id = self._generate_id(portal_name, title, detail_url)
            closing_date = self._extract_date(row_text)

            if closing_date and closing_date < self.today_date:
                continue

            record = TenderRecord(
                tender_id=tender_id,
                source_portal=portal_name,
                source_type="PORTAL",
                source_language="EN" if any(c.isascii() for c in title) else "BN",
                title=title,
                category_matched=category,
                procuring_entity=portal_name,
                publish_date=self.today_date,
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
