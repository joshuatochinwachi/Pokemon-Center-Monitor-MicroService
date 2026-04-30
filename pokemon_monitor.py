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

# --- SMART KEY SELECTOR ---
SUPABASE_URL = os.getenv("SUPABASE_URL")
SERVICE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
ANON_KEY = os.getenv("SUPABASE_ANON_KEY")

# Default to Service Key, fallback to Anon
PRIMARY_KEY = SERVICE_KEY if SERVICE_KEY else ANON_KEY

def get_headers(key=None):
    use_key = key or PRIMARY_KEY
    return {
        "apikey": use_key,
        "Authorization": f"Bearer {use_key}",
        "Content-Type": "application/json"
    }

async def fire_push_notifications(state):
    """Fetches subscribers and fires Expo push notifications"""
    try:
        async with httpx.AsyncClient() as client:
            # 1. Fetch active subscribers
            resp = await client.get(
                f"{SUPABASE_URL}/rest/v1/pc_monitor_subscribers?is_active=eq.true&select=fcm_token",
                headers=get_headers()
            )
            data = resp.json() if resp.status_code == 200 else []
            tokens = [r['fcm_token'] for r in data if 'fcm_token' in r]
            
            if not tokens: return

            # 2. Fire notifications in batches
            push_payload = [{
                "to": t,
                "title": "🚨 POKEMON CENTER QUEUE LIVE!",
                "body": "The waiting room is active. Join the line now!",
                "data": {"type": "pc_monitor", "state": state},
                "sound": "default",
                "priority": "high"
            } for t in tokens if t.startswith("ExponentPushToken")]

            if push_payload:
                await client.post("https://exp.host/--/api/v2/push/send", json=push_payload)
                print(f"[PUSH] Alerts fired to {len(push_payload)} users")
    except Exception as e:
        print(f"[PUSH] Error: {e}")

async def update_supabase_state(state, confidence=1.0, details=None):
    """Updates the global monitor state in Supabase with Fail-Safe logic"""
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

            endpoint = f"{SUPABASE_URL}/rest/v1/pc_monitor_state?id=neq.00000000-0000-0000-0000-000000000000"
            
            # Try with Primary Key
            response = await client.patch(endpoint, headers=get_headers(), json=payload)
            
            # FAIL-SAFE: If Primary fails, try Backup (Anon Key)
            if response.status_code not in [200, 204] and ANON_KEY and PRIMARY_KEY != ANON_KEY:
                print(f"⚠️ Primary Key Failed ({response.status_code}). Trying Fail-Safe with Anon Key...")
                response = await client.patch(endpoint, headers=get_headers(ANON_KEY), json=payload)

            print(f"[DB] State updated to {state}: {response.status_code}")
            
            # Transition Logic: If state changed to QUEUE_ACTIVE, fire notifications
            # (In a real scenario, you'd track the 'previous' state to avoid spam)
            if state == "QUEUE_ACTIVE" and response.status_code in [200, 204]:
                await fire_push_notifications(state)

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

    if "queue-it.net" in page.url:
        signals["url_match"] = True

    content = await page.content()
    queue_keywords = ["waiting room", "you are now in line", "queue-it", "approximate wait time"]
    if any(word in content.lower() for word in queue_keywords):
        signals["text_match"] = True

    cookies = await page.context.cookies()
    if any("QueueITAccepted" in c['name'] for c in cookies):
        signals["cookie_match"] = True

    fired = [k for k, v in signals.items() if v]
    confidence = len(fired) / 4.0
    is_active = len(fired) > 0
    
    return is_active, confidence, signals

async def monitor_loop():
    print("🚀 Pokémon Center Stealth Monitor Started")
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={'width': 1280, 'height': 720}
        )

        while True:
            try:
                page = await context.new_page()
                await page.goto(PC_URL, wait_until="domcontentloaded", timeout=60000)
                await asyncio.sleep(2) 
                
                is_queue, confidence, signals = await detect_queue(page)
                new_state = "QUEUE_ACTIVE" if is_queue else "NORMAL"
                
                await update_supabase_state(new_state, confidence, signals)
                await page.close()
                
            except Exception as e:
                print(f"⚠️ Monitor Error: {e}")

            sleep_time = random.uniform(POLL_INTERVAL_MIN, POLL_INTERVAL_MAX)
            await asyncio.sleep(sleep_time)

if __name__ == "__main__":
    asyncio.run(monitor_loop())
