from scrapers.egp_scraper import EGPScraper
from scrapers.portal_scraper import PortalScraper

# Active portal scrapers registry: e-GP Portal Scraper & Direct Bank/Corporate Portal Scraper
ACTIVE_SCRAPERS = [
    EGPScraper,
    PortalScraper,
]

