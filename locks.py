"""
Shared cross-module synchronisation primitives.

browser_lock — RLock that serialises ALL Playwright/Chromium launches
across the entire process (API pipeline, enrich job, rotation job).
Using RLock so job_scrape_rotation can hold the lock while calling
run_pipeline(), which also acquires it internally, without deadlocking.
"""
import threading

browser_lock = threading.RLock()
