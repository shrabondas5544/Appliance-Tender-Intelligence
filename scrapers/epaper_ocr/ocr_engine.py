import os
import logging
import pytesseract
import cv2
import numpy as np
from PIL import Image

logger = logging.getLogger(__name__)

class OCREngine:
    def __init__(self, lang: str = "eng"):
        self.lang = lang

    def preprocess_image(self, image_path: str) -> np.ndarray:
        """Grayscale and threshold image to improve OCR accuracy on scanned print."""
        img = cv2.imread(image_path)
        if img is None:
            raise FileNotFoundError(f"Could not read image at {image_path}")

        # Grayscale conversion
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        
        # Noise reduction
        blur = cv2.GaussianBlur(gray, (3, 3), 0)
        
        # Otsu thresholding to separate text from background
        _, thresh = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        return thresh

    def extract_text(self, image_path: str) -> str:
        if not os.path.exists(image_path):
            return ""
        try:
            # First try image preprocessing for better Tesseract accuracy
            try:
                processed_img = self.preprocess_image(image_path)
                # PSM 3: Fully automatic page segmentation
                custom_config = r'--oem 3 --psm 3'
                text = pytesseract.image_to_string(processed_img, lang=self.lang, config=custom_config)
                if text.strip():
                    return text
            except Exception as e:
                logger.debug("OpenCV preprocessing failed, falling back to PIL raw OCR: %s", e)

            # Fallback to loading the image directly with PIL (no OpenCV)
            img = Image.open(image_path)
            custom_config = r'--oem 3 --psm 3'
            return pytesseract.image_to_string(img, lang=self.lang, config=custom_config)

        except Exception as e:
            logger.error("OCR failed for %s: %s", image_path, e)
            return ""
