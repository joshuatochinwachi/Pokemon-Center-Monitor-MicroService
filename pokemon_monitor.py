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
POLL_INTERVAL_MIN = 25
POLL_INTERVAL_MAX = 45

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
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{SUPABASE_URL}/rest/v1/pc_monitor_subscribers?is_active=eq.true&select=fcm_token",
                headers=get_headers()
            )
            data = resp.json() if resp.status_code == 200 else []
            tokens = [r['fcm_token'] for r in data if 'fcm_token' in r]
            if not tokens: return
            push_payload = [{
                "to": t,
                "title": "🚨 POKEMON CENTER QUEUE LIVE!",
                "body": "The waiting room is active. Join the line now!",
                "data": {"type": "pc_monitor", "state": state},
                "sound": "default", "priority": "high"
            } for t in tokens if t.startswith("ExponentPushToken")]
            if push_payload:
                await client.post("https://exp.host/--/api/v2/push/send", json=push_payload)
                log_to_dashboard(f"Alerts fired to {len(push_payload)} users", "success")
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
        "dom_heuristics": False, "cookie_fingerprint": False,
        "url_redirect": False, "text_keywords": False
    }
    if "queue-it.net" in page.url or "waitingroom" in page.url:
        signals["url_redirect"] = True
    content = await page.content()
    if 'queue-it.js' in content or 'queueit' in content.lower() or 'Challenge_Banner' in content:
        signals["dom_heuristics"] = True
    queue_keywords = ["hi, trainer!", "virtual queue", "now in line", "approximate wait", "estimated wait", "queue is full", "do not refresh", "stay in the queue", "lose your place", "high volume of requests", "redirected to the site", "guarantee product availability", "sell out", "become unavailable", "waiting in line", "trainer", "line is paused", "queue-it", "queueit", "waiting room"]
    if any(word in content.lower() for word in queue_keywords):
        signals["text_keywords"] = True
    import re
    if re.search(r'\d{1,2}:\d{2}:\d{2}', content):
        signals["text_keywords"] = True
    cookies = await page.context.cookies()
    if any("QueueIT" in c['name'] for c in cookies):
        signals["cookie_fingerprint"] = True
    fired = [k for k, v in signals.items() if v]
    confidence = len(fired) / 5.0
    is_active = len(fired) >= 2
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
            
    block_keywords = [
        "access is temporarily restricted",
        "unusual activity from your device",
        "error 15",
        "incident id",
        "powered by imperva",
        "verifying the device",
        "verification required",
        "slide right to secure your access",
        "we detected unusual activity"
    ]
    if any(word in content.lower() for word in block_keywords):
        return True
    return False

async def monitor_loop():
    global current_proxy_index
    log_to_dashboard("Elite Monitor Engine Starting...", "info")
    async with async_playwright() as p:
        while True:
            launch_args = {"headless": True}
            
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
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
                viewport={'width': 1280, 'height': 720},
                extra_http_headers={
                    "Accept-Language": "en-US,en;q=0.9",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
                    "Sec-Ch-Ua": '"Chromium";v="122", "Not(A:Brand";v="24", "Google Chrome";v="122"',
                    "Sec-Ch-Ua-Mobile": "?0", "Sec-Ch-Ua-Platform": '"Windows"'
                }
            )
            
            while True:
                try:
                    page = await context.new_page()
                    await stealth_async(page)
                    network_signals = {'queue_it_detected': False}
                    page.on("request", lambda r: network_signals.update({'queue_it_detected': True}) if "queue-it.net" in r.url else None)
                    
                    log_to_dashboard("Checking Pokémon Center...")
                    await page.goto(PC_URL, wait_until="domcontentloaded", timeout=60000)
                    await asyncio.sleep(3)
                    
                    # Take Screenshot for Dashboard
                    screenshot = await page.screenshot(type='jpeg', quality=50)
                    base64_screenshot = base64.b64encode(screenshot).decode('utf-8')
                    socketio.emit('screenshot', base64_screenshot)
                    
                    is_blocked = await detect_block(page)
                    
                    if is_blocked:
                        log_to_dashboard("⚠️ IP BLOCKED BY IMPERVA (Access Restricted)", "error")
                        log_to_dashboard("🔄 Rotating to next proxy in pool...", "info")
                        if proxy_pool:
                            current_proxy_index = (current_proxy_index + 1) % len(proxy_pool)
                        await page.close()
                        await browser.close()
                        break # Break inner loop, launch new browser with new proxy
                    else:
                        is_queue, confidence, signals = await detect_queue(page, network_signals)
                        new_state = "QUEUE_ACTIVE" if is_queue else "NORMAL"
                        await update_supabase_state(new_state, confidence, signals)
                        monitor_stats["state"] = new_state
                        log_to_dashboard(f"Check Complete. Result: {new_state} ({confidence*100}%)", "success" if not is_queue else "error")
                    
                    monitor_stats["checks"] += 1
                    socketio.emit('stats_update', monitor_stats)
                    
                    await page.close()
                    
                except Exception as e:
                    log_to_dashboard(f"Monitor Loop Error: {e}", "error")
                    if proxy_pool:
                        current_proxy_index = (current_proxy_index + 1) % len(proxy_pool)
                    await browser.close()
                    break # Break inner loop on crash to reset browser

                sleep_time = random.uniform(POLL_INTERVAL_MIN, POLL_INTERVAL_MAX)
                await asyncio.sleep(sleep_time)

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
