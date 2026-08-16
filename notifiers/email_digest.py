"""
Daily HTML email digest, sent via SMTP using a Gmail app password (or any SMTP provider).

Renders source badges ([e-GP], [Direct Portal], [E-Paper OCR]), dual-language snippet previews,
and OCR confidence scores.
"""

from __future__ import annotations

import logging
import smtplib
import sqlite3
from datetime import date
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import List, Optional

from core.settings import settings

logger = logging.getLogger(__name__)

CATEGORY_LABELS = {
    "air_conditioner": "Air Conditioner",
    "air_cooler": "Air Cooler",
    "air_purifier": "Air Purifier",
    "television": "Television",
    "soundbar": "Soundbar",
    "refrigerator": "Refrigerator",
    "freezer": "Freezer / Deep Freezer",
    "washing_machine": "Washing Machine",
    "fan": "Fan (Ceiling / Rechargeable)",
}


def _row_group(rows: List[sqlite3.Row], predicate) -> List[sqlite3.Row]:
    return [r for r in rows if predicate(r)]


def _get_source_badge(source_type: str, portal_name: str) -> str:
    s_type = (source_type or "EGP").upper()
    if s_type == "EGP":
        return f'<span style="background:#2980b9;color:#fff;padding:2px 6px;border-radius:4px;font-size:11px;font-weight:bold;">e-GP</span>'
    elif s_type == "PORTAL":
        return f'<span style="background:#8e44ad;color:#fff;padding:2px 6px;border-radius:4px;font-size:11px;font-weight:bold;">{portal_name}</span>'
    elif s_type == "EPAPER_OCR":
        return f'<span style="background:#d35400;color:#fff;padding:2px 6px;border-radius:4px;font-size:11px;font-weight:bold;">OCR: {portal_name}</span>'
    return f'<span style="background:#7f8c8d;color:#fff;padding:2px 6px;border-radius:4px;font-size:11px;font-weight:bold;">{portal_name}</span>'


def build_html(rows: List[sqlite3.Row], today_date: Optional[date] = None) -> str:
    if today_date is None:
        today_date = date.today()
    today_str = today_date.isoformat()

    active_rows = [
        r for r in rows
        if not r["closing_date"] or r["closing_date"] >= today_str
    ]

    critical = _row_group(active_rows, lambda r: r["is_critical"])
    high_value = _row_group(active_rows, lambda r: r["is_high_value"] and not r["is_critical"])
    manual = _row_group(active_rows, lambda r: r["is_manual_tender"] and not r["is_critical"] and not r["is_high_value"])
    standard = _row_group(
        active_rows,
        lambda r: not r["is_critical"] and not r["is_high_value"] and not r["is_manual_tender"],
    )

    def render_section(title: str, color: str, section_rows: List[sqlite3.Row]) -> str:
        if not section_rows:
            return ""
        rows_html = ""
        for r in section_rows:
            category = CATEGORY_LABELS.get(r["category_matched"], r["category_matched"])
            value = f"৳{r['estimated_value_bdt']:,.0f}" if r["estimated_value_bdt"] else "—"
            qty = r["quantity"] if r["quantity"] else "—"
            entity = r["procuring_entity"] or "—"
            closing = r["closing_date"] or "—"
            link = r["detail_url"] or "#"
            s_type = r["source_type"] if "source_type" in r.keys() else "EGP"
            badge = _get_source_badge(s_type, r["source_portal"])

            has_ocr = "ocr_confidence" in r.keys() and r["ocr_confidence"] is not None
            ocr_conf = f"<br/><small style='color:#7f8c8d;'>OCR Conf: {r['ocr_confidence']}%</small>" if has_ocr else ""

            rows_html += f"""
            <tr>
                <td style="padding:10px;border-bottom:1px solid #eee;">
                    <div style="margin-bottom:4px;">{badge}</div>
                    <a href="{link}" style="color:#1a5276;text-decoration:none;font-weight:600;font-size:14px;">{r['title']}</a>
                    {ocr_conf}
                </td>
                <td style="padding:10px;border-bottom:1px solid #eee;">{category}</td>
                <td style="padding:10px;border-bottom:1px solid #eee;">{entity}</td>
                <td style="padding:10px;border-bottom:1px solid #eee;">{closing}</td>
                <td style="padding:10px;border-bottom:1px solid #eee;">{value}</td>
                <td style="padding:10px;border-bottom:1px solid #eee;">{qty}</td>
            </tr>"""
        return f"""
        <h3 style="color:{color};margin-top:24px;">{title} ({len(section_rows)})</h3>
        <table style="width:100%;border-collapse:collapse;font-family:Arial,sans-serif;font-size:13px;">
            <tr style="background:#f4f4f4;text-align:left;">
                <th style="padding:8px;">Source & Tender</th>
                <th style="padding:8px;">Category</th>
                <th style="padding:8px;">Entity</th>
                <th style="padding:8px;">Closing</th>
                <th style="padding:8px;">Value</th>
                <th style="padding:8px;">Qty</th>
            </tr>
            {rows_html}
        </table>"""

    body = (
        render_section("🔴 Critical — Closing Soon", "#c0392b", critical)
        + render_section("🟠 High Value / High Quantity", "#e67e22", high_value)
        + render_section("📋 Manual / Portal Tenders (offline or bank purchase)", "#7f8c8d", manual)
        + render_section("🟢 Standard", "#27ae60", standard)
    )

    if not active_rows:
        body = "<p>No new appliance tenders found today across e-GP, Direct Portals, and E-Paper OCR engines.</p>"

    return f"""
    <html>
    <body style="font-family:Arial,sans-serif;color:#222;line-height:1.5;">
        <h2 style="color:#2c3e50;">Appliance Tender Intelligence — Dual-Engine Digest ({today_date.isoformat()})</h2>
        <p style="color:#555;">{len(active_rows)} new tender(s) captured across <b>e-GP</b>, <b>Direct Portals</b>, and <b>E-Paper Print OCR</b>.</p>
        {body}
    </body>
    </html>
    """


def send_digest(rows: List[sqlite3.Row], today_date: Optional[date] = None) -> bool:
    if today_date is None:
        today_date = date.today()
    if not settings.EMAIL_TO:
        logger.error("No EMAIL_TO configured — skipping send. Check your .env file.")
        return False
    if not settings.SMTP_USERNAME or not settings.SMTP_APP_PASSWORD:
        logger.error("SMTP credentials missing — check SMTP_USERNAME / SMTP_APP_PASSWORD in .env.")
        return False

    html = build_html(rows, today_date)

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"Appliance Tender Digest — {today_date.isoformat()} ({len(rows)} new)"
    msg["From"] = settings.EMAIL_FROM or settings.SMTP_USERNAME
    msg["To"] = ", ".join(settings.EMAIL_TO)
    msg.attach(MIMEText(html, "html"))

    try:
        with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT) as server:
            server.starttls()
            server.login(settings.SMTP_USERNAME, settings.SMTP_APP_PASSWORD)
            server.sendmail(msg["From"], settings.EMAIL_TO, msg.as_string())
        logger.info("Digest sent to %s (%d records)", settings.EMAIL_TO, len(rows))
        return True
    except Exception as exc:
        logger.error("Failed to send digest (SMTP/Network error): %s", exc)
        return False
