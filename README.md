# HollowScan: Pokémon Center Elite Monitor 🛡️🔥
## End-to-End System Architecture & Specification

This document provides a comprehensive technical overview of the **HollowScan Pokémon Center Monitor**, a high-performance, stealth-focused microservice designed to bypass enterprise-grade WAFs (Imperva) and provide real-time intelligence to premium users.

---

## 1. High-Level Architecture

The system is composed of four primary layers working in perfect synchronization:

```mermaid
graph TD
    subgraph "External Web"
        PC["Pokémon Center Website"]
        EXPO["Expo Push API"]
    end

    subgraph "PC Monitor Microservice (Ghost-Mode)"
        MONITOR["Monitor Loop (Playwright)"]
        DETECTION["6-Sensor Fusion Engine"]
        PUSH["Premium Gating Push Engine"]
    end

    subgraph "Database (Supabase)"
        DB_STATE[("pc_monitor_state")]
        DB_SUB[("pc_monitor_subscribers")]
        DB_USER[("users (Tokens & Premium Status)")]
    end

    subgraph "FastAPI Backend"
        API["Gated Status API"]
        AUTH["Subscription Verification"]
    end

    subgraph "Mobile App"
        APP_UI["PCMonitorHub.js (UI States)"]
    end

    PC <-->|Stealth Check| MONITOR
    MONITOR --> DETECTION
    DETECTION -->|Update State| DB_STATE
    DETECTION -->|Trigger| PUSH
    PUSH -->|Relational Join| DB_SUB
    PUSH -->|Verify Status| DB_USER
    PUSH -->|Alert| EXPO
    
    APP_UI <-->|Poll Status| API
    API <-->|Verify| AUTH
    API <-->|Fetch State| DB_STATE
    API <-->|Check Sub| DB_SUB
```

---

## 2. Working Mechanism: The "Ghost-Mode" Loop

Every weekday (Monday–Friday), the monitor operates on a high-intensity **"Power Hour" schedule**. It remains completely dormant during weekends and off-hours to maximize bandwidth conservation.

### Step A: Total Session Isolation & Bandwidth Optimization
1.  **Browser Destruction**: The previous browser instance is completely killed. No cache, no cookies, no local storage persists.
2.  **Proxy Jump**: A new gateway is selected from the 100-node Webshare pool.
3.  **Fingerprint Randomization**: A unique User-Agent and viewport are assigned.
4.  **⚡ Bandwidth Saver Mode**: To stay within a 1GB/month budget, the monitor **blocks all images and heavy CSS assets** during the scan. This reduces page weight by ~90%, allowing for thousands of checks per month.
5.  **🕒 Power Hour Scheduling**: The engine normally wakes up between **2:00 PM and 8:00 PM UTC** on weekdays. During this window, it performs scans every **45–60 minutes**.
6.  **🐕 Persistent Watchdog (Override)**: If a queue is detected, the monitor **ignores its bedtime**. It will stay awake 24/7 and increase scan frequency to **every 30 minutes** until the site returns to normal.
7.  **Weekend Pause**: The system automatically enters a deep-sleep state on Saturdays and Sundays (unless a queue was already active).

```mermaid
stateDiagram-v2
    [*] --> Idle: Weekend or Off-Hours
    Idle --> PowerHour: Weekday 14:00 UTC
    PowerHour --> Idle: Weekday 20:00 UTC
    
    PowerHour --> QueueWatch: Queue Detected!
    Idle --> QueueWatch: Queue Detected! (Override)
    
    QueueWatch --> QueueWatch: Scan every 30m
    QueueWatch --> Idle: Queue Over & Past 20:00 UTC
    QueueWatch --> PowerHour: Queue Over & Inside Window
```

### **Engine Modes (Log Key)**
When viewing the **Live Dashboard**, you will see these modes in the logs:
*   `ACTIVE_SCANNING`: Power Hour is active; checking every 45-60 mins.
*   `QUEUE_WATCH`: 🐕 Watchdog active; queue detected, checking every 30 mins 24/7.
*   `MORNING_WAIT`: Weekday morning; sleeping until 2:00 PM UTC.
*   `EVENING_PAUSE`: Power Hour ended; sleeping until 2:00 PM UTC tomorrow.
*   `WEEKEND_PAUSE`: Saturday/Sunday; sleeping until Monday 2:00 PM UTC.
*   `WEEKEND_WATCH`: Queue started on a weekend; staying awake to monitor.

### Step B: Human Behavioral Simulation
To bypass behavioral analysis, the monitor does NOT just load the page. It mimics a human:
*   **Bezier Curves**: Mouse movements follow organic, non-linear paths.
*   **Gaussian Delays**: Wait times between actions are calculated using a normal distribution (no robotic "exactly 2 seconds" waits).
*   **Momentum Scrolling**: The bot scrolls up and down randomly to simulate reading.

### Step C: The 6-Sensor Detection Engine
The system doesn't just look for "Queue". it uses a confidence-based fusion of 6 independent sensors:

| Sensor | Description | Weight/Confidence |
| :--- | :--- | :--- |
| **Network Traffic** | Detects background calls to `queue-it.net`. | 100% (Instant Live) |
| **URL Redirect** | Detects `waitingroom` or `queue-it` in the address bar. | 100% (Instant Live) |
| **DOM Heuristics** | Scans for hidden `queue-it.js` or `Challenge_Banner` IDs. | 80% |
| **Cookie Fingerprint** | Detects the `QueueIT` cookie dropped by the WAF. | 60% |
| **Text Keywords** | Intelligent, case-insensitive scan (e.g., "Hi, Trainer!", "Virtual Queue"). | 40% |
| **Regex Timer** | Detects digital or text countdowns (e.g., `00:05:00`, `10 mins`). | 40% |

---

## 3. The Premium Gating Logic

We ensure that **Zero Alerts** are leaked to non-premium or expired users through a real-time Relational Join.

```mermaid
sequenceDiagram
    participant M as Monitor Script
    participant S as Supabase (Relational)
    participant E as Expo Push Service
    participant U as User Device

    M->>S: 🔍 Fetch Subscribers WHERE is_active=true
    Note over S: Join with 'users' table on id
    S-->>M: Return { push_tokens[], subscription_status, subscription_end }
    
    loop For each Subscriber
        M->>M: Verify status == 'active'
        M->>M: Verify end_date > now()
        
        alt Valid Premium
            M->>E: Send Push to all tokens in array
            E->>U: 🚨 "QUEUE IS LIVE!"
        else Expired or Free
            M->>M: Skip User (Security Gating)
        end
    end
```

---

## 4. User Scenarios

### Scenario 1: The Free User
*   **Mobile App**: The `PCMonitorHub` sees the user is not premium. It displays a blurred "LOCKED" card with a padlock. 🔒
*   **API**: The FastAPI backend refuses to return any live data, returning `state: LOCKED`.
*   **Result**: The user is encouraged to upgrade but sees zero queue intelligence.

### Scenario 2: The Premium User (Not Subscribed)
*   **Mobile App**: Sees the monitor is "Normal".
*   **Call to Action**: The subtitle says: *"Tap 'Enable Alerts' to get notified instantly! 🔔"*.
*   **Result**: User has access but must opt-in to notifications.

### Scenario 3: The Premium User (Subscribed)
*   **Mobile App**: Subtitle confirms *"Monitoring 24/7 • Site Normal"*.
*   **Alert**: The moment a queue hits, they receive a push notification on all their devices.
*   **Join**: They click the "JOIN" button and are taken directly to the Pokémon Center waiting room.

### Scenario 4: The Expired User
*   **Context**: User was premium and subscribed, but their sub ended yesterday.
*   **Security Gating**: The Monitor Script detects their expiry in the real-time join. 
*   **Result**: They receive **zero notifications**. The App UI reverts to the "LOCKED" state automatically.

---

## 5. Deployment & Configuration

The monitor is designed to be deployed on **Railway.app** or any VPS with Docker support. It requires the following environment variables to be set:

### **Supabase Connectivity**
*   `SUPABASE_URL`: Your Supabase project URL.
*   `SUPABASE_SERVICE_ROLE_KEY`: The 'service_role' key for administrative database access.
*   `SUPABASE_ANON_KEY`: (Optional) Fallback key for state updates.

### **Proxy Configuration**
*   `PROXY_LIST`: A comma-separated list of Webshare-style proxies in `ip:port:user:pass` format.
*   *OR*
*   `PROXY_SERVER`, `PROXY_USERNAME`, `PROXY_PASSWORD`: For single proxy configurations.

### **Server Settings**
*   `PORT`: The port for the Live Dashboard (Defaults to 5000 on Railway).

## 6. System Health & Maintenance

The monitor is designed for "Set and Forget" operation:
*   **Auto-Healing (Imperva Resilience)**: If it detects an "IP Block," it applies a 15-second penalty cooldown and rotates to a new proxy. It will attempt this up to **50 times** per cycle to ensure a successful check even during high-traffic blocks.
*   **Persistent Dashboard**: Uses a server-side memory cache (Socket.io) to store the `last_screenshot` and `recent_logs`. Stakeholders opening the link see the latest data **instantly** without triggering a new, bandwidth-expensive scan.
*   **Confidence Threshold**: `is_active` is only triggered if **2 or more sensors** fire simultaneously, ensuring near-zero false alarms.
*   **State-Aware Sleep**: Automatically respects the 2 PM – 8 PM UTC window but stays awake past bedtime if a queue is detected.
