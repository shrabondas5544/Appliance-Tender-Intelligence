"""
OCR Processor.

Image Pre-processing (OpenCV) & Dual-Language OCR (Tesseract ben+eng).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, Tuple

logger = logging.getLogger(__name__)


class OCRProcessor:
    def __init__(self, lang: str = "ben+eng"):
        self.lang = lang

    def preprocess_image(self, image_path: Path) -> Path:
        """Apply OpenCV grayscale, binarization, and noise reduction for OCR optimization."""
        processed_path = image_path.parent / f"proc_{image_path.name}"
        if processed_path.exists():
            return processed_path

        try:
            import cv2
            img = cv2.imread(str(image_path))
            if img is None:
                return image_path

            # Grayscale conversion
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            # Noise reduction
            blur = cv2.GaussianBlur(gray, (3, 3), 0)
            # Otsu's binarization thresholding
            _, thresh = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

            cv2.imwrite(str(processed_path), thresh)
            return processed_path
        except Exception as exc:
            logger.debug("OpenCV preprocessing unavailable or failed for %s: %s", image_path, exc)
            return image_path

    def extract_text_and_confidence(self, image_path: Path) -> Tuple[str, float]:
        """Perform Tesseract OCR and return (extracted_text, average_confidence_score)."""
        processed_path = self.preprocess_image(image_path)

        try:
            import pytesseract
            from PIL import Image

            img = Image.open(processed_path)

            # Extract detailed data for confidence estimation
            data = pytesseract.image_to_data(img, lang=self.lang, output_type=pytesseract.Output.DICT)
            confidences = [int(c) for c in data.get("conf", []) if str(c).replace("-", "").isdigit() and int(c) >= 0]
            avg_conf = float(sum(confidences) / len(confidences)) if confidences else 75.0

            extracted_text = pytesseract.image_to_string(img, lang=self.lang)
            return extracted_text, round(avg_conf, 2)

        except Exception as exc:
            logger.info("Tesseract OCR unavailable or failed for %s: %s. Using text fallback.", image_path, exc)
            return "", 0.0
