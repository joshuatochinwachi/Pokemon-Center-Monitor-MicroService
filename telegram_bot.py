import asyncio
import os
import logging
from datetime import datetime, timezone
from typing import List, Tuple, Dict, Any, Callable
import httpx
from dotenv import load_dotenv

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
from telegram.constants import ParseMode
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

load_dotenv()

# Logging setup
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger("PCMonitorBot")

# Environment Variables
SUPABASE_URL = os.getenv("SUPABASE_URL")
SERVICE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
ANON_KEY = os.getenv("SUPABASE_ANON_KEY")
PRIMARY_KEY = SERVICE_KEY if SERVICE_KEY else ANON_KEY

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAM_TOKEN")
ADMIN_USER_IDS = [
    uid.strip()
    for uid in (os.getenv("TELEGRAM_ADMIN_ID") or "").split(",")
    if uid.strip()
]

if not SUPABASE_URL:
    logger.error("❌ ERROR: SUPABASE_URL not found in environment variables!")

def get_headers(key: str = None) -> Dict[str, str]:
    use_key = key or PRIMARY_KEY
    return {
        "apikey": use_key,
        "Authorization": f"Bearer {use_key}",
        "Content-Type": "application/json",
    }

def is_admin(user_id: int) -> bool:
    if not ADMIN_USER_IDS:
        return True  # If no admin ID specified, allow all commands
    return str(user_id) in ADMIN_USER_IDS

# --- CORE SUPABASE & PUSH NOTIFICATION LOGIC ---

async def fetch_active_subscribers_and_tokens() -> Tuple[List[str], int, int]:
    """
    Fetch active premium subscribers from Supabase and extract valid ExponentPushTokens.
    Returns: (valid_tokens, active_subscriber_count, total_queried)
    """
    if not SUPABASE_URL:
        return [], 0, 0

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(
            f"{SUPABASE_URL}/rest/v1/pc_monitor_subscribers?is_active=eq.true&select=users!inner(subscription_status,subscription_end,push_tokens)",
            headers=get_headers(),
        )

        if resp.status_code != 200:
            logger.error(f"Push Query Failed: {resp.status_code} {resp.text}")
            return [], 0, 0

        data = resp.json()
        valid_tokens = []
        active_sub_count = 0

        for row in data:
            user = row.get("users")
            if not user:
                continue

            is_active = user.get("subscription_status") == "active"
            sub_end = user.get("subscription_end")
            user_tokens = user.get("push_tokens") or []

            is_expired = False
            if sub_end:
                try:
                    end_dt = datetime.fromisoformat(sub_end.replace("Z", "+00:00"))
                    if end_dt < datetime.now(timezone.utc):
                        is_expired = True
                except Exception:
                    pass

            if is_active and not is_expired:
                active_sub_count += 1
                if isinstance(user_tokens, list):
                    for token in user_tokens:
                        if token and str(token).startswith("ExponentPushToken"):
                            valid_tokens.append(str(token))

        return valid_tokens, active_sub_count, len(data)

async def fire_push_notifications(
    state: str, log_cb: Callable[[str], None] = None
) -> int:
    """
    Fires Expo push notifications to all active premium subscribers.
    """
    def log(msg: str):
        logger.info(msg)
        if log_cb:
            log_cb(msg)

    log("🔍 Searching for active premium subscribers...")
    valid_tokens, active_subs, total_rows = await fetch_active_subscribers_and_tokens()

    if not valid_tokens:
        log("⚠️ No valid push tokens found for active subscribers.")
        return 0

    log(f"📱 Found {len(valid_tokens)} valid tokens across {active_subs} active subscribers.")
    log("🚀 Sending push notifications via Expo API...")

    push_payload = [
        {
            "to": t,
            "title": "🚨 Pokémon Center Monitor",
            "body": "The Queue is LIVE! • Join the line now! 🏃‍♂️💨",
            "data": {"type": "pc_monitor", "state": state, "manual_trigger": True},
            "sound": "default",
            "priority": "high",
            "badge": 1,
        }
        for t in valid_tokens
    ]

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(
                "https://exp.host/--/api/v2/push/send",
                headers={"Accept": "application/json", "Content-Type": "application/json"},
                json=push_payload,
            )

            if resp.status_code == 200:
                log(f"✅ Success! Sent {len(push_payload)} Expo push notifications.")
                return len(push_payload)
            else:
                log(f"❌ Expo API Error: {resp.status_code} - {resp.text}")
                return 0
    except Exception as e:
        log(f"❌ Push Network Error: {e}")
        return 0

async def update_supabase_state(
    state: str, log_cb: Callable[[str], None] = None
) -> bool:
    """
    Updates pc_monitor_state in Supabase.
    If state == QUEUE_ACTIVE, triggers push notifications.
    """
    def log(msg: str):
        logger.info(msg)
        if log_cb:
            log_cb(msg)

    log(f"📡 Updating Supabase monitor state to: <b>{state}</b>...")
    now_iso = datetime.now(timezone.utc).isoformat()

    payload = {
        "state": state,
        "confidence_score": 1.0,
        "last_checked": now_iso,
        "queue_details": {"manual_override": True, "triggered_via": "TelegramBot"},
        "monitor_healthy": True,
        "updated_at": now_iso,
        "detected_at": now_iso if state == "QUEUE_ACTIVE" else None,
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            endpoint = f"{SUPABASE_URL}/rest/v1/pc_monitor_state?id=neq.00000000-0000-0000-0000-000000000000"
            resp = await client.patch(endpoint, headers=get_headers(), json=payload)

            if resp.status_code in [200, 204]:
                log(f"✅ Supabase successfully updated to <b>{state}</b>.")
                if state == "QUEUE_ACTIVE":
                    await fire_push_notifications(state, log_cb=log_cb)
                return True
            else:
                log(f"❌ DB Update Failed: HTTP {resp.status_code} - {resp.text}")
                return False
    except Exception as e:
        log(f"❌ Database Exception: {e}")
        return False

async def fetch_supabase_state() -> Dict[str, Any]:
    """
    Fetches the current pc_monitor_state record from Supabase.
    """
    if not SUPABASE_URL:
        return {}

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            endpoint = f"{SUPABASE_URL}/rest/v1/pc_monitor_state?select=*&limit=1"
            resp = await client.get(endpoint, headers=get_headers())

            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, list) and len(data) > 0:
                    return data[0]
    except Exception as e:
        logger.error(f"Error fetching Supabase state: {e}")
    return {}

# --- TELEGRAM BOT UI & KEYBOARDS ---

def get_control_keyboard() -> InlineKeyboardMarkup:
    keyboard = [
        [
            InlineKeyboardButton("🔴 SET QUEUE LIVE", callback_data="cb_set_live"),
            InlineKeyboardButton("🟢 SET SITE NORMAL", callback_data="cb_set_normal"),
        ],
        [
            InlineKeyboardButton("📊 CHECK STATUS", callback_data="cb_status"),
            InlineKeyboardButton("🔔 TEST PUSH ALERT", callback_data="cb_test_push"),
        ],
        [
            InlineKeyboardButton("👥 SUBSCRIBERS", callback_data="cb_subscribers"),
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

# --- COMMAND HANDLERS ---

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    welcome_text = (
        f"<b>⚡ HollowScan: Pokémon Center Manual Control Bot</b>\n\n"
        f"Welcome <b>{user.first_name}</b>!\n"
        f"This bot gives you direct manual control over the Pokémon Center Queue Monitor state and push notifications.\n\n"
        f"<b>Available Commands:</b>\n"
        f"• /live - 🔴 Set state to QUEUE_ACTIVE & trigger Expo push alerts\n"
        f"• /normal - 🟢 Reset state to NORMAL\n"
        f"• /status - 📊 Check current DB state & subscriber count\n"
        f"• /push - 🔔 Fire push alerts manually without changing state\n"
        f"• /subscribers - 👥 View active subscriber details\n"
        f"• /help - ℹ️ Show this help message\n\n"
        f"<i>Use the quick control buttons below:</i>"
    )
    await update.message.reply_text(
        welcome_text,
        parse_mode=ParseMode.HTML,
        reply_markup=get_control_keyboard()
    )

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await cmd_start(update, context)

async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("⏳ Fetching live monitor status...")
    await send_status_report(msg.edit_text)

async def send_status_report(edit_func: Callable):
    state_data = await fetch_supabase_state()
    valid_tokens, active_subs, total_rows = await fetch_active_subscribers_and_tokens()

    state = state_data.get("state", "UNKNOWN")
    healthy = state_data.get("monitor_healthy", False)
    confidence = state_data.get("confidence_score", 0.0)
    last_checked = state_data.get("last_checked", "N/A")
    updated_at = state_data.get("updated_at", "N/A")
    detected_at = state_data.get("detected_at") or "None"

    state_emoji = "🔴" if state == "QUEUE_ACTIVE" else ("🟢" if state == "NORMAL" else "⚠️")
    health_emoji = "✅ Healthy" if healthy else "❌ Unhealthy"

    report_text = (
        f"<b>📊 Pokémon Center Monitor Status</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"<b>Current State:</b> {state_emoji} <code>{state}</code>\n"
        f"<b>Monitor Health:</b> {health_emoji}\n"
        f"<b>Confidence Score:</b> <code>{confidence}</code>\n"
        f"<b>Last Checked:</b> <code>{last_checked}</code>\n"
        f"<b>Last Updated:</b> <code>{updated_at}</code>\n"
        f"<b>Detected At:</b> <code>{detected_at}</code>\n\n"
        f"<b>👥 Premium Subscribers:</b>\n"
        f"• Active Subscribers: <b>{active_subs}</b>\n"
        f"• Valid Push Tokens: <b>{len(valid_tokens)}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━"
    )

    await edit_func(
        report_text,
        parse_mode=ParseMode.HTML,
        reply_markup=get_control_keyboard()
    )

async def cmd_live(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("⛔ <b>Unauthorized:</b> You do not have permission to control the monitor.", parse_mode=ParseMode.HTML)
        return

    msg = await update.message.reply_text("⏳ <b>[INITIATING]</b> Triggering QUEUE_ACTIVE state...", parse_mode=ParseMode.HTML)
    logs = ["<b>🔴 EXECUTING /live COMMAND:</b>\n"]

    async def update_log(line: str):
        logs.append(line)
        try:
            await msg.edit_text("\n".join(logs), parse_mode=ParseMode.HTML)
        except Exception:
            pass

    success = await update_supabase_state("QUEUE_ACTIVE", log_cb=update_log)
    if success:
        logs.append("\n🎉 <b>COMPLETE: State set to QUEUE_ACTIVE & alerts sent!</b>")
    else:
        logs.append("\n❌ <b>FAILED: Could not complete queue live trigger.</b>")

    try:
        await msg.edit_text(
            "\n".join(logs),
            parse_mode=ParseMode.HTML,
            reply_markup=get_control_keyboard()
        )
    except Exception:
        pass

async def cmd_normal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("⛔ <b>Unauthorized:</b> You do not have permission to control the monitor.", parse_mode=ParseMode.HTML)
        return

    msg = await update.message.reply_text("⏳ <b>[INITIATING]</b> Resetting state to NORMAL...", parse_mode=ParseMode.HTML)
    logs = ["<b>🟢 EXECUTING /normal COMMAND:</b>\n"]

    async def update_log(line: str):
        logs.append(line)
        try:
            await msg.edit_text("\n".join(logs), parse_mode=ParseMode.HTML)
        except Exception:
            pass

    success = await update_supabase_state("NORMAL", log_cb=update_log)
    if success:
        logs.append("\n✅ <b>COMPLETE: Site state reset to NORMAL.</b>")
    else:
        logs.append("\n❌ <b>FAILED: Could not update Supabase state.</b>")

    try:
        await msg.edit_text(
            "\n".join(logs),
            parse_mode=ParseMode.HTML,
            reply_markup=get_control_keyboard()
        )
    except Exception:
        pass

async def cmd_push(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("⛔ <b>Unauthorized:</b> You do not have permission to send push notifications.", parse_mode=ParseMode.HTML)
        return

    msg = await update.message.reply_text("⏳ <b>[INITIATING]</b> Triggering push alert dispatch...", parse_mode=ParseMode.HTML)
    logs = ["<b>🔔 EXECUTING /push COMMAND:</b>\n"]

    async def update_log(line: str):
        logs.append(line)
        try:
            await msg.edit_text("\n".join(logs), parse_mode=ParseMode.HTML)
        except Exception:
            pass

    count = await fire_push_notifications("QUEUE_ACTIVE", log_cb=update_log)
    logs.append(f"\n✅ <b>FINISHED: Sent {count} push notifications.</b>")

    try:
        await msg.edit_text(
            "\n".join(logs),
            parse_mode=ParseMode.HTML,
            reply_markup=get_control_keyboard()
        )
    except Exception:
        pass

async def cmd_subscribers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("⏳ Querying subscriber database...")
    valid_tokens, active_subs, total_rows = await fetch_active_subscribers_and_tokens()

    sub_text = (
        f"<b>👥 Pokémon Center Monitor Subscribers</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"• Active Premium Users: <b>{active_subs}</b>\n"
        f"• Valid Push Tokens: <b>{len(valid_tokens)}</b>\n"
        f"• Total DB Records Inspected: <b>{total_rows}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━"
    )
    await msg.edit_text(
        sub_text,
        parse_mode=ParseMode.HTML,
        reply_markup=get_control_keyboard()
    )

# --- CALLBACK QUERY HANDLER FOR BUTTONS ---

async def handle_callback_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = query.from_user.id

    if data == "cb_status":
        await send_status_report(query.edit_message_text)

    elif data == "cb_subscribers":
        valid_tokens, active_subs, total_rows = await fetch_active_subscribers_and_tokens()
        sub_text = (
            f"<b>👥 Pokémon Center Monitor Subscribers</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"• Active Premium Users: <b>{active_subs}</b>\n"
            f"• Valid Push Tokens: <b>{len(valid_tokens)}</b>\n"
            f"• Total DB Records Inspected: <b>{total_rows}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━"
        )
        await query.edit_message_text(
            sub_text,
            parse_mode=ParseMode.HTML,
            reply_markup=get_control_keyboard()
        )

    elif data in ["cb_set_live", "cb_set_normal", "cb_test_push"]:
        if not is_admin(user_id):
            await query.message.reply_text("⛔ <b>Unauthorized:</b> You do not have permission for this action.", parse_mode=ParseMode.HTML)
            return

        if data == "cb_set_live":
            logs = ["<b>🔴 EXECUTING QUEUE LIVE TRIGGER:</b>\n"]
            await query.edit_message_text("\n".join(logs), parse_mode=ParseMode.HTML)

            async def update_log(line: str):
                logs.append(line)
                try:
                    await query.edit_message_text("\n".join(logs), parse_mode=ParseMode.HTML)
                except Exception:
                    pass

            success = await update_supabase_state("QUEUE_ACTIVE", log_cb=update_log)
            if success:
                logs.append("\n🎉 <b>COMPLETE: State set to QUEUE_ACTIVE & alerts sent!</b>")
            else:
                logs.append("\n❌ <b>FAILED: Could not set state.</b>")

            await query.edit_message_text(
                "\n".join(logs),
                parse_mode=ParseMode.HTML,
                reply_markup=get_control_keyboard()
            )

        elif data == "cb_set_normal":
            logs = ["<b>🟢 EXECUTING SITE NORMAL TRIGGER:</b>\n"]
            await query.edit_message_text("\n".join(logs), parse_mode=ParseMode.HTML)

            async def update_log(line: str):
                logs.append(line)
                try:
                    await query.edit_message_text("\n".join(logs), parse_mode=ParseMode.HTML)
                except Exception:
                    pass

            success = await update_supabase_state("NORMAL", log_cb=update_log)
            if success:
                logs.append("\n✅ <b>COMPLETE: Site state reset to NORMAL.</b>")
            else:
                logs.append("\n❌ <b>FAILED: Could not set state.</b>")

            await query.edit_message_text(
                "\n".join(logs),
                parse_mode=ParseMode.HTML,
                reply_markup=get_control_keyboard()
            )

        elif data == "cb_test_push":
            logs = ["<b>🔔 EXECUTING PUSH NOTIFICATION TEST:</b>\n"]
            await query.edit_message_text("\n".join(logs), parse_mode=ParseMode.HTML)

            async def update_log(line: str):
                logs.append(line)
                try:
                    await query.edit_message_text("\n".join(logs), parse_mode=ParseMode.HTML)
                except Exception:
                    pass

            count = await fire_push_notifications("QUEUE_ACTIVE", log_cb=update_log)
            logs.append(f"\n✅ <b>FINISHED: Sent {count} push notifications.</b>")

            await query.edit_message_text(
                "\n".join(logs),
                parse_mode=ParseMode.HTML,
                reply_markup=get_control_keyboard()
            )

# --- POST INITIALIZATION ---

async def post_init(application):
    commands = [
        BotCommand("start", "Start bot & view control menu"),
        BotCommand("live", "🔴 Set state to QUEUE_ACTIVE & send alerts"),
        BotCommand("normal", "🟢 Reset state to NORMAL"),
        BotCommand("status", "📊 Check DB state & subscriber count"),
        BotCommand("push", "🔔 Test push alerts without changing state"),
        BotCommand("subscribers", "👥 View active subscriber details"),
        BotCommand("help", "ℹ️ Display help & menu"),
    ]
    await application.bot.set_my_commands(commands)
    logger.info("🤖 Telegram bot command list set successfully.")

def main():
    if not TELEGRAM_TOKEN:
        print("\n" + "="*60)
        print("❌ CRITICAL ERROR: TELEGRAM_BOT_TOKEN environment variable is missing!")
        print("Please set TELEGRAM_BOT_TOKEN (or TELEGRAM_TOKEN) in your .env file or Railway variables.")
        print("="*60 + "\n")
        return

    logger.info("🚀 Starting Pokémon Center Manual Control Telegram Bot...")

    app = (
        ApplicationBuilder()
        .token(TELEGRAM_TOKEN)
        .post_init(post_init)
        .build()
    )

    # Command Handlers
    app.add_handler(CommandHandler(["start", "help"], cmd_start))
    app.add_handler(CommandHandler(["status"], cmd_status))
    app.add_handler(CommandHandler(["live", "set_live", "queue_active"], cmd_live))
    app.add_handler(CommandHandler(["normal", "set_normal", "queue_normal"], cmd_normal))
    app.add_handler(CommandHandler(["push", "test_push"], cmd_push))
    app.add_handler(CommandHandler(["subscribers"], cmd_subscribers))

    # Callback Query Handler
    app.add_handler(CallbackQueryHandler(handle_callback_query))

    logger.info("✅ Bot initialized. Listening for messages...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
