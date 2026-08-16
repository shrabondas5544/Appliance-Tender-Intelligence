from datetime import date
from scrapers.epaper_ocr.prothom_alo import ProthomAloScraper

if __name__ == "__main__":
    target = date(2026, 8, 16)
    print(f"Testing Prothom Alo OCR Scraper for date: {target}...")
    
    scraper = ProthomAloScraper()
    results = scraper.scrape_date(target_date=target)
    
    print(f"\nCompleted! Total Appliance Tenders Found: {len(results)}")
    for r in results:
        print(f"\n[+] Category: {r.category_matched.upper()}")
        print(f"    Title:    {r.title}")
        print(f"    Snippet:  {r.raw_snippet}")
        print(f"    Source:   {r.detail_url}")
