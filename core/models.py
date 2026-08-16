"""
Common data contract every scraper must produce.

Supports multi-engine ingestion: eprocure.gov.bd (EGP), Direct Bank/Corporate Portals (PORTAL),
and Newspaper E-Paper OCR Engine (EPAPER_OCR).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, Field


class TenderRecord(BaseModel):
    # --- Identity / dedup key ---
    tender_id: str = Field(..., description="Portal or source native tender ID or content hash")
    source_portal: str = Field(..., description="e.g. 'eprocure.gov.bd', 'Prime Bank', 'Financial Express'")
    source_type: str = Field(default="EGP", description="EGP | PORTAL | EPAPER_OCR")
    source_language: str = Field(default="EN", description="EN | BN | DUAL")

    # --- Core content ---
    title: str
    category_matched: str = Field(..., description="air_conditioner / television / refrigerator / washing_machine / fan")
    procuring_entity: Optional[str] = None

    # --- Dates ---
    publish_date: Optional[date] = None
    closing_date: Optional[date] = None

    # --- Commercial signals ---
    estimated_value_bdt: Optional[float] = None
    quantity: Optional[int] = None

    # --- Engine 2 (OCR) & Engine 1 (Portal) Metadata ---
    ocr_confidence: Optional[float] = Field(default=None, description="Average confidence score from Tesseract OCR (0-100)")
    clipped_image_url: Optional[str] = Field(default=None, description="Local path or URL to clipped advertisement snippet image")

    # --- Links / flags ---
    detail_url: Optional[str] = None
    is_manual_tender: bool = False
    is_high_value: bool = False
    is_critical: bool = False

    # --- Bookkeeping ---
    scraped_at: datetime = Field(default_factory=datetime.utcnow)
    raw_snippet: Optional[str] = Field(
        default=None,
        description="Original title/description text or OCR text snippet.",
    )

    def dedup_key(self) -> str:
        return f"{self.source_portal}::{self.tender_id}"
