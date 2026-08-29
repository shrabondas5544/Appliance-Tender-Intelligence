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
    "consumer_lighting": "Consumer Lighting (LED Bulbs, Tubes & Panels)",
    "commercial_lighting": "Commercial & Outdoor Lighting (Street, Track & Tunnel)",
    "electrical_accessories": "Electrical Accessories & Breakers",
    "small_appliances": "Small Appliances (Dry Iron, Mosquito Bat)",
    "water_heater": "Room & Water Heaters (Geyser)",
    "kitchen_appliances": "Kitchen Appliances (Blender, Kettle, Toaster, Coffee Maker)",
    "cleaning_appliances": "Cleaning Appliances (Vacuum & Floor Cleaner)",
    "personal_care": "Personal Care (Hair Dryer, Shaver, Trimmer)",
}


def _get_source_badge(source_type: str, portal_name: str) -> str:
    s_type = (source_type or "EGP").upper()
    if s_type == "EGP":
        return f'<span style="background:#2980b9;color:#fff;padding:2px 6px;border-radius:4px;font-size:11px;font-weight:bold;">e-GP</span>'
    return f'<span style="background:#8e44ad;color:#fff;padding:2px 6px;border-radius:4px;font-size:11px;font-weight:bold;">{portal_name}</span>'


def build_html(rows: List[sqlite3.Row], today_date: Optional[date] = None) -> str:
    if today_date is None:
        today_date = date.today()
    today_str = today_date.isoformat()

    active_rows = [
        r for r in rows
        if not r["closing_date"] or r["closing_date"] >= today_str
    ]

    if not active_rows:
        body = "<p>No new appliance tenders found today across e-GP and Direct Portals.</p>"
    else:
        rows_html = ""
        for r in active_rows:
            category = CATEGORY_LABELS.get(r["category_matched"], r["category_matched"])
            start_date = r["publish_date"] or "—"
            entity = r["procuring_entity"] or "—"
            closing = r["closing_date"] or "—"
            link = r["detail_url"] or "#"
            s_type = r["source_type"] if "source_type" in r.keys() else "EGP"
            badge = _get_source_badge(s_type, r["source_portal"])

            rows_html += f"""
            <tr>
                <td class="col-tender" style="padding:10px;border-bottom:1px solid #eee;">
                    <div style="margin-bottom:4px;">{badge}</div>
                    <a href="{link}" style="color:#1a5276;text-decoration:none;font-weight:600;font-size:14px;">{r['title']}</a>
                </td>
                <td class="col-category" style="padding:10px;border-bottom:1px solid #eee;">
                    <span class="mobile-label">Category: </span>{category}
                </td>
                <td class="col-entity" style="padding:10px;border-bottom:1px solid #eee;">
                    <span class="mobile-label">Entity: </span>{entity}
                </td>
                <td class="col-start" style="padding:10px;border-bottom:1px solid #eee;">
                    <span class="mobile-label">Start Date: </span>{start_date}
                </td>
                <td class="col-close" style="padding:10px;border-bottom:1px solid #eee;">
                    <span class="mobile-label">Closing Date: </span>{closing}
                </td>
            </tr>"""

        body = f"""
        <table class="responsive-table" style="width:100%;border-collapse:collapse;font-family:Arial,sans-serif;font-size:13px;margin-top:16px;">
            <thead>
                <tr style="background:#f4f4f4;text-align:left;">
                    <th style="padding:8px;">Source & Tender</th>
                    <th style="padding:8px;">Category</th>
                    <th style="padding:8px;">Entity</th>
                    <th style="padding:8px;">Start Date</th>
                    <th style="padding:8px;">Closing Date</th>
                </tr>
            </thead>
            <tbody>
                {rows_html}
            </tbody>
        </table>"""

    return f"""
    <html>
    <head>
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <style>
            @media screen and (max-width: 600px) {{
                .responsive-table, .responsive-table tbody, .responsive-table tr, .responsive-table td {{
                    display: block !important;
                    width: 100% !important;
                    box-sizing: border-box !important;
                }}
                .responsive-table thead {{
                    display: none !important;
                }}
                .responsive-table tr {{
                    border: 1px solid #e0e0e0 !important;
                    border-radius: 8px !important;
                    margin-bottom: 16px !important;
                    padding: 12px !important;
                    background: #ffffff !important;
                    box-shadow: 0 2px 6px rgba(0,0,0,0.06) !important;
                }}
                .responsive-table td {{
                    border: none !important;
                    border-bottom: 1px solid #f0f0f0 !important;
                    padding: 8px 0 !important;
                    position: relative !important;
                    text-align: left !important;
                }}
                .responsive-table td:last-child {{
                    border-bottom: none !important;
                }}
                .responsive-table td.col-tender {{
                    padding-bottom: 10px !important;
                }}
                .mobile-label {{
                    display: inline-block !important;
                    font-weight: bold !important;
                    color: #555 !important;
                    min-width: 95px !important;
                    font-size: 12px !important;
                    text-transform: uppercase !important;
                    letter-spacing: 0.5px !important;
                }}
            }}
            @media screen and (min-width: 601px) {{
                .mobile-label {{
                    display: none !important;
                }}
            }}
        </style>
    </head>
    <body style="font-family:Arial,sans-serif;color:#222;line-height:1.5;">
        <h2 style="color:#2c3e50;">Appliance Tender Intelligence Digest ({today_date.isoformat()})</h2>
        <p style="color:#555;">{len(active_rows)} new tender(s) captured across <b>e-GP</b> and <b>Direct Portals</b>.</p>
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
