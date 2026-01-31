"""
LinkedIn Job Scraper using Playwright with persistent browser profile.
Extracts job listings and full descriptions from LinkedIn Jobs.
"""

import asyncio
import random
import json
from pathlib import Path
from dataclasses import dataclass
from typing import Optional
from playwright.async_api import async_playwright, Page, BrowserContext


# Cap raw HTML at ~100KB for debugging storage
MAX_RAW_HTML_CHARS = 100_000


@dataclass
class JobData:
    """Structured job data extracted from LinkedIn."""
    company: str
    title: str
    location: str
    job_url: str
    description: str
    posted_age: Optional[str] = None
    raw_html: Optional[str] = None  # Captured on failure for debugging
    extraction_failed: bool = False


class LinkedInScraper:
    """Scrapes job listings from LinkedIn using Playwright."""

    def __init__(self, config_path: Path):
        self.config = self._load_config(config_path)
        self.profile_dir = Path(__file__).parent.parent / "browser_profile"
        self.profile_dir.mkdir(exist_ok=True)
        self.browser_context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None

    def _load_config(self, config_path: Path) -> dict:
        """Load configuration from searches.json."""
        with open(config_path) as f:
            return json.load(f)

    async def _random_delay(self, delay_type: str = "between_actions"):
        """Insert human-like random delay."""
        delays = self.config.get("settings", {})
        if delay_type == "between_jobs":
            delay_config = delays.get("delay_between_jobs_ms", {"min": 2000, "max": 5000})
        else:
            delay_config = delays.get("delay_between_actions_ms", {"min": 500, "max": 1500})

        delay_ms = random.randint(delay_config["min"], delay_config["max"])
        await asyncio.sleep(delay_ms / 1000)

    async def start_browser(self, headless: bool = False):
        """Start browser with persistent profile."""
        print(f"[SCRAPER] Starting browser with profile at: {self.profile_dir}")

        self.playwright = await async_playwright().start()
        self.browser_context = await self.playwright.chromium.launch_persistent_context(
            user_data_dir=str(self.profile_dir),
            headless=headless,
            viewport={"width": 1280, "height": 900},
            slow_mo=100,  # Slow down actions for reliability
        )
        self.page = await self.browser_context.new_page()
        print("[SCRAPER] Browser started successfully")

    async def close_browser(self):
        """Close browser and cleanup."""
        if self.browser_context:
            await self.browser_context.close()
        if hasattr(self, 'playwright'):
            await self.playwright.stop()
        print("[SCRAPER] Browser closed")

    async def check_login_status(self) -> bool:
        """Check if user is logged into LinkedIn."""
        try:
            await self.page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded", timeout=60000)
            await self._random_delay()

            # Check for login page redirect or login elements
            current_url = self.page.url
            if "login" in current_url or "authwall" in current_url or "checkpoint" in current_url:
                print("[SCRAPER] Not logged in - manual login required")
                return False

            print("[SCRAPER] LinkedIn session active")
            return True
        except Exception as e:
            print(f"[SCRAPER] Could not verify login status: {e}")
            return False

    async def wait_for_manual_login(self):
        """Wait for user to manually log in."""
        # Navigate to LinkedIn login page
        try:
            await self.page.goto("https://www.linkedin.com/login", wait_until="domcontentloaded", timeout=60000)
        except Exception:
            pass  # Page might already be on login

        print("\n" + "="*50)
        print("MANUAL LOGIN REQUIRED")
        print("1. Log into LinkedIn in the browser window")
        print("2. Wait for your feed to load")
        print("3. Press Enter here when done...")
        print("="*50 + "\n")

        # Wait for user input
        await asyncio.get_event_loop().run_in_executor(None, input)

        print("[SCRAPER] Verifying login...")
        await asyncio.sleep(2)  # Give page time to settle

    async def navigate_to_search(self, search_url: str) -> bool:
        """Navigate to a LinkedIn Jobs search URL."""
        print(f"[SCRAPER] Navigating to search: {search_url[:80]}...")

        try:
            # Use domcontentloaded instead of networkidle (LinkedIn never goes idle)
            await self.page.goto(search_url, wait_until="domcontentloaded", timeout=60000)
            await self._random_delay()

            # Wait for job cards to actually appear
            job_card_selectors = [
                ".jobs-search-results-list",
                ".scaffold-layout__list-container",
                "[data-job-id]",
                ".job-card-container",
            ]

            for selector in job_card_selectors:
                try:
                    await self.page.wait_for_selector(selector, timeout=10000)
                    print(f"[SCRAPER] Search page loaded (found {selector})")
                    await self._random_delay()
                    return True
                except Exception:
                    continue

            # Last resort: just wait and check if any content loaded
            await asyncio.sleep(3)
            content = await self.page.content()
            if "job" in content.lower():
                print("[SCRAPER] Search page loaded (content check)")
                return True

            print("[SCRAPER] Could not find job cards on page")
            return False

        except Exception as e:
            print(f"[SCRAPER] Failed to load search page: {e}")
            return False

    async def get_job_cards(self, max_jobs: int = 25) -> list:
        """Get list of job card elements."""
        # LinkedIn uses various selectors for job cards
        selectors = [
            ".jobs-search-results__list-item",
            ".scaffold-layout__list-item",
            "[data-occludable-job-id]",
        ]

        job_cards = []
        for selector in selectors:
            cards = await self.page.query_selector_all(selector)
            if cards:
                job_cards = cards[:max_jobs]
                print(f"[SCRAPER] Found {len(job_cards)} job cards")
                break

        if not job_cards:
            print("[SCRAPER] No job cards found")

        return job_cards

    async def scroll_to_element(self, element):
        """Smoothly scroll element into view."""
        await element.scroll_into_view_if_needed()
        await self._random_delay()

    async def extract_job_data(self, card_index: int) -> Optional[JobData]:
        """Click a job card and extract full job details."""
        print(f"[SCRAPER] Processing job {card_index + 1}...")

        try:
            # Re-fetch job cards (DOM may have changed)
            job_cards = await self.get_job_cards()
            if card_index >= len(job_cards):
                print(f"[SCRAPER] Job card {card_index} no longer exists")
                return None

            card = job_cards[card_index]

            # Scroll to card and click
            await self.scroll_to_element(card)
            await card.click()
            await self._random_delay("between_jobs")

            # Wait for job details panel to load
            await self.page.wait_for_selector(
                ".jobs-details, .job-details-jobs-unified-top-card__job-title",
                timeout=10000
            )
            await self._random_delay()

            # Extract job details
            job_data = await self._extract_details_from_panel()

            if job_data:
                print(f"[SCRAPER] Extracted: {job_data.title} at {job_data.company}")

            return job_data

        except Exception as e:
            print(f"[SCRAPER] Failed to extract job {card_index + 1}: {e}")
            return None

    async def _extract_details_from_panel(self, capture_html_on_failure: bool = True) -> Optional[JobData]:
        """Extract job details from the detail panel."""
        raw_html = None

        try:
            # Company name
            company = await self._get_text_content([
                ".job-details-jobs-unified-top-card__company-name a",
                ".job-details-jobs-unified-top-card__company-name",
                ".jobs-unified-top-card__company-name a",
                ".jobs-unified-top-card__company-name",
            ])

            # Job title
            title = await self._get_text_content([
                ".job-details-jobs-unified-top-card__job-title",
                ".jobs-unified-top-card__job-title",
                "h1.t-24",
            ])

            # Location
            location = await self._get_text_content([
                ".job-details-jobs-unified-top-card__primary-description-container .tvm__text",
                ".jobs-unified-top-card__bullet",
                ".job-details-jobs-unified-top-card__primary-description",
            ])

            # Posted age
            posted_age = await self._get_text_content([
                ".job-details-jobs-unified-top-card__primary-description-container time",
                ".jobs-unified-top-card__posted-date",
            ])

            # Full job description - may need to click "See more"
            await self._expand_description()

            description = await self._get_text_content([
                ".jobs-description-content__text",
                ".jobs-description__content",
                "#job-details",
            ])

            # Get job URL from current page or data attribute
            job_url = self.page.url

            if not company or not title or not description:
                print("[SCRAPER] Missing required fields")
                # Capture raw HTML on failure for debugging
                if capture_html_on_failure:
                    try:
                        raw_html = await self.page.content()
                        if raw_html and len(raw_html) > MAX_RAW_HTML_CHARS:
                            raw_html = raw_html[:MAX_RAW_HTML_CHARS] + "\n<!-- TRUNCATED -->"
                    except Exception:
                        pass
                return JobData(
                    company=company or "Unknown",
                    title=title or "Unknown",
                    location=location or "Unknown",
                    job_url=job_url,
                    description=description or "",
                    posted_age=posted_age,
                    raw_html=raw_html,
                    extraction_failed=True,
                )

            return JobData(
                company=company.strip(),
                title=title.strip(),
                location=location.strip() if location else "Unknown",
                job_url=job_url,
                description=description.strip(),
                posted_age=posted_age.strip() if posted_age else None,
            )

        except Exception as e:
            print(f"[SCRAPER] Error extracting details: {e}")
            # Capture raw HTML on failure for debugging
            if capture_html_on_failure:
                try:
                    raw_html = await self.page.content()
                    if raw_html and len(raw_html) > MAX_RAW_HTML_CHARS:
                        raw_html = raw_html[:MAX_RAW_HTML_CHARS] + "\n<!-- TRUNCATED -->"
                except Exception:
                    pass
            return JobData(
                company="Unknown",
                title="Unknown",
                location="Unknown",
                job_url=self.page.url if self.page else "",
                description="",
                raw_html=raw_html,
                extraction_failed=True,
            )

    async def _get_text_content(self, selectors: list) -> Optional[str]:
        """Try multiple selectors and return first matching text content."""
        for selector in selectors:
            try:
                element = await self.page.query_selector(selector)
                if element:
                    text = await element.text_content()
                    if text and text.strip():
                        return text.strip()
            except Exception:
                continue
        return None

    async def _expand_description(self):
        """Click 'See more' button if present to expand job description."""
        try:
            see_more_selectors = [
                ".jobs-description__footer-button",
                "button[aria-label='Click to see more description']",
                ".show-more-less-html__button",
            ]

            for selector in see_more_selectors:
                button = await self.page.query_selector(selector)
                if button:
                    await button.click()
                    await self._random_delay()
                    break
        except Exception:
            pass  # Description might already be expanded

    async def _get_job_ids_from_cards(self) -> list[str]:
        """Extract job IDs from visible job cards."""
        job_ids = []
        try:
            # LinkedIn stores job ID in data attributes
            cards = await self.page.query_selector_all("[data-occludable-job-id]")
            for card in cards:
                job_id = await card.get_attribute("data-occludable-job-id")
                if job_id:
                    job_ids.append(job_id)

            # Fallback: try other selectors
            if not job_ids:
                cards = await self.page.query_selector_all("[data-job-id]")
                for card in cards:
                    job_id = await card.get_attribute("data-job-id")
                    if job_id:
                        job_ids.append(job_id)
        except Exception as e:
            print(f"[SCRAPER] Error getting job IDs: {e}")

        return job_ids

    async def scrape_search(self, search_name: str, search_url: str,
                           is_stored_callback: callable) -> list[JobData]:
        """Scrape jobs from a search URL until we find target_new truly new jobs."""
        print(f"\n[SCRAPER] Starting search: {search_name}")

        if not await self.navigate_to_search(search_url):
            return []

        settings = self.config.get("settings", {})
        target_new = settings.get("target_new_jobs_per_search", 10)
        max_to_check = settings.get("max_jobs_to_check_per_search", 100)

        # Warn if using defaults (config discipline)
        if "target_new_jobs_per_search" not in settings:
            print("[SCRAPER] WARNING: target_new_jobs_per_search not in config, using default: 10")
        if "max_jobs_to_check_per_search" not in settings:
            print("[SCRAPER] WARNING: max_jobs_to_check_per_search not in config, using default: 100")

        new_jobs = []
        total_checked = 0
        seen_job_ids = set()  # Track IDs checked this session
        page_num = 1

        try:
            while len(new_jobs) < target_new and total_checked < max_to_check:
                print(f"[SCRAPER] Processing page {page_num}...")

                # Get job IDs from visible cards
                current_job_ids = await self._get_job_ids_from_cards()

                if not current_job_ids:
                    print("[SCRAPER] No job cards found")
                    break

                # Filter to IDs we haven't checked this session
                unchecked_ids = [jid for jid in current_job_ids if jid not in seen_job_ids]

                if not unchecked_ids:
                    # All jobs on this page already checked, try next page
                    if not await self._go_to_next_page():
                        break
                    page_num += 1
                    continue

                for job_id in unchecked_ids:
                    if len(new_jobs) >= target_new or total_checked >= max_to_check:
                        break

                    seen_job_ids.add(job_id)
                    total_checked += 1

                    # Build canonical_id for storage check (keeps identity logic consistent)
                    canonical_id = f"linkedin:job:{job_id}"

                    # Check storage BEFORE extracting full details
                    if is_stored_callback(canonical_id):
                        print(f"[SCRAPER] Skipping stored: {job_id}")
                        continue

                    # Extract full job details (expensive operation)
                    try:
                        job_data = await asyncio.wait_for(
                            self._extract_job_by_id(job_id),
                            timeout=30
                        )
                    except asyncio.TimeoutError:
                        print(f"[SCRAPER] Timeout extracting {job_id}")
                        continue

                    if job_data and not job_data.extraction_failed:
                        new_jobs.append(job_data)
                        print(f"[SCRAPER] New job {len(new_jobs)}/{target_new}: {job_data.title}")

                    await self._random_delay("between_jobs")

                # Stale search early exit: if we've checked 50+ and found nothing, bail
                if total_checked >= 50 and len(new_jobs) == 0:
                    print("[SCRAPER] Search appears stale (50 checked, 0 new), exiting early")
                    break

                # Need more? Go to next page
                if len(new_jobs) < target_new and total_checked < max_to_check:
                    if await self._go_to_next_page():
                        page_num += 1
                    else:
                        print("[SCRAPER] No more pages")
                        break

        except asyncio.CancelledError:
            # Timeout - return whatever we collected so far
            print(f"[SCRAPER] Timeout - returning {len(new_jobs)} jobs collected so far")

        # Log efficiency metrics
        efficiency = (len(new_jobs) / total_checked * 100) if total_checked > 0 else 0
        print(f"[SCRAPER] Completed '{search_name}': {len(new_jobs)} new / {total_checked} checked ({efficiency:.1f}% efficiency)")
        return new_jobs

    async def _extract_job_by_id(self, job_id: str) -> Optional[JobData]:
        """Extract job data by clicking a specific job card by ID."""
        try:
            # Find the card with this job ID
            card = await self.page.query_selector(f"[data-occludable-job-id='{job_id}']")
            if not card:
                card = await self.page.query_selector(f"[data-job-id='{job_id}']")

            if not card:
                return None

            # Scroll card into view and click (same as original extract_job_data)
            await self.scroll_to_element(card)
            await card.click()
            await self._random_delay("between_jobs")

            # Wait for job details panel to load (same selector as original)
            await self.page.wait_for_selector(
                ".jobs-details, .job-details-jobs-unified-top-card__job-title",
                timeout=10000
            )
            await self._random_delay()

            # Reuse existing extraction logic
            job_data = await self._extract_details_from_panel()

            if job_data:
                print(f"[SCRAPER] Extracted: {job_data.title} at {job_data.company}")

            return job_data

        except Exception as e:
            print(f"[SCRAPER] Error extracting job {job_id}: {e}")
            return None

    async def _go_to_next_page(self) -> bool:
        """Click the Next button to go to next page of results. Returns True if successful."""
        try:
            # Look for the Next button or pagination controls
            next_button_selectors = [
                "button[aria-label='View next page']",
                ".jobs-search-pagination button[aria-label='Next']",
                ".artdeco-pagination__button--next",
                "li.artdeco-pagination__indicator--number.active + li button",  # Next page number
            ]

            for selector in next_button_selectors:
                next_btn = await self.page.query_selector(selector)
                if next_btn:
                    is_disabled = await next_btn.get_attribute("disabled")
                    if is_disabled:
                        print("[SCRAPER] Next button is disabled - no more pages")
                        return False

                    await next_btn.scroll_into_view_if_needed()
                    await self._random_delay()
                    await next_btn.click()
                    print("[SCRAPER] Clicked Next page button")

                    # Wait for new page to load
                    await self._random_delay("between_jobs")
                    await self.page.wait_for_selector("[data-job-id]", timeout=10000)
                    await self._random_delay()
                    return True

            # Try clicking the next page number directly
            current_page = await self.page.query_selector(".artdeco-pagination__indicator--number.active")
            if current_page:
                next_page = await current_page.evaluate_handle("el => el.nextElementSibling")
                if next_page:
                    next_btn = await next_page.query_selector("button")
                    if next_btn:
                        await next_btn.click()
                        print("[SCRAPER] Clicked next page number")
                        await self._random_delay("between_jobs")
                        await self.page.wait_for_selector("[data-job-id]", timeout=10000)
                        return True

            print("[SCRAPER] No next page button found")
            return False

        except Exception as e:
            print(f"[SCRAPER] Pagination failed: {e}")
            return False


async def main():
    """Test the scraper standalone."""
    config_path = Path(__file__).parent.parent / "config" / "searches.json"
    scraper = LinkedInScraper(config_path)

    try:
        await scraper.start_browser(headless=False)

        if not await scraper.check_login_status():
            await scraper.wait_for_manual_login()

        print("[TEST] Scraper ready for use")

    finally:
        await scraper.close_browser()


if __name__ == "__main__":
    asyncio.run(main())
