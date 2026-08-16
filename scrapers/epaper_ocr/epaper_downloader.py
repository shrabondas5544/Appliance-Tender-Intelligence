"""
E-Paper Downloader.

Downloads target edition pages (images or PDFs) from daily newspaper e-papers
(Financial Express, Daily Star, Prothom Alo, Bonik Barta, Ittefaq).
"""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional
import requests

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
}


class EPaperDownloader:
    def __init__(self, download_dir: Optional[Path] = None):
        from core.settings import settings
        self.download_dir = download_dir or (settings.PROJECT_ROOT / "data" / "epaper_cache")
        self.download_dir.mkdir(parents=True, exist_ok=True)

    def fetch_page_images(self, epaper_config: Dict, target_date: date) -> List[Path]:
        """Download and return paths to daily target page images."""
        name = epaper_config.get("name", "Unknown Paper")
        url_template = epaper_config.get("url_template")
        target_pages = epaper_config.get("target_pages", ["1"])

        if not url_template:
            return []

        date_str = target_date.strftime("%Y-%m-%d")
        downloaded_paths: List[Path] = []

        for page in target_pages:
            url = url_template.format(page=page, date=date_str)
            filename = f"{name.lower().replace(' ', '_')}_{date_str}_p{page}.jpg"
            save_path = self.download_dir / filename

            if save_path.exists():
                downloaded_paths.append(save_path)
                continue

            try:
                resp = requests.get(url, headers=HEADERS, timeout=20)
                if resp.status_code == 200 and len(resp.content) > 5000:
                    save_path.write_bytes(resp.content)
                    downloaded_paths.append(save_path)
                    logger.info("[%s] Downloaded page %s image -> %s", name, page, save_path.name)
            except Exception as exc:
                logger.debug("[%s] Could not download e-paper page %s: %s", name, page, exc)

        return downloaded_paths
