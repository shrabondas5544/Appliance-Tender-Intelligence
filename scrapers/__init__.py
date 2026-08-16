from scrapers.egp_scraper import EGPScraper
from scrapers.epaper_ocr.financial_express import FinancialExpressScraper
from scrapers.epaper_ocr.prothom_alo import ProthomAloScraper
from scrapers.epaper_ocr.dhaka_tribune import DhakaTribuneScraper

# Active scrapers registry: Financial Express, Prothom Alo & Dhaka Tribune (Engine 2) + e-GP Scraper
ACTIVE_SCRAPERS = [
    FinancialExpressScraper,
    ProthomAloScraper,
    DhakaTribuneScraper,
    EGPScraper,
]

