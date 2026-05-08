import asyncio
import httpx
import os
from datetime import datetime, timezone
from dotenv import load_dotenv

# Load your Supabase keys from your .env file
load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SERVICE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
ANON_KEY = os.getenv("SUPABASE_ANON_KEY")

if not SUPABASE_URL:
    print("❌ ERROR: SUPABASE_URL not found in .env file!")
    exit(1)

print(f"📡 Using Supabase: {SUPABASE_URL}")
PRIMARY_KEY = SERVICE_KEY if SERVICE_KEY else ANON_KEY

def get_headers(key=None):
    use_key = key or PRIMARY_KEY
    return {
        "apikey": use_key,
        "Authorization": f"Bearer {use_key}",
        "Content-Type": "application/json"
    }

def log(message, type="info"):
    print(f"[{type.upper()}] {message}")

async def fire_push_notifications(state):
    log("Checking for subscribers to notify...")
    try:
        async with httpx.AsyncClient() as client:
            # Query active premium subscribers
            resp = await client.get(
                f"{SUPABASE_URL}/rest/v1/pc_monitor_subscribers?is_active=eq.true&select=users!inner(subscription_status,subscription_end,push_tokens)",
                headers=get_headers()
            )
            
            if resp.status_code != 200:
                log(f"Push Query Failed: {resp.status_code}", "error")
                return
                
            data = resp.json()
            valid_tokens = []
            
            for row in data:
                user = row.get("users")
                if not user: continue
                
                # Check status and expiry
                is_active = user.get("subscription_status") == "active"
                sub_end = user.get("subscription_end")
                user_tokens = user.get("push_tokens") or [] 
                
                is_expired = False
                if sub_end:
                    try:
                        end_dt = datetime.fromisoformat(sub_end.replace('Z', '+00:00'))
                        if end_dt < datetime.now(timezone.utc):
                            is_expired = True
                    except: pass
                
                if is_active and not is_expired and isinstance(user_tokens, list):
                    for token in user_tokens:
                        if token and token.startswith("ExponentPushToken"):
                            valid_tokens.append(token)
            
            if not valid_tokens:
                log("No active premium subscribers found. Is your account active in the app?", "warning")
                return
                
            log(f"Found {len(valid_tokens)} tokens. Sending notifications via Expo...")
            
            push_payload = [{
                "to": t,
                "title": "🚨 Pokémon Center Monitor",
                "body": "The Queue is LIVE! • Join the line now! 🏃‍♂️💨",
                "data": {"type": "pc_monitor", "state": state},
                "sound": "default",
                "priority": "high",
                "badge": 1
            } for t in valid_tokens]
            
            await client.post(
                "https://exp.host/--/api/v2/push/send",
                headers={"Accept": "application/json", "Content-Type": "application/json"},
                json=push_payload
            )
            log(f"✅ Success! {len(push_payload)} notifications sent.", "success")
    except Exception as e:
        log(f"Push Error: {e}", "error")

async def update_supabase_state(state):
    log(f"Updating Supabase state to: {state}...")
    try:
        async with httpx.AsyncClient() as client:
            payload = {
                "state": state,
                "confidence_score": 1.0,
                "last_checked": datetime.now(timezone.utc).isoformat(),
                "queue_details": {"test_run": True},
                "monitor_healthy": True,
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "detected_at": datetime.now(timezone.utc).isoformat() if state == "QUEUE_ACTIVE" else None
            }
            
            endpoint = f"{SUPABASE_URL}/rest/v1/pc_monitor_state?id=neq.00000000-0000-0000-0000-000000000000"
            response = await client.patch(endpoint, headers=get_headers(), json=payload)
            
            if response.status_code in [200, 204]:
                log(f"✅ Supabase updated to {state}.")
                if state == "QUEUE_ACTIVE":
                    await fire_push_notifications(state)
            else:
                log(f"❌ DB Update Failed: {response.status_code} {response.text}", "error")
    except Exception as e:
        log(f"DB Error: {e}", "error")

async def main():
    print("\n--- POKÉMON CENTER MONITOR: PIPELINE TEST ---")
    print("This script will simulate a LIVE QUEUE detection.")
    print("1. It will update Supabase.")
    print("2. It will trigger push notifications to all active premium users.")
    print("---------------------------------------------\n")
    
    confirm = input("Are you ready to trigger the alert? (y/n): ")
    if confirm.lower() == 'y':
        await update_supabase_state("QUEUE_ACTIVE")
        
        print("\nNow waiting 10 seconds... Check your phone and your app!")
        await asyncio.sleep(10)
        
        reset = input("\nDo you want to reset the state back to NORMAL? (y/n): ")
        if reset.lower() == 'y':
            await update_supabase_state("NORMAL")
    else:
        print("Test cancelled.")

if __name__ == "__main__":
    asyncio.run(main())
