import asyncio
import time
import random
from datetime import datetime, timezone
import httpx
from playwright.async_api import async_playwright
import os
from dotenv import load_dotenv

load_dotenv()

# --- CONFIG ---
PC_URL = "https://www.pokemoncenter.com"
POLL_INTERVAL_MIN = 25  # seconds
POLL_INTERVAL_MAX = 45  # seconds
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")  # Use Service Role Key
HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json"
}

async def update_supabase_state(state, confidence=1.0, details=None):
    """Updates the global monitor state in Supabase"""
    try:
        async with httpx.AsyncClient() as client:
            payload = {
                "state": state,
                "confidence_score": confidence,
                "last_checked": datetime.now(timezone.utc).isoformat(),
                "queue_details": details or {},
                "monitor_healthy": True,
                "updated_at": datetime.now(timezone.utc).isoformat()
            }
            if state == "QUEUE_ACTIVE":
                payload["detected_at"] = datetime.now(timezone.utc).isoformat()

            # Update the record (Assuming only one row exists)
            response = await client.patch(
                f"{SUPABASE_URL}/rest/v1/pc_monitor_state?id=neq.00000000-0000-0000-0000-000000000000",
                headers=HEADERS,
                json=payload
            )
            print(f"[DB] State updated to {state}: {response.status_code}")
    except Exception as e:
        print(f"[DB] Error: {e}")

async def detect_queue(page):
    """Multi-signal queue detection logic"""
    signals = {
        "text_match": False,
        "cookie_match": False,
        "network_match": False,
        "url_match": False
    }

    # 1. URL Check (Queue-it often redirects)
    if "queue-it.net" in page.url:
        signals["url_match"] = True

    # 2. Text Content Check
    content = await page.content()
    queue_keywords = ["waiting room", "you are now in line", "queue-it", "approximate wait time"]
    if any(word in content.lower() for word in queue_keywords):
        signals["text_match"] = True

    # 3. Cookie Check
    cookies = await page.context.cookies()
    if any("QueueITAccepted" in c['name'] for c in cookies):
        signals["cookie_match"] = True

    # Calculate confidence
    fired = [k for k, v in signals.items() if v]
    confidence = len(fired) / 4.0
    is_active = len(fired) > 0
    
    return is_active, confidence, signals

async def monitor_loop():
    print("🚀 Pokémon Center Stealth Monitor Started")
    
    async with async_playwright() as p:
        # Launch browser with stealth settings
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={'width': 1280, 'height': 720}
        )

        while True:
            try:
                page = await context.new_page()
                print(f"[{datetime.now().strftime('%H:%M:%S')}] Checking Pokémon Center...")
                
                # Navigate with timeout and realistic waiting
                await page.goto(PC_URL, wait_until="domcontentloaded", timeout=60000)
                await asyncio.sleep(2) # Give a moment for potential redirects
                
                is_queue, confidence, signals = await detect_queue(page)
                
                new_state = "QUEUE_ACTIVE" if is_queue else "NORMAL"
                await update_supabase_state(new_state, confidence, signals)
                
                await page.close()
                
            except Exception as e:
                print(f"⚠️ Monitor Error: {e}")
                # Don't update state to ERROR immediately unless it's a persistent failure
                # await update_supabase_state("ERROR", 0, {"error": str(e)})

            # Randomized Sleep to stay stealthy
            sleep_time = random.uniform(POLL_INTERVAL_MIN, POLL_INTERVAL_MAX)
            await asyncio.sleep(sleep_time)

if __name__ == "__main__":
    asyncio.run(monitor_loop())
