import asyncio
import time
import random
from datetime import datetime, timezone
import httpx
from playwright.async_api import async_playwright
from playwright_stealth import stealth_async
import os
import base64
import threading
from flask import Flask, render_template_string
from flask_socketio import SocketIO
from dotenv import load_dotenv

load_dotenv()

# --- CONFIG ---
PC_URL = "https://www.pokemoncenter.com"
POLL_INTERVAL_MIN = 1800
POLL_INTERVAL_MAX = 3600

# --- SMART KEY SELECTOR ---
SUPABASE_URL = os.getenv("SUPABASE_URL")
SERVICE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
ANON_KEY = os.getenv("SUPABASE_ANON_KEY")
PRIMARY_KEY = SERVICE_KEY if SERVICE_KEY else ANON_KEY

# --- PROXY POOL SETUP ---
PROXY_LIST_RAW = os.getenv("PROXY_LIST", "")
proxy_pool = []
if PROXY_LIST_RAW:
    for p_str in PROXY_LIST_RAW.split(","):
        parts = p_str.strip().split(":")
        if len(parts) == 4:
            proxy_pool.append({
                "server": f"http://{parts[0]}:{parts[1]}",
                "username": parts[2],
                "password": parts[3]
            })
current_proxy_index = 0

# --- FLASK DASHBOARD SETUP ---
app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>Elite PC Monitor - Live View</title>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.0.1/socket.io.js"></script>
    <style>
        body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background: #0A0A0B; color: #E4E4E7; margin: 0; padding: 20px; }
        .header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 20px; border-bottom: 1px solid #27272A; padding-bottom: 10px; }
        .status-badge { padding: 6px 12px; borderRadius: 20px; font-size: 12px; font-weight: bold; text-transform: uppercase; }
        .status-active { background: rgba(34, 197, 94, 0.2); color: #4ADE80; border: 1px solid #22C55E; }
        .container { display: grid; grid-template-columns: 1fr 350px; gap: 20px; }
        .view-panel { background: #18181B; border-radius: 12px; padding: 15px; border: 1px solid #27272A; position: relative; }
        .log-panel { background: #18181B; border-radius: 12px; padding: 15px; border: 1px solid #27272A; height: 600px; display: flex; flex-direction: column; }
        #live-stream { width: 100%; border-radius: 8px; border: 1px solid #3F3F46; background: #000; }
        .logs { flex-grow: 1; overflow-y: auto; font-family: 'Courier New', Courier, monospace; font-size: 12px; color: #A1A1AA; margin-top: 10px; }
        .log-entry { margin-bottom: 4px; border-bottom: 1px solid #27272A; padding-bottom: 2px; }
        .log-time { color: #71717A; margin-right: 8px; }
        .log-msg-info { color: #3B82F6; }
        .log-msg-success { color: #22C55E; }
        .log-msg-error { color: #EF4444; }
        .stats { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-top: 15px; }
        .stat-card { background: #27272A; padding: 10px; border-radius: 8px; text-align: center; }
        .stat-val { display: block; font-size: 18px; font-weight: bold; color: #FFF; }
        .stat-lbl { font-size: 10px; color: #A1A1AA; text-transform: uppercase; }
    </style>
</head>
<body>
    <div class="header">
        <div style="display: flex; align-items: center; gap: 12px;">
            <div style="width: 12px; height: 12px; background: #4ADE80; border-radius: 50%; box-shadow: 0 0 10px #22C55E;"></div>
            <h2 style="margin: 0;">Pokémon Center Elite Monitor</h2>
        </div>
        <div id="state-badge" class="status-badge status-active">Monitoring Active</div>
    </div>

    <div class="container">
        <div class="view-panel">
            <h4 style="margin: 0 0 10px 0; color: #A1A1AA;">🎭 LIVE STEALTH VIEW</h4>
            <img id="live-stream" src="" alt="Waiting for browser check..." />
            <div class="stats">
                <div class="stat-card"><span id="stat-checks" class="stat-val">0</span><span class="stat-lbl">Checks</span></div>
                <div class="stat-card"><span id="stat-state" class="stat-val">NORMAL</span><span class="stat-lbl">Current State</span></div>
            </div>
        </div>
        <div class="log-panel">
            <h4 style="margin: 0; color: #A1A1AA;">📜 SYSTEM LOGS</h4>
            <div id="logs" class="logs"></div>
        </div>
    </div>

    <script>
        const socket = io();
        const img = document.getElementById('live-stream');
        const logs = document.getElementById('logs');
        
        socket.on('screenshot', data => {
            img.src = 'data:image/jpeg;base64,' + data;
        });

        socket.on('log', data => {
            const div = document.createElement('div');
            div.className = 'log-entry';
            const typeClass = data.type === 'success' ? 'log-msg-success' : (data.type === 'error' ? 'log-msg-error' : 'log-msg-info');
            div.innerHTML = `<span class="log-time">[${new Date().toLocaleTimeString()}]</span><span class="${typeClass}">${data.message}</span>`;
            logs.insertBefore(div, logs.firstChild);
            if (logs.children.length > 50) logs.removeChild(logs.lastChild);
        });

        socket.on('stats_update', data => {
            document.getElementById('stat-checks').textContent = data.checks;
            document.getElementById('stat-state').textContent = data.state;
            const badge = document.getElementById('state-badge');
            if (data.state === 'QUEUE_ACTIVE') {
                badge.textContent = '🚨 QUEUE DETECTED';
                badge.style.background = 'rgba(239, 68, 68, 0.2)';
                badge.style.color = '#F87171';
                badge.style.borderColor = '#EF4444';
            } else {
                badge.textContent = 'Monitoring Active';
                badge.style.background = 'rgba(34, 197, 94, 0.2)';
                badge.style.color = '#4ADE80';
                badge.style.borderColor = '#22C55E';
            }
        });
    </script>
</body>
</html>
"""

# --- GLOBAL TRACKERS ---
monitor_stats = {"checks": 0, "state": "NORMAL"}

def log_to_dashboard(message, type="info"):
    print(f"[{type.upper()}] {message}")
    socketio.emit('log', {'message': message, 'type': type})

@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE)

# --- MONITOR CORE ---
def get_headers(key=None):
    use_key = key or PRIMARY_KEY
    return {
        "apikey": use_key,
        "Authorization": f"Bearer {use_key}",
        "Content-Type": "application/json"
    }

async def fire_push_notifications(state):
    try:
        now_iso = datetime.now(timezone.utc).isoformat()
        async with httpx.AsyncClient() as client:
            # VETERAN FIX: Pull push_tokens directly from users table via relational inner join
            # This ensures we use the real tokens saved during app login/signup
            resp = await client.get(
                f"{SUPABASE_URL}/rest/v1/pc_monitor_subscribers?is_active=eq.true&select=users!inner(subscription_status,subscription_end,push_tokens)",
                headers=get_headers()
            )
            
            if resp.status_code != 200:
                log_to_dashboard(f"Push Query Failed: {resp.status_code}", "error")
                return
                
            data = resp.json()
            valid_tokens = []
            
            for row in data:
                user = row.get("users")
                if not user: continue
                
                # Verify Premium Gating (Status & Expiry)
                is_active = user.get("subscription_status") == "active"
                sub_end = user.get("subscription_end")
                user_tokens = user.get("push_tokens") or [] 
                
                is_expired = False
                if sub_end:
                    try:
                        end_dt = datetime.fromisoformat(sub_end.replace('Z', '+00:00'))
                        if end_dt < datetime.now(timezone.utc):
                            is_expired = True
                    except:
                        pass
                
                if is_active and not is_expired and isinstance(user_tokens, list):
                    for token in user_tokens:
                        if token and token.startswith("ExponentPushToken"):
                            valid_tokens.append(token)
            
            if not valid_tokens:
                log_to_dashboard("No active premium subscribers found with valid tokens.", "info")
                return
                
            push_payload = [{
                "to": t,
                "title": "🚨 Pokémon Center Monitor",
                "body": "The Queue is LIVE! • Join the line now! 🏃‍♂️💨",
                "data": {
                    "type": "pc_monitor", 
                    "state": state
                },
                "sound": "default",
                "priority": "high",
                "badge": 1,
                "channelId": "default",
                "ttl": 2419200
            } for t in valid_tokens]
            
            await client.post(
                "https://exp.host/--/api/v2/push/send",
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                },
                json=push_payload
            )
            log_to_dashboard(f"Alerts fired to {len(push_payload)} premium users", "success")
    except Exception as e:
        log_to_dashboard(f"Push Error: {e}", "error")

async def update_supabase_state(state, confidence=1.0, details=None):
    try:
        async with httpx.AsyncClient() as client:
            payload = {
                "state": state, "confidence_score": confidence,
                "last_checked": datetime.now(timezone.utc).isoformat(),
                "queue_details": details or {}, "monitor_healthy": True,
                "updated_at": datetime.now(timezone.utc).isoformat()
            }
            if state == "QUEUE_ACTIVE":
                payload["detected_at"] = datetime.now(timezone.utc).isoformat()
            endpoint = f"{SUPABASE_URL}/rest/v1/pc_monitor_state?id=neq.00000000-0000-0000-0000-000000000000"
            response = await client.patch(endpoint, headers=get_headers(), json=payload)
            if response.status_code not in [200, 204] and ANON_KEY and PRIMARY_KEY != ANON_KEY:
                log_to_dashboard("Primary Key Failed. Trying Fail-Safe...", "error")
                response = await client.patch(endpoint, headers=get_headers(ANON_KEY), json=payload)
            
            if state == "QUEUE_ACTIVE" and response.status_code in [200, 204]:
                await fire_push_notifications(state)
            
            monitor_stats["state"] = state
            socketio.emit('stats_update', monitor_stats)
    except Exception as e:
        log_to_dashboard(f"DB Error: {e}", "error")

async def detect_queue(page, network_signals):
    signals = {
        "network_traffic": network_signals['queue_it_detected'],
        "dom_heuristics": False, 
        "cookie_fingerprint": False,
        "url_redirect": False, 
        "text_keywords": False,
        "timer_detected": False
    }
    
    if "queue-it.net" in page.url or "waitingroom" in page.url:
        signals["url_redirect"] = True
        
    content = await page.content()
    content_lower = content.lower()
    
    if 'queue-it.js' in content or 'queueit' in content_lower or 'Challenge_Banner' in content:
        signals["dom_heuristics"] = True
        
    # Strictly high-intent queue phrases only
    queue_keywords = [
        "hi, trainer!", "virtual queue", "now in line", "approximate wait", 
        "estimated wait time", "queue is full", "do not refresh", "stay in the queue", 
        "lose your place", "high volume of requests", "redirected to the site", 
        "guarantee product availability", "sell out", "become unavailable", 
        "waiting in line", "line is paused", "queue-it", "waiting room"
    ]
    
    if any(word in content_lower for word in queue_keywords):
        signals["text_keywords"] = True
        
    # Dedicated Hyper-Intelligent Timer Sensor
    # Pattern 1: Standard Digital (02:30:00 or 15:45)
    # Pattern 2: Text-based (5 minutes, 1 hour, 30 secs)
    # Pattern 3: Hybrid (00h 05m 10s)
    import re
    time_patterns = [
        r'\b\d{1,2}:\d{2}(:\d{2})?\b', 
        r'\d+\s*(?:hr|min|sec|hour|minute|second)s?\b',
        r'\d+h\s*\d+m\s*\d+s'
    ]
    
    if any(re.search(p, content_lower) for p in time_patterns):
        signals["timer_detected"] = True
        
    cookies = await page.context.cookies()
    if any("QueueIT" in c['name'] for c in cookies):
        signals["cookie_fingerprint"] = True
        
    # Elite Weighted Scoring Engine (6-Sensor Fusion)
    weights = {
        "network_traffic": 100,
        "url_redirect": 100,
        "dom_heuristics": 80,
        "cookie_fingerprint": 60,
        "text_keywords": 40,
        "timer_detected": 40
    }
    
    # CRITICAL OVERRIDE: Network or URL redirect is a 100% lock
    if signals["network_traffic"] or signals["url_redirect"]:
        confidence = 1.0
        is_active = True
        log_to_dashboard("🎯 CRITICAL SENSOR FIRE: Instant 100% Confidence.", "success")
    else:
        # COMBINED SUPPORT HEURISTICS
        total_possible = sum(weights.values())
        current_score = sum(weights[k] for k, v in signals.items() if v)
        confidence = current_score / total_possible
        # THRESHOLD: Require >= 50% (at least 3 sensors) for support triggers
        is_active = confidence >= 0.50
        
    return is_active, confidence, signals

async def detect_block(page):
    content = await page.content()
    
    # Imperva often puts the CAPTCHA inside an iframe. We must scan all frames.
    for frame in page.frames:
        try:
            frame_content = await frame.content()
            content += " " + frame_content
        except:
            pass
            
    content_lower = content.lower()
            
    block_keywords = [
        "access is temporarily restricted",
        "unusual activity from your device",
        "error 15",
        "incident id",
        "powered by imperva",
        "verifying the device",
        "verification required",
        "slide right to secure your access",
        "we detected unusual activity",
        "additional security check is required",
        "i am human"
    ]
    
    if any(word in content_lower for word in block_keywords):
        return True
        
    # Failsafe: If the page is a blank white screen or a completely unknown error
    # it won't even mention "pokemon" or the "queue". Treat as blocked/failed.
    if "pokemon" not in content_lower and "queue" not in content_lower:
        return True
        
    return False

async def smart_delay(base_min, base_max, variance=0.3):
    """Intelligent delay with Gaussian distribution for more natural randomness"""
    mid = (base_min + base_max) / 2
    sigma = (base_max - base_min) * variance
    delay = random.gauss(mid, sigma)
    delay = max(base_min, min(base_max, delay))
    await asyncio.sleep(delay)

async def advanced_mouse_movement(page):
    """Ultra-realistic mouse movement with curves and acceleration"""
    try:
        viewport = page.viewport_size
        if not viewport:
            return
            
        start_x = random.randint(50, viewport['width'] - 50)
        start_y = random.randint(50, viewport['height'] - 50)
        num_moves = random.randint(2, 4)
        
        for _ in range(num_moves):
            target_x = random.randint(100, viewport['width'] - 100)
            target_y = random.randint(100, viewport['height'] - 100)
            
            distance = ((target_x - start_x)**2 + (target_y - start_y)**2)**0.5
            steps = max(5, int(distance / 50))
            
            for i in range(steps):
                t = i / steps
                noise_x = random.uniform(-10, 10)
                noise_y = random.uniform(-10, 10)
                
                current_x = start_x + (target_x - start_x) * t + noise_x
                current_y = start_y + (target_y - start_y) * t + noise_y
                
                await page.mouse.move(current_x, current_y)
                speed_factor = 1 - abs(t - 0.5) * 0.5
                if speed_factor <= 0: speed_factor = 0.1
                await asyncio.sleep(0.01 / speed_factor)
            
            start_x, start_y = target_x, target_y
            await smart_delay(0.2, 0.5)
    except Exception:
        pass

async def realistic_scroll_behavior(page):
    """Advanced scrolling with momentum and natural deceleration"""
    try:
        num_scrolls = random.randint(2, 4)
        for _ in range(num_scrolls):
            scroll_down = random.random() < 0.7
            if scroll_down:
                base_scroll = random.randint(150, 500)
            else:
                base_scroll = -random.randint(100, 300)
            
            momentum_steps = random.randint(3, 7)
            for step in range(momentum_steps):
                step_scroll = base_scroll * (1 - step / momentum_steps) / momentum_steps
                await page.mouse.wheel(0, step_scroll)
                await asyncio.sleep(random.uniform(0.02, 0.06))
            
            await smart_delay(0.4, 1.0)
            
            if random.random() < 0.25:
                await page.mouse.wheel(0, random.randint(-100, -50))
                await smart_delay(0.3, 0.7)
    except Exception:
        pass

def get_realistic_user_agent():
    """Return a consistent set of matching user-agent and platform headers"""
    profiles = [
        {
            "ua": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "ch_ua": '"Chromium";v="124", "Not(A:Brand";v="24", "Google Chrome";v="124"',
            "platform": '"Windows"'
        },
        {
            "ua": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "ch_ua": '"Chromium";v="124", "Not(A:Brand";v="24", "Google Chrome";v="124"',
            "platform": '"macOS"'
        }
    ]
    return random.choice(profiles)

async def simulate_human_behavior(page):
    """Orchestrates elite human behavior."""
    try:
        await smart_delay(0.5, 1.5)
        await advanced_mouse_movement(page)
        await realistic_scroll_behavior(page)
        if random.random() > 0.5:
            await advanced_mouse_movement(page)
    except Exception:
        pass

async def monitor_loop():
    global current_proxy_index
    log_to_dashboard("Elite Monitor Engine Starting...", "info")
    async with async_playwright() as p:
        while True:
            launch_args = {
                "headless": True,
                "args": [
                    "--disable-blink-features=AutomationControlled",
                    "--disable-infobars",
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--ignore-certificate-errors",
                ]
            }
            
            if proxy_pool:
                current_proxy = proxy_pool[current_proxy_index]
                launch_args["proxy"] = current_proxy
                ip_display = current_proxy['server'].split('//')[1].split(':')[0]
                log_to_dashboard(f"🛡️ Using Proxy Pool IP: {ip_display}", "info")
            elif os.getenv("PROXY_SERVER"):
                launch_args["proxy"] = {
                    "server": os.getenv("PROXY_SERVER"),
                    "username": os.getenv("PROXY_USERNAME", ""),
                    "password": os.getenv("PROXY_PASSWORD", "")
                }
                log_to_dashboard("🛡️ Single Proxy Configured.", "info")
                
            browser = await p.chromium.launch(**launch_args)
            
            profile = get_realistic_user_agent()
            context = await browser.new_context(
                user_agent=profile["ua"],
                viewport={'width': random.randint(1280, 1920), 'height': random.randint(800, 1080)},
                extra_http_headers={
                    "Accept-Language": "en-US,en;q=0.9",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
                    "Sec-Ch-Ua": profile["ch_ua"],
                    "Sec-Ch-Ua-Mobile": "?0", 
                    "Sec-Ch-Ua-Platform": profile["platform"],
                    "Upgrade-Insecure-Requests": "1"
                }
            )
            
            while True:
                try:
                    page = await context.new_page()
                    await stealth_async(page)
                    network_signals = {'queue_it_detected': False}
                    page.on("request", lambda r: network_signals.update({'queue_it_detected': True}) if "queue-it.net" in r.url else None)

                    # ⚡ BANDWIDTH SAVER: Block heavy resources (saves up to 80% bandwidth)
                    # CRITICAL: Always allow queue-it.net through for accurate detection 
                    BLOCKED_DOMAINS = [
                        "google-analytics.com", "googletagmanager.com", "doubleclick.net",
                        "facebook.com", "hotjar.com", "newrelic.com", "quantserve.com",
                        "scorecardresearch.com", "amazon-adsystem.com", "adnxs.com",
                        "criteo.com", "segment.com", "mixpanel.com", "amplitude.com"
                    ]
                    async def block_heavy_resources(route, request):
                        url = request.url.lower()
                        # ALWAYS let queue-it.net through — needed for detection
                        if "queue-it.net" in url:
                            await route.continue_()
                            return
                        # Block heavy resource types
                        if request.resource_type in ["image", "stylesheet", "font", "media"]:
                            await route.abort()
                            return
                        # Block analytics & ad trackers
                        if any(domain in url for domain in BLOCKED_DOMAINS):
                            await route.abort()
                            return
                        await route.continue_()
                    await page.route("**/*", block_heavy_resources)

                    log_to_dashboard("⚡ Bandwidth Saver Active. Checking Pokémon Center...")
                    await page.goto(PC_URL, wait_until="domcontentloaded", timeout=60000)
                    
                    # Stealth Patience: Allow Imperva JS challenges to resolve
                    log_to_dashboard("⏳ Solving Stealth Challenges...", "info")
                    await asyncio.sleep(random.uniform(3.0, 5.0))
                    
                    # Simulate human behavior to defeat behavioral tracking
                    await simulate_human_behavior(page)
                    
                    # Smart-Eye: Wait for actual content before screenshotting (Logo or Header)
                    try:
                        await page.wait_for_selector("header, .main-content, #main-content", timeout=10000)
                    except:
                        pass # Site might be in a different state or blocked, capture anyway
                    
                    # Take Screenshot for Dashboard
                    screenshot = await page.screenshot(type='jpeg', quality=50)
                    base64_screenshot = base64.b64encode(screenshot).decode('utf-8')
                    socketio.emit('screenshot', base64_screenshot)
                    
                    is_blocked = await detect_block(page)
                    
                    if is_blocked:
                        log_to_dashboard("⚠️ IP BLOCKED BY IMPERVA (Access Restricted)", "error")
                        log_to_dashboard("⏳ Applying 15s penalty cooldown for Webshare proxy to rotate properly...", "info")
                        await asyncio.sleep(15)  # Penalty delay to prevent spamming and allow Webshare rotation
                        log_to_dashboard("🔄 Rotating to next proxy in pool...", "info")
                        if proxy_pool:
                            current_proxy_index = (current_proxy_index + 1) % len(proxy_pool)
                        await page.close()
                        await browser.close()
                        break # Break inner loop, launch new browser with new proxy
                    else:
                        is_queue, confidence, signals = await detect_queue(page, network_signals)
                        
                        # --- DOUBLE-TAP VERIFICATION (NO FALSE ALARMS) ---
                        if is_queue and confidence < 1.0:
                            log_to_dashboard("🔍 Heuristic Trigger. Double-checking in 5s...", "info")
                            await asyncio.sleep(5)
                            is_queue, confidence, signals = await detect_queue(page, network_signals)
                            if not is_queue:
                                log_to_dashboard("🛡️ False Alarm Prevented by Double-Tap Verification.", "success")
                        
                        # --- PERSISTENCE CHECK (NEVER MISS A QUEUE) ---
                        # If we see even a tiny hint (confidence > 0) but it didn't trigger 'is_queue',
                        # we do one more quick check to ensure the page wasn't just slow-loading.
                        if not is_queue and confidence > 0:
                            log_to_dashboard("📡 Minor signal detected. Performing Deep-Scan retry...", "info")
                            await asyncio.sleep(3)
                            is_queue, confidence, signals = await detect_queue(page, network_signals)
                        
                        new_state = "QUEUE_ACTIVE" if is_queue else "NORMAL"
                        await update_supabase_state(new_state, confidence, signals)
                        monitor_stats["state"] = new_state
                        log_to_dashboard(f"Check Complete. Result: {new_state} ({int(confidence*100)}%)", "success" if not is_queue else "error")
                    
                        monitor_stats["checks"] += 1
                        socketio.emit('stats_update', monitor_stats)
                        
                        await page.close()
                        
                        # Sleep before next check
                        sleep_time = random.uniform(POLL_INTERVAL_MIN, POLL_INTERVAL_MAX)
                        log_to_dashboard(f"💤 Sleeping for {int(sleep_time/60)} minutes before next check...", "info")
                        await asyncio.sleep(sleep_time)
                        
                        # Force complete session isolation: destroy browser and rotate country gateway
                        if proxy_pool:
                            current_proxy_index = (current_proxy_index + 1) % len(proxy_pool)
                        await browser.close()
                        break # Break inner loop, launch entirely new browser for next check
                    
                except Exception as e:
                    log_to_dashboard(f"Monitor Loop Error: {e}", "error")
                    if proxy_pool:
                        current_proxy_index = (current_proxy_index + 1) % len(proxy_pool)
                    await browser.close()
                    break # Break inner loop on crash to reset browser

def run_monitor():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(monitor_loop())

if __name__ == "__main__":
    # Start monitor in a background thread
    monitor_thread = threading.Thread(target=run_monitor, daemon=True)
    monitor_thread.start()
    
    # Start Flask dashboard on main thread
    port = int(os.environ.get("PORT", 5000))
    print(f"🚀 Dashboard live at http://localhost:{port}")
    socketio.run(app, host='0.0.0.0', port=port, allow_unsafe_werkzeug=True)
